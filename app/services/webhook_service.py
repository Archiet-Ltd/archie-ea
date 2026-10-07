"""
Webhook service: subscriptions, signed ordered delivery from the event log,
replay and redelivery.

Delivery reads the organisation's event log (``event_log``) in ordinal order.
``fan_out`` turns log events into ``pending`` delivery rows per subscription,
``dispatch_due`` attempts the oldest undelivered row of each subscription (head
of line) and backs off on failure, and ``replay`` / ``redeliver`` create new
rows from the log. Nothing here starts a thread or sleeps; the scheduled job
``webhook_dispatch`` calls ``fan_out`` and ``dispatch_due`` per organisation.
"""

from __future__ import annotations

import calendar
import hashlib
import hmac
import json
import random
import secrets as _secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests
from flask import current_app, g

from app.extensions import db
from app.models.event_log import EventLogRecord
from app.models.webhook import WebhookDelivery, WebhookEvent, WebhookSubscription
from app.services import event_catalogue
from app.services.event_log_service import max_ordinal, read_from_offset, replay_from
from app.utils.ssrf_guard import BlockedOutboundURL, validate_outbound_url

SIGNATURE_TOLERANCE_SECONDS = 300
REPLAY_CAP = 10_000
FAN_OUT_PAGE = 200
ATTEMPTS_PER_RUN = 50
DEAD_AFTER = timedelta(hours=24)
REQUEST_TIMEOUT = (5, 10)
_BACKOFF_SECONDS = (30, 60, 120, 300, 600)
_BACKOFF_CAP_SECONDS = 900
_FORBIDDEN_HEADERS = {"host", "content-length", "transfer-encoding"}
_SIGNED_STATUSES = ("delivered", "success")
_OPEN_STATUSES = ("pending", "retrying")


class WebhookError(ValueError):
    """A request the service refuses, with a plain-words message."""


class WebhookValidationError(WebhookError):
    """Input that is not acceptable (a bad URL, header or event list)."""


class WebhookSecretUnavailable(WebhookError):
    """Secret storage is not configured, so nothing can be stored."""


# --------------------------------------------------------------------------- #
# Signing primitives
# --------------------------------------------------------------------------- #


def hmac_sha256_hex(secret: str, message: bytes) -> str:
    """The one HMAC-SHA256 primitive used to sign and to verify webhooks."""
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _unix(moment: datetime) -> int:
    return calendar.timegm(moment.utctimetuple())


def sign_body(secret: str, timestamp: int, body: bytes) -> str:
    """``v1`` signature: HMAC-SHA256(secret, "<t>." + body)."""
    return hmac_sha256_hex(secret, f"{timestamp}.".encode("ascii") + body)


def verify_signature(
    secret: str,
    header: str,
    body,
    *,
    now: Optional[int] = None,
    tolerance: int = SIGNATURE_TOLERANCE_SECONDS,
) -> bool:
    """True only for a matching ``v1`` signature whose timestamp is within tolerance.

    *header* is ``t=<unix seconds>,v1=<hex>``; *body* is the exact bytes received.
    """
    if not secret or not header:
        return False
    if isinstance(body, str):
        body = body.encode("utf-8")
    parts: Dict[str, List[str]] = {}
    for item in str(header).split(","):
        key, sep, value = item.strip().partition("=")
        if sep:
            parts.setdefault(key, []).append(value)
    try:
        timestamp = int(parts.get("t", [""])[0])
    except ValueError:
        return False
    current = int(now if now is not None else _unix(datetime.utcnow()))
    if abs(current - timestamp) > tolerance:
        return False
    expected = sign_body(secret, timestamp, body)
    return any(hmac.compare_digest(expected, candidate) for candidate in parts.get("v1", []))


def next_backoff(attempt_number: int, *, jitter: bool = True) -> float:
    """Seconds to wait after the *attempt_number*-th failed attempt.

    30 s, 1 min, 2 min, 5 min, 10 min, then every 15 min, plus up to 10% jitter.
    """
    n = max(int(attempt_number), 1)
    base = _BACKOFF_SECONDS[n - 1] if n <= len(_BACKOFF_SECONDS) else _BACKOFF_CAP_SECONDS
    return base * (1 + random.uniform(0, 0.10)) if jitter else float(base)  # fabricated-ok: retry back-off jitter, never displayed as data


def display_status(status: Optional[str]) -> str:
    """Map legacy delivery statuses onto the current ones for display."""
    return {"success": "delivered", "failed": "dead"}.get(status or "", status or "pending")


def _clean_headers(headers) -> Dict[str, str]:
    if headers in (None, ""):
        return {}
    if not isinstance(headers, dict):
        raise WebhookValidationError("headers must be an object of header names and values")
    cleaned: Dict[str, str] = {}
    for name, value in headers.items():
        lowered = str(name).strip().lower()
        if lowered in _FORBIDDEN_HEADERS or lowered.startswith("entelim-"):
            raise WebhookValidationError(f"header {name!r} cannot be set on a subscription")
        if not isinstance(value, (str, int, float)):
            raise WebhookValidationError(f"header {name!r} must have a text value")
        cleaned[str(name)] = str(value)
    return cleaned


def _clean_events(events) -> List[str]:
    if not isinstance(events, list) or not events:
        return ["*"]
    cleaned = []
    for pattern in events:
        if not isinstance(pattern, str) or not pattern.strip() or len(pattern) > 100:
            raise WebhookValidationError(
                "events must be a list of event types, prefixes ending .* or *"
            )
        cleaned.append(pattern.strip())
    return cleaned


def _check_url(url) -> str:
    if not isinstance(url, str) or not url.strip():
        raise WebhookValidationError("A webhook URL is required.")
    url = url.strip()
    try:
        validate_outbound_url(url, require_https=True)
    except BlockedOutboundURL as exc:
        raise WebhookValidationError(f"This URL cannot be used: {exc}") from exc
    return url


def _ensure_secret_storage() -> None:
    """Refuse early when secrets cannot be stored encrypted."""
    from app.modules.codegen.services.credential_encryption import encrypt_credential

    try:
        encrypt_credential("probe")
    except RuntimeError as exc:
        raise WebhookSecretUnavailable(
            "Webhook signing secrets cannot be stored right now because encryption is not configured."
        ) from exc


def _current_org_id() -> Optional[int]:
    return getattr(g, "current_org_id", None)


class WebhookService:
    """The only writer of webhook subscriptions and deliveries."""

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    def create_subscription(
        self,
        user_id: str,
        url: str,
        events: List[str],
        secret: Optional[str] = None,
        description: Optional[str] = None,
        filters: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        webhook_type: str = "generic",
        organization_id: Optional[int] = None,
    ) -> WebhookSubscription:
        """Create a subscription.

        A secret is generated when none is supplied; the generated value is on
        the returned object as ``one_time_secret`` (and nowhere else).
        """
        url = _check_url(url)
        headers = _clean_headers(headers)
        events = _clean_events(events)
        _ensure_secret_storage()

        org_id = organization_id if organization_id is not None else _current_org_id()
        generated = None
        if not secret:
            generated = secret = _secrets.token_hex(32)

        subscription = WebhookSubscription(
            id=str(uuid.uuid4()),
            user_id=str(user_id),
            url=url,
            events=events,
            description=description or "",
            webhook_type=webhook_type
            if webhook_type in ("generic", "teams", "slack")
            else "generic",
            filters=filters or {},
            headers=headers,
            is_active=True,
            # A new subscription does not receive history unless it is replayed.
            last_ordinal=max_ordinal(org_id) if org_id is not None else 0,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        if org_id is not None:
            subscription.organization_id = org_id
        subscription.set_secret(secret)
        db.session.add(subscription)
        db.session.commit()
        subscription.one_time_secret = generated

        current_app.logger.info(
            "Created webhook subscription %s for user %s", subscription.id, user_id
        )
        return subscription

    def _find(
        self, subscription_id: str, user_id: Optional[str] = None
    ) -> Optional[WebhookSubscription]:
        query = WebhookSubscription.query.filter_by(id=subscription_id, is_active=True)
        if user_id is not None:
            query = query.filter_by(user_id=str(user_id))
        return query.first()

    def get_user_subscriptions(self, user_id: str) -> List[WebhookSubscription]:
        """Get all subscriptions for a user"""
        # user_id column is varchar; current_user.id is int -> cast to avoid
        # "operator does not exist: character varying = integer".
        return WebhookSubscription.query.filter_by(user_id=str(user_id), is_active=True).all()

    def list_subscriptions(self) -> List[WebhookSubscription]:
        """Every active subscription of the caller's organisation, newest first."""
        return (
            WebhookSubscription.query.filter_by(is_active=True)
            .order_by(WebhookSubscription.created_at.desc())
            .all()
        )

    def get_subscription(
        self, subscription_id: str, user_id: Optional[str] = None
    ) -> Optional[WebhookSubscription]:
        """One subscription; scoped to *user_id* when given, else to the organisation."""
        return self._find(subscription_id, user_id)

    def get_subscription_by_id(self, subscription_id: str) -> Optional[WebhookSubscription]:
        """Get a subscription by ID (internal use)"""
        return self._find(subscription_id)

    def update_subscription(
        self, subscription_id: str, user_id: Optional[str], updates: Dict
    ) -> Optional[WebhookSubscription]:
        """Update a subscription. *user_id* None means any user of the organisation."""
        subscription = self._find(subscription_id, user_id)
        if not subscription:
            return None

        allowed_fields = [
            "url",
            "events",
            "secret",
            "description",
            "webhook_type",
            "filters",
            "headers",
            "is_active",
        ]
        clean: Dict = {}
        for field, value in updates.items():
            if field not in allowed_fields:
                continue
            if field == "url":
                value = _check_url(value)
            elif field == "headers":
                value = _clean_headers(value)
            elif field == "events":
                value = _clean_events(value)
            elif field == "webhook_type" and value not in ("generic", "teams", "slack"):
                value = "generic"
            clean[field] = value
        if "secret" in clean:
            _ensure_secret_storage()
        for field, value in clean.items():
            if field == "secret":
                if value:
                    subscription.set_secret(str(value))
            else:
                setattr(subscription, field, value)

        subscription.updated_at = datetime.utcnow()
        db.session.commit()
        current_app.logger.info("Updated webhook subscription %s", subscription_id)
        return subscription

    def rotate_secret(self, subscription_id: str, user_id: Optional[str] = None) -> Optional[str]:
        """Generate a new secret, store it encrypted, return it once. None if not found."""
        subscription = self._find(subscription_id, user_id)
        if not subscription:
            return None
        _ensure_secret_storage()
        new_secret = _secrets.token_hex(32)
        subscription.set_secret(new_secret)
        subscription.updated_at = datetime.utcnow()
        db.session.commit()
        current_app.logger.info("Rotated secret of webhook subscription %s", subscription_id)
        return new_secret

    def delete_subscription(self, subscription_id: str, user_id: Optional[str] = None) -> bool:
        """Deactivate a subscription."""
        subscription = self._find(subscription_id, user_id)
        if not subscription:
            return False
        subscription.is_active = False
        subscription.updated_at = datetime.utcnow()
        db.session.commit()
        current_app.logger.info("Deleted webhook subscription %s", subscription_id)
        return True

    # ------------------------------------------------------------------
    # Bodies
    # ------------------------------------------------------------------

    def build_body(self, subscription: WebhookSubscription, event: Dict) -> str:
        """Serialise *event* once for *subscription*; these exact bytes are signed and sent.

        *event* has ``event_type``, ``event_id``, ``payload``, ``ordinal``,
        ``created_at`` (ISO text), ``organization_id`` and optionally
        ``entity_type`` / ``entity_id``.
        """
        webhook_type = subscription.webhook_type or "generic"
        timestamp = event.get("created_at") or datetime.utcnow().isoformat()
        if webhook_type in ("teams", "slack"):
            event_data = {
                "event_type": event["event_type"],
                "payload": event.get("payload") or {},
                "metadata": {},
                "event_id": event.get("event_id"),
                "timestamp": timestamp,
            }
            formatted = (
                self.format_teams_payload(event_data)
                if webhook_type == "teams"
                else self.format_slack_payload(event_data)
            )
            return json.dumps(formatted, sort_keys=True, separators=(",", ":"))

        subject = None
        if event.get("entity_type") and event.get("entity_id") is not None:
            subject = f"/{event['entity_type']}/{event['entity_id']}"
        org_id = event.get("organization_id")
        envelope = {
            "specversion": "1.0",
            "id": event.get("event_id"),
            "type": event["event_type"],
            "source": f"/organisations/{org_id}/entelim",
            "subject": subject,
            "time": timestamp,
            "datacontenttype": "application/json",
            "organisationid": str(org_id),
            "sequence": str(event.get("ordinal") or 0),
            "data": event.get("payload") or {},
        }
        return json.dumps(envelope, sort_keys=True, separators=(",", ":"))

    # ------------------------------------------------------------------
    # Fan-out and delivery
    # ------------------------------------------------------------------

    def _matches(self, subscription: WebhookSubscription, event: Dict) -> bool:
        patterns = subscription.events or ["*"]
        if not any(event_catalogue.matches(p, event["event_type"]) for p in patterns):
            return False
        if subscription.filters and not self._matches_filters(
            event.get("payload") or {}, subscription.filters
        ):
            return False
        return True

    def _new_delivery(
        self,
        subscription: WebhookSubscription,
        event: Dict,
        *,
        is_replay=False,
        replay_of_id=None,
        is_test=False,
    ) -> WebhookDelivery:
        return WebhookDelivery(
            id=str(uuid.uuid4()),
            subscription_id=subscription.id,
            organization_id=subscription.organization_id,
            log_event_id=event.get("event_id"),
            event_ordinal=event.get("ordinal"),
            event_type=event["event_type"],
            payload=event.get("payload") or {},
            request_body=self.build_body(subscription, event),
            status="pending",
            attempt_count=0,
            is_replay=is_replay,
            replay_of_id=replay_of_id,
            is_test=is_test,
            created_at=datetime.utcnow(),
        )

    def _active_subscriptions(self, org_id: int) -> List[WebhookSubscription]:
        return (
            WebhookSubscription.query.filter(
                WebhookSubscription.organization_id == org_id,
                WebhookSubscription.is_active.is_(True),
            )
            .order_by(WebhookSubscription.created_at, WebhookSubscription.id)
            .all()
        )

    def fan_out(self, org_id: int) -> int:
        """Create pending deliveries from new event-log entries; returns how many."""
        created = 0
        for sub_id in [s.id for s in self._active_subscriptions(org_id)]:
            subscription = (
                WebhookSubscription.query.filter(
                    WebhookSubscription.id == sub_id,
                    WebhookSubscription.organization_id == org_id,
                    WebhookSubscription.is_active.is_(True),
                )
                .with_for_update(skip_locked=True)
                .first()
            )
            if subscription is None:
                continue
            events = read_from_offset(org_id, subscription.last_ordinal or 0, limit=FAN_OUT_PAGE)
            if not events:
                db.session.commit()  # releases the row lock; nothing is pending
                continue
            for event in events:
                if self._matches(subscription, event):
                    db.session.add(self._new_delivery(subscription, event))
                    created += 1
            subscription.last_ordinal = events[-1]["ordinal"]
            db.session.commit()
        return created

    def _head_of_line(self, subscription_id: str, org_id: int) -> Optional[WebhookDelivery]:
        return (
            WebhookDelivery.query.filter(
                WebhookDelivery.subscription_id == subscription_id,
                WebhookDelivery.organization_id == org_id,
                WebhookDelivery.is_test.is_(False),
                WebhookDelivery.status.in_(_OPEN_STATUSES),
            )
            .order_by(
                WebhookDelivery.is_replay,
                WebhookDelivery.event_ordinal,
                WebhookDelivery.created_at,
                WebhookDelivery.id,
            )
            .first()
        )

    def dispatch_due(self, org_id: int, *, now: Optional[datetime] = None) -> int:
        """Attempt due deliveries, head of line per subscription; returns attempts made."""
        attempts = 0
        for sub_id in [s.id for s in self._active_subscriptions(org_id)]:
            while attempts < ATTEMPTS_PER_RUN:
                subscription = (
                    WebhookSubscription.query.filter(
                        WebhookSubscription.id == sub_id,
                        WebhookSubscription.organization_id == org_id,
                        WebhookSubscription.is_active.is_(True),
                    )
                    .with_for_update(skip_locked=True)
                    .first()
                )
                if subscription is None:
                    break
                head = self._head_of_line(sub_id, org_id)
                moment = now or datetime.utcnow()
                if head is None or (head.next_attempt_at and head.next_attempt_at > moment):
                    db.session.commit()  # releases the row lock; nothing is pending
                    break
                self.attempt(head, now=moment)
                attempts += 1
                if head.status in _OPEN_STATUSES:
                    break  # failed and waiting: nothing behind it may go first
        return attempts

    def attempt(self, delivery: WebhookDelivery, *, now: Optional[datetime] = None) -> bool:
        """Make one signed attempt and record it. True when the subscriber answered 2xx."""
        moment = now or datetime.utcnow()
        subscription = WebhookSubscription.query.filter(
            WebhookSubscription.id == delivery.subscription_id,
            WebhookSubscription.organization_id == delivery.organization_id,
        ).first()
        delivery.attempt_count = (delivery.attempt_count or 0) + 1
        delivery.last_attempt_at = moment
        if delivery.first_attempt_at is None:
            delivery.first_attempt_at = moment
        delivery.response_status = None
        delivery.response_body = None
        delivery.error_message = None

        if subscription is None:
            return self._record_failure(delivery, moment, "subscription no longer exists")

        body = (delivery.request_body or "").encode("utf-8")
        secret = subscription.get_secret()
        if not secret:
            # Every delivery is signed. A row with no usable secret (an old row
            # created without one, or a stored secret that no longer decrypts)
            # is not sent unsigned; rotating the secret on the screen fixes it.
            return self._record_failure(
                delivery, moment, "no usable signing secret: rotate the secret"
            )
        custom = {
            name: value
            for name, value in (subscription.headers or {}).items()
            if str(name).lower() not in _FORBIDDEN_HEADERS
            and not str(name).lower().startswith("entelim-")
        }
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Entelim-Webhook/1.0",
            **custom,
            "Entelim-Webhook-Id": delivery.id,
            "Entelim-Event-Id": delivery.log_event_id or "",
            "Entelim-Event-Type": delivery.event_type,
        }
        timestamp = _unix(moment)
        delivery.signature_timestamp = timestamp
        delivery.signature = None
        headers["Entelim-Timestamp"] = str(timestamp)
        delivery.signature = sign_body(secret, timestamp, body)
        headers["Entelim-Signature"] = f"t={timestamp},v1={delivery.signature}"

        try:
            validate_outbound_url(subscription.url, require_https=not subscription.is_plain_http)
        except BlockedOutboundURL:
            return self._record_failure(delivery, moment, "target address not allowed")

        try:
            response = requests.post(
                subscription.url,
                data=body,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            return self._record_failure(delivery, moment, f"request failed: {type(exc).__name__}")

        delivery.response_status = response.status_code
        delivery.response_body = (response.text or "")[:1000]
        if 200 <= response.status_code < 300:
            delivery.status = "delivered"
            delivery.delivered_at = moment
            delivery.next_attempt_at = None
            db.session.commit()
            return True
        if 300 <= response.status_code < 400:
            return self._record_failure(delivery, moment, "redirects are not followed")
        return self._record_failure(delivery, moment, f"HTTP {response.status_code}")

    def _record_failure(self, delivery: WebhookDelivery, moment: datetime, reason: str) -> bool:
        delivery.error_message = reason
        first = delivery.first_attempt_at or moment
        if delivery.is_test or moment - first >= DEAD_AFTER:
            delivery.status = "dead"
            delivery.dead_lettered_at = moment
            delivery.next_attempt_at = None
        else:
            delivery.status = "retrying"
            delivery.next_attempt_at = moment + timedelta(
                seconds=next_backoff(delivery.attempt_count)
            )
        db.session.commit()
        return False

    def test_subscription(
        self, subscription_id: str, user_id: Optional[str] = None
    ) -> Optional[Dict]:
        """Send one synchronous ``webhook.test`` attempt and record it (no retry)."""
        subscription = self._find(subscription_id, user_id)
        if not subscription:
            return None
        now = datetime.utcnow()
        event = {
            "event_type": "webhook.test",
            "event_id": str(uuid.uuid4()),
            "ordinal": 0,
            "organization_id": subscription.organization_id,
            "created_at": now.isoformat(),
            "payload": {
                "action": "test",
                "id": None,
                "message": "This is a test webhook",
                "subscription_id": subscription_id,
            },
        }
        delivery = self._new_delivery(subscription, event, is_test=True)
        delivery.event_ordinal = None
        db.session.add(delivery)
        db.session.commit()
        success = self.attempt(delivery, now=now)
        return {
            "delivery_id": delivery.id,
            "success": success,
            "attempts": delivery.attempt_count,
            "status": delivery.status,
        }

    # ------------------------------------------------------------------
    # Replay and redelivery
    # ------------------------------------------------------------------

    def replay(
        self,
        subscription_id: str,
        *,
        from_ordinal: Optional[int] = None,
        since: Optional[datetime] = None,
        actor=None,
    ) -> Optional[Dict]:
        """Queue matching log events again for one subscription, in ordinal order.

        Returns ``{"count", "capped", "cap"}`` or None when the subscription is
        not found in the caller's organisation. ``last_ordinal`` is not moved.
        """
        subscription = self._find(subscription_id)
        if subscription is None:
            return None
        org_id = subscription.organization_id
        if (from_ordinal is None) == (since is None):
            raise WebhookValidationError("Give either a sequence number or a time to replay from.")

        if since is not None and since.tzinfo is None:
            # The log's timestamps are timezone-aware; a bare time means UTC.
            since = since.replace(tzinfo=timezone.utc)

        events: List[Dict] = []
        if from_ordinal is not None:
            cursor = max(int(from_ordinal) - 1, 0)
            while len(events) <= REPLAY_CAP:
                page = read_from_offset(org_id, cursor, limit=1000)
                if not page:
                    break
                events.extend(page)
                cursor = page[-1]["ordinal"]
        else:
            events = replay_from(org_id, since, limit=REPLAY_CAP + 1)
        capped = len(events) > REPLAY_CAP
        events = events[:REPLAY_CAP]

        count = 0
        for event in events:
            if self._matches(subscription, event):
                db.session.add(self._new_delivery(subscription, event, is_replay=True))
                count += 1
        db.session.commit()
        current_app.logger.info(
            "Webhook replay of %s queued %s deliveries (actor %s)", subscription_id, count, actor
        )
        return {"count": count, "capped": capped, "cap": REPLAY_CAP}

    def redeliver(self, delivery_id: str, *, actor=None) -> Optional[WebhookDelivery]:
        """Queue a copy of one delivery as a new pending replay; None if not found."""
        original = WebhookDelivery.query.filter_by(id=delivery_id).first()
        if original is None or original.is_test or not original.request_body:
            return None
        subscription = self._find(original.subscription_id)
        if subscription is None or subscription.organization_id != original.organization_id:
            return None
        copy = WebhookDelivery(
            id=str(uuid.uuid4()),
            subscription_id=original.subscription_id,
            organization_id=original.organization_id,
            log_event_id=original.log_event_id,
            event_ordinal=original.event_ordinal,
            event_type=original.event_type,
            payload=original.payload,
            request_body=original.request_body,
            status="pending",
            attempt_count=0,
            is_replay=True,
            replay_of_id=original.id,
            created_at=datetime.utcnow(),
        )
        db.session.add(copy)
        db.session.commit()
        current_app.logger.info(
            "Webhook redelivery of %s queued as %s (actor %s)", delivery_id, copy.id, actor
        )
        return copy

    def retry_event(self, event_id: str, *, actor=None) -> Optional[int]:
        """Redeliver this event's dead or retrying deliveries; None when the event is unknown."""
        known = EventLogRecord.query.filter(EventLogRecord.event_id == event_id).first()
        if known is None:
            return None
        rows = (
            WebhookDelivery.query.filter(
                WebhookDelivery.log_event_id == event_id,
                WebhookDelivery.status.in_(("dead", "retrying", "failed")),
                WebhookDelivery.is_test.is_(False),
            )
            .order_by(WebhookDelivery.event_ordinal, WebhookDelivery.created_at)
            .all()
        )
        count = 0
        for row in rows:
            if self.redeliver(row.id, actor=actor) is not None:
                count += 1
        return count

    def list_deliveries(self, subscription_id: str, *, limit: int = 50, offset: int = 0):
        """Newest deliveries of one subscription (None when it is not in the caller's organisation)."""
        subscription = self._find(subscription_id)
        if subscription is None:
            return None
        return (
            WebhookDelivery.query.filter(
                WebhookDelivery.subscription_id == subscription.id,
                WebhookDelivery.organization_id == subscription.organization_id,
            )
            .order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

    def signature_status(self, subscription: WebhookSubscription, delivery: WebhookDelivery) -> str:
        """Plain-words signature state of a delivery's last attempt."""
        if not delivery.signature or delivery.signature_timestamp is None:
            return "Not signed yet"
        secret = subscription.get_secret()
        body = (delivery.request_body or "").encode("utf-8")
        when = datetime.utcfromtimestamp(delivery.signature_timestamp).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
        if secret and hmac.compare_digest(
            sign_body(secret, delivery.signature_timestamp, body), delivery.signature
        ):
            return f"Signed at {when}, verifies with the current secret"
        return f"Signed at {when} with a previous secret"

    def get_events(self, limit: int = 50, offset: int = 0) -> List[EventLogRecord]:
        """The caller organisation's event log, newest first."""
        return (
            EventLogRecord.query.order_by(EventLogRecord.ordinal.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

    # ------------------------------------------------------------------
    # Filters and formatters
    # ------------------------------------------------------------------

    def _matches_filters(self, payload: Dict, filters: Dict) -> bool:
        """Check if payload matches the subscription filters"""
        for key, expected_value in filters.items():
            if key not in payload:
                return False

            actual_value = payload[key]
            if isinstance(expected_value, dict):
                # Nested filter
                if not isinstance(actual_value, dict):
                    return False
                if not self._matches_filters(actual_value, expected_value):
                    return False
            elif actual_value != expected_value:
                return False

        return True

    def format_teams_payload(self, event_data: Dict) -> Dict:
        """Format event data as a Microsoft Teams Adaptive Card payload.

        Produces a valid Teams Incoming Webhook message with an AdaptiveCard
        v1.4 attachment so rich formatting is rendered in the Teams client.
        """
        event_type = event_data.get("event_type", "event")
        payload = event_data.get("payload", {})
        timestamp = event_data.get("timestamp", datetime.utcnow().isoformat())

        # Build a human-readable summary from the inner payload dict
        summary_lines = []
        for key, value in payload.items() if isinstance(payload, dict) else []:
            if isinstance(value, (str, int, float, bool)) and value not in ("", None):
                label = key.replace("_", " ").title()
                summary_lines.append(f"**{label}:** {value}")
        summary_text = "\n\n".join(summary_lines) if summary_lines else "No additional details."

        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [
                            {
                                "type": "TextBlock",
                                "text": event_type.replace(".", " ").title(),
                                "weight": "Bolder",
                                "size": "Medium",
                                "wrap": True,
                            },
                            {
                                "type": "TextBlock",
                                "text": summary_text,
                                "wrap": True,
                                "spacing": "Medium",
                            },
                            {
                                "type": "TextBlock",
                                "text": f"Sent at {timestamp}",
                                "isSubtle": True,
                                "size": "Small",
                                "wrap": True,
                            },
                        ],
                    },
                }
            ],
        }

    def format_slack_payload(self, event_data: Dict) -> Dict:
        """Format event data as a Slack Block Kit message payload.

        Produces a Slack message with a header block and a mrkdwn section so
        the notification renders well in Slack channels.
        """
        event_type = event_data.get("event_type", "event")
        payload = event_data.get("payload", {})
        timestamp = event_data.get("timestamp", datetime.utcnow().isoformat())

        # Build field lines from the inner payload dict
        field_lines = []
        for key, value in payload.items() if isinstance(payload, dict) else []:
            if isinstance(value, (str, int, float, bool)) and value not in ("", None):
                label = key.replace("_", " ").title()
                field_lines.append(f"*{label}:* {value}")
        body_text = "\n".join(field_lines) if field_lines else "_No additional details._"

        return {
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": event_type.replace(".", " ").title(),
                        "emoji": False,
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": body_text,
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Sent at {timestamp}",
                        }
                    ],
                },
            ]
        }

    def process_incoming_webhook(self, subscription_id: str, payload: Dict, headers: Dict) -> Dict:
        """Process an incoming webhook from external services"""
        # This route is unauthenticated (verified by the subscription's own HMAC
        # secret, not a session) -- there is no g.current_org_id to fall back on,
        # so the receiving subscription's own org is the only trustworthy source.
        subscription = self.get_subscription_by_id(subscription_id)
        # Store the incoming webhook event
        event = WebhookEvent(
            id=str(uuid.uuid4()),
            organization_id=subscription.organization_id if subscription else None,
            event_type="webhook.incoming",
            payload={"subscription_id": subscription_id, "payload": payload, "headers": headers},
            user_id=None,  # External webhook
            event_metadata={"incoming": True},
            created_at=datetime.utcnow(),
        )

        db.session.add(event)
        db.session.commit()

        # Here you could trigger internal workflows based on the webhook
        # For now, just log and return success
        current_app.logger.info(f"Processed incoming webhook for subscription {subscription_id}")

        return {"event_id": event.id, "processed_at": event.created_at.isoformat()}

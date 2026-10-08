"""
Webhook service for managing event-driven notifications
"""

import hashlib
import hmac
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests
from flask import current_app

from app.extensions import db
from app.models.webhook import WebhookDelivery, WebhookEvent, WebhookSubscription
from app.utils.ssrf_guard import BlockedOutboundURL, validate_outbound_url

# One bounded pool per process runs every background delivery. A thread per
# event let a hanging receiver pile up unlimited workers.
_DEFAULT_DELIVERY_WORKERS = 8
# A delivery row left "pending" this long was lost with its process.
PENDING_STALE_AFTER = timedelta(minutes=10)

_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()


def _get_delivery_executor(max_workers: int) -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=max(1, int(max_workers)), thread_name_prefix="webhook-delivery"
            )
        return _executor


class WebhookService:
    """Service for managing webhook subscriptions and event delivery"""

    def __init__(self):
        self.max_retries = current_app.config.get("WEBHOOK_MAX_RETRIES", 3)
        self.retry_delay = current_app.config.get("WEBHOOK_RETRY_DELAY", 60)  # seconds
        self.timeout = current_app.config.get("WEBHOOK_TIMEOUT", 30)  # seconds
        self.max_workers = current_app.config.get(
            "WEBHOOK_DELIVERY_MAX_WORKERS", _DEFAULT_DELIVERY_WORKERS
        )

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
    ) -> WebhookSubscription:
        """Create a new webhook subscription"""
        subscription = WebhookSubscription(
            id=str(uuid.uuid4()),
            user_id=user_id,
            url=url,
            events=events,
            secret=secret,
            description=description or "",
            webhook_type=webhook_type if webhook_type in ("generic", "teams", "slack") else "generic",
            filters=filters or {},
            headers=headers or {},
            is_active=True,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        db.session.add(subscription)
        db.session.commit()

        current_app.logger.info(
            f"Created webhook subscription {subscription.id} for user {user_id}"
        )
        return subscription

    def get_user_subscriptions(self, user_id: str) -> List[WebhookSubscription]:
        """Get all subscriptions for a user"""
        # user_id column is varchar; current_user.id is int -> cast to avoid
        # "operator does not exist: character varying = integer".
        return WebhookSubscription.query.filter_by(
            user_id=str(user_id), is_active=True
        ).all()

    def get_subscription(self, subscription_id: str, user_id: str) -> Optional[WebhookSubscription]:
        """Get a specific subscription for a user"""
        return WebhookSubscription.query.filter_by(
            id=subscription_id, user_id=user_id, is_active=True
        ).first()

    def get_subscription_by_id(self, subscription_id: str) -> Optional[WebhookSubscription]:
        """Get a subscription by ID (internal use)"""
        return WebhookSubscription.query.filter_by(id=subscription_id, is_active=True).first()

    def update_subscription(
        self, subscription_id: str, user_id: str, updates: Dict
    ) -> Optional[WebhookSubscription]:
        """Update a webhook subscription"""
        subscription = self.get_subscription(subscription_id, user_id)
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
        for field, value in updates.items():
            if field in allowed_fields:
                setattr(subscription, field, value)

        subscription.updated_at = datetime.utcnow()
        db.session.commit()

        current_app.logger.info(f"Updated webhook subscription {subscription_id}")
        return subscription

    def delete_subscription(self, subscription_id: str, user_id: str) -> bool:
        """Delete a webhook subscription"""
        subscription = self.get_subscription(subscription_id, user_id)
        if not subscription:
            return False

        subscription.is_active = False
        subscription.updated_at = datetime.utcnow()
        db.session.commit()

        current_app.logger.info(f"Deleted webhook subscription {subscription_id}")
        return True

    def test_subscription(self, subscription_id: str, user_id: str) -> Optional[Dict]:
        """Test a webhook subscription by sending a test event"""
        subscription = self.get_subscription(subscription_id, user_id)
        if not subscription:
            return None

        test_event = {
            "event_type": "webhook.test",
            "payload": {
                "message": "This is a test webhook",
                "timestamp": datetime.utcnow().isoformat(),
                "subscription_id": subscription_id,
            },
            "metadata": {"test": True},
        }

        return self._deliver_webhook(subscription, test_event)

    def publish_event(
        self, event_type: str, payload: Dict, user_id: str, metadata: Optional[Dict] = None
    ) -> WebhookEvent:
        """Publish an event to all subscribed webhooks"""
        event = WebhookEvent(
            id=str(uuid.uuid4()),
            event_type=event_type,
            payload=payload,
            user_id=user_id,
            event_metadata=metadata or {},
            created_at=datetime.utcnow(),
        )

        db.session.add(event)
        db.session.commit()

        # Find matching subscriptions
        subscriptions = self._find_matching_subscriptions(event_type, payload)

        # Deliver to subscriptions asynchronously on the bounded pool. The worker
        # has no request, so it gets the real app and plain ids, not ORM
        # instances bound to this request's session.
        org_id = event.organization_id
        if org_id is None:
            current_app.logger.error(
                f"Webhook event {event.id} ({event_type}) has no organization; nothing delivered"
            )
        elif subscriptions:
            subscription_ids = [s.id for s in subscriptions if s.organization_id == org_id]
            if subscription_ids:
                self._delivery_future = _get_delivery_executor(self.max_workers).submit(
                    self._run_delivery_in_app_context,
                    current_app._get_current_object(),
                    event.id,
                    org_id,
                    subscription_ids,
                )

        current_app.logger.info(
            f"Published event {event_type} with {len(subscriptions)} subscriptions"
        )
        return event

    def _find_matching_subscriptions(
        self, event_type: str, payload: Dict
    ) -> List[WebhookSubscription]:
        """Find subscriptions that match the event"""
        subscriptions = WebhookSubscription.query.filter_by(is_active=True).all()
        matching = []

        for subscription in subscriptions:
            # Check if event type matches
            if event_type not in subscription.events and "*" not in subscription.events:
                continue

            # Check filters
            if subscription.filters:
                if not self._matches_filters(payload, subscription.filters):
                    continue

            matching.append(subscription)

        return matching

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

    @staticmethod
    def _snapshot_subscription(subscription: WebhookSubscription) -> SimpleNamespace:
        """Plain copy of what delivery needs, so no ORM object (and no open
        transaction) is touched while the network call is in flight."""
        return SimpleNamespace(
            id=subscription.id,
            organization_id=subscription.organization_id,
            url=subscription.url,
            headers=dict(subscription.headers or {}),
            secret=subscription.secret,
            webhook_type=getattr(subscription, "webhook_type", "generic") or "generic",  # model-safety-ok
        )

    @staticmethod
    def _deliverable(subscription, org_id) -> bool:
        """Active at delivery time and owned by the event's organisation."""
        return (
            subscription is not None
            and bool(subscription.is_active)
            and subscription.organization_id == org_id
        )

    def _run_delivery_in_app_context(self, app, event_id, org_id, subscription_ids):
        """Worker body: deliver one event inside its own application context.

        Re-loads the event and subscriptions by id under the event's tenant
        scope, so nothing from the publishing request's session is shared with
        this worker. Never raises: failures are logged through the captured app.
        """
        from app.jobs.tenant_safe_job import tenant_scope

        if org_id is None:
            app.logger.error(f"Webhook event {event_id} has no organization; nothing delivered")
            return
        with app.app_context():
            try:
                with tenant_scope(org_id):
                    event = db.session.get(WebhookEvent, event_id)
                    if event is None or event.organization_id != org_id:
                        return
                    snapshots = []
                    for sid in subscription_ids:
                        sub = db.session.get(WebhookSubscription, sid)
                        if self._deliverable(sub, org_id):
                            snapshots.append(self._snapshot_subscription(sub))
                    event_data = {
                        "event_type": event.event_type,
                        "payload": event.payload,
                        "metadata": event.event_metadata,
                        "event_id": event.id,
                        "timestamp": event.created_at.isoformat(),
                    }
                    db.session.remove()  # no transaction or connection held from here on
                    self._deliver_snapshots(event_data, snapshots)
            except Exception as e:  # never let the worker die silently
                try:
                    db.session.rollback()
                except Exception:
                    pass
                app.logger.error(f"Webhook delivery failed for event {event_id}: {e}")
            finally:
                try:
                    db.session.remove()
                except Exception:
                    pass

    def _deliver_snapshots(self, event_data: Dict, snapshots: List[SimpleNamespace]):
        for snap in snapshots:
            try:
                self._deliver_webhook(snap, event_data)
            except Exception as e:
                try:
                    db.session.rollback()
                except Exception:
                    pass
                current_app.logger.error(
                    f"Failed to deliver event {event_data.get('event_id')} "
                    f"to subscription {snap.id}: {e}"
                )

    # ------------------------------------------------------------------
    # Payload formatters for Teams and Slack
    # ------------------------------------------------------------------

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
        for key, value in (payload.items() if isinstance(payload, dict) else []):
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
        for key, value in (payload.items() if isinstance(payload, dict) else []):
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

    def _build_payload_for_subscription(
        self, subscription: WebhookSubscription, event_data: Dict
    ) -> Dict:
        """Return the payload formatted for the subscription's webhook_type."""
        webhook_type = getattr(subscription, "webhook_type", "generic") or "generic"  # model-safety-ok
        if webhook_type == "teams":
            return self.format_teams_payload(event_data)
        if webhook_type == "slack":
            return self.format_slack_payload(event_data)
        return event_data

    def _outbound_headers(self, snap, formatted_payload: Dict) -> Dict:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Enterprise-Architecture-Webhook/1.0",
            **(snap.headers or {}),
        }
        # Signature is set last and covers the formatted payload.
        if snap.secret:
            payload_str = json.dumps(formatted_payload, sort_keys=True)
            headers["X-Webhook-Signature"] = hmac.new(
                snap.secret.encode(), payload_str.encode(), hashlib.sha256
            ).hexdigest()
        return headers

    def _deliver_webhook(self, subscription, event_data: Dict) -> Dict:
        """Deliver webhook to a single subscription (ORM row or snapshot)."""
        snap = (
            subscription
            if isinstance(subscription, SimpleNamespace)
            else self._snapshot_subscription(subscription)
        )
        formatted_payload = self._build_payload_for_subscription(snap, event_data)
        headers = self._outbound_headers(snap, formatted_payload)

        delivery = WebhookDelivery(
            id=str(uuid.uuid4()),
            event_id=event_data.get("event_id"),
            subscription_id=snap.id,
            # Explicit, not the column default: this runs on a worker with no
            # request context, so TenantMixin's g.current_org_id default can't
            # resolve it. The subscription was org-checked before delivery.
            organization_id=snap.organization_id,
            event_type=event_data.get("event_type"),
            payload=formatted_payload,
            status="pending",
            attempt_count=0,
            created_at=datetime.utcnow(),
        )
        delivery_id = delivery.id
        db.session.add(delivery)
        db.session.commit()

        success, attempts = self._attempt_delivery(delivery_id, snap, headers, formatted_payload)
        return {"delivery_id": delivery_id, "success": success, "attempts": attempts}

    def _record_attempt(self, delivery_id: str, **fields) -> None:
        """Write one attempt's outcome in its own short transaction."""
        try:
            delivery = db.session.get(WebhookDelivery, delivery_id)
            if delivery is not None:
                for key, value in fields.items():
                    setattr(delivery, key, value)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        finally:
            db.session.remove()

    def _attempt_delivery(self, delivery_id: str, snap, headers: Dict, payload: Dict):
        """Send with retries. Holds no transaction or pooled connection while
        the HTTP call is in flight; each outcome is a new short transaction.

        Returns (success, attempts). Logs the subscription id and URL host only:
        the full URL is a bearer credential for Slack/Teams/Azure endpoints.
        """
        url = snap.url
        host = urlparse(url).hostname or "unknown-host"
        label = f"subscription {snap.id} (host {host})"

        try:
            validate_outbound_url(url, require_https=False)
        except BlockedOutboundURL as e:
            self._record_attempt(
                delivery_id,
                status="failed",
                attempt_count=1,
                last_attempt_at=datetime.utcnow(),
                error_message=f"Blocked outbound URL: {e}"[:500],
            )
            current_app.logger.error(f"Webhook to {label} blocked by outbound URL guard")
            return False, 1

        attempts = 0
        for attempt in range(self.max_retries):
            attempts = attempt + 1
            now = datetime.utcnow()
            try:
                response = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
            except requests.RequestException as e:
                self._record_attempt(
                    delivery_id,
                    status="failed",
                    attempt_count=attempts,
                    last_attempt_at=now,
                    error_message=str(e)[:500],
                )
            else:
                outcome = dict(
                    attempt_count=attempts,
                    last_attempt_at=now,
                    response_status=response.status_code,
                    response_body=response.text[:1000],
                )
                if 200 <= response.status_code < 300:
                    self._record_attempt(
                        delivery_id, status="success", delivered_at=datetime.utcnow(),
                        error_message=None, **outcome,
                    )
                    current_app.logger.info(f"Delivered webhook to {label}")
                    return True, attempts
                self._record_attempt(
                    delivery_id,
                    status="failed",
                    error_message=f"HTTP {response.status_code}: {response.text[:200]}",
                    **outcome,
                )
            if attempt < self.max_retries - 1:
                time.sleep(self.retry_delay * (attempt + 1))  # Exponential backoff

        current_app.logger.error(
            f"Failed to deliver webhook to {label} after {self.max_retries} attempts"
        )
        return False, attempts

    def get_events(self, limit: int = 50, offset: int = 0) -> List[WebhookEvent]:
        """Get webhook events (admin function)"""
        return (
            WebhookEvent.query.order_by(WebhookEvent.created_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

    def retry_event(self, event_id: str) -> bool:
        """Retry this event's failed deliveries, and deliveries left pending
        for over ten minutes (a worker lost with its process). Runs on the same
        bounded pool and delivery body as first delivery: signed, with the
        subscription's custom headers."""
        event = WebhookEvent.query.get(event_id)
        if not event:
            return False
        org_id = event.organization_id
        if org_id is None:
            current_app.logger.error(f"Webhook event {event_id} has no organization; no retry")
            return True
        self._delivery_future = _get_delivery_executor(self.max_workers).submit(
            self._run_retry_in_app_context,
            current_app._get_current_object(),
            event_id,
            org_id,
        )
        return True

    def _run_retry_in_app_context(self, app, event_id, org_id):
        from sqlalchemy import and_, or_

        from app.jobs.tenant_safe_job import tenant_scope

        if org_id is None:
            app.logger.error(f"Webhook event {event_id} has no organization; no retry")
            return
        with app.app_context():
            try:
                with tenant_scope(org_id):
                    stale = datetime.utcnow() - PENDING_STALE_AFTER
                    rows = WebhookDelivery.query.filter(
                        WebhookDelivery.event_id == event_id,
                        or_(
                            WebhookDelivery.status == "failed",
                            and_(
                                WebhookDelivery.status == "pending",
                                WebhookDelivery.created_at < stale,
                            ),
                        ),
                    ).all()
                    work = []
                    for delivery in rows:
                        sub = db.session.get(WebhookSubscription, delivery.subscription_id)
                        if self._deliverable(sub, org_id):
                            snap = self._snapshot_subscription(sub)
                            work.append((delivery.id, snap, delivery.payload))
                    db.session.remove()
                    for delivery_id, snap, payload in work:
                        try:
                            self._attempt_delivery(
                                delivery_id, snap, self._outbound_headers(snap, payload), payload
                            )
                        except Exception as e:
                            db.session.rollback()
                            app.logger.error(f"Retry of delivery {delivery_id} failed: {e}")
            except Exception as e:
                try:
                    db.session.rollback()
                except Exception:
                    pass
                app.logger.error(f"Webhook retry failed for event {event_id}: {e}")
            finally:
                try:
                    db.session.remove()
                except Exception:
                    pass

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

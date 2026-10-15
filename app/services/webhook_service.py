"""
Webhook service for managing event-driven notifications
"""

import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import requests
from flask import current_app

from app.extensions import db
from app.models.webhook import WebhookDelivery, WebhookEvent, WebhookSubscription
from app.utils.ssrf_guard import BlockedOutboundURL, validate_user_supplied_url

logger = logging.getLogger(__name__)

_MAX_URL_LENGTH = 500  # webhook_subscriptions.url column width
_MAX_CUSTOM_HEADERS = 20
_MAX_HEADER_VALUE_LENGTH = 1024
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")

# Headers a subscriber may never set: they steer routing (Host), framing
# (Content-Length, Transfer-Encoding), connection handling, proxying, or
# forge the signature the receiver trusts.
_FORBIDDEN_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "content-type",
        "transfer-encoding",
        "connection",
        "keep-alive",
        "te",
        "trailer",
        "upgrade",
        "expect",
        "cookie",
        "user-agent",
        "x-webhook-signature",
    }
)
_FORBIDDEN_HEADER_PREFIXES = ("proxy-", "x-forwarded-", "x-real-ip", "sec-")

# What a delivery record keeps about a failed attempt. Never the remote body
# or the exception text: both can carry the content of an internal service.
GENERIC_DELIVERY_ERRORS = {
    "refused": "Target not permitted",
    "connection": "Connection failed",
    "timeout": "Request timed out",
    "redirect": "Redirects are not followed",
}


class WebhookTargetRefused(ValueError):
    """A subscription URL or header set was refused. The message is safe to show."""


def validate_webhook_url(url) -> str:
    """Return the stripped URL, or raise :class:`WebhookTargetRefused`.

    One guard for every webhook sender (this service and the solution webhook
    sender in drift_remediation_service): ``validate_user_supplied_url``. The
    detail it gives (resolved addresses) is logged, not returned.
    """
    if not isinstance(url, str) or not url.strip():
        raise WebhookTargetRefused("url must be a non-empty string")
    url = url.strip()
    if len(url) > _MAX_URL_LENGTH:
        raise WebhookTargetRefused("url is too long")
    try:
        validate_user_supplied_url(url)
    except BlockedOutboundURL as exc:
        logger.warning("Webhook target refused: %s", exc)
        raise WebhookTargetRefused("url is not an allowed webhook target") from exc
    return url


def sanitize_webhook_headers(headers) -> Dict[str, str]:
    """Validate subscriber-supplied headers; raise :class:`WebhookTargetRefused`."""
    if headers in (None, {}):
        return {}
    if not isinstance(headers, dict):
        raise WebhookTargetRefused("headers must be an object")
    if len(headers) > _MAX_CUSTOM_HEADERS:
        raise WebhookTargetRefused("too many custom headers")
    clean: Dict[str, str] = {}
    for name, value in headers.items():
        if not isinstance(name, str) or not _HEADER_NAME_RE.match(name):
            raise WebhookTargetRefused("header names may contain letters, digits and hyphens only")
        lowered = name.lower()
        if lowered in _FORBIDDEN_HEADERS or lowered.startswith(_FORBIDDEN_HEADER_PREFIXES):
            raise WebhookTargetRefused(f"header {name} may not be set")
        if not isinstance(value, str) or len(value) > _MAX_HEADER_VALUE_LENGTH:
            raise WebhookTargetRefused("header values must be short strings")
        if any(ch in value for ch in ("\r", "\n", "\x00")):
            raise WebhookTargetRefused("header values may not contain line breaks")
        clean[name] = value
    return clean


def _active_org_id():
    """The caller's active organisation, or None (callers then see nothing)."""
    from app.middleware.tenant_context import current_org_id

    return current_org_id()


class WebhookService:
    """Service for managing webhook subscriptions and event delivery"""

    def __init__(self):
        self.max_retries = current_app.config.get("WEBHOOK_MAX_RETRIES", 3)
        self.retry_delay = current_app.config.get("WEBHOOK_RETRY_DELAY", 60)  # seconds
        self.timeout = current_app.config.get("WEBHOOK_TIMEOUT", 30)  # seconds

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
        """Create a new webhook subscription.

        Raises :class:`WebhookTargetRefused` for a URL or header set the server
        must not call or send, and :class:`PermissionError` when no active
        organisation is known (a subscription with no organisation would be
        read by every organisation).
        """
        org_id = _active_org_id()
        if org_id is None:
            raise PermissionError("No active organisation")
        url = validate_webhook_url(url)
        headers = sanitize_webhook_headers(headers)
        subscription = WebhookSubscription(
            id=str(uuid.uuid4()),
            organization_id=org_id,
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
        org_id = _active_org_id()
        if org_id is None:
            return []
        return WebhookSubscription.query.filter_by(
            user_id=str(user_id), organization_id=org_id, is_active=True
        ).all()

    def get_subscription(self, subscription_id: str, user_id: str) -> Optional[WebhookSubscription]:
        """Get a specific subscription for a user"""
        org_id = _active_org_id()
        if org_id is None:
            return None
        return WebhookSubscription.query.filter_by(
            id=subscription_id,
            user_id=str(user_id),
            organization_id=org_id,
            is_active=True,
        ).first()

    def get_subscription_by_id(self, subscription_id: str) -> Optional[WebhookSubscription]:
        """Get a subscription by ID (internal use).

        Not a user-facing lookup: callers are the unauthenticated receiver
        (inside platform_scope, where the id names the organisation) and
        retry_event, which has already proven the organisation.
        """
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
        # Validate everything before changing anything, so a refused update
        # leaves the stored subscription exactly as it was.
        checked = {}
        for field, value in updates.items():
            if field not in allowed_fields:
                continue
            if field == "url":
                value = validate_webhook_url(value)
            elif field == "headers":
                value = sanitize_webhook_headers(value)
            elif field == "webhook_type" and value not in ("generic", "teams", "slack"):
                raise WebhookTargetRefused("webhook_type must be generic, teams or slack")
            checked[field] = value
        for field, value in checked.items():
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

        # Deliver to subscriptions asynchronously
        if subscriptions:
            threading.Thread(
                target=self._deliver_to_subscriptions, args=(event, subscriptions), daemon=True
            ).start()

        current_app.logger.info(
            f"Published event {event_type} with {len(subscriptions)} subscriptions"
        )
        return event

    def _find_matching_subscriptions(
        self, event_type: str, payload: Dict
    ) -> List[WebhookSubscription]:
        """Find subscriptions that match the event"""
        org_id = _active_org_id()
        if org_id is None:
            return []
        subscriptions = WebhookSubscription.query.filter_by(
            organization_id=org_id, is_active=True
        ).all()
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

    def _deliver_to_subscriptions(
        self, event: WebhookEvent, subscriptions: List[WebhookSubscription]
    ):
        """Deliver event to multiple subscriptions"""
        for subscription in subscriptions:
            try:
                self._deliver_webhook(
                    subscription,
                    {
                        "event_type": event.event_type,
                        "payload": event.payload,
                        "metadata": event.event_metadata,
                        "event_id": event.id,
                        "timestamp": event.created_at.isoformat(),
                    },
                )
            except Exception as e:
                current_app.logger.error(
                    f"Failed to deliver event {event.id} to subscription {subscription.id}: {str(e)}"
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

    def _deliver_webhook(self, subscription: WebhookSubscription, event_data: Dict) -> Dict:
        """Deliver webhook to a single subscription"""
        # Subscriber headers first, then ours, so a stored header can never
        # replace Content-Type, User-Agent or the signature. A stored set that
        # no longer passes validation (rows older than the check) is dropped
        # whole rather than sent.
        try:
            custom_headers = sanitize_webhook_headers(subscription.headers)
        except WebhookTargetRefused:
            custom_headers = {}
        headers = {
            **custom_headers,
            "Content-Type": "application/json",
            "User-Agent": "Enterprise-Architecture-Webhook/1.0",
        }

        # Format payload according to webhook_type (teams/slack/generic)
        formatted_payload = self._build_payload_for_subscription(subscription, event_data)

        # Add signature if secret is configured (sign the formatted payload)
        if subscription.secret:
            payload_str = json.dumps(formatted_payload, sort_keys=True)
            import hashlib
            import hmac

            signature = hmac.new(
                subscription.secret.encode(), payload_str.encode(), hashlib.sha256
            ).hexdigest()
            headers["X-Webhook-Signature"] = signature

        delivery = WebhookDelivery(
            id=str(uuid.uuid4()),
            subscription_id=subscription.id,
            # Explicit, not the column default: this runs on a background thread
            # (see _deliver_to_subscriptions) with no request context, so
            # TenantMixin's g.current_org_id-reading default can't resolve it.
            # The subscription itself was already org-scoped before the thread
            # started, so it's the trustworthy source here.
            organization_id=subscription.organization_id,
            event_type=event_data.get("event_type"),
            payload=formatted_payload,
            status="pending",
            attempt_count=0,
            created_at=datetime.utcnow(),
        )

        db.session.add(delivery)
        db.session.commit()

        # Attempt delivery
        success = self._attempt_delivery(delivery, subscription.url, headers, formatted_payload)

        return {"delivery_id": delivery.id, "success": success, "attempts": delivery.attempt_count}

    def _attempt_delivery(
        self, delivery: WebhookDelivery, url: str, headers: Dict, payload: Dict
    ) -> bool:
        """Attempt to deliver webhook with retries.

        The target is re-checked on every attempt (a host can start resolving to
        an internal address after it was accepted), redirects are never followed,
        and nothing the remote side says is kept: only the status code and a
        fixed, generic reason. A subscriber chooses the URL, so the response
        body of an internal service must never reach the delivery record or the
        API that shows it.
        """
        try:
            validate_webhook_url(url)
        except WebhookTargetRefused:
            delivery.attempt_count = (delivery.attempt_count or 0) + 1
            delivery.last_attempt_at = datetime.utcnow()
            delivery.status = "failed"
            delivery.response_body = None
            delivery.error_message = GENERIC_DELIVERY_ERRORS["refused"]
            db.session.commit()
            return False
        for attempt in range(self.max_retries):
            try:
                delivery.attempt_count = attempt + 1
                delivery.last_attempt_at = datetime.utcnow()

                response = requests.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                    allow_redirects=False,
                )

                delivery.response_status = response.status_code
                delivery.response_body = None

                if response.status_code >= 200 and response.status_code < 300:
                    delivery.status = "success"
                    delivery.delivered_at = datetime.utcnow()
                    db.session.commit()
                    current_app.logger.info("Successfully delivered webhook %s", delivery.id)
                    return True
                else:
                    delivery.status = "failed"
                    delivery.error_message = (
                        GENERIC_DELIVERY_ERRORS["redirect"]
                        if 300 <= response.status_code < 400
                        else f"HTTP {response.status_code}"
                    )
                    db.session.commit()

                    if attempt < self.max_retries - 1:
                        time.sleep(self.retry_delay * (attempt + 1))  # Exponential backoff

            except requests.RequestException as e:
                delivery.status = "failed"
                delivery.error_message = (
                    GENERIC_DELIVERY_ERRORS["timeout"]
                    if isinstance(e, requests.Timeout)
                    else GENERIC_DELIVERY_ERRORS["connection"]
                )
                current_app.logger.warning(
                    "Webhook delivery attempt failed: %s", type(e).__name__
                )
                db.session.commit()

                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))

        current_app.logger.error(
            "Failed to deliver webhook %s after %s attempts", delivery.id, self.max_retries
        )
        return False

    def get_events(self, limit: int = 50, offset: int = 0) -> List[WebhookEvent]:
        """Get webhook events (admin function)"""
        org_id = _active_org_id()
        if org_id is None:
            return []
        return (
            WebhookEvent.query.filter_by(organization_id=org_id)
            .order_by(WebhookEvent.created_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

    def retry_event(self, event_id: str) -> bool:
        """Retry delivering a failed event"""
        org_id = _active_org_id()
        if org_id is None:
            return False
        event = WebhookEvent.query.filter_by(id=event_id, organization_id=org_id).first()
        if not event:
            return False

        # Find failed deliveries for this event
        failed_deliveries = WebhookDelivery.query.filter_by(
            event_type=event.event_type, status="failed", organization_id=org_id
        ).all()

        for delivery in failed_deliveries:
            subscription = self.get_subscription_by_id(delivery.subscription_id)
            if subscription:
                threading.Thread(
                    target=self._attempt_delivery,
                    args=(delivery, subscription.url, {}, delivery.payload),
                    daemon=True,
                ).start()

        return True

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

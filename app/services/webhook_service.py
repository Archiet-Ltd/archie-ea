"""
Webhook service for managing event-driven notifications
"""

import json
import threading
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import requests
from flask import current_app

from app.extensions import db
from app.models.webhook import WebhookDelivery, WebhookEvent, WebhookSubscription


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
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Enterprise-Architecture-Webhook/1.0",
            **subscription.headers,
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
        """Attempt to deliver webhook with retries"""
        for attempt in range(self.max_retries):
            try:
                delivery.attempt_count = attempt + 1
                delivery.last_attempt_at = datetime.utcnow()

                response = requests.post(url, json=payload, headers=headers, timeout=self.timeout)

                delivery.response_status = response.status_code
                delivery.response_body = response.text[:1000]  # Limit response size

                if response.status_code >= 200 and response.status_code < 300:
                    delivery.status = "success"
                    delivery.delivered_at = datetime.utcnow()
                    db.session.commit()
                    current_app.logger.info(f"Successfully delivered webhook to {url}")
                    return True
                else:
                    delivery.status = "failed"
                    delivery.error_message = f"HTTP {response.status_code}: {response.text[:200]}"
                    db.session.commit()

                    if attempt < self.max_retries - 1:
                        time.sleep(self.retry_delay * (attempt + 1))  # Exponential backoff

            except requests.RequestException as e:
                delivery.status = "failed"
                delivery.error_message = str(e)
                db.session.commit()

                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))

        current_app.logger.error(
            f"Failed to deliver webhook to {url} after {self.max_retries} attempts"
        )
        return False

    def get_events(self, limit: int = 50, offset: int = 0) -> List[WebhookEvent]:
        """Get webhook events (admin function)"""
        return (
            WebhookEvent.query.order_by(WebhookEvent.created_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

    def retry_event(self, event_id: str) -> bool:
        """Retry delivering a failed event"""
        event = WebhookEvent.query.get(event_id)
        if not event:
            return False

        # Find failed deliveries for this event
        failed_deliveries = WebhookDelivery.query.filter_by(
            event_type=event.event_type, status="failed"
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

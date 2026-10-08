"""
Webhook service for Enterprise Architecture Platform
Provides event-driven notifications and integrations
"""

import hashlib
import hmac
import json
import secrets
from collections import defaultdict
from datetime import datetime

from flask import Blueprint, current_app, g, jsonify, request
from flask_login import current_user

from app import csrf
from app.decorators import audit_log, require_auth
from app.middleware.tenant_decorators import require_org_or_platform_admin
from app.services.rate_limiter import RateLimitExceeded, _rate_limiter
from app.services.webhook_service import WebhookService
from app.utils.pagination import safe_int_arg
webhook_bp = Blueprint("webhook", __name__, url_prefix="/api/webhooks")

# Event publishes allowed per organisation per window, across all its users (the
# app-wide limiter already holds each user to 30 writes a minute).
PUBLISH_RATE_LIMIT = 120
PUBLISH_RATE_WINDOW_SECONDS = 60


# IP-based rate limiter for the public webhook receiver endpoint
class _WebhookRateLimiter:
    """Rate limiter for unauthenticated webhook receiver (IP-based)."""

    def __init__(self, max_requests=30, window_seconds=60):
        self._requests = defaultdict(list)
        self._max_requests = max_requests
        self._window_seconds = window_seconds

    def is_allowed(self, ip_address):
        now = datetime.utcnow()
        # Prune old entries
        self._requests[ip_address] = [
            ts for ts in self._requests[ip_address]
            if (now - ts).total_seconds() < self._window_seconds
        ]
        if len(self._requests[ip_address]) >= self._max_requests:
            return False
        self._requests[ip_address].append(now)
        return True


_webhook_rate_limiter = _WebhookRateLimiter(max_requests=30, window_seconds=60)


def verify_webhook_signature(payload, signature, secret):
    """Verify webhook signature for security.

    Fails closed. This previously returned True when no secret was configured,
    so "I have no way to check this" and "this checked out" were the same answer
    - the failure mode a signature check exists to prevent. The one caller
    happens to guard with `if subscription.secret:` first, so the old branch was
    unreachable from there, but it left the helper unsafe for the next caller.
    """
    if not secret or not signature:
        return False

    expected_signature = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    return hmac.compare_digest(signature, expected_signature)


@webhook_bp.route("/subscriptions", methods=["GET"])
@require_auth
@audit_log("webhook_subscriptions_list")
def list_subscriptions():
    """List webhook subscriptions for the authenticated user"""
    try:
        user_id = str(current_user.id)  # webhook tables store user_id as String(36)
        service = WebhookService()

        subscriptions = service.get_user_subscriptions(user_id)
        return jsonify(
            {"success": True, "data": [sub.to_dict() for sub in subscriptions]}
        )
    except Exception as e:
        current_app.logger.error(f"Error listing webhook subscriptions: {str(e)}")
        return jsonify(
            {"success": False, "error": "Failed to list webhook subscriptions"}
        ), 500


@webhook_bp.route("/subscriptions", methods=["POST"])
@require_auth
@audit_log("webhook_subscription_create")
def create_subscription():
    """Create a new webhook subscription"""
    try:
        user_id = str(current_user.id)  # webhook tables store user_id as String(36)
        data = request.get_json()

        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        required_fields = ["url", "events"]
        for field in required_fields:
            if field not in data:
                return jsonify(
                    {"success": False, "error": f"Missing required field: {field}"}
                ), 400

        service = WebhookService()
        # Generate one when the caller supplies none. The receiver only enforces
        # signatures when the subscription has a secret, so a secretless
        # subscription was an unauthenticated write endpoint that anyone knowing
        # its id could post to - rate-limited by IP and nothing else. Making the
        # field effectively mandatory is what closes that, rather than asking
        # callers to remember.
        supplied_secret = data.get("secret")
        secret = supplied_secret or secrets.token_hex(32)
        subscription = service.create_subscription(
            user_id=user_id,
            url=data["url"],
            events=data["events"],
            secret=secret,
            description=data.get("description"),
            filters=data.get("filters", {}),
            headers=data.get("headers", {}),
        )

        payload = subscription.to_dict()
        if not supplied_secret:
            # to_dict() deliberately withholds the secret, so a generated one has
            # to be returned here or the caller could never sign anything. Shown
            # once, at creation, and never retrievable again.
            payload["secret"] = secret
            payload["secret_notice"] = (
                "Generated because none was supplied. Store it now - it is not "
                "retrievable later. Sign payloads as "
                "HMAC-SHA256(body, secret) in the X-Webhook-Signature header."
            )
        return jsonify({"success": True, "data": payload}), 201

    except Exception as e:
        current_app.logger.error(f"Error creating webhook subscription: {str(e)}")
        return jsonify(
            {"success": False, "error": "Failed to create webhook subscription"}
        ), 500


@webhook_bp.route("/subscriptions/<subscription_id>", methods=["GET"])
@require_auth
@audit_log("webhook_subscription_get")
def get_subscription(subscription_id):
    """Get a specific webhook subscription"""
    try:
        user_id = str(current_user.id)  # webhook tables store user_id as String(36)
        service = WebhookService()

        subscription = service.get_subscription(subscription_id, user_id)
        if not subscription:
            return jsonify({"success": False, "error": "Subscription not found"}), 404

        return jsonify({"success": True, "data": subscription.to_dict()})
    except Exception as e:
        current_app.logger.error(f"Error getting webhook subscription: {str(e)}")
        return jsonify(
            {"success": False, "error": "Failed to get webhook subscription"}
        ), 500


@webhook_bp.route("/subscriptions/<subscription_id>", methods=["PUT"])
@require_auth
@audit_log("webhook_subscription_update")
def update_subscription(subscription_id):
    """Update a webhook subscription"""
    try:
        user_id = str(current_user.id)  # webhook tables store user_id as String(36)
        data = request.get_json()

        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        service = WebhookService()
        subscription = service.update_subscription(
            subscription_id=subscription_id, user_id=user_id, updates=data
        )

        if not subscription:
            return jsonify({"success": False, "error": "Subscription not found"}), 404

        return jsonify({"success": True, "data": subscription.to_dict()})
    except Exception as e:
        current_app.logger.error(f"Error updating webhook subscription: {str(e)}")
        return jsonify(
            {"success": False, "error": "Failed to update webhook subscription"}
        ), 500


@webhook_bp.route("/subscriptions/<subscription_id>", methods=["DELETE"])
@require_auth
@audit_log("webhook_subscription_delete")
def delete_subscription(subscription_id):
    """Delete a webhook subscription"""
    try:
        user_id = str(current_user.id)  # webhook tables store user_id as String(36)
        service = WebhookService()

        success = service.delete_subscription(subscription_id, user_id)
        if not success:
            return jsonify({"success": False, "error": "Subscription not found"}), 404

        return jsonify(
            {"success": True, "message": "Subscription deleted successfully"}
        )
    except Exception as e:
        current_app.logger.error(f"Error deleting webhook subscription: {str(e)}")
        return jsonify(
            {"success": False, "error": "Failed to delete webhook subscription"}
        ), 500


@webhook_bp.route("/subscriptions/<subscription_id>/test", methods=["POST"])
@require_auth
@audit_log("webhook_subscription_test")
def test_subscription(subscription_id):
    """Test a webhook subscription by sending a test event"""
    try:
        user_id = str(current_user.id)  # webhook tables store user_id as String(36)
        service = WebhookService()

        result = service.test_subscription(subscription_id, user_id)
        if not result:
            return (
                jsonify(
                    {"success": False, "error": "Subscription not found or test failed"}
                ),
                404,
            )

        return jsonify(
            {
                "success": True,
                "message": "Test webhook sent successfully",
                "data": result,
            }
        )
    except Exception as e:
        current_app.logger.error(f"Error testing webhook subscription: {str(e)}")
        return jsonify(
            {"success": False, "error": "Failed to test webhook subscription"}
        ), 500


@webhook_bp.route("/events", methods=["GET"])
@require_auth
@audit_log("webhook_events_list")
def list_events():
    """List webhook events (for debugging/admin purposes)"""
    # Admin of the organisation being acted in, or a platform admin (403 otherwise).
    require_org_or_platform_admin(getattr(g, "current_org_id", None))
    try:
        service = WebhookService()

        events = service.get_events(
            limit=safe_int_arg('limit', 50, minimum=1, maximum=500),
            offset=safe_int_arg('offset', 0, minimum=0),
        )

        return jsonify({"success": True, "data": [event.to_dict() for event in events]})
    except Exception as e:
        current_app.logger.error(f"Error listing webhook events: {str(e)}")
        return jsonify(
            {"success": False, "error": "Failed to list webhook events"}
        ), 500


@webhook_bp.route("/events/<event_id>/retry", methods=["POST"])
@require_auth
@audit_log("webhook_event_retry")
def retry_event(event_id):
    """Retry sending a failed webhook event"""
    require_org_or_platform_admin(getattr(g, "current_org_id", None))
    try:
        service = WebhookService()

        success = service.retry_event(event_id)
        if not success:
            return jsonify(
                {"success": False, "error": "Event not found or retry failed"}
            ), 404

        return jsonify(
            {"success": True, "message": "Event retry initiated successfully"}
        )
    except Exception as e:
        current_app.logger.error(f"Error retrying webhook event: {str(e)}")
        return jsonify(
            {"success": False, "error": "Failed to retry webhook event"}
        ), 500


# Public webhook receiver endpoint (no user auth — secured by signature + rate limiting)
@webhook_bp.route("/receiver/<subscription_id>", methods=["POST"])
# csrf.exempt: webhook receiver — external systems cannot include CSRF tokens
@csrf.exempt
def receive_webhook(subscription_id):
    """Receive webhook from external services (for two-way integrations)"""
    try:
        # Rate limit by IP address
        client_ip = request.remote_addr or "unknown"
        if not _webhook_rate_limiter.is_allowed(client_ip):
            current_app.logger.warning(
                "Webhook rate limit exceeded for IP %s on subscription %s",
                client_ip, subscription_id
            )
            return jsonify({"success": False, "error": "Rate limit exceeded"}), 429

        # Get raw payload for signature verification
        payload = request.get_data()
        signature = request.headers.get("X-Webhook-Signature")

        service = WebhookService()

        # Verify subscription exists
        subscription = service.get_subscription_by_id(subscription_id)
        if not subscription:
            # Do not reveal whether subscription exists — use generic message
            current_app.logger.warning(
                "Webhook received for non-existent subscription %s from IP %s",
                subscription_id, client_ip
            )
            return jsonify({"success": False, "error": "Unauthorized"}), 401

        # Enforce signature verification when a secret is configured.
        #
        # Subscriptions created before secrets were mandatory can still have none,
        # and those stay accepted rather than breaking a live integration on
        # deploy. They are unauthenticated, so say so on every request instead of
        # letting them look identical to a verified one in the log.
        if not subscription.secret:
            current_app.logger.warning(
                "Webhook accepted WITHOUT signature verification: subscription %s "
                "has no secret (from IP %s). Recreate it to get one.",
                subscription_id, client_ip
            )
        if subscription.secret:
            if not signature:
                current_app.logger.warning(
                    "Webhook missing signature for subscription %s from IP %s",
                    subscription_id, client_ip
                )
                return jsonify({"success": False, "error": "Missing signature"}), 401
            if not verify_webhook_signature(payload, signature, subscription.secret):
                current_app.logger.warning(
                    "Webhook invalid signature for subscription %s from IP %s",
                    subscription_id, client_ip
                )
                return jsonify({"success": False, "error": "Invalid signature"}), 401

        # Parse payload
        try:
            data = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            data = {"raw_payload": payload.decode("utf-8")}

        # Process the incoming webhook
        result = service.process_incoming_webhook(
            subscription_id=subscription_id, payload=data, headers=dict(request.headers)
        )

        return jsonify(
            {
                "success": True,
                "message": "Webhook received successfully",
                "data": result,
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error processing incoming webhook: {str(e)}")
        return jsonify({"success": False, "error": "Failed to process webhook"}), 500


# ---------------------------------------------------------------------------
# Slack Events API receiver
# ---------------------------------------------------------------------------

@webhook_bp.route("/slack/events", methods=["POST"])
# csrf.exempt: external Slack platform cannot include CSRF tokens
@csrf.exempt
def slack_events():
    """Receive Slack Events API payloads (URL verification + event dispatch)."""
    try:
        from app.services.slack_architect_service import SlackArchitectService

        raw_body = request.get_data()
        timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
        signature = request.headers.get("X-Slack-Signature", "")

        cfg = SlackArchitectService.get_config()
        signing_secret = cfg.get("signing_secret", "")

        if signing_secret:
            if not SlackArchitectService.verify_signature(raw_body, timestamp, signature, signing_secret):
                current_app.logger.warning("slack: invalid request signature from %s", request.remote_addr)
                return jsonify({"error": "Invalid signature"}), 401

        payload = request.get_json(silent=True) or {}

        # Slack URL verification handshake
        if payload.get("type") == "url_verification":
            return jsonify({"challenge": payload.get("challenge", "")})

        # Dispatch event asynchronously to avoid Slack's 3s timeout
        import threading
        threading.Thread(
            target=SlackArchitectService.handle_event,
            args=(payload,),
            daemon=True,
        ).start()

        return jsonify({"ok": True})
    except Exception as exc:
        current_app.logger.error("slack events receiver error: %s", exc)
        return jsonify({"ok": False}), 500


# ---------------------------------------------------------------------------
# Microsoft Teams / Graph change notification receiver
# ---------------------------------------------------------------------------

@webhook_bp.route("/teams/notifications", methods=["POST"])
# csrf.exempt: Microsoft Graph cannot include CSRF tokens
@csrf.exempt
def teams_notifications():
    """Receive Microsoft Graph callRecords change notifications."""
    try:
        from app.services.teams_meeting_service import TeamsMeetingService

        # Graph sends a validationToken query param for subscription validation
        validation_token = request.args.get("validationToken")
        if validation_token:
            # Echo the token back as plain text to confirm the endpoint
            from flask import Response
            return Response(validation_token, content_type="text/plain")

        payload = request.get_json(silent=True) or {}

        import threading
        threading.Thread(
            target=TeamsMeetingService.handle_notification,
            args=(payload,),
            daemon=True,
        ).start()

        return "", 202
    except Exception as exc:
        current_app.logger.error("teams notifications receiver error: %s", exc)
        return "", 500


def _enforce_publish_rate_limit() -> None:
    """One bucket per organisation in the app's shared rate limiter: the cost of
    a publish is the deliveries it queues, and those are the organisation's, not
    the individual user's."""
    if not current_app.config.get("RATE_LIMITING_ENABLED", True):
        return
    limit = int(current_app.config.get("WEBHOOK_PUBLISH_RATE_LIMIT", PUBLISH_RATE_LIMIT))
    key = f"webhook-publish:org:{getattr(g, 'current_org_id', None)}"
    allowed, retry_after = _rate_limiter.check_rate_limit(key, limit, PUBLISH_RATE_WINDOW_SECONDS)
    if not allowed:
        raise RateLimitExceeded(limit, "1m", retry_after)


@webhook_bp.route("/public/events", methods=["POST"])
@require_auth
@audit_log("webhook_event_publish")
def publish_event():
    """Publish a custom event to all subscribed webhooks"""
    _enforce_publish_rate_limit()
    try:
        user_id = str(current_user.id)  # webhook tables store user_id as String(36)
        data = request.get_json()

        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        required_fields = ["event_type", "payload"]
        for field in required_fields:
            if field not in data:
                return jsonify(
                    {"success": False, "error": f"Missing required field: {field}"}
                ), 400

        service = WebhookService()
        event = service.publish_event(
            event_type=data["event_type"],
            payload=data["payload"],
            user_id=user_id,
            metadata=data.get("metadata", {}),
        )

        return jsonify({"success": True, "data": event.to_dict()}), 201

    except Exception as e:
        current_app.logger.error(f"Error publishing webhook event: {str(e)}")
        return jsonify(
            {"success": False, "error": "Failed to publish webhook event"}
        ), 500

"""
Webhook service for Enterprise Architecture Platform
Provides event-driven notifications and integrations
"""

import hmac
import json
import secrets
from collections import defaultdict
from datetime import datetime

from flask import Blueprint, current_app, g, jsonify, request
from flask_login import current_user

from app import csrf
from app.decorators import audit_log, require_auth
from app.extensions import db
from app.middleware.tenant_decorators import org_admin_required
from app.services import event_catalogue
from app.services.outbox import emit_event
from app.services.webhook_service import (
    WebhookError,
    WebhookSecretUnavailable,
    WebhookService,
)
from app.utils.pagination import safe_int_arg
webhook_bp = Blueprint("webhook", __name__, url_prefix="/api/webhooks")


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

    # Imported here so this helper stays self-contained (the signature tests
    # load its source on its own); the one HMAC primitive lives in the service.
    from app.services.webhook_service import hmac_sha256_hex

    expected_signature = hmac_sha256_hex(secret, payload)

    return hmac.compare_digest(signature, expected_signature)


def _json_error(message, status, **extra):
    body = {"success": False, "error": message}
    body.update(extra)
    return jsonify(body), status


def _scope_user_id():
    """The owner filter for subscription routes: organisation admins see them all."""
    if getattr(current_user, "is_org_admin", False):
        return None
    return str(current_user.id)  # webhook tables store user_id as String(36)


def _handle_service_error(exc):
    if isinstance(exc, WebhookSecretUnavailable):
        return _json_error(str(exc), 503)
    return _json_error(str(exc), 400)


@webhook_bp.route("/catalogue", methods=["GET"])
@require_auth
def event_catalogue_index():
    """The versioned event catalogue: every event type, version and schema."""
    catalogue = event_catalogue.get_catalogue()
    return jsonify(
        {
            "success": True,
            "data": {
                "catalogue_version": catalogue["catalogue_version"],
                "source": catalogue["source"],
                "envelope": catalogue["envelope"],
                "schemas": catalogue["schemas"],
                "events": [event_catalogue.get_event(t) for t in event_catalogue.all_types()],
            },
        }
    )


@webhook_bp.route("/catalogue/<path:event_type>", methods=["GET"])
@require_auth
def event_catalogue_entry(event_type):
    """One catalogue entry with its schema, or 404."""
    entry = event_catalogue.get_event(event_type)
    if entry is None:
        return _json_error("Event type not found", 404)
    return jsonify({"success": True, "data": entry})


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
@audit_log("webhook_sub_create")
def create_subscription():
    """Create a new webhook subscription"""
    try:
        user_id = str(current_user.id)  # webhook tables store user_id as String(36)
        data = request.get_json(silent=True)

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
        try:
            subscription = service.create_subscription(
                user_id=user_id,
                url=data["url"],
                events=data["events"],
                secret=secret,
                description=data.get("description"),
                filters=data.get("filters", {}),
                headers=data.get("headers", {}),
                webhook_type=data.get("webhook_type", "generic"),
            )
        except WebhookError as exc:
            return _handle_service_error(exc)

        payload = subscription.to_dict()
        if not supplied_secret:
            # to_dict() deliberately withholds the secret, so a generated one has
            # to be returned here or the caller could never sign anything. Shown
            # once, at creation, and never retrievable again.
            payload["secret"] = secret
            payload["secret_notice"] = (
                "Generated because none was supplied. Store it now - it is not "
                "retrievable later. Each delivery carries an Entelim-Signature "
                "header of the form t=<timestamp>,v1=<hex>, where v1 is "
                "HMAC-SHA256(secret, '<timestamp>.' + the exact request body)."
            )
        return jsonify({"success": True, "data": payload}), 201

    except Exception as e:
        current_app.logger.error(f"Error creating webhook subscription: {type(e).__name__}")
        return jsonify(
            {"success": False, "error": "Failed to create webhook subscription"}
        ), 500


@webhook_bp.route("/subscriptions/<subscription_id>", methods=["GET"])
@require_auth
@audit_log("webhook_subscription_get")
def get_subscription(subscription_id):
    """Get a specific webhook subscription"""
    try:
        service = WebhookService()

        subscription = service.get_subscription(subscription_id, _scope_user_id())
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
@audit_log("webhook_sub_update")
def update_subscription(subscription_id):
    """Update a webhook subscription"""
    try:
        data = request.get_json(silent=True)

        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        service = WebhookService()
        try:
            subscription = service.update_subscription(
                subscription_id=subscription_id, user_id=_scope_user_id(), updates=data
            )
        except WebhookError as exc:
            return _handle_service_error(exc)

        if not subscription:
            return jsonify({"success": False, "error": "Subscription not found"}), 404

        return jsonify({"success": True, "data": subscription.to_dict()})
    except Exception as e:
        current_app.logger.error(f"Error updating webhook subscription: {type(e).__name__}")
        return jsonify(
            {"success": False, "error": "Failed to update webhook subscription"}
        ), 500


@webhook_bp.route("/subscriptions/<subscription_id>", methods=["DELETE"])
@require_auth
@audit_log("webhook_sub_delete")
def delete_subscription(subscription_id):
    """Delete a webhook subscription"""
    try:
        service = WebhookService()

        success = service.delete_subscription(subscription_id, _scope_user_id())
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


@webhook_bp.route("/subscriptions/<subscription_id>/rotate-secret", methods=["POST"])
@require_auth
@audit_log("webhook_sec_rotate")
def rotate_secret(subscription_id):
    """Replace the signing secret; the new one is returned once and the old one stops signing."""
    try:
        service = WebhookService()
        try:
            new_secret = service.rotate_secret(subscription_id, _scope_user_id())
        except WebhookError as exc:
            return _handle_service_error(exc)
        if new_secret is None:
            return jsonify({"success": False, "error": "Subscription not found"}), 404
        return jsonify(
            {
                "success": True,
                "data": {
                    "secret": new_secret,
                    "secret_notice": "Store it now - it is not retrievable later.",
                },
            }
        )
    except Exception as e:
        current_app.logger.error(f"Error rotating webhook secret: {type(e).__name__}")
        return jsonify({"success": False, "error": "Failed to rotate secret"}), 500


@webhook_bp.route("/subscriptions/<subscription_id>/test", methods=["POST"])
@require_auth
@audit_log("webhook_test")
def test_subscription(subscription_id):
    """Test a webhook subscription by sending a test event"""
    try:
        service = WebhookService()

        result = service.test_subscription(subscription_id, _scope_user_id())
        if not result:
            return (
                jsonify({"success": False, "error": "Subscription not found"}),
                404,
            )

        return jsonify(
            {
                "success": True,
                "message": "Test webhook sent"
                if result.get("success")
                else "Test webhook sent; the subscriber did not accept it",
                "data": result,
            }
        )
    except Exception as e:
        current_app.logger.error(f"Error testing webhook subscription: {str(e)}")
        return jsonify(
            {"success": False, "error": "Failed to test webhook subscription"}
        ), 500


@webhook_bp.route("/subscriptions/<subscription_id>/deliveries", methods=["GET"])
@require_auth
@audit_log("webhook_dlv_list")
def list_deliveries(subscription_id):
    """Deliveries of one subscription, newest first (paged)."""
    try:
        service = WebhookService()
        if not service.get_subscription(subscription_id, _scope_user_id()):
            return jsonify({"success": False, "error": "Subscription not found"}), 404
        limit = safe_int_arg("limit", 50, minimum=1, maximum=200)
        offset = safe_int_arg("offset", 0, minimum=0)
        rows = service.list_deliveries(subscription_id, limit=limit, offset=offset) or []
        return jsonify(
            {
                "success": True,
                "data": [row.to_dict() for row in rows],
                "limit": limit,
                "offset": offset,
            }
        )
    except Exception as e:
        current_app.logger.error(f"Error listing webhook deliveries: {type(e).__name__}")
        return jsonify({"success": False, "error": "Failed to list deliveries"}), 500


@webhook_bp.route("/subscriptions/<subscription_id>/replay", methods=["POST"])
@org_admin_required
@audit_log("webhook_replay")
def replay_subscription(subscription_id):
    """Queue the organisation's logged events again for one subscription."""
    try:
        data = request.get_json(silent=True) or {}
        from_ordinal = data.get("from_ordinal")
        since_raw = data.get("since")
        since = None
        try:
            if from_ordinal is not None:
                from_ordinal = int(from_ordinal)
                if from_ordinal < 1:
                    raise ValueError("from_ordinal")
            if since_raw is not None:
                since = datetime.fromisoformat(str(since_raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return _json_error(
                "from_ordinal must be a whole number from 1, and since an ISO 8601 time", 400
            )

        service = WebhookService()
        try:
            result = service.replay(
                subscription_id,
                from_ordinal=from_ordinal,
                since=since,
                actor=str(current_user.id),
            )
        except WebhookError as exc:
            return _handle_service_error(exc)
        if result is None:
            return jsonify({"success": False, "error": "Subscription not found"}), 404
        return jsonify({"success": True, "data": result})
    except Exception as e:
        current_app.logger.error(f"Error replaying webhook events: {type(e).__name__}")
        return jsonify({"success": False, "error": "Failed to replay events"}), 500


@webhook_bp.route("/deliveries/<delivery_id>/redeliver", methods=["POST"])
@org_admin_required
@audit_log("webhook_redeliver")
def redeliver_delivery(delivery_id):
    """Queue a copy of one delivery to be sent again."""
    try:
        service = WebhookService()
        copy = service.redeliver(delivery_id, actor=str(current_user.id))
        if copy is None:
            return jsonify({"success": False, "error": "Delivery not found"}), 404
        return jsonify({"success": True, "data": copy.to_dict()}), 201
    except Exception as e:
        current_app.logger.error(f"Error redelivering webhook: {type(e).__name__}")
        return jsonify({"success": False, "error": "Failed to redeliver"}), 500


@webhook_bp.route("/events", methods=["GET"])
@org_admin_required
@audit_log("webhook_events_list")
def list_events():
    """The caller organisation's event log, newest first (paged)."""
    try:
        service = WebhookService()
        limit = safe_int_arg("limit", 50, minimum=1, maximum=500)
        offset = safe_int_arg("offset", 0, minimum=0)
        events = service.get_events(limit=limit, offset=offset)
        return jsonify(
            {
                "success": True,
                "data": [
                    {
                        "sequence": e.ordinal,
                        "event_id": e.event_id,
                        "event_type": e.event_type,
                        "payload": e.payload_json,
                        "created_at": e.created_at.isoformat() if e.created_at else None,
                    }
                    for e in events
                ],
                "limit": limit,
                "offset": offset,
            }
        )
    except Exception as e:
        current_app.logger.error(f"Error listing webhook events: {str(e)}")
        return jsonify(
            {"success": False, "error": "Failed to list webhook events"}
        ), 500


@webhook_bp.route("/events/<event_id>/retry", methods=["POST"])
@org_admin_required
@audit_log("webhook_redeliver")
def retry_event(event_id):
    """Redeliver this event's dead or retrying deliveries in this organisation."""
    try:
        service = WebhookService()
        count = service.retry_event(event_id, actor=str(current_user.id))
        if count is None:
            return jsonify({"success": False, "error": "Event not found"}), 404

        return jsonify(
            {"success": True, "message": "Event queued for redelivery", "data": {"queued": count}}
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
        subscription_secret = subscription.get_secret()
        if not subscription_secret:
            current_app.logger.warning(
                "Webhook accepted WITHOUT signature verification: subscription %s "
                "has no secret (from IP %s). Recreate it to get one.",
                subscription_id, client_ip
            )
        if subscription_secret:
            if not signature:
                current_app.logger.warning(
                    "Webhook missing signature for subscription %s from IP %s",
                    subscription_id, client_ip
                )
                return jsonify({"success": False, "error": "Missing signature"}), 401
            if not verify_webhook_signature(payload, signature, subscription_secret):
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


@webhook_bp.route("/public/events", methods=["POST"])
@require_auth
@audit_log("webhook_event_publish")
def publish_event():
    """Publish a custom event to the organisation's event log (and so to its subscribers)."""
    try:
        data = request.get_json(silent=True)

        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        required_fields = ["event_type", "payload"]
        for field in required_fields:
            if field not in data:
                return jsonify(
                    {"success": False, "error": f"Missing required field: {field}"}
                ), 400

        event_type = data["event_type"]
        try:
            event_catalogue.validate(event_type, data["payload"])
        except event_catalogue.EventCatalogueError as exc:
            detail = exc.detail
            if isinstance(exc, event_catalogue.EventSchemaError):
                detail = f"{exc.path}: {exc.detail}"
            return (
                jsonify(
                    {
                        "success": False,
                        "error": exc.code,
                        "event_type": exc.event_type,
                        "detail": detail,
                    }
                ),
                400,
            )

        org_id = getattr(g, "current_org_id", None)
        if org_id is None:
            return jsonify({"success": False, "error": "No organisation selected"}), 400

        # The outbox requires an entity type on every entity-less event too; this
        # names the producer so a subscriber can tell published events apart.
        event = emit_event(
            organization_id=org_id,
            event_type=event_type,
            payload=data["payload"],
            entity_type="published_event",
        )
        db.session.commit()

        return (
            jsonify(
                {
                    "success": True,
                    "data": {"event_id": event.event_id, "event_type": event_type},
                }
            ),
            201,
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error publishing webhook event: {type(e).__name__}")
        return jsonify(
            {"success": False, "error": "Failed to publish webhook event"}
        ), 500

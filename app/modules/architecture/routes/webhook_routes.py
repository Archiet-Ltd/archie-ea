"""
Webhook Routes — TPM-008

Exposes a Jira webhook receiver endpoint that Jira Cloud/Server can call
whenever an issue changes status.
"""

import logging

from flask import Blueprint, jsonify, request

from app import csrf
from app.modules.integrations.jira.jira_webhook_handler import handle_jira_webhook
logger = logging.getLogger(__name__)

webhook_routes_bp = Blueprint("webhook_routes", __name__, url_prefix="/webhooks")


@webhook_routes_bp.route("/jira", methods=["POST"])
# csrf.exempt: webhook receiver — external systems cannot include CSRF tokens
@csrf.exempt
def jira_webhook():
    """Receive a Jira issue-update event and sync the linked KanbanCard."""
    raw_body = request.get_data()
    payload = request.get_json(silent=True) or {}
    signature_header = request.headers.get("X-Hub-Signature-256", "")

    try:
        result = handle_jira_webhook(
            payload=payload,
            raw_body=raw_body,
            secret_header=signature_header,
        )
    except Exception:
        logger.exception("[TPM-008] Unhandled error in jira_webhook")
        return jsonify({"error": "internal error"}), 500

    if not result.get("configured", True):
        return jsonify({"error": "webhook not configured"}), 503

    if not result.get("verified", True):
        return jsonify({"error": "invalid signature"}), 401

    return jsonify({
        "updated": result.get("updated", False),
        "card_ref": result.get("card_ref"),
    }), 200

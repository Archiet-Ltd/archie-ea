"""
Jira Webhook Handler — TPM-008

Receives inbound Jira webhook payloads, verifies the HMAC-SHA256 signature,
and syncs the issue status back to the matching KanbanCard.
"""

import hashlib
import hmac
import logging

from flask import current_app

from app import db
from app.modules.integrations.jira.jira_webhook_security import (
    card_for_issue_in_org,
    jira_connection_org_id,
    webhook_secret_configured,
)

logger = logging.getLogger(__name__)

# Map Jira status names → KanbanCard.status values
_JIRA_STATUS_MAP = {
    "To Do": "todo",
    "In Progress": "in_progress",
    "In Review": "review",
    "Done": "done",
    "Backlog": "backlog",
}


def _verify_signature(raw_body: bytes, signature_header: str, secret: str) -> bool:
    """Return True when the HMAC-SHA256 digest matches the request header.

    Fails CLOSED when no secret is configured. This previously returned True in
    that case, described as "dev/test mode" - but `POST /webhooks/jira` is
    unauthenticated and csrf-exempt by necessity, and the handler updates
    KanbanCard rows outside any request-scoped tenant context, so the tenant
    filter does not apply. Combined with `JIRA_WEBHOOK_SECRET` never having been
    defined in config.py, the secret was always "" and every caller on the
    internet verified successfully and could rewrite any organisation's kanban
    statuses.

    An unset secret means the integration is not configured, which must mean
    "reject", never "trust everyone". Set JIRA_WEBHOOK_SECRET to enable it.
    """
    if not secret:
        logger.error(
            "[TPM-008] Jira webhook rejected: JIRA_WEBHOOK_SECRET is not set. "
            "Set it to the secret configured on the Jira webhook to enable this "
            "endpoint; it stays disabled rather than accepting unsigned requests."
        )
        return False
    if not signature_header:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def handle_jira_webhook(payload: dict, raw_body: bytes, secret_header: str) -> dict:
    """
    Process an inbound Jira webhook event.

    Returns
    -------
    dict  with keys:
        - configured (bool) — False when no webhook secret is set (refuse, 503)
        - verified (bool)  — False when signature check failed
        - updated  (bool)  — True when a KanbanCard was modified
        - card_ref (str|None) — jira_issue_key of the updated card
    """
    secret = current_app.config.get("JIRA_WEBHOOK_SECRET", "")

    if not webhook_secret_configured(secret):
        logger.error(
            "[TPM-008] Jira webhook refused: JIRA_WEBHOOK_SECRET is not set. "
            "The integration is disabled until a secret is configured; it will "
            "not accept unverified calls."
        )
        return {"configured": False, "verified": False, "updated": False, "card_ref": None}

    if not _verify_signature(raw_body, secret_header, secret):
        logger.warning("[TPM-008] Jira webhook HMAC verification failed")
        return {"configured": True, "verified": False, "updated": False, "card_ref": None}

    # Extract issue key and status from Jira Cloud / Server payload shape
    issue = payload.get("issue") or {}
    issue_key = issue.get("key") or payload.get("issue_key")
    fields = issue.get("fields") or {}
    status_obj = fields.get("status") or {}
    jira_status = status_obj.get("name")

    if not issue_key:
        logger.debug("[TPM-008] Webhook payload has no issue key — ignoring")
        return {"configured": True, "verified": True, "updated": False, "card_ref": None}

    # Scope to the organisation that owns the signed Jira connection. A verified
    # signature only proves the caller holds the connection's secret; it is this
    # scope that keeps a call for one organisation from moving another
    # organisation's card when both reference the same issue key.
    organization_id = jira_connection_org_id()
    card = card_for_issue_in_org(issue_key, organization_id)
    if card is None:
        logger.debug(
            "[TPM-008] No KanbanCard found for jira_issue_key=%s in organisation %s",
            issue_key,
            organization_id,
        )
        return {"configured": True, "verified": True, "updated": False, "card_ref": issue_key}

    if jira_status:
        new_status = _JIRA_STATUS_MAP.get(jira_status, jira_status.lower().replace(" ", "_"))
        card.status = new_status

    card.jira_push_status = "synced"

    try:
        db.session.commit()
        logger.info("[TPM-008] KanbanCard %s synced from Jira status '%s'", issue_key, jira_status)
    except Exception:
        db.session.rollback()
        logger.exception("[TPM-008] DB commit failed for card %s", issue_key)
        raise

    return {"configured": True, "verified": True, "updated": True, "card_ref": issue_key}

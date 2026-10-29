"""AI chat approval workflow routes."""

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.models.ai_chat_crud_approval import AIChatCRUDApproval
from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
from app.utils.route_guards import require_entity_json

from . import unified_ai_chat_bp


def _approval_service():
    return AIChatApprovalService(current_user.id)


@unified_ai_chat_bp.route("/approvals/pending", methods=["GET"])
@login_required
def pending_approvals():
    # ARCH-020: optional chat_session_id narrows to "what's pending in THIS
    # conversation" — what the agent needs to answer "did I already queue
    # this" instead of only seeing the whole user's flat pending list.
    chat_session_id = request.args.get("chat_session_id")
    if chat_session_id:
        approvals = _approval_service().get_pending_for_session(chat_session_id)
    else:
        approvals = _approval_service().get_pending_approvals()
    return jsonify({"success": True, "approvals": approvals})


def _decision_status(result):
    """Map structured decision results without exposing foreign approval IDs."""
    if result.get("success"):
        return 200
    return {
        "NOT_FOUND": 404,
        "FORBIDDEN": 403,
        "APPROVAL_DENIED": 403,
        "CONFLICT": 409,
    }.get(result.get("code"), 400)


@unified_ai_chat_bp.route("/approvals/queue", methods=["GET"])
@login_required
def approver_queue():
    """Approvals from other requesters the current same-org reviewer may decide."""
    result = _approval_service().get_approver_queue()
    return jsonify(result), _decision_status(result)


@unified_ai_chat_bp.route("/approvals/decided", methods=["GET"])
@login_required
def decided_approvals():
    """The organisation's latest approved and rejected proposals, with reasons."""
    result = _approval_service().get_recent_decisions()
    return jsonify(result), _decision_status(result)


@unified_ai_chat_bp.route("/approvals/<int:approval_id>/impact", methods=["GET"])
@login_required
def approval_impact(approval_id):
    """What approving this proposal would affect: services, interfaces, owners."""
    _, missing = require_entity_json(AIChatCRUDApproval, approval_id, label="Approval")
    if missing:
        return missing
    result = _approval_service().get_approval_impact(approval_id)
    return jsonify(result), _decision_status(result)


@unified_ai_chat_bp.route("/approvals/<int:approval_id>/approve", methods=["POST"])
@login_required
def approve_pending_approval(approval_id):
    # approve_and_execute, not approve_approval: the latter has never existed on
    # AIChatApprovalService, so every approve click from the /ai-chat modal
    # (static/js/ai_chat/approval_modal.js) raised AttributeError and 500'd. The
    # queue itself worked - the blueprint page reaches it via a different route -
    # so the flow was sound and only this call site was wrong.
    _, missing = require_entity_json(AIChatCRUDApproval, approval_id, label="Approval")
    if missing:
        return missing
    result = _approval_service().approve_and_execute(approval_id, current_user.id)
    if request.form.get("from_inbox"):
        return _back_to_inbox(result, "Approved. The change has been made.")
    return jsonify(result), _decision_status(result)


@unified_ai_chat_bp.route("/approvals/<int:approval_id>/reject", methods=["POST"])
@login_required
def reject_pending_approval(approval_id):
    _, missing = require_entity_json(AIChatCRUDApproval, approval_id, label="Approval")
    if missing:
        return missing
    if request.form.get("from_inbox"):
        result = _approval_service().reject_approval(approval_id, request.form.get("reason") or None)
        return _back_to_inbox(result, "Rejected. The reason stays with the proposal.")
    payload = request.get_json(silent=True) or {}
    reason = payload.get("reason")
    result = _approval_service().reject_approval(approval_id, reason)
    return jsonify(result), _decision_status(result)


def _back_to_inbox(result, done_message):
    """The inbox page posts plain forms; send the person back to it with the outcome."""
    if result.get("success"):
        flash(done_message, "success")
    else:
        flash(result.get("error") or "That decision could not be saved. Try again.", "error")
    return redirect(url_for("unified_ai_chat.approval_inbox"))


def _when(value):
    """An ISO timestamp from the approval service as a datetime, or None."""
    from datetime import datetime

    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


@unified_ai_chat_bp.route("/approvals/inbox", methods=["GET"])
@login_required
def approval_inbox():
    """Approval inbox page — one queue for every pending change proposal.

    Requires GENERAL permission (write/approval access) — Viewer roles
    cannot reach this page, matching the sidebar link guard.
    """
    from app.models.user import Permission

    if not current_user.can(Permission.GENERAL):
        from flask import abort

        abort(403)
    from datetime import datetime

    service = _approval_service()
    queue = service.get_approver_queue()
    approvals = queue.get("approvals", []) if queue.get("success") else []
    now = datetime.utcnow()
    for item in approvals:
        item["created"] = _when(item.get("created_at"))
        item["expires"] = _when(item.get("expires_at"))
        expires = item["expires"]
        if expires is not None and expires.tzinfo is not None:
            expires = expires.replace(tzinfo=None)
        item["overdue"] = bool(expires and expires < now)
    decided = service.get_recent_decisions()
    decisions = decided.get("decisions", []) if decided.get("success") else []
    for row in decisions:
        row["proposed"] = _when(row.get("created_at"))
    # What one proposal would affect is worked out on demand, for the proposal the
    # person opened (a notification link or "What does this affect?"), so the page
    # stays quick however long the queue is.
    focus_id = request.args.get("approval", type=int)
    impact = None
    if focus_id and any(item["id"] == focus_id for item in approvals):
        try:
            result = service.get_approval_impact(focus_id)
            impact = result.get("impact") if result.get("success") else {"error": True}
        except Exception:
            impact = {"error": True}
    return render_template(
        "ai_chat/approval_inbox.html",
        approvals=approvals,
        queue_error=None if queue.get("success") else "The approval inbox could not be loaded. Refresh the page to try again.",
        decisions=decisions,
        decided_error=None if decided.get("success") else "Recent decisions could not be loaded.",
        focus_id=focus_id,
        impact=impact,
    )


__all__ = [
    "pending_approvals",
    "approver_queue",
    "decided_approvals",
    "approval_impact",
    "approve_pending_approval",
    "reject_pending_approval",
    "approval_inbox",
]

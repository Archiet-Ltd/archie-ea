"""AI chat approval workflow routes."""

from flask import jsonify, render_template, request
from flask_login import current_user, login_required

from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

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


@unified_ai_chat_bp.route("/approvals/<int:approval_id>/approve", methods=["POST"])
@login_required
def approve_pending_approval(approval_id):
    # approve_and_execute, not approve_approval: the latter has never existed on
    # AIChatApprovalService, so every approve click from the /ai-chat modal
    # (static/js/ai_chat/approval_modal.js) raised AttributeError and 500'd. The
    # queue itself worked - the blueprint page reaches it via a different route -
    # so the flow was sound and only this call site was wrong.
    result = _approval_service().approve_and_execute(approval_id, current_user.id)
    return jsonify(result), _decision_status(result)


@unified_ai_chat_bp.route("/approvals/<int:approval_id>/reject", methods=["POST"])
@login_required
def reject_pending_approval(approval_id):
    payload = request.get_json(silent=True) or {}
    reason = payload.get("reason")
    result = _approval_service().reject_approval(approval_id, reason)
    return jsonify(result), _decision_status(result)


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
    return render_template("ai_chat/approval_inbox.html")


__all__ = [
    "pending_approvals",
    "approver_queue",
    "approve_pending_approval",
    "reject_pending_approval",
    "approval_inbox",
]

"""AI chat approval workflow routes."""

from flask import jsonify, render_template, request
from flask_login import current_user, login_required

from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

from . import unified_ai_chat_bp


def _approval_service():
    return AIChatApprovalService(current_user.id)


def _approval_or_404(approval_id):
    """The tenant-scoped existence guard: a foreign or unknown id is a 404
    before the service is reached (the service checks the organisation again)."""
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval
    from app.utils.route_guards import require_entity_json

    return require_entity_json(AIChatCRUDApproval, approval_id, label="Approval")


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
    _, missing = _approval_or_404(approval_id)
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
    _, missing = _approval_or_404(approval_id)
    if missing:
        return missing
    result = _approval_service().approve_and_execute(approval_id, current_user.id)
    return jsonify(result), _decision_status(result)


@unified_ai_chat_bp.route("/approvals/<int:approval_id>/reject", methods=["POST"])
@login_required
def reject_pending_approval(approval_id):
    _, missing = _approval_or_404(approval_id)
    if missing:
        return missing
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
    "decided_approvals",
    "approval_impact",
    "approve_pending_approval",
    "reject_pending_approval",
    "approval_inbox",
]

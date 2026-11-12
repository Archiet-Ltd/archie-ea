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
        "ACCOUNT_OFF": 403,
        "CONFLICT": 409,
        "DECLINED": 409,
        "IN_REVIEW": 409,
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


@unified_ai_chat_bp.route("/approvals/<int:approval_id>/undo", methods=["POST"])
@login_required
def undo_applied_approval(approval_id):
    """Undo an applied item from the record: put back what it changed."""
    _, missing = require_entity_json(AIChatCRUDApproval, approval_id, label="Approval")
    if missing:
        return missing
    result = _approval_service().undo_applied(approval_id)
    if request.form.get("csrf_token") is not None:
        if result.get("success"):
            flash(result.get("message") or "Undone.", "success")
        else:
            flash(result.get("error") or "It could not be undone. Nothing was changed.", "error")
        return redirect(url_for("unified_ai_chat.approval_inbox"))
    return jsonify(result), _decision_status(result)


@unified_ai_chat_bp.route("/approvals/inbox", methods=["GET"])
@login_required
def approval_inbox():
    """The Approvals record: what was applied, linked, declined and retired, and why.

    Every proposal is settled by the server, so nothing here waits for a person and there is no Approve or
    Reject. Requires GENERAL permission (write access), matching the sidebar link guard."""
    from app.models.user import Permission

    if not current_user.can(Permission.GENERAL):
        from flask import abort

        abort(403)
    from app.modules.ai_chat.services.change_review import SOURCE_WORDS

    from datetime import datetime

    service = _approval_service()
    source = (request.args.get("source") or "").strip() or None
    # Only a proposal from a source that is not settled by the server yet waits for a person; everything else
    # is in the record below, with what became of it.
    queue = service.get_approver_queue()
    approvals = [a for a in (queue.get("approvals", []) if queue.get("success") else []) if not a.get("in_loop")]
    now = datetime.utcnow()
    for item in approvals:
        item["created"] = _when(item.get("created_at"))
        item["expires"] = _when(item.get("expires_at"))
        expires = item["expires"]
        if expires is not None and expires.tzinfo is not None:
            expires = expires.replace(tzinfo=None)
        item["overdue"] = bool(expires and expires < now)
    found = service.get_settled_records(source)
    records = found.get("records", []) if found.get("success") else []
    # The record of one reviewed item (checks, rounds, both models, every reason), read only, for the item the
    # person opened from the assistant's answer or from the list.
    focus_id = request.args.get("approval", type=int)
    impact = None
    if focus_id and any(item["id"] == focus_id for item in approvals):
        try:
            result = service.get_approval_impact(focus_id)
            impact = result.get("impact") if result.get("success") else {"error": True}
        except Exception:
            impact = {"error": True}
    record = None
    record_missing = False
    if focus_id:
        opened = service.get_review_record(focus_id)
        if opened.get("success"):
            record = opened["record"]
        else:
            record_missing = True
    return render_template(
        "ai_chat/approval_inbox.html",
        record=record,
        record_missing=record_missing,
        approvals=approvals,
        queue_error=None if queue.get("success") else "The approval inbox could not be loaded.",
        impact=impact,
        records=records,
        records_error=None if found.get("success") else "The record could not be loaded.",
        source_label=(SOURCE_WORDS.get(source) or ("Asked for in the AI chat" if source == "chat" else source))
        if source else None,
        focus_id=focus_id,
    )


__all__ = [
    "pending_approvals",
    "approver_queue",
    "decided_approvals",
    "approval_impact",
    "approve_pending_approval",
    "reject_pending_approval",
    "undo_applied_approval",
    "approval_inbox",
]

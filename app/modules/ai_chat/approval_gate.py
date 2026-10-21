"""
Approval gate for AI-originated CRUD operations (A95-008).

When REQUIRE_AI_APPROVAL=true, a gated write does NOT execute immediately.
Instead `queue_ai_write()` creates a real, executable AIChatCRUDApproval row
via AIChatApprovalService.create_pending_approval and returns an HTTP 202
response carrying that row's real id. The row appears in
GET /ai-chat/approvals/pending, and POST /ai-chat/approvals/<id>/approve
calls AIChatApprovalService.approve_and_execute, which performs the actual
write through AIDataInteractionService -- the same service method the route
calls directly when the gate is off.

This used to be a bare decorator (`@require_ai_approval`) that returned the
202/pending_approval shape without creating any approval record at all:
nothing appeared in the pending list, there was no approval_id, and the
write was silently dropped forever -- fabricated progress. It is now a
function, `queue_ai_write(operation_type, entity_type, payload, summary,
entity_id=None)`, called *inline* from inside the view body rather than as a
decorator wrapping the whole function. That placement matters: several of
these routes validate and sanitize `request.get_json()` (HTML-escaping,
enum checks, length limits) before handing the result to the
AIDataInteractionService call. A decorator wrapping the entire view sees
only the raw, unvalidated body and would queue *that* -- so approving it
later would run unsanitized input through the same write path the direct
route deliberately protects. Calling queue_ai_write() after validation, with
the already-validated `data` dict as `payload`, means the approval record
(and what approve_and_execute eventually writes) is byte-for-byte what the
route would have written itself.

operation_type/entity_type are not free-form labels: they must be one of
the pairs AIChatApprovalService.approve_and_execute's dispatch actually
executes ("create"/capability|application|vendor|capability_mapping|
work_package, "update"/capability|application|vendor, "link"/
application_capability_mapping, "delete"/capability|application|vendor --
see that file). A write that cannot be expressed in that vocabulary must
not call queue_ai_write() at all -- see the routes in workflow_routes.py
that execute immediately with a comment explaining why (add-compliance-
requirement, create-requirement, apply-archimate, apply-apqc): an honest
immediate write beats queuing something the approval step could never
actually execute.
"""

import logging

from flask import current_app, jsonify, request
from flask_login import current_user

_ai_action_logger = logging.getLogger('ai_originated_actions')
_ai_action_count = 0  # incremented by tag_ai_action


def tag_ai_action(action_type: str, entity_type: str, entity_id=None, metadata: dict = None):
    """Log an AI-originated action for the 'routine decisions automated' metric."""
    global _ai_action_count
    _ai_action_count += 1
    _ai_action_logger.info(
        'ai_action',
        extra={
            'ai_originated': True,
            'action_type': action_type,
            'entity_type': entity_type,
            'entity_id': str(entity_id) if entity_id else None,
            'metadata': metadata or {},
        }
    )


def get_ai_action_count():
    """Return the number of AI-originated actions recorded in this process."""
    return _ai_action_count


def queue_ai_write(operation_type: str, entity_type: str, payload: dict, summary: str, entity_id=None):
    """Queue (or clear the way for) an AI-originated write.

    Call this from inside a view, after any input validation/sanitization,
    with the fully-validated payload that would be passed to the
    AIDataInteractionService method the route would otherwise call directly.

    Returns:
        A Flask response tuple `(body, 202)` to return immediately from the
        view if REQUIRE_AI_APPROVAL is enabled (the write has been queued,
        not executed) -- or `None` if the gate is disabled and the caller
        should proceed to execute the write itself. `None` also tags the
        action via tag_ai_action for the 'routine decisions automated'
        metric, mirroring the old decorator's disabled-gate behaviour.
    """
    if entity_type == "vendor" and operation_type in ("create", "update", "delete"):
        # A vendor row is shared by every organisation: refuse at queue time
        # for anyone but a platform administrator (the replay refuses again).
        from app.middleware.tenant_decorators import (
            SHARED_VENDOR_REFUSAL, shared_vendor_edit_allowed,
        )

        if not shared_vendor_edit_allowed(current_user):
            return jsonify({"success": False, "error": SHARED_VENDOR_REFUSAL}), 403

    if not current_app.config.get("REQUIRE_AI_APPROVAL", False):
        tag_ai_action(
            action_type=request.endpoint,
            entity_type=entity_type,
            entity_id=entity_id,
            metadata={'method': request.method, 'path': request.path},
        )
        return None

    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    svc = AIChatApprovalService(user_id=current_user.id)
    queued = svc.create_pending_approval(
        operation_type=operation_type,
        entity_type=entity_type,
        original_command=f"{request.method} {request.path}",
        operation_payload=payload,
        summary=summary,
        entity_id=entity_id,
    )

    if not queued.get("success"):
        return (
            jsonify(
                {
                    "status": "error",
                    "error": queued.get("error", "Failed to queue approval"),
                    "ai_originated": True,
                }
            ),
            500,
        )

    return (
        jsonify(
            {
                "status": "pending_approval",
                "message": queued["message"],
                "approval_id": queued["approval_id"],
                "action": request.endpoint,
                "payload": payload,
                "ai_originated": True,
            }
        ),
        202,
    )

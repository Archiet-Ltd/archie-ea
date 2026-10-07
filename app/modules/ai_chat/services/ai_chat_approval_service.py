"""
-> app.modules.ai_chat.services

AI Chat Approval Service

Manages approval workflow for CRUD operations initiated via AI chat.
Ensures no data modifications happen without explicit user confirmation.
"""

import json
import logging
from datetime import datetime, timedelta
from html import escape
from typing import Any, Dict, List, Optional


from app import db
from app.models.ai_chat_crud_approval import AIChatApprovalAuditLog, AIChatCRUDApproval, ApprovalStatus
from app.models.audit_log import AuditLog
from app.models.user import User
from app.services.ai_data_interaction_service import AIDataInteractionService
from app.utils.duplicate_guard import normalize_name

logger = logging.getLogger(__name__)



def _load_acting_user(user_id):
    """Load the user a request is executing as, scoped to the current tenant.

    Deliberately NOT User.query.get(). Query.get() is tenant-scoped only on an
    identity-map MISS (CLAUDE.md): on a hit it returns the cached object without
    emitting SQL, so do_orm_execute never runs and no tenant predicate is
    applied. A permission check must never be able to authorise against a user
    cached from another organisation — harmless per-request, but the agent
    runner, CLI and scheduler all loop over tenants inside one session, and that
    is exactly where an autonomous agent executes.

    User is tenant-owned but does NOT carry TenantMixin, so the org predicate is
    added explicitly here rather than injected by the middleware.
    """
    from flask import g
    from app.models.user import User

    query = User.query.filter_by(id=user_id)
    org_id = getattr(g, "current_org_id", None)
    if org_id is not None:
        query = query.filter_by(organization_id=org_id)
    return query.first()


class AIChatApprovalError(ValueError):
    """Base class for an approval decision approve_and_execute refuses to make.

    Mirrors app.services.arb_governance_service's ARBDecisionError — same
    governance surface, same rule (V-01/M-05), independently discovered on the
    ARB side first (85c2924) and closed here second: refuse before any
    mutation, audit-log the refusal itself.
    """


class SelfApprovalError(AIChatApprovalError):
    """The requester attempted to approve their own queued AI operation (V-01)."""


class MissingApproverError(AIChatApprovalError):
    """No resolvable approver identity, or the approver lacks write permission."""


def create_approval_record(
    *,
    organization_id: int,
    operation_type: str,
    entity_type: str,
    summary: str,
    operation_payload: Dict[str, Any],
    entity_id: Optional[int] = None,
    user_id: Optional[int] = None,
    original_command: Optional[str] = None,
    chat_session_id: Optional[str] = None,
    agent_turn_id: Optional[str] = None,
    source_table: Optional[str] = None,
    source_id: Optional[int] = None,
    expiry_minutes: int = 15,
    persona: Optional[str] = None,
) -> AIChatCRUDApproval:
    """The one writer of ai_chat_crud_approvals (consolidation).

    Lower-level than AIChatApprovalService.create_pending_approval, which
    wraps this for the live-chat path (auth check, duplicate detection, a
    chat-formatted response). This is what a non-chat writer uses instead:
    the backfill command, and the repointed constructor sites of
    ReviewQueueItem, RelationshipSuggestion and SolutionBlueprintProposal,
    none of which have a live chat_session_id, original_command or
    necessarily a human user_id.

    Adds and flushes; does not commit — the caller controls the transaction
    boundary (a single commit per request for the chat path, one per
    organisation for the backfill command, one per record for a repointed
    constructor site that commits its own work as before).
    """
    approval = AIChatCRUDApproval(
        user_id=user_id,
        organization_id=organization_id,
        operation_type=operation_type,
        entity_type=entity_type,
        entity_id=entity_id,
        original_command=original_command or f"system: {operation_type} {entity_type}",
        operation_payload=json.dumps(operation_payload),
        summary=summary,
        status=ApprovalStatus.PENDING,
        expires_at=datetime.utcnow() + timedelta(minutes=expiry_minutes),
        chat_session_id=chat_session_id,
        agent_turn_id=agent_turn_id,
        source_table=source_table,
        source_id=source_id,
        persona=persona,
    )
    db.session.add(approval)
    db.session.flush()
    db.session.add(
        AIChatApprovalAuditLog(
            approval_id=approval.id,
            from_status=None,
            to_status=ApprovalStatus.PENDING.value,
            event="created",
            actor_user_id=user_id,
            actor_type="user" if user_id is not None else "system",
        )
    )
    return approval


def _restrict_access_refusal(approval, actor) -> Optional[Dict[str, Any]]:
    """R1-B88: who may decide an export flag (``restrict_access`` on a user).

    None when the decision may go ahead; otherwise the refusal to return. Run
    before an approval is claimed, so a refused attempt (the flagged person
    deciding their own flag, or someone who is not an administrator) leaves the
    flag open instead of burning it.
    """
    if approval.operation_type != "restrict_access" or approval.entity_type != "user":
        return None
    from app.middleware.tenant_decorators import is_platform_admin
    from app.services.rbac_service import rbac_service

    refusal = {"success": False, "code": "FORBIDDEN"}
    if actor.id == approval.entity_id:
        return {**refusal, "error": "You cannot decide a flag about your own downloads."}
    if not (
        is_platform_admin(actor)
        or rbac_service.is_org_admin(actor, approval.organization_id)
    ):
        return {**refusal, "error": "Only an organisation administrator can decide this flag."}
    # tenant-scoping-ok: User is not TenantMixin; organization_id is in the predicate.
    target = User.query.filter(
        User.id == approval.entity_id,
        User.organization_id == approval.organization_id,
    ).first()
    if target is None:
        return {"success": False, "code": "NOT_FOUND", "error": "The flagged person is not a member."}
    if getattr(target, "is_platform_admin", False):
        return {**refusal, "error": "A platform administrator's access is not managed here."}
    return None


class AIChatApprovalService:
    """
    Service for managing AI chat CRUD operation approvals.

    All CRUD operations detected in natural language chat messages are
    converted to pending approvals that require explicit user confirmation.
    """

    # Default expiration time for pending approvals (15 minutes)
    DEFAULT_EXPIRY_MINUTES = 15

    def __init__(self, user_id: Optional[int] = None):
        self.user_id = user_id
        self.logger = logging.getLogger(__name__)

    def _acting_user(self, *, require_general: bool = False):
        """Resolve the actor once, with the current request tenant still in force."""
        if not self.user_id:
            return None, {
                "success": False,
                "code": "FORBIDDEN",
                "error": "Authentication required to manage approvals",
            }
        actor = _load_acting_user(self.user_id)
        if not actor or actor.organization_id is None:
            return None, {
                "success": False,
                "code": "FORBIDDEN",
                "error": "An organization-scoped user is required to manage approvals",
            }
        if require_general:
            from app.models.user import Permission

            if not actor.can(Permission.GENERAL):
                return None, {
                    "success": False,
                    "code": "FORBIDDEN",
                    "error": "Your role does not include write/approval permission",
                }
        return actor, None

    def _record_refused_tool_approval(self, approval_id: int) -> None:
        """When someone without write access tries to run a queued AI tool
        call, record the refusal where the administrator's audit screen reads
        it -- the same record the tool executor writes when it refuses a call.

        Only a queued tool call in the person's own organisation is recorded:
        another organisation's approval id stays invisible, as it does in the
        response.
        """
        actor = _load_acting_user(self.user_id) if self.user_id else None
        if actor is None or actor.organization_id is None:
            return
        from app.models.user import Permission

        if actor.can(Permission.GENERAL):
            return
        approval = self._load_scoped_approval(approval_id, actor)
        if approval is None or approval.operation_type != "tool_use":
            return
        try:
            arguments = json.loads(approval.operation_payload or "{}")
        except (TypeError, ValueError):
            arguments = {}
        from app.modules.ai_chat.tools.executor import record_refused_tool_call

        record_refused_tool_call(actor, approval.entity_type, arguments, via="approval")

    @staticmethod
    def _load_scoped_approval(approval_id: int, actor: User) -> Optional[AIChatCRUDApproval]:
        """Load a decision target by id *and* actor organization.

        A foreign row deliberately looks absent. This prevents approval-id
        enumeration and makes every decision path share the same tenant fence.
        """
        return (
            AIChatCRUDApproval.query.filter_by(
                id=approval_id,
                organization_id=actor.organization_id,
            ).first()
        )

    @staticmethod
    def _claim_pending_approval(
        approval_id: int,
        organization_id: int,
        approver_id: int,
        *,
        session=None,
    ) -> bool:
        """Durably claim one pending approval before any mutable dispatch.

        The conditional update is the at-most-once boundary.  An executor may
        commit its own work, or the process may crash after the claim; both
        cases leave the approval APPROVED and therefore fail closed on retry
        rather than allowing a second caller to dispatch the write.
        Overdue-not-expired: a PENDING approval stays claimable past
        expires_at (overdue, not expired) — no expires_at check here.
        """
        session = session or db.session
        now = datetime.utcnow()
        updated = (
            session.query(AIChatCRUDApproval)
            .filter(
                AIChatCRUDApproval.id == approval_id,
                AIChatCRUDApproval.organization_id == organization_id,
                AIChatCRUDApproval.status == ApprovalStatus.PENDING,
            )
            .update(
                {
                    AIChatCRUDApproval.status: ApprovalStatus.APPROVED,
                    AIChatCRUDApproval.approved_by_id: approver_id,
                    AIChatCRUDApproval.approved_at: now,
                },
                synchronize_session=False,
            )
        )
        return updated == 1

    @staticmethod
    def _reject_pending_approval(
        approval_id: int,
        organization_id: int,
        *,
        reason: Optional[str],
        session=None,
    ) -> bool:
        """Atomically reject a pending approval without clobbering a claim.

        Overdue-not-expired: rejectable past expires_at too (overdue, not expired).
        """
        session = session or db.session
        updated = (
            session.query(AIChatCRUDApproval)
            .filter(
                AIChatCRUDApproval.id == approval_id,
                AIChatCRUDApproval.organization_id == organization_id,
                AIChatCRUDApproval.status == ApprovalStatus.PENDING,
            )
            .update(
                {AIChatCRUDApproval.rejected_reason: reason,
                 AIChatCRUDApproval.status: ApprovalStatus.REJECTED},
                synchronize_session=False,
            )
        )
        return updated == 1

    def _audit_nonblocking(self, approval: AIChatCRUDApproval, event: str, **kwargs) -> None:
        """Record an audit event without rolling back an already durable transition."""
        try:
            self._audit(approval, event=event, **kwargs)
            db.session.commit()
        except Exception:
            db.session.rollback()
            self.logger.warning(
                "Approval %s audit event %s could not be recorded after its durable transition",
                approval.id,
                event,
                exc_info=True,
            )

    # ------------------------------------------------------------------ #
    # Audit trail (ARCH-022)                                              #
    # ------------------------------------------------------------------ #

    def _audit(
        self,
        approval: AIChatCRUDApproval,
        event: str,
        to_status: str,
        actor_user_id: Optional[int],
        from_status: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Append one immutable transition row. Never updates, never deletes.

        Refuses (raises) rather than silently writing a system-actor row for
        "approved" or "executed" — those two events must be traceable to a
        human. This is what lets application code guarantee approved_by_id is
        never null on anything that reached an executed state: the guarantee
        is enforced here, at the one place every transition passes through,
        not re-implemented at each call site.
        """
        if event in ("approved", "executed") and not actor_user_id:
            raise ValueError(
                f"Refusing to record '{event}' on approval {approval.id} with no actor_user_id "
                "— execution requires a human approver."
            )
        db.session.add(
            AIChatApprovalAuditLog(
                approval_id=approval.id,
                from_status=from_status,
                to_status=to_status,
                event=event,
                actor_user_id=actor_user_id,
                actor_type="user" if actor_user_id else "system",
                reason=reason,
            )
        )

        # F-01: /admin/audit-log (soc2_audit_log) only ever queried AuditLog,
        # so these governance transitions were recorded but invisible on the
        # compliance page even though they were captured in full detail here.
        # Mirror the decision-relevant events onto AuditLog too — richer
        # detail stays in AIChatApprovalAuditLog, this is just so "who
        # approved this change" has one answerable page.
        if event in ("approved", "cancelled", "rejected", "executed", "expired"):
            try:
                AuditLog.log(
                    action=event[:20],
                    table_name=f"ai_chat_approval:{approval.entity_type}",
                    record_id=approval.entity_id,
                    # The approval itself is the durable tenant snapshot. An
                    # expiry has no actor, so deriving org from a user used to
                    # drop that compliance record into a global/NULL scope.
                    organization_id=approval.organization_id,
                    user_id=actor_user_id,
                    new_value={
                        "approval_id": approval.id,
                        "operation_type": approval.operation_type,
                        "entity_type": approval.entity_type,
                        "from_status": from_status,
                        "to_status": to_status,
                        "reason": reason,
                    },
                )
            except Exception:
                logger.warning(
                    "AuditLog mirror-write failed for approval %s event %s (non-blocking)",
                    approval.id, event, exc_info=True,
                )

    # ------------------------------------------------------------------ #
    # Duplicate detection (ARCH-021)                                       #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalized_payload_key(operation_type: str, entity_type: str, payload: Dict[str, Any]) -> str:
        """A comparison key for 'is this the same operation, worded differently'.

        Reuses duplicate_guard.normalize_name (casefold + whitespace collapse)
        field-by-field rather than a byte-for-byte JSON compare, so two payloads
        differing only in description wording or key order still match — which
        is exactly the ARCH-021 case (two approvals for the same operation that
        differed only in description wording).
        """
        normalized_fields = {
            k: normalize_name(v) if isinstance(v, str) else v
            for k, v in sorted(payload.items())
            if k not in ("description", "summary", "original_command", "rationale")
        }
        return f"{operation_type}:{entity_type}:{json.dumps(normalized_fields, sort_keys=True, default=str)}"

    def find_duplicate_pending(
        self, operation_type: str, entity_type: str, payload: Dict[str, Any]
    ) -> Optional[AIChatCRUDApproval]:
        """An existing PENDING approval for the same operation, or None."""
        if not self.user_id:
            return None
        candidate_key = self._normalized_payload_key(operation_type, entity_type, payload)
        pending = (
            AIChatCRUDApproval.query.filter_by(
                user_id=self.user_id,
                operation_type=operation_type,
                entity_type=entity_type,
                status=ApprovalStatus.PENDING,
            )
            .all()
        )
        for existing in pending:
            try:
                existing_payload = json.loads(existing.operation_payload)
            except (json.JSONDecodeError, TypeError):
                continue
            existing_key = self._normalized_payload_key(operation_type, entity_type, existing_payload)
            if existing_key == candidate_key:
                return existing
        return None

    def create_pending_approval(
        self,
        operation_type: str,
        entity_type: str,
        original_command: str,
        operation_payload: Dict[str, Any],
        summary: str,
        entity_id: Optional[int] = None,
        chat_session_id: Optional[str] = None,
        agent_turn_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a pending approval for a CRUD operation.

        Args:
            operation_type: Type of operation (create, update, delete)
            entity_type: Type of entity being modified (capability, application, etc.)
            original_command: The natural language command that triggered this
            operation_payload: The data payload for the operation
            summary: Human-readable summary of what will happen
            entity_id: ID of entity (for update/delete operations)
            chat_session_id: Chat session identifier — ARCH-020. Every call site in
                this codebase now has one available (multi_domain_chat_service.py's
                self._stable_session_id, or AgentRunner.chat_session_id) and must
                pass it; a None here is what let approvals float free of the
                conversation that raised them.
            agent_turn_id: Identifier for the specific agent turn/run that raised
                this approval, distinct from the session (a conversation, ARCH-020).

        Returns:
            Dict with approval details and confirmation instructions
        """
        try:
            # ENT-042: Block anonymous CRUD — a None user_id creates unfilterable approvals.
            if not self.user_id:
                return {
                    "success": False,
                    "code": "FORBIDDEN",
                    "error": "Authentication required to request CRUD operations",
                }

            requester, actor_error = self._acting_user()
            if actor_error:
                return actor_error

            # ARCH-021: reuse an existing pending approval for the same operation
            # rather than creating a duplicate. Surfaced to the caller/agent so it
            # can tell the user "already pending" instead of silently re-queuing.
            duplicate = self.find_duplicate_pending(operation_type, entity_type, operation_payload)
            if duplicate:
                self.logger.info(
                    f"Duplicate pending approval detected for {operation_type} {entity_type}; "
                    f"reusing approval {duplicate.id} instead of creating a new one"
                )
                return {
                    "success": True,
                    "approval_id": duplicate.id,
                    "status": "pending_approval",
                    "operation_type": duplicate.operation_type,
                    "entity_type": duplicate.entity_type,
                    "summary": duplicate.summary,
                    "expires_at": duplicate.expires_at.isoformat(),
                    "duplicate_of_existing": True,
                    "message": (
                        f"This is already pending as approval {duplicate.id} "
                        f"(\"{duplicate.summary}\"). It is queued for another authorized user "
                        f"to review; type 'reject {duplicate.id}' to cancel — I won't queue it twice."
                    ),
                    "requires_approval": True,
                }

            # Create approval record
            approval = create_approval_record(
                organization_id=requester.organization_id,
                operation_type=operation_type,
                entity_type=entity_type,
                entity_id=entity_id,
                original_command=original_command,
                operation_payload=operation_payload,
                summary=summary,
                user_id=self.user_id,
                chat_session_id=chat_session_id,
                agent_turn_id=agent_turn_id,
                expiry_minutes=self.DEFAULT_EXPIRY_MINUTES,
            )
            db.session.commit()

            self.logger.info(
                f"Created pending approval {approval.id} for {operation_type} {entity_type} "
                f"(chat_session_id={chat_session_id!r}, agent_turn_id={agent_turn_id!r})"
            )

            return {
                "success": True,
                "approval_id": approval.id,
                "status": "pending_approval",
                "operation_type": operation_type,
                "entity_type": entity_type,
                "summary": summary,
                "expires_at": approval.expires_at.isoformat(),
                "message": (
                    f"I've prepared a {operation_type} operation for {entity_type}. "
                    f"It is queued for another authorized user to review:\n\n"
                    f"**Summary:** {summary}\n\n"
                    f"**Action Required:** Another authorized user can approve it; "
                    f"you may type 'reject {approval.id}' to cancel it. "
                    f"This request expires in {self.DEFAULT_EXPIRY_MINUTES} minutes."
                ),
                "requires_approval": True,
            }

        except Exception as e:
            db.session.rollback()
            self.logger.error(f"Failed to create approval: {e}")
            return {
                "success": False,
                "error": f"Failed to create approval: {str(e)}",
            }

    @staticmethod
    def _execution_failure_response(result: Dict[str, Any], approval_id: int) -> Dict[str, Any]:
        """Preserve structured recovery from a failed approved tool execution."""
        response = {
            "success": False,
            "error": result.get("error", "Operation failed"),
            "approval_id": approval_id,
        }
        for key in ("reason_codes", "missing_evidence", "recovery", "charter_refused", "code"):
            if key in result:
                response[key] = result[key]
        return response

    def approve_and_execute(
        self, approval_id: int, approving_user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Approve and execute a pending CRUD operation.

        Args:
            approval_id: ID of the approval to execute
            approving_user_id: User ID of the approver (may differ from requester)

        Returns:
            Dict with execution result
        """
        try:
            # Route callers must not claim to be another approver. The service
            # identity is the authenticated actor; accepting a second id here
            # would turn a harmless parameter into an impersonation primitive.
            if approving_user_id is not None and approving_user_id != self.user_id:
                return {
                    "success": False,
                    "code": "FORBIDDEN",
                    "error": "Approval actor does not match the signed-in user",
                }

            actor, actor_error = self._acting_user(require_general=True)
            if actor_error:
                self._record_refused_tool_approval(approval_id)
                return actor_error
            effective_approver_id = actor.id
            approval = self._load_scoped_approval(approval_id, actor)
            if not approval:
                return {
                    "success": False,
                    "code": "NOT_FOUND",
                    "error": f"Approval {approval_id} not found",
                }

            if approval.user_id == effective_approver_id:
                self._audit(
                    approval,
                    event="self_approval_refused",
                    to_status=approval.status.value,
                    actor_user_id=effective_approver_id,
                    from_status=approval.status.value,
                    reason="approver is the same user who requested the operation",
                )
                db.session.commit()
                raise SelfApprovalError(
                    "You cannot approve your own request. Ask another user with write "
                    "permission to review and approve it."
                )

            # Check status
            if approval.status != ApprovalStatus.PENDING:
                return {
                    "success": False,
                    "code": "CONFLICT",
                    "error": f"Approval is already {approval.status.value}",
                }

            restricted_refusal = _restrict_access_refusal(approval, actor)
            if restricted_refusal is not None:
                return restricted_refusal

            # Overdue-not-expired: an item past expires_at is overdue, not expired —
            # it stays actionable indefinitely (escalate_overdue_approvals, run
            # periodically, notifies the organisation's administrators the first
            # time it goes overdue; see AIChatCRUDApproval.is_overdue()). Nothing
            # here refuses the approval or forces a status transition on it.

            # Persist the claim before dispatch.  This is deliberately before
            # parsing or calling any executor because some executors commit
            # internally; a second reviewer must observe the claim even then.
            if not self._claim_pending_approval(
                approval_id,
                actor.organization_id,
                effective_approver_id,
            ):
                return {
                    "success": False,
                    "code": "CONFLICT",
                    "error": "Approval was already claimed or is no longer actionable",
                }
            # The claim is the at-most-once boundary and must be committed
            # before a best-effort audit can fail.  In particular, rolling back
            # a failed audit must never reopen a write that may be dispatched.
            db.session.commit()
            db.session.expire_all()
            approval = self._load_scoped_approval(approval_id, actor)
            if not approval:
                return {
                    "success": False,
                    "code": "NOT_FOUND",
                    "error": f"Approval {approval_id} not found after claiming",
                }
            self._audit_nonblocking(
                approval,
                event="approved",
                to_status=ApprovalStatus.APPROVED.value,
                actor_user_id=effective_approver_id,
                from_status=ApprovalStatus.PENDING.value,
                reason="claimed before execution; retries fail closed",
            )

            # ARCH-022: execution is REFUSED without a resolvable human approver.
            # approving_user_id may be explicitly None from a caller; fall back to
            # self.user_id (already required above), but never proceed with both
            # unset — that is precisely the "approved_by_id: null reached
            # executed" defect.
            if not effective_approver_id:
                db.session.add(
                    AIChatApprovalAuditLog(
                        approval_id=approval.id,
                        from_status=ApprovalStatus.PENDING.value,
                        to_status=ApprovalStatus.PENDING.value,
                        event="execution_refused",
                        actor_user_id=None,
                        actor_type="system",
                        reason="no resolvable approving user id",
                    )
                )
                db.session.commit()
                return {"success": False, "error": "Execution refused: no approving user identified"}

            # Parse operation payload
            try:
                payload = json.loads(approval.operation_payload)
            except json.JSONDecodeError:
                return {"success": False, "error": "Invalid operation payload"}

            # Execute the operation
            data_service = AIDataInteractionService(user_id=self.user_id)

            if approval.operation_type == "create":
                if approval.entity_type == "capability":
                    result = data_service.create_capability(payload)
                elif approval.entity_type == "application":
                    result = data_service.create_application(payload)
                elif approval.entity_type == "vendor":
                    result = data_service.create_vendor(payload)
                elif approval.entity_type == "capability_mapping":
                    result = data_service.create_capability_mapping(payload)
                elif approval.entity_type == "work_package":
                    result = data_service.create_work_package(payload)
                else:
                    return {"success": False, "error": f"Unknown entity type: {approval.entity_type}"}

            elif approval.operation_type == "link":
                if approval.entity_type == "application_capability_mapping":
                    result = data_service.link_application_to_capability(payload)
                else:
                    return {"success": False, "error": f"Unknown link entity type: {approval.entity_type}"}

            elif approval.operation_type == "update":
                entity_id = approval.entity_id
                if approval.entity_type == "capability":
                    result = data_service.update_capability(entity_id, payload)
                elif approval.entity_type == "application":
                    result = data_service.update_application(entity_id, payload)
                elif approval.entity_type == "vendor":
                    result = data_service.update_vendor(entity_id, payload)
                else:
                    return {"success": False, "error": f"Unknown entity type: {approval.entity_type}"}

            elif approval.operation_type == "agent_charter_change":
                # R1-B56: the proposed charter version was never created at
                # request time -- only approving it creates the real
                # AgentCharter row, so AgentCharter.current_for never sees
                # an unreviewed change as current.
                from app.modules.ai_chat.services.agent_registry_service import (
                    execute_charter_change,
                )
                from app.models.agent_registration import AgentRegistration

                registration = AgentRegistration.query.filter_by(
                    id=approval.entity_id, organization_id=approval.organization_id,
                ).first()
                if registration is None:
                    return {"success": False, "error": "Agent registration not found"}
                charter = execute_charter_change(registration, payload)
                result = {"success": True, "charter_id": charter.id, "version": charter.version}

            elif approval.operation_type == "end_of_support_alert":
                # R1-B85: approving the alert is the acknowledgement that a
                # refresh owner has been assigned (via the existing
                # ApplicationOwner flow on the affected application's own
                # page -- this is not a second owner-assignment mechanism).
                # There is nothing further to execute against the vendor
                # product itself, so this is a deliberate no-op dispatch
                # rather than falling through to "Unsupported operation
                # type", which would leave the claim permanently stuck.
                result = {"success": True, "acknowledged": True}

            elif approval.operation_type == "tool_use":
                # AgentRunner._queue_approval (agent_runner.py) writes exactly this
                # operation_type for every queued agent tool call — both the
                # always-approve tier (update_application_status,
                # submit_for_arb_review, generate_blueprint_narrative) and, since
                # the write-approval gate, any mutating tool queued because
                # auto-execute was off. entity_type carries the tool name and
                # operation_payload carries its arguments, exactly what ToolCall
                # needs. This mirrors POST /ai-chat/tools/approve/<id>
                # (chat_core.py: approve_tool_action) — same dispatch, same
                # ToolExecutor — so both approval surfaces (the blueprint panel's
                # dedicated endpoint and the main chat's approval modal, which
                # only ever calls this service) execute a queued tool call
                # identically instead of the main chat modal 400ing with
                # "Unsupported operation type: tool_use".
                from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor

                executor = ToolExecutor(self.user_id, persona=approval.persona)
                tc = ToolCall(id=str(approval_id), name=approval.entity_type, arguments=payload)
                result = executor.execute(tc)

            elif approval.operation_type == "restrict_access":
                # R1-B88: an export flag. Approving reduces the flagged member to
                # view-only and signs them out; the decider was checked above.
                from app.services.rbac_service import MemberAccessError, rbac_service

                try:
                    rbac_service.revoke_member_access(
                        approval.organization_id,
                        approval.entity_id,
                        actor_id=effective_approver_id,
                        reason="export_spike_restricted",
                        end_sessions=True,
                    )
                    result = {"success": True, "restricted_user_id": approval.entity_id}
                except MemberAccessError as exc:
                    result = {"success": False, "error": exc.message}

            elif approval.operation_type == "delete":
                # Hard delete — admin-only at execution time (double guard)
                # tenant-scoping-ok: self.user_id is the acting user's own id.
                actor = User.query.filter_by(id=self.user_id).first()
                if not actor or not actor.is_admin():
                    return {"success": False, "error": "Delete operations require administrator privileges"}
                entity_id = approval.entity_id
                if approval.entity_type == "capability":
                    result = data_service.delete_capability(entity_id)
                elif approval.entity_type == "application":
                    result = data_service.delete_application(entity_id)
                elif approval.entity_type == "vendor":
                    result = data_service.delete_vendor(entity_id)
                else:
                    return {"success": False, "error": f"Unknown entity type for delete: {approval.entity_type}"}

            else:
                return {
                    "success": False,
                    "error": f"Unsupported operation type: {approval.operation_type}",
                }

            # The approval state was durably claimed before dispatch.  A crash
            # or execution failure remains APPROVED with no executed_at value,
            # which intentionally forbids retrying an operation that may have
            # reached a downstream system.
            if result.get("success"):
                approval.execute(result)
                self._audit(
                    approval,
                    event="executed",
                    to_status=ApprovalStatus.APPROVED.value,
                    actor_user_id=effective_approver_id,
                    from_status=ApprovalStatus.APPROVED.value,
                )
                db.session.commit()

                self.logger.info(f"Approved and executed operation {approval_id} by user {effective_approver_id}")

                return {
                    "success": True,
                    "message": f"{approval.operation_type} operation executed successfully.",
                    "result": result,
                    "approval_id": approval_id,
                }
            else:
                # Execution failed but we still mark it as attempted
                approval.execute(result)
                self._audit(
                    approval,
                    event="execution_failed",
                    to_status=ApprovalStatus.APPROVED.value,
                    actor_user_id=effective_approver_id,
                    from_status=ApprovalStatus.APPROVED.value,
                    reason=result.get("error", "Operation failed after durable claim"),
                )
                db.session.commit()

                return self._execution_failure_response(result, approval_id)

        except (SelfApprovalError, MissingApproverError) as e:
            # Refusal was already audit-logged and committed above the raise;
            # nothing here to roll back except any in-progress work from this
            # same call, which there isn't since we never reached dispatch.
            self.logger.warning(f"Approval {approval_id} refused: {e}")
            return {"success": False, "error": str(e), "code": "APPROVAL_DENIED"}
        except Exception as e:
            db.session.rollback()
            self.logger.error(f"Failed to approve/execute operation {approval_id}: {e}")
            return {"success": False, "error": f"Execution failed: {str(e)}"}

    def reject_approval(self, approval_id: int, reason: Optional[str] = None) -> Dict[str, Any]:
        """
        Reject a pending approval.

        Args:
            approval_id: ID of the approval to reject
            reason: Optional reason for rejection

        Returns:
            Dict with rejection result
        """
        try:
            actor, actor_error = self._acting_user()
            if actor_error:
                return actor_error
            approval = self._load_scoped_approval(approval_id, actor)
            if not approval:
                return {
                    "success": False,
                    "code": "NOT_FOUND",
                    "error": f"Approval {approval_id} not found",
                }

            restricted_refusal = _restrict_access_refusal(approval, actor)
            if restricted_refusal is not None:
                return restricted_refusal

            # A requester can always cancel their own change. A separate
            # reviewer may reject it only with the same GENERAL permission the
            # approval action requires; a Viewer cannot interfere with either.
            if approval.user_id != actor.id:
                _, reviewer_error = self._acting_user(require_general=True)
                if reviewer_error:
                    return reviewer_error

            # Check status
            if approval.status != ApprovalStatus.PENDING:
                return {
                    "success": False,
                    "code": "CONFLICT",
                    "error": f"Approval is already {approval.status.value}",
                }

            is_requester_cancellation = approval.user_id == actor.id
            if not self._reject_pending_approval(
                approval_id,
                actor.organization_id,
                reason=reason,
            ):
                return {
                    "success": False,
                    "code": "CONFLICT",
                    "error": "Approval was already claimed or is no longer actionable",
                }
            # As for approval claims, make the state transition durable before
            # a non-critical audit write. A stale reject must never overwrite a
            # concurrent claim, and an audit failure must not reopen it.
            db.session.commit()
            db.session.expire_all()
            approval = self._load_scoped_approval(approval_id, actor)
            if not approval:
                return {
                    "success": False,
                    "code": "NOT_FOUND",
                    "error": f"Approval {approval_id} not found after rejection",
                }
            self._audit_nonblocking(
                approval,
                event="cancelled" if is_requester_cancellation else "rejected",
                to_status=ApprovalStatus.REJECTED.value,
                actor_user_id=actor.id,
                from_status=ApprovalStatus.PENDING.value,
                reason=reason,
            )

            event_label = "cancelled" if is_requester_cancellation else "rejected"
            self.logger.info(f"{event_label.title()} approval {approval_id}")

            return {
                "success": True,
                "message": (
                    f"{approval.operation_type} approval request cancelled."
                    if is_requester_cancellation
                    else f"{approval.operation_type} operation rejected."
                ),
                "approval_id": approval_id,
            }

        except Exception as e:
            db.session.rollback()
            self.logger.error(f"Failed to reject approval {approval_id}: {e}")
            return {"success": False, "error": f"Rejection failed: {str(e)}"}

    def get_pending_approvals(self) -> List[Dict[str, Any]]:
        """
        Get all pending approvals for the current user.

        Returns:
            List of pending approval dictionaries
        """
        actor, actor_error = self._acting_user()
        if actor_error:
            return []
        approvals = (
            AIChatCRUDApproval.query.filter_by(
                user_id=actor.id,
                organization_id=actor.organization_id,
                status=ApprovalStatus.PENDING,
            )
            .all()
        )
        return [approval.to_dict() for approval in approvals]

    def get_approver_queue(self) -> Dict[str, Any]:
        """Pending same-org approvals a different user may review.

        Overdue-not-expired: includes overdue rows (no expires_at filter — being
        overdue must never make an item disappear from the queue). A row with
        no requester (user_id NULL, backfilled or system-originated) is every
        eligible approver's to review, so the "not the requester" exclusion
        only applies when there is a requester to exclude.
        """
        actor, actor_error = self._acting_user(require_general=True)
        if actor_error:
            return actor_error
        approvals = (
            AIChatCRUDApproval.query.filter_by(
                organization_id=actor.organization_id,
                status=ApprovalStatus.PENDING,
            )
            .filter(
                db.or_(
                    AIChatCRUDApproval.user_id.is_(None),
                    AIChatCRUDApproval.user_id != actor.id,
                )
            )
            .order_by(AIChatCRUDApproval.created_at.desc())
            .all()
        )
        requester_ids = {approval.user_id for approval in approvals}
        requesters = {
            user.id: user
            for user in User.query.filter(
                User.organization_id == actor.organization_id,
                User.id.in_(requester_ids),
            ).all()
        } if requester_ids else {}
        return {
            "success": True,
            "approvals": [
                {
                    "id": approval.id,
                    "operation_type": approval.operation_type,
                    "entity_type": approval.entity_type,
                    "summary": approval.summary,
                    # This is the exact decoded JSON supplied to the executor;
                    # the modal renders it with x-text, never HTML insertion.
                    "arguments": json.loads(approval.operation_payload),
                    "created_at": approval.created_at.isoformat() if approval.created_at else None,
                    "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
                    # The queue query filters to PENDING only, so every item
                    # here is "pending". The inbox template's isOverdue()
                    # checks this field to decide whether to show the Overdue
                    # indicator — without it the indicator never renders even
                    # for genuinely overdue items.
                    "status": approval.status.value if approval.status else "pending",
                    # Source table/id for backfilled items (e.g. confidence
                    # reviews). The inbox template renders a source badge when
                    # these are present; without them the badge is always dead.
                    "source_table": getattr(approval, "source_table", None),
                    "source_id": getattr(approval, "source_id", None),
                    "requester": {
                        "id": approval.user_id,
                        "display_name": " ".join(
                            part for part in (
                                getattr(requesters.get(approval.user_id), "first_name", None),
                                getattr(requesters.get(approval.user_id), "last_name", None),
                            ) if part
                        ) or "Unknown requester",
                    },
                }
                for approval in approvals
            ],
        }

    def get_pending_for_session(self, chat_session_id: Optional[str]) -> List[Dict[str, Any]]:
        """Pending approvals for this user within one chat session (ARCH-020).

        This is what lets the agent answer "is anything already pending for
        THIS conversation" before deciding whether to queue a new approval or
        point the user at the existing one.
        """
        actor, actor_error = self._acting_user()
        if actor_error or not chat_session_id:
            return []
        approvals = (
            AIChatCRUDApproval.query.filter_by(
                user_id=actor.id,
                organization_id=actor.organization_id,
                chat_session_id=chat_session_id,
                status=ApprovalStatus.PENDING,
            )
            .order_by(AIChatCRUDApproval.created_at.desc())
            .all()
        )
        return [approval.to_dict() for approval in approvals]

    def check_for_confirmation_command(self, message: str) -> Optional[Dict[str, Any]]:
        """
        Check if a chat message is a confirmation command.

        Supports:
        - "confirm [approval_id]"
        - "approve [approval_id]"
        - "reject [approval_id]"
        - "cancel [approval_id]"

        Args:
            message: The chat message to check

        Returns:
            Dict with action and approval_id if matched, None otherwise
        """
        import re

        message = message.strip().lower()

        # Match confirmation commands
        confirm_patterns = [
            r'^confirm\s+(\d+)',
            r'^approve\s+(\d+)',
            r'^yes\s+(\d+)',
        ]

        for pattern in confirm_patterns:
            match = re.match(pattern, message)
            if match:
                return {
                    "action": "confirm",
                    "approval_id": int(match.group(1)),
                }

        # Match rejection commands
        reject_patterns = [
            r'^reject\s+(\d+)',
            r'^cancel\s+(\d+)',
            r'^no\s+(\d+)',
        ]

        for pattern in reject_patterns:
            match = re.match(pattern, message)
            if match:
                return {
                    "action": "reject",
                    "approval_id": int(match.group(1)),
                }

        # ARCH-020: id-less natural-language affirmations/rejections. "I approve,
        # proceed" has no approval_id in it, so the id-anchored patterns above
        # never match — the caller must resolve this against whatever is
        # PENDING for the current session (see resolve_natural_confirmation).
        #
        # These MUST full-match. An earlier version anchored only at the start
        # (r"^go ahead\b"), so an ordinary sentence that merely BEGAN with an
        # affirming word silently executed whatever write was pending:
        # "go ahead and explain the risk register", "approve it only after you
        # have checked X", "do it later" all matched. A prefix test is not a
        # safe test for consent on a path that mutates the system of record,
        # because the words after the prefix can reverse the meaning entirely.
        # The whole message must be the affirmation and nothing else.
        core_affirm = (
            r"(?:i\s+)?approve(?:\s+it|\s+this)?"
            r"|go\s+ahead"
            r"|(?:please\s+)?proceed"
            r"|confirm(?:\s+it|\s+this)?"
            r"|looks\s+good"
            r"|do\s+it"
            r"|yes"
        )
        core_deny = (
            r"(?:i\s+)?reject(?:\s+it|\s+this)?"
            r"|cancel(?:\s+it|\s+this)?"
            r"|don'?t\s+do\s+(?:it|that)"
            r"|no"
        )
        # Politeness and a second affirming clause ("I approve, proceed") are
        # allowed; anything carrying new instructions is not.
        _filler = r"(?:\s*[,.!;]\s*|\s+)(?:please|now|thanks|thank\s+you|ok|okay)"

        def _is_bare(core):
            pattern = (
                r"\s*(?:ok|okay)?\s*[,.!;]?\s*"
                r"(?:" + core + r")"
                r"(?:(?:\s*[,.!;]\s*|\s+)(?:" + core + r"))*"
                r"(?:" + _filler + r")*"
                r"\s*[.!]*\s*"
            )
            return re.fullmatch(pattern, message) is not None

        if _is_bare(core_affirm):
            return {"action": "confirm", "approval_id": None}

        if _is_bare(core_deny):
            return {"action": "reject", "approval_id": None}

        return None

    def resolve_natural_confirmation(
        self, confirmation: Dict[str, Any], chat_session_id: Optional[str]
    ) -> Dict[str, Any]:
        """Resolve an id-less confirmation (ARCH-020) against this session's queue.

        "I approve" carries no id, so it must never be treated as a brand-new
        request — the observed defect was exactly that: the agent, unable to
        see any approval state, queued a SECOND identical approval and asked
        again. This looks up what is actually PENDING for the user's current
        chat_session_id and acts on it, or tells the user plainly there is
        nothing to approve rather than silently re-queuing anything.
        """
        if confirmation.get("approval_id") is not None:
            if confirmation["action"] == "confirm":
                return self.approve_and_execute(confirmation["approval_id"])
            return self.reject_approval(confirmation["approval_id"])

        actor, actor_error = self._acting_user()
        if actor_error:
            return actor_error
        pending = (
            AIChatCRUDApproval.query.filter_by(
                user_id=actor.id,
                organization_id=actor.organization_id,
                chat_session_id=chat_session_id,
                status=ApprovalStatus.PENDING,
            )
            .order_by(AIChatCRUDApproval.created_at.desc())
            .all()
        )
        if not pending:
            return {
                "success": False,
                "requires_approval": False,
                "message": (
                    "There's nothing pending for me to approve in this conversation right now."
                ),
            }
        if len(pending) > 1:
            listing = "\n".join(f"- {p.id}: {p.summary}" for p in pending)
            return {
                "success": False,
                "requires_approval": True,
                "message": (
                    "There's more than one pending approval in this conversation — "
                    f"tell me which one by id:\n{listing}"
                ),
            }
        target = pending[0]
        if confirmation["action"] == "confirm":
            return self.approve_and_execute(target.id)
        return self.reject_approval(target.id)


def escalate_overdue_approvals(app=None) -> Dict[str, int]:
    """Overdue-not-expired: notify each organisation's administrators about its
    overdue (past expires_at, still PENDING, not yet escalated) approvals.

    One email per organisation, listing every overdue item currently pending
    for it. Idempotent: a row's escalated_at is set once escalation is
    attempted, so a later sweep never re-notifies for the same row (matching
    the "no duplicate work" shape of the digest-email jobs this reuses). Runs
    outside any request/tenant context — same shape as
    app/_bootstrap/_digest_emails.py's scheduled digests — so it resolves
    recipients and organisation membership explicitly per row rather than
    relying on request-scoped tenant filtering.

    Returns a summary: organisations notified and rows escalated.
    """
    from collections import defaultdict

    from flask import current_app

    from app._bootstrap._digest_emails import _get_recipients_by_roles, _safe_send_email

    app = app or current_app._get_current_object()
    overdue = AIChatCRUDApproval.get_overdue_unescalated()
    by_org: Dict[int, List[AIChatCRUDApproval]] = defaultdict(list)
    for row in overdue:
        if row.organization_id is not None:
            by_org[row.organization_id].append(row)

    organisations_notified = 0
    rows_escalated = 0
    now = datetime.utcnow()
    for organization_id, rows in by_org.items():
        # The brief asks for the organisation's own administrators, not only
        # the enterprise-wide platform_admin role: an organisation can have
        # a real is_org_admin without anyone holding platform_admin, and a
        # platform_admin-only lookup silently escalates to nobody for it
        # (reviews/pr302-final-check-v1.md). Union both -- an org admin for
        # this tenant, plus any platform_admin who also wants every escalation.
        # is_org_admin is a derived property (app/models/user.py, PR 291), not
        # a queryable column -- filter organisation + confirmed in SQL, then
        # the canonical admin rule in Python, same as every other caller of it.
        org_admins = [
            u for u in User.query.filter(
                User.organization_id == organization_id,
                User.confirmed.is_(True),
            ).all()
            if u.is_org_admin
        ]
        # _get_recipients_by_roles always filters by organization_id (it has
        # no global mode, by design -- see its own docstring), so this reaches
        # platform admins who belong to this organisation, not every platform
        # admin on the instance. That is deliberate: an overdue approval is
        # this tenant's data, and only this tenant's admins should be told
        # about it by email.
        recipients = sorted({u.email for u in org_admins if u.email} |
                             set(_get_recipients_by_roles(["platform_admin"], organization_id)))
        # summary/entity_type/operation_type all trace back to user- or
        # AI-generated content (a capability name, a blueprint proposal
        # name); escape before interpolating into hand-built HTML.
        items_html = "".join(
            f"<li>#{row.id}: {escape(str(row.operation_type))} {escape(str(row.entity_type))} — "
            f"{escape(str(row.summary))} "
            f"(raised {escape(row.created_at.isoformat() if row.created_at else 'unknown')})</li>"
            for row in rows
        )
        sent = _safe_send_email(
            app,
            subject=f"{len(rows)} approval request(s) overdue for review",
            recipients=recipients,
            html_body=(
                f"<p>{len(rows)} pending change{'s' if len(rows) != 1 else ''} "
                "in your organisation's approval queue passed their review-by "
                f"time and still need a decision:</p><ul>{items_html}</ul>"  # raw-html-ok: items_html is built above from escape()d fields only
            ),
        )
        for row in rows:
            row.escalated_at = now
            db.session.add(
                AIChatApprovalAuditLog(
                    approval_id=row.id,
                    from_status=row.status.value,
                    to_status=row.status.value,
                    event="escalated",
                    actor_user_id=None,
                    actor_type="system",
                    reason=f"overdue, {len(recipients)} administrator(s) notified" if sent
                    else "overdue, no SMTP configured — logged instead",
                )
            )
            rows_escalated += 1
        organisations_notified += 1
    db.session.commit()
    return {"organisations_notified": organisations_notified, "rows_escalated": rows_escalated}

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

    Mirrors app.services.arb_governance_service's ARBDecisionError: refuse before any
    mutation, audit-log the refusal itself.
    """


class SelfApprovalError(AIChatApprovalError):
    """The requester attempted to approve their own queued operation that the review loop does not settle."""


class MissingApproverError(AIChatApprovalError):
    """No resolvable approver identity, or the approver lacks write permission."""


def _review_rubric_version() -> str:
    from app.modules.ai_chat.services.change_review import RUBRIC_VERSION

    return RUBRIC_VERSION


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
    drafter_model: Optional[str] = None,
    settle: bool = False,
    review_budget=None,
    enter_review: bool = False,
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

    ``drafter_model`` is the "provider/model" that drafted the change (None when
    a rule or a form did). ``settle=True`` is for a caller that wants the change
    checked, reviewed and applied or declined in the same turn: the record is
    committed first, then ``settle_after_review`` runs, and the verdict is left on
    the returned record as ``review_verdict``. ``settle`` and ``enter_review`` mark
    the record as in the review loop (it carries the rubric version), so the sweep
    settles it if it waits. A record written by any other writer is left to that
    writer until it is moved into the loop.
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
        drafter_model=drafter_model,
        rubric_version=_review_rubric_version() if (settle or enter_review) else None,
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
    if settle and user_id is not None:
        db.session.commit()
        approval.review_verdict = AIChatApprovalService(user_id).settle_after_review(
            approval.id, budget=review_budget
        )
    return approval


def _plain(code: Optional[str]) -> str:
    """A stored code as words: ``tool_use`` reads "Tool use"."""
    text = str(code or "").replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def card_words(approval, payload: Dict[str, Any], people: Dict[int, str]) -> Dict[str, Any]:
    """The words an inbox card shows for one approval: the one mapping from its source and kind to words.

    The page shows these as they are; a stored code never reaches the card. ``people`` names the
    organisation's users by id."""
    if getattr(approval, "source_table", None) == "vendor_detection":
        from app.services.vendor_detection import card_words as vendor_words

        return vendor_words(approval, payload, people)
    source = getattr(approval, "source_table", None)
    return {
        "operation": _plain(approval.operation_type),
        "entity": _plain(approval.entity_type),
        "source": "From " + _plain(source).lower() if source else None,
        "requester": people.get(approval.user_id) or "The system",
    }


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
        if not actor.is_active:
            # The one rule for who can sign in (User.is_active): an account that is switched off acts
            # for no one, so a change it asked for is never applied and nothing it clicks is accepted.
            return None, {
                "success": False,
                "code": "ACCOUNT_OFF",
                "error": "This account is switched off",
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

    @staticmethod
    def _decline_failed_application(
        approval_id: int, organization_id: int, *, reason: str, session=None
    ) -> bool:
        """Mark a reviewed change declined when the tool refused or failed after the claim.

        The claim already made the record APPROVED, which keeps a second caller from dispatching the
        write again. Nothing was written, so the record must read as declined, not applied; the status
        stays closed to every other caller (it is no longer PENDING)."""
        session = session or db.session
        updated = (
            session.query(AIChatCRUDApproval)
            .filter(
                AIChatCRUDApproval.id == approval_id,
                AIChatCRUDApproval.organization_id == organization_id,
                AIChatCRUDApproval.status == ApprovalStatus.APPROVED,
            )
            .update(
                {AIChatCRUDApproval.status: ApprovalStatus.REJECTED,
                 AIChatCRUDApproval.rejected_reason: reason},
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

    def _discard_staged_connector_credential(self, approval: AIChatCRUDApproval) -> None:
        """M1: a rejected connectors proposal must never let its staged
        secret become reachable later -- delete it, best-effort, the same
        way _audit_nonblocking treats its own write as non-critical next to
        the already-durable rejection above it.
        """
        try:
            import json

            from app.modules.codegen.services.credential_vault import OrgCredentialVault

            payload = json.loads(approval.operation_payload or "{}")
            connector_type = payload.get("connector_type")
            if not connector_type:
                return
            from app.services.connector_framework import CONNECTOR_CREDENTIAL_TYPES

            vault = OrgCredentialVault()
            credential_type = CONNECTOR_CREDENTIAL_TYPES.get(connector_type)
            if credential_type:
                vault.discard_staged(approval.organization_id, connector_type, credential_type, approval.id)
            vault.discard_staged(approval.organization_id, connector_type, "credentials", approval.id)
            db.session.commit()
        except Exception:
            db.session.rollback()
            self.logger.warning(
                "Approval %s rejected but its staged connector credential could not be discarded",
                approval.id,
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
        actor_type: Optional[str] = None,
    ) -> None:
        """Append one immutable transition row. Never updates, never deletes.

        A person's "approved" or "executed" is traceable to that person: with no
        actor_user_id the row is refused unless the transition is explicitly the
        server's own (``actor_type="system"``), which is what the review of a
        requested change does (see change_review.py). The server's rows are
        written as actor type "system" and carry no user, so a system row can
        never be mistaken for a person's decision.
        """
        if event in ("approved", "executed") and not actor_user_id and actor_type != "system":
            raise ValueError(
                f"Refusing to record '{event}' on approval {approval.id} with no actor_user_id "
                "— only a person or the server's own review can approve."
            )
        db.session.add(
            AIChatApprovalAuditLog(
                approval_id=approval.id,
                from_status=from_status,
                to_status=to_status,
                event=event,
                actor_user_id=actor_user_id,
                actor_type=actor_type or ("user" if actor_user_id else "system"),
                reason=reason,
            )
        )

        # F-01: /admin/audit-log (soc2_audit_log) only ever queried AuditLog,
        # so these governance transitions were recorded but invisible on the
        # compliance page even though they were captured in full detail here.
        # Mirror the decision-relevant events onto AuditLog too — richer
        # detail stays in AIChatApprovalAuditLog, this is just so "who
        # approved this change" has one answerable page.
        if event in ("approved", "cancelled", "rejected", "executed", "expired", "applied", "declined"):
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
                        f"This is already being checked as change {duplicate.id} "
                        f"(\"{duplicate.summary}\"). I won't ask for it twice; "
                        f"type 'reject {duplicate.id}' to cancel it."
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
                enter_review=True,
            )
            db.session.commit()
            approval_id = approval.id
            expires_at = approval.expires_at.isoformat()

            self.logger.info(
                f"Created pending approval {approval_id} for {operation_type} {entity_type} "
                f"(chat_session_id={chat_session_id!r}, agent_turn_id={agent_turn_id!r})"
            )

            # The server checks the change, has a model other than the drafter review it, and
            # applies or declines it now, in this turn.
            from app.modules.ai_chat.services import change_review

            verdict = self.settle_after_review(approval_id)
            state = verdict.get("state")
            base = {
                "success": True,
                "approval_id": approval_id,
                "operation_type": operation_type,
                "entity_type": entity_type,
                "summary": summary,
                "expires_at": expires_at,
                "verdict": verdict,
                "record_url": verdict.get("record_url"),
            }
            if state == "applied":
                return {**base, "status": "applied", "requires_approval": False, "message": verdict["message"]}
            if state == "declined":
                return {**base, "status": "declined", "requires_approval": False, "message": verdict["message"]}

            # Still waiting: the owners of the thing being changed are told, in the app and by email.
            # After the commit and fully contained: a notification problem must never undo or fail a
            # recorded proposal.
            self._notify_owners_of_new_approval(approval, requester, operation_payload)
            if verdict.get("settles") is False:
                # A proposal this review does not settle yet keeps its own review.
                wait = (
                    f"It is waiting for review on its record (change {approval_id}); "
                    f"type 'reject {approval_id}' to cancel it."
                )
            elif change_review.WRITES_PAUSED_REASON in (verdict.get("reasons") or []):
                wait = (
                    f"This change is held because changes by the assistant are paused for this workspace. "
                    f"It is made when they resume, after it is checked and reviewed (change {approval_id}); "
                    f"type 'reject {approval_id}' to cancel it."
                )
            else:
                wait = (
                    f"No independent reviewer could be reached just now. I'll keep trying and the result "
                    f"will be on its record (change {approval_id}); type 'reject {approval_id}' to cancel it."
                )
            return {
                **base,
                "status": "pending_approval",
                "requires_approval": True,
                "message": (
                    f"I've prepared a {operation_type} operation for {entity_type} and it is being checked:\n\n"
                    f"**Summary:** {summary}\n\n" + wait
                ),
            }

        except Exception as e:
            db.session.rollback()
            self.logger.error(f"Failed to create approval: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Failed to create approval: {str(e)}",
            }

    def settle_after_review(self, approval_id: int, budget=None, approved_by: Optional[int] = None) -> Dict[str, Any]:
        """Check, review, revise and then apply or decline one recorded change, on the server.

        The change is checked (organisation, target, details, value, repeat, the requester's role),
        judged against the written rubric by a model other than the one that drafted it, revised up
        to twice, and then applied through ``approve_and_execute`` as the system, or declined with
        its reasons. Nothing waits for a second person. A change that cannot be reviewed yet (no
        independent reviewer reachable, or this run's review limit spent) stays in review, pending,
        and is picked up by the five-minute sweep.

        ``approved_by`` is the person who pressed Approve, when it was a person who asked for the change to
        be settled. It changes nothing about the review: only when the review has passed, the record then
        names that person as the one who approved it (the audit rows carry their id, not the system's).

        The record is looked up in the caller's own organisation only; another organisation's id
        looks absent. Returns the verdict: state (applied, declined, in_review), the summary, the
        reasons, the rounds, both model names and the path of the record.
        """
        from app.modules.ai_chat.services import change_review

        try:
            actor, actor_error = self._acting_user()
            if actor_error:
                if actor_error.get("code") == "ACCOUNT_OFF" and self.user_id:
                    # A switched-off account's own change is declined, with that reason and no review.
                    # (Its person is never the one who settles anyone else's.)
                    off = _load_acting_user(self.user_id)
                    mine = self._load_scoped_approval(approval_id, off) if off is not None else None
                    if mine is not None and mine.user_id == off.id and change_review.in_scope(mine):
                        return change_review.decline(self, mine, change_review.ACCOUNT_OFF_REASON)
                return actor_error
            approval = self._load_scoped_approval(approval_id, actor)
            if not approval:
                return {"success": False, "code": "NOT_FOUND", "error": f"Approval {approval_id} not found"}
            if approval.user_id is None or not change_review.in_scope(approval):
                return {**change_review.verdict(approval, "in_review", approval.summary, []), "settles": False}
            runner = self if approval.user_id == self.user_id else AIChatApprovalService(approval.user_id)
            requester, requester_error = runner._acting_user()
            if requester is not None and requester.organization_id != approval.organization_id:
                requester, requester_error = None, {"code": "MOVED"}
            if requester_error:
                return change_review.decline(
                    runner, approval,
                    change_review.ACCOUNT_OFF_REASON if requester_error.get("code") == "ACCOUNT_OFF"
                    else change_review.MOVED_REASON,
                )
            return change_review.settle(
                runner, approval, requester, budget, approved_by, self if approved_by else None)
        except Exception:
            db.session.rollback()
            self.logger.exception("Review of approval %s failed; it stays in review", approval_id)
            # A failed attempt counts like an unreachable reviewer, so a change that can never be
            # reviewed ends declined after the same number of tries instead of waiting for ever.
            ended = change_review.count_failed_attempt(self, approval_id)
            if ended is not None:
                return ended
            return {
                "success": False,
                "state": "in_review",
                "approval_id": approval_id,
                "summary": "",
                "reasons": ["The review could not be completed just now."],
                "message": "In review: the review could not be completed just now and will be retried.",
                "rounds": 0,
                "drafter_model": None,
                "reviewer_model": None,
                "same_provider": False,
                "record_url": change_review.record_path(approval_id),
            }

    def get_review_record(self, approval_id: int) -> Dict[str, Any]:
        """One change as the page shows it: the verdict, the rounds, both models and every reason.

        Read only, and limited to the active organisation; another organisation's id is not found."""
        from app.modules.ai_chat.services import change_review

        _, org_id, error = self._active_org_actor()
        if error:
            return error
        approval = AIChatCRUDApproval.query.filter_by(id=approval_id, organization_id=org_id).first()
        if not approval:
            return {"success": False, "code": "NOT_FOUND", "error": f"Approval {approval_id} not found"}
        return {"success": True, "record": change_review.describe(approval)}

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

    def _settle_on_person_approval(self, approval: AIChatCRUDApproval, actor: User) -> Dict[str, Any]:
        """A person pressed Approve (or said "approve it") on a change the review loop settles.

        Their approve is a request to settle it now, not a way round the review: the change goes through
        ``settle_after_review`` (checks, an independent reviewer, then apply or decline) and the answer is
        the change's real outcome. If the review fails, is unreachable or the change is declined, nothing
        is applied."""
        approval_id = approval.id
        self._audit(
            approval,
            event="settle_requested",
            to_status=approval.status.value,
            actor_user_id=actor.id,
            from_status=approval.status.value,
            reason="a person asked for this change to be settled now; the review decides",
        )
        db.session.commit()
        verdict = self.settle_after_review(approval_id, approved_by=actor.id)
        state = verdict.get("state")
        base = {"approval_id": approval_id, "state": state, "verdict": verdict, "record_url": verdict.get("record_url")}
        if state == "applied":
            return {**base, "success": True, "message": verdict.get("message"), "result": {"message": verdict.get("summary")}}
        if state == "declined":
            declined = {**base, "success": False, "code": "DECLINED", "message": verdict.get("message"),
                        "error": verdict.get("message") or "The change was declined."}
            if verdict.get("charter_refused"):
                declined["charter_refused"] = True
            return declined
        if state is None:
            # settle_after_review answered with an error (not found, not allowed): pass it on unchanged.
            return {**verdict, "approval_id": approval_id}
        reason = " ".join(verdict.get("reasons") or []) or "It has not been reviewed yet."
        return {**base, "success": False, "code": "IN_REVIEW", "message": verdict.get("message"),
                "error": "Not applied. %s It is applied only after it passes review." % reason}

    def approve_and_execute(
        self,
        approval_id: int,
        approving_user_id: Optional[int] = None,
        *,
        settled_by_review: bool = False,
        approved_by: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Approve and execute a pending CRUD operation.

        Args:
            approval_id: ID of the approval to execute
            approving_user_id: User ID of the approver (may differ from requester)
            settled_by_review: True only when the server's own review of the change passed
                (``settle_after_review``). The change is then applied as the requester, who
                must still hold the role it needs, and the approval is recorded as the
                system's: the audit rows carry actor type "system" and no approving user.

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
            # A change the server's review passed is applied as the person who asked for it:
            # the system is the approver, so no user id is recorded as approving it.
            # When a person pressed Approve, the review passed first and that person is then recorded as the
            # one who approved it.
            effective_approver_id = approved_by if settled_by_review else actor.id
            audit_actor_type = "system" if settled_by_review and not approved_by else None
            approval = self._load_scoped_approval(approval_id, actor)
            if not approval:
                return {
                    "success": False,
                    "code": "NOT_FOUND",
                    "error": f"Approval {approval_id} not found",
                }
            if settled_by_review and approval.user_id != actor.id and approved_by != actor.id:
                return {
                    "success": False,
                    "code": "FORBIDDEN",
                    "error": "A reviewed change is applied only as the person who asked for it or the person who approved it",
                }

            if settled_by_review:
                # The flag is a claim that the review passed; the record must show it. The latest round on
                # the record has to be a pass, so nothing can apply a change by passing the flag alone.
                rounds = (approval.review_reasons or {}).get("rounds") if isinstance(approval.review_reasons, dict) else None
                if not rounds or not rounds[-1].get("passed"):
                    return {
                        "success": False,
                        "code": "FORBIDDEN",
                        "error": "This change has not passed review, so it cannot be applied",
                    }

            # A person's approve never applies a change the review has not passed. A change the review loop
            # settles (see change_review.in_scope) goes through the same settle path as the assistant's
            # own turn and the sweep: the checks, the independent review, then apply or decline. A record
            # the loop does not settle yet keeps the older rule until its own brief: nobody approves
            # their own request.
            from app.modules.ai_chat.services import change_review

            settled_here = not settled_by_review and change_review.in_scope(approval)
            if not settled_by_review and not settled_here and approval.user_id == actor.id:
                self._audit(
                    approval,
                    event="self_approval_refused",
                    to_status=approval.status.value,
                    actor_user_id=actor.id,
                    from_status=approval.status.value,
                    reason="approver is the same user who requested the operation",
                )
                db.session.commit()
                raise SelfApprovalError(
                    "You cannot approve your own request. Ask another user with write "
                    "permission to review and approve it."
                )

            # Connector changes require an administrator (every connector route carries
            # admin_required) but GENERAL write permission alone was enough to reach this
            # method, so an ordinary user could apply an admin-only connector
            # create/update/delete/sync. Refuse before the entity_type == "connectors"
            # dispatch below ever runs. For a reviewed change the person checked is the
            # requester, since no other approver exists.
            if approval.entity_type == "connectors" and not actor.is_admin():
                self._audit(
                    approval,
                    event="execution_refused",
                    to_status=approval.status.value,
                    actor_user_id=actor.id,
                    from_status=approval.status.value,
                    reason="connector changes require an administrator",
                )
                db.session.commit()
                return {
                    "success": False,
                    "code": "FORBIDDEN",
                    "error": "Connector changes require an administrator.",
                }

            # Check status
            if approval.status != ApprovalStatus.PENDING:
                return {
                    "success": False,
                    "code": "CONFLICT",
                    "error": f"Approval is already {approval.status.value}",
                }

            if settled_here:
                return self._settle_on_person_approval(approval, actor)

            # A record the loop does not settle is applied by the person's Approve alone, so the stop-all-writes
            # switch is read here, before the claim: while writes are paused nothing is applied and the record
            # stays pending, to be approved after they resume.
            if not settled_by_review and change_review._writes_paused(approval.organization_id):
                change_review.record_refused_while_paused(self, approval, actor.id)
                db.session.commit()
                return {
                    "success": False,
                    "code": "IN_REVIEW",
                    "error": "Not applied. Changes by the assistant are paused for this workspace, so this change "
                             "is held. Approve it again after they resume.",
                }

            # A GitHub proposal that cannot land yet (a part whose application is not
            # in the model, a link whose end is not) is refused here, before it is
            # claimed, so it stays pending and can be approved once its prerequisite is.
            if approval.operation_type in ("repository_classification", "repository_relationship", "repository_technology"):
                from app.connectors.github import proposal_blocker

                blocker = proposal_blocker(approval)
                if blocker:
                    return {"success": False, "code": "CONFLICT", "error": blocker}

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
            # The "resumed" row is written only now that nothing blocks the change and it is claimed, and
            # in the same commit as the claim, so a blocked or lost Approve leaves no such row.
            if not settled_by_review:
                change_review.record_resumed_after_pause(self, approval)
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
                actor_type=audit_actor_type,
            )

            # ARCH-022: execution is REFUSED without a resolvable human approver.
            # approving_user_id may be explicitly None from a caller; fall back to
            # self.user_id (already required above), but never proceed with both
            # unset — that is precisely the "approved_by_id: null reached
            # executed" defect.
            if not effective_approver_id and not settled_by_review:
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

            # Connector changes (create/update/delete/sync) execute through
            # the one connector framework, whose change path only accepts an
            # approved proposal for the same organisation (or an explicit
            # organisational delegation, which never reaches this dispatch).
            # The framework's apply path refuses a write without one.
            if approval.entity_type == "connectors":
                from app.services.connector_framework import execute_connector_proposal

                result = execute_connector_proposal(approval)

            elif approval.operation_type in ("repository_classification", "owner_proposal", "repository_relationship", "repository_technology"):
                # The GitHub connector's proposals: an unmatched repository
                # classified for a person to decide (an approved application is
                # created through data_service, a part joins its application),
                # a link the code states between two repositories, and a proposed
                # owner that would replace a person-recorded one. Queued by
                # app/connectors/github.py, executed by the same module.
                from app.connectors.github import execute_github_proposal

                result = execute_github_proposal(approval, data_service)

            elif approval.operation_type in ("vendor_detection", "vendor_architecture"):
                # A vendor product found on the organisation's website, in its domain records or in what
                # a person told the assistant, and the catalogue's architecture for it once it is
                # recorded. Queued and executed by app/services/vendor_detection.py.
                from app.services.vendor_detection import execute_vendor_proposal

                result = execute_vendor_proposal(approval, data_service)

            elif approval.operation_type == "create":
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
                elif approval.entity_type == "data_entity_classification":
                    # Accept a proposed classification label.
                    # The label is stored on the entity and propagated
                    # downstream along DataLineage; conflicts are flagged.
                    from app.modules.architecture.services.data_stewardship_service import (
                        DataStewardshipService,
                    )
                    org_id = approval.organization_id
                    result = DataStewardshipService.accept_classification(
                        approval_id=approval.id,
                        organization_id=org_id,
                        accepted_by=effective_approver_id,
                    )
                elif approval.entity_type == "ai_system_classification":
                    # Accept a proposed AI system risk classification.
                    # The risk class is written onto the element's typed
                    # properties (acm_properties), not custom_properties.
                    from app.modules.enterprise_genome.services.ai_system_classification import (
                        accept_classification,
                    )
                    org_id = approval.organization_id
                    accepted = accept_classification(
                        approval_id=approval.id,
                        organization_id=org_id,
                    )
                    result = {"success": accepted}
                elif approval.entity_type == "ai_system_assessment":
                    # Accept a proposed AI system assessment. The accepted
                    # approval row IS the assessment record — the second
                    # approval type, through the same queue, no new table.
                    from app.modules.enterprise_genome.services.ai_system_classification import (
                        accept_assessment,
                    )
                    org_id = approval.organization_id
                    accepted = accept_assessment(
                        approval_id=approval.id,
                        organization_id=org_id,
                    )
                    result = {"success": accepted}
                elif approval.entity_type == "ai_system_grounding_source":
                    # Accept a proposed grounding data source. Approving
                    # writes the proposed-use property through the property
                    # service; refusing writes nothing.
                    from app.modules.enterprise_genome.services.ai_system_grounding import (
                        accept_grounding_source,
                    )
                    org_id = approval.organization_id
                    accepted = accept_grounding_source(
                        approval_id=approval.id,
                        organization_id=org_id,
                    )
                    result = {"success": accepted}
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

            elif approval.operation_type == "delete":
                # Hard delete — admin-only at execution time (double guard)
                # tenant-scoping-ok: self.user_id is the acting user's own id.
                #
                # D-4 (admin-rbac-active-org continuation): ``actor.is_admin()``
                # is a global ``Permission.ADMINISTER`` flag, independent of
                # which organisation is active in the session
                # (``g.current_org_id``). Since every self-registered user is
                # Administrator of their own organisation, a user who merely
                # accepted a Viewer invitation into another organisation and
                # switched their session into it could hard-delete that
                # organisation's capabilities/applications through this
                # approval-execution path too -- the exact bug
                # ``admin_required``/``org_admin_required`` already fix
                # elsewhere in this PR.
                actor = User.query.filter_by(id=self.user_id).first()
                from flask import g

                from app.middleware.tenant_decorators import is_platform_admin
                from app.services.rbac_service import rbac_service

                active_org_id = getattr(g, "current_org_id", None)
                if not actor or not (
                    is_platform_admin(actor)
                    or rbac_service.is_org_admin(actor, active_org_id)
                ):
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
                    actor_type=audit_actor_type,
                )
                db.session.commit()

                self.logger.info(
                    f"Approved and executed operation {approval_id} by "
                    f"{'the server review' if settled_by_review else 'user %s' % effective_approver_id}"
                )

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
                    actor_type=audit_actor_type,
                )
                db.session.commit()

                return self._execution_failure_response(result, approval_id)

        except (SelfApprovalError, MissingApproverError) as e:
            # Refusal was already audit-logged and committed above the raise; nothing to roll back.
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
            if approval.entity_type == "connectors":
                self._discard_staged_connector_credential(approval)
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

    def _notify_owners_of_new_approval(
        self, approval: AIChatCRUDApproval, requester: User, operation_payload: Dict[str, Any]
    ) -> int:
        """Tell the owners of what a new proposal changes, in-app and by email.

        Recipients are the owners the one ownership store records for the
        proposal's target (never the requester), in the approval's own
        organisation. Written through the one notification writer; the email
        goes through the shared log-or-send helper, so an unconfigured mail
        server logs instead of dropping it. Returns the number of owners told.
        Never raises: the proposal is already committed.
        """
        try:
            from html import escape

            from flask import after_this_request, current_app, has_request_context, request

            from app._bootstrap._digest_emails import _safe_send_email
            from app.services.architecture_change_impact_service import (
                resolve_approval_targets,
                target_owner_rows,
            )
            from app.services.notification_service import NotificationService

            org_id = approval.organization_id
            if org_id is None:
                return 0
            targets = resolve_approval_targets(
                approval.operation_type,
                approval.entity_type,
                approval.entity_id,
                operation_payload if isinstance(operation_payload, dict) else {},
                org_id,
            )
            owner_user_ids = {row.user_id for row in target_owner_rows(targets, org_id)}
            owner_user_ids.discard(requester.id)
            if not owner_user_ids:
                return 0
            recipients = (
                User.query.filter(User.organization_id == org_id, User.id.in_(owner_user_ids))
                .order_by(User.id)
                .all()
            )
            from app.modules.ai_chat.services.change_review import record_path

            inbox_path = record_path(approval.id)
            message = f"A change needs your review: {approval.summary}"
            notifications = [
                (
                    user,
                    NotificationService.create(
                        user.id,
                        message,
                        inbox_path,
                        organization_id=org_id,
                        event_type="approval_requested",
                        entity_type="approval",
                        entity_id=approval.id,
                    ),
                )
                for user in recipients
            ]
            db.session.commit()

            subject = "A change to something you own needs review"
            # The email link must be absolute: a relative href goes nowhere in
            # a mail client. The public base URL, else this request's host;
            # with neither, the link is left out rather than sent relative.
            base = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
            if not base and has_request_context():
                base = request.host_url.rstrip("/")
            link = (
                f'<p><a href="{escape(base + inbox_path)}">Open the approval inbox</a></p>'
                if base
                else ""
            )
            body = (
                "<p>A proposed change to something you own is waiting for review:</p>"
                f"<p>{escape(approval.summary or '')}</p>" + link
            )
            targets = [(user.email, note.id) for user, note in notifications if user.email]
            app_obj = current_app._get_current_object()

            def send_emails():
                # Own application context: this runs after the response has
                # gone, so a slow mail server never holds up the proposal.
                from flask import g

                from app.models.models import Notification

                with app_obj.app_context():
                    g.current_org_id = org_id
                    sent_ids = [
                        note_id
                        for email, note_id in targets
                        if _safe_send_email(app_obj, subject, [email], body)
                    ]
                    if sent_ids:
                        try:
                            Notification.query.filter(
                                Notification.id.in_(sent_ids),
                                Notification.organization_id == org_id,
                            ).update({Notification.email_sent_at: datetime.utcnow()}, synchronize_session=False)
                            db.session.commit()
                        except Exception:
                            db.session.rollback()
                            self.logger.warning("Could not record email delivery for approval %s", approval.id)

            if has_request_context():

                @after_this_request
                def _send_when_closed(response):
                    response.call_on_close(send_emails)
                    return response

            else:
                send_emails()
            return len(notifications)
        except Exception as exc:
            db.session.rollback()
            self.logger.warning("Could not notify owners of approval %s: %s", approval.id, exc)
            return 0

    def _active_org_actor(self):
        """The reviewer (GENERAL) and the ACTIVE organisation, or an error.

        The automatic tenant filter does nothing when no organisation is
        active, so an unset organisation is refused here as not found rather
        than read through an unfiltered query. That the reviewer belongs to the
        active organisation is settled by ``_acting_user`` (it loads the user
        inside the active organisation), as it is for approve and reject.
        """
        from app.middleware.tenant_context import current_org_id

        actor, actor_error = self._acting_user(require_general=True)
        if actor_error:
            return None, None, actor_error
        org_id = current_org_id()
        if org_id is None:
            return None, None, {
                "success": False,
                "code": "NOT_FOUND",
                "error": "No active organization for this request",
            }
        return actor, org_id, None

    def get_approval_impact(self, approval_id: int) -> Dict[str, Any]:
        """What approving one proposal would affect (services, interfaces, owners).

        Computed on demand for one proposal by the change-impact service over
        the canonical impact engine, so the inbox list stays flat in the number
        of proposals. A foreign or unknown id is not found.
        """
        _, org_id, error = self._active_org_actor()
        if error:
            return error
        approval = AIChatCRUDApproval.query.filter_by(id=approval_id, organization_id=org_id).first()
        if not approval:
            return {"success": False, "code": "NOT_FOUND", "error": f"Approval {approval_id} not found"}
        from app.services.architecture_change_impact_service import ArchitectureChangeImpactService

        return {
            "success": True,
            "impact": ArchitectureChangeImpactService().assess_approval_impact(approval, org_id),
        }

    RECENT_DECISIONS_LIMIT = 20

    def get_recent_decisions(self) -> Dict[str, Any]:
        """The organisation's latest approved and rejected proposals, newest first.

        A rejected proposal keeps its reason here, so the decision is still
        visible after a reload. One read, bounded by RECENT_DECISIONS_LIMIT.
        """
        from app.modules.ai_chat.services.change_review import record_path

        _, org_id, error = self._active_org_actor()
        if error:
            return error
        rows = (
            AIChatCRUDApproval.query.filter(
                AIChatCRUDApproval.organization_id == org_id,
                AIChatCRUDApproval.status.in_([ApprovalStatus.APPROVED, ApprovalStatus.REJECTED]),
            )
            .order_by(AIChatCRUDApproval.id.desc())
            .limit(self.RECENT_DECISIONS_LIMIT)
            .all()
        )
        return {
            "success": True,
            "decisions": [
                {
                    "id": row.id,
                    "operation_type": row.operation_type,
                    "entity_type": row.entity_type,
                    "summary": row.summary,
                    "status": row.status.value,
                    "record_url": record_path(row.id),
                    "rejected_reason": row.rejected_reason if row.status == ApprovalStatus.REJECTED else None,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "approved_at": row.approved_at.isoformat() if row.approved_at else None,
                }
                for row in rows
            ],
        }

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

    @staticmethod
    def _approver_queue_query(actor):
        """The same-organisation changes still in review, whoever asked for them (the queue's one filter).

        A change is no longer hidden from the person who asked for it: nobody else has to approve it."""
        return AIChatCRUDApproval.query.filter_by(
            organization_id=actor.organization_id,
            status=ApprovalStatus.PENDING,
        )

    def count_approver_queue(self) -> int:
        """How many items the approvals inbox lists for this person (0 when they may not open it)."""
        actor, actor_error = self._acting_user(require_general=True)
        if actor_error:
            return 0
        return self._approver_queue_query(actor).count()

    def get_approver_queue(self) -> Dict[str, Any]:
        """Same-org changes still in review, including the ones the viewer asked for.

        Overdue-not-expired: includes overdue rows (no expires_at filter — being
        overdue must never make an item disappear from the queue).
        """
        from app.modules.ai_chat.services import change_review

        actor, actor_error = self._acting_user(require_general=True)
        if actor_error:
            return actor_error
        approvals = (
            self._approver_queue_query(actor)
            .order_by(AIChatCRUDApproval.created_at.desc())
            .all()
        )
        paused = change_review._writes_paused(actor.organization_id)
        payloads = {approval.id: json.loads(approval.operation_payload) for approval in approvals}
        requester_ids = {approval.user_id for approval in approvals} | {
            p.get("requested_by") for p in payloads.values() if isinstance(p, dict)
        }
        requester_ids = {i for i in requester_ids if isinstance(i, int)}
        people = {
            user.id: " ".join(part for part in (user.first_name, user.last_name) if part) or user.email
            for user in User.query.filter(
                User.organization_id == actor.organization_id,
                User.id.in_(requester_ids),
            ).all()
        } if requester_ids else {}
        is_admin = actor.is_admin()
        looped, recorded, can_approve, approve_note, pre_loop = {}, {}, {}, {}, {}
        for approval in approvals:
            looped[approval.id] = change_review.in_scope(approval)
            saved = approval.review_reasons if isinstance(approval.review_reasons, dict) else {}
            recorded[approval.id] = bool(saved.get("rounds") or saved.get("checks"))
            # A row recorded before automatic review: no rubric version and no review event, and the sweep
            # leaves it alone, so nothing will check it until a person acts on it.
            pre_loop[approval.id] = looped[approval.id] and change_review.is_before_the_loop(approval)
            connector = approval.entity_type == "connectors"
            own_outside_loop = not looped[approval.id] and approval.user_id == actor.id
            can_approve[approval.id] = not paused and not (connector and not is_admin) and not own_outside_loop
            approve_note[approval.id] = None
            if can_approve[approval.id] or (paused and looped[approval.id] and not pre_loop[approval.id]):
                continue
            if paused:
                approve_note[approval.id] = (
                    "Changes by the assistant are paused for this workspace, so this cannot be approved until "
                    "they resume.")
            elif connector and not is_admin:
                approve_note[approval.id] = "Connector changes are approved by an administrator."
            else:
                approve_note[approval.id] = (
                    "You asked for this, so another %s approves it." % (
                        "administrator" if connector else "person with write permission"))
        words = {
            approval.id: card_words(
                approval, payloads[approval.id] if isinstance(payloads[approval.id], dict) else {}, people
            )
            for approval in approvals
        }
        rows = [
                {
                    "id": approval.id,
                    "operation_type": approval.operation_type,
                    "entity_type": approval.entity_type,
                    "operation_label": words[approval.id]["operation"],
                    "entity_label": words[approval.id]["entity"],
                    "source_label": words[approval.id]["source"],
                    "summary": approval.summary,
                    # This is the exact decoded JSON supplied to the executor;
                    # the modal renders it with x-text, never HTML insertion.
                    "arguments": payloads[approval.id],
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
                    # The loop decides this change by itself (checked, reviewed, then applied or declined). That
                    # is said only of a change that has a recorded check or review and is not held.
                    "settled_by_review": looped[approval.id] and recorded[approval.id] and not paused,
                    # A change in the loop while writes are paused waits for them to resume.
                    "held_by_pause": paused and looped[approval.id],
                    # Approve is offered only where it acts for this person: not while writes are paused, not on
                    # a connector record unless they are an administrator, and not on their own record outside
                    # the loop.
                    "can_approve": can_approve[approval.id],
                    "approve_note": approve_note[approval.id],
                    "requester": {
                        "id": approval.user_id,
                        "display_name": words[approval.id]["requester"],
                    },
                }
                for approval in approvals
        ]
        # Two facts are carried only by the rows they are true of, so every other row keeps its shape: a row
        # recorded before automatic review (nothing has checked it), and a held row with a check on its record.
        for row in rows:
            if pre_loop[row["id"]]:
                row["pre_loop"] = True
            elif row["held_by_pause"] and recorded[row["id"]]:
                row["has_check_record"] = True
        return {"success": True, "approvals": rows}

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

    def recorded_changes(self, chat_session_ids, turn_id) -> List[Dict[str, Any]]:
        """The changes one assistant turn, the one that failed, recorded for the signed-in person.

        Read from the one approval record, filtered by the person's organisation, the person, the
        conversation and the turn (``agent_turn_id``). The turn's id is only a filter: it narrows what the
        person's own rows already allow and names no change itself. With no turn id nothing is returned;
        the latest turn is never guessed. ``chat_session_ids`` is the conversation's id, or
        every id the conversation's changes may carry (the first turn of a new chat is recorded under the
        person's own stable id, before the conversation has one). Each is
        ``{"approval_id", "tool", "arguments", "summary", "applied"}``; ``applied`` is False while the
        change is recorded and waiting. A rejected or expired row is not a recorded change. A retry reads
        this to leave those changes alone and to say what state each is in.
        """
        actor, actor_error = self._acting_user()
        ids = [i for i in ([chat_session_ids] if isinstance(chat_session_ids, str) else chat_session_ids or []) if i]
        if actor_error or not ids or not isinstance(turn_id, str) or not turn_id.strip():
            return []
        rows = (
            AIChatCRUDApproval.query.filter(
                AIChatCRUDApproval.user_id == actor.id,
                AIChatCRUDApproval.agent_turn_id == turn_id.strip()[:64],
                AIChatCRUDApproval.organization_id == actor.organization_id,
                AIChatCRUDApproval.chat_session_id.in_(ids),
                AIChatCRUDApproval.operation_type == "tool_use",
                AIChatCRUDApproval.status.in_([ApprovalStatus.PENDING, ApprovalStatus.APPROVED]),
            )
            .order_by(AIChatCRUDApproval.created_at.desc(), AIChatCRUDApproval.id.desc())
            .all()
        )
        changes = []
        for row in reversed(rows):
            try:
                arguments = json.loads(row.operation_payload)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(arguments, dict):
                continue
            changes.append({
                "approval_id": row.id,
                "tool": row.entity_type,
                "arguments": arguments,
                "summary": row.summary,
                "applied": row.status == ApprovalStatus.APPROVED,
            })
        return changes

    @staticmethod
    def same_change(tool: str, arguments: Dict[str, Any], recorded_tool: str, recorded_arguments: Dict[str, Any]) -> bool:
        """Whether a change a retry asks for is one already recorded: the same tool on the same target record
        (its ``*_id`` fields), changing the same thing. A reworded name still matches (one name's words are
        inside the other's); the description, summary and rationale wording never matters. Where there is no
        name, the same fields changed to the same values match."""
        if tool != recorded_tool:
            return False

        def target(args):
            return {k: v for k, v in args.items() if k == "id" or k.endswith("_id")}

        if target(arguments) != target(recorded_arguments):
            return False
        compared = False
        for field in ("name", "title"):
            a, b = arguments.get(field), recorded_arguments.get(field)
            if isinstance(a, str) and isinstance(b, str):
                compared = True
                words_a, words_b = set(normalize_name(a).split()), set(normalize_name(b).split())
                if not words_a or not words_b or not (words_a <= words_b or words_b <= words_a):
                    return False
        if compared:
            return True
        ignored = ("description", "summary", "original_command", "rationale")
        fields = lambda args: {  # noqa: E731
            k: json.dumps(v, sort_keys=True, default=str)
            for k, v in args.items() if k not in ignored and k not in target(args)
        }
        return fields(arguments) == fields(recorded_arguments)

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

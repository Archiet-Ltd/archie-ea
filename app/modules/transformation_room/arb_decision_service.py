"""Replay-safe terminal decisions for typed ARB review cycles."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import json

from sqlalchemy import or_, select, text

from app.models.architecture_review_board import (
    ARBBoardMember,
    ARBReviewCycle,
    ARBReviewItem,
)
from app.models.arb_decision_event import ARBCondition, ARBDecisionEvent
from app.models.transformation_decision import DecisionBriefVersion
from app.models.transformation_programme import (
    ProgrammeRoleAssignment,
    ProgrammeWorkstream,
)
from app.models.user import User
from app.modules.transformation_room.arb_submission_service import (
    TypedARBSubmissionService,
)
from app.modules.transformation_room.command_service import CommandService
from app.modules.transformation_room.domain import (
    ActorContext,
    CommandConflict,
    DomainMutationResult,
    NotAuthorised,
    NotFound,
)


DECISION_ROLES = frozenset(
    {
        "chief_architect",
        "cto",
        "enterprise_architect",
        "platform_admin",
        "organization_admin",
        "administrator",
        "decision_authority",
        "arb_member",
    }
)
_TYPED_COLUMNS = (
    "subject_type",
    "subject_id",
    "decision_brief_id",
    "solution_id",
    "architecture_model_id",
    "adr_id",
    "decision_brief_version_id",
    "solution_evidence_snapshot_id",
    "subject_evidence_snapshot_id",
)


def _decision_brief_service():
    from app.modules.transformation_room.decision_service import DecisionBriefService

    return DecisionBriefService


def may_decide_review(session, user, review, *, actor=None, for_update=False):
    """The one decision-authority rule, for typed and untyped reviews alike.

    A user decides when they are an organisation or platform administrator,
    hold a deciding role, or hold a voting seat on the review's board.
    Separation of duties (a submitter never decides their own submission) is
    applied by the callers.
    """
    if (
        user.is_org_admin
        or user.is_platform_admin
        or user.enterprise_role in DECISION_ROLES
    ):
        return True
    if actor is None:
        actor = ActorContext(
            user_id=user.id,
            organization_id=user.organization_id,
            roles=frozenset(),
            request_id="",
        )
    return TypedARBDecisionService._has_board_authority(
        session, actor, review, for_update=for_update
    )


class TypedARBDecisionService:
    OPERATION = "arb.decision.record"
    DECISION_EVENT_TYPE = "decided"
    TERMINAL_OUTCOMES = frozenset(
        {
            "approved",
            "approved_with_conditions",
            "rejected",
            "returned_for_evidence",
            "returned_for_options",
        }
    )
    OPEN_STATUSES = frozenset(
        {
            "submitted",
            "under_review",
            "pending_information",
            "pending_info",
            "pending",
        }
    )
    MAX_CONDITIONS = 50
    MAX_CONDITIONS_JSON_BYTES = 64 * 1024
    MAX_RATIONALE_CHARS = 10_000

    @staticmethod
    def _canonical_conditions(conditions):
        def normalized(value, field, limit, *, optional=False):
            if value is None and optional:
                return None
            value = str(value or "")
            if any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise ValueError(f"condition {field} contains control characters")
            value = " ".join(value.split())
            if not value:
                if optional:
                    return None
                raise ValueError(f"condition {field} is required")
            if len(value) > limit:
                raise ValueError(f"condition {field} exceeds {limit} characters")
            return value

        supplied = list(conditions or ())
        if len(supplied) > TypedARBDecisionService.MAX_CONDITIONS:
            raise ValueError("at most 50 conditions are allowed")
        canonical = []
        seen = set()
        for ordinal, raw in enumerate(supplied, start=1):
            if not isinstance(raw, dict):
                raise ValueError("conditions must be objects")
            number = normalized(
                raw.get("condition_number") or raw.get("code") or f"COND-{ordinal}",
                "condition_number",
                80,
            )
            description = normalized(
                raw.get("description") or raw.get("text"),
                "description",
                4000,
            )
            if number in seen:
                raise ValueError("condition numbers must be unique after normalization")
            seen.add(number)
            due = raw.get("due_date")
            if isinstance(due, datetime):
                due = due.date()
            elif isinstance(due, str):
                try:
                    due = date.fromisoformat(due)
                except ValueError as exc:
                    raise ValueError("condition due_date must be ISO YYYY-MM-DD") from exc
            elif due is not None and not isinstance(due, date):
                raise ValueError("condition due_date must be ISO YYYY-MM-DD")
            canonical.append(
                {
                    "condition_number": number,
                    "description": description,
                    "category": normalized(
                        raw.get("category"), "category", 80, optional=True
                    ),
                    "due_date": due.isoformat() if due else None,
                    "blocks_execution": True,
                }
            )
        canonical.sort(key=lambda condition: condition["condition_number"])
        encoded = json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        if len(encoded) > TypedARBDecisionService.MAX_CONDITIONS_JSON_BYTES:
            raise ValueError("canonical conditions exceed 64 KiB")
        return canonical

    @classmethod
    def decide(
        cls,
        *,
        actor,
        command_key,
        cycle_id,
        outcome,
        rationale,
        conditions=None,
    ):
        if not isinstance(actor, ActorContext):
            raise TypeError("actor must be ActorContext")
        if not isinstance(command_key, str) or not command_key.strip():
            raise ValueError("command_key is required")
        if isinstance(cycle_id, bool) or not isinstance(cycle_id, int) or cycle_id <= 0:
            raise ValueError("cycle_id must be a positive integer")
        if outcome not in cls.TERMINAL_OUTCOMES:
            raise ValueError("unsupported ARB decision outcome")
        rationale = rationale.strip() if isinstance(rationale, str) else ""
        if not rationale:
            raise ValueError("rationale is required")
        if len(rationale) > cls.MAX_RATIONALE_CHARS:
            raise ValueError("rationale exceeds 10000 characters")
        if any(ord(character) < 32 or ord(character) == 127 for character in rationale):
            raise ValueError("rationale contains control characters")
        supplied_conditions = cls._canonical_conditions(conditions)
        if outcome == "approved_with_conditions":
            if not supplied_conditions:
                raise ValueError("approved_with_conditions requires conditions")
        elif supplied_conditions:
            raise ValueError("conditions require approved_with_conditions")

        natural_key = f"arb-decision:{actor.organization_id}:{cycle_id}"
        payload = {
            "cycle_id": cycle_id,
            "outcome": outcome,
            "rationale": rationale,
            "conditions": supplied_conditions,
        }

        def authorize(session, runtime_actor, operation, supplied_key):
            if operation != cls.OPERATION or supplied_key != natural_key:
                raise NotAuthorised("arb_decision_command_mismatch")
            if runtime_actor.organization_id != actor.organization_id:
                raise NotAuthorised("arb_decision_actor_mismatch")
            cls.authorise_decision(session, runtime_actor, cycle_id)

        return CommandService.execute(
            actor=actor,
            operation=cls.OPERATION,
            idempotency_key=command_key.strip(),
            payload=payload,
            natural_key=natural_key,
            authorizer=authorize,
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._decide_locked(
                session=session,
                actor=actor,
                cycle_id=cycle_id,
                outcome=outcome,
                rationale=rationale,
                conditions=supplied_conditions,
                claim=claim,
            ),
        )

    @classmethod
    def authorise_decision(cls, session, actor, cycle_id, *, for_update=False):
        user_statement = select(User).where(
                User.id == actor.user_id,
                User.organization_id == actor.organization_id,
            )
        if for_update:
            user_statement = user_statement.with_for_update()
        user = session.execute(user_statement).scalar_one_or_none()
        if user is None:
            raise NotAuthorised("arb_decision_not_authorised")
        cycle, review = cls._load_cycle_and_review(
            session, actor, cycle_id, for_update=False
        )
        if review.submitter_id == user.id:
            raise NotAuthorised("arb_decision_separation_of_duties")
        cls._assert_cycle_review_projection_equal(cycle, review)
        if cycle.subject_type == "decision_brief":
            if not cls._has_decision_brief_authority(
                session, actor, cycle, for_update=for_update
            ):
                raise NotAuthorised("arb_decision_not_authorised")
            return
        if not may_decide_review(
            session, user, review, actor=actor, for_update=for_update
        ):
            raise NotAuthorised("arb_decision_not_authorised")

    @staticmethod
    def _has_board_authority(session, actor, review, *, for_update):
        if review.arb_session_id is None:
            return False
        statement = select(ARBBoardMember).where(
            ARBBoardMember.organization_id == actor.organization_id,
            ARBBoardMember.arb_session_id == review.arb_session_id,
            ARBBoardMember.user_id == actor.user_id,
            ARBBoardMember.voting_member.is_(True),
            ARBBoardMember.attendance_status.notin_(("declined", "absent")),
        )
        if for_update:
            statement = statement.execution_options(
                populate_existing=True
            ).with_for_update()
        return session.execute(statement).scalar_one_or_none() is not None

    @staticmethod
    def _has_decision_brief_authority(session, actor, cycle, *, for_update):
        version_statement = select(DecisionBriefVersion).where(
            DecisionBriefVersion.id == cycle.decision_brief_version_id,
            DecisionBriefVersion.brief_id == cycle.decision_brief_id,
            DecisionBriefVersion.organization_id == actor.organization_id,
        )
        if for_update:
            version_statement = version_statement.execution_options(
                populate_existing=True
            ).with_for_update()
        version = session.execute(version_statement).scalar_one_or_none()
        if version is None:
            return False
        workstream_statement = select(ProgrammeWorkstream).where(
            ProgrammeWorkstream.organization_id == actor.organization_id,
            ProgrammeWorkstream.id == version.workstream_id,
        )
        if for_update:
            workstream_statement = workstream_statement.execution_options(
                populate_existing=True
            ).with_for_update()
        workstream = session.execute(workstream_statement).scalar_one_or_none()
        if workstream is None:
            return False
        assignment_statement = (
            select(ProgrammeRoleAssignment)
            .where(
                ProgrammeRoleAssignment.organization_id == actor.organization_id,
                ProgrammeRoleAssignment.programme_id == workstream.programme_id,
                or_(
                    ProgrammeRoleAssignment.workstream_id.is_(None),
                    ProgrammeRoleAssignment.workstream_id == workstream.id,
                ),
                ProgrammeRoleAssignment.user_id == actor.user_id,
                ProgrammeRoleAssignment.role == "decision_authority",
                ProgrammeRoleAssignment.effective_from <= date.today(),
                or_(
                    ProgrammeRoleAssignment.effective_to.is_(None),
                    ProgrammeRoleAssignment.effective_to >= date.today(),
                ),
            )
            .order_by(ProgrammeRoleAssignment.id)
        )
        if for_update:
            assignment_statement = assignment_statement.execution_options(
                populate_existing=True
            ).with_for_update()
        assigned = session.execute(assignment_statement).first() is not None
        named_or_assigned = version.decision_authority_id == actor.user_id or assigned
        return named_or_assigned and _decision_brief_service()._user_has_decision_authority(
            session,
            actor.organization_id,
            workstream.programme_id,
            workstream.id,
            actor.user_id,
            lock=for_update,
        )

    @classmethod
    def _load_cycle_and_review(cls, session, actor, cycle_id, *, for_update):
        cycle_statement = select(ARBReviewCycle).where(
            ARBReviewCycle.id == cycle_id,
            ARBReviewCycle.organization_id == actor.organization_id,
        )
        review_statement = select(ARBReviewItem).where(
            ARBReviewItem.review_cycle_id == cycle_id,
            ARBReviewItem.organization_id == actor.organization_id,
        )
        if for_update:
            cycle_statement = cycle_statement.with_for_update()
            review_statement = review_statement.with_for_update()
        cycle = session.execute(cycle_statement).scalar_one_or_none()
        review = session.execute(review_statement).scalar_one_or_none()
        if cycle is None or review is None:
            raise NotFound("arb_review_cycle_not_found")
        return cycle, review

    @classmethod
    def _load_cycle_and_review_for_update(cls, session, actor, cycle_id):
        return cls._load_cycle_and_review(
            session, actor, cycle_id, for_update=True
        )

    @staticmethod
    def _assert_cycle_review_projection_equal(cycle, review):
        if review.review_cycle_id != cycle.id or any(
            getattr(review, name) != getattr(cycle, name) for name in _TYPED_COLUMNS
        ):
            raise CommandConflict("arb_cycle_review_projection_mismatch")

    @classmethod
    def _decide_locked(
        cls,
        *,
        session,
        actor,
        cycle_id,
        outcome,
        rationale,
        conditions,
        claim,
    ):
        cls._lock_subject_decision(session, actor, cycle_id)
        cycle, review = cls._load_cycle_and_review_for_update(
            session, actor, cycle_id
        )
        cls._assert_cycle_review_projection_equal(cycle, review)
        if cycle.status == "historical_unverified":
            raise CommandConflict("historical_unverified_cycle_not_decidable")
        if cycle.status not in cls.OPEN_STATUSES or cycle.closed_at is not None:
            raise CommandConflict("arb_cycle_already_terminal")
        if review.status != cycle.status or review.decision is not None:
            raise CommandConflict("arb_cycle_review_projection_mismatch")
        cls.authorise_decision(session, actor, cycle_id, for_update=True)

        now = CommandService._database_now(session)
        from_state = cycle.status
        supplied_json = deepcopy(conditions)
        typed_values = {name: getattr(cycle, name) for name in _TYPED_COLUMNS}
        decision_event = ARBDecisionEvent(
            organization_id=actor.organization_id,
            review_cycle_id=cycle.id,
            review_item_id=review.id,
            outcome=outcome,
            from_state=from_state,
            to_state=outcome,
            rationale=rationale,
            conditions_json=supplied_json,
            **typed_values,
            actor_id=actor.user_id,
            command_receipt_id=claim.receipt_id,
            command_generation=claim.generation,
        )
        session.add(decision_event)
        session.flush()
        condition_rows = []
        for condition in conditions:
            condition_row = ARBCondition(
                    organization_id=actor.organization_id,
                    decision_event_id=decision_event.id,
                    review_cycle_id=cycle.id,
                    review_item_id=review.id,
                    condition_number=condition["condition_number"],
                    description=condition["description"],
                    category=condition.get("category"),
                    due_date=date.fromisoformat(condition["due_date"])
                    if condition.get("due_date")
                    else None,
                    blocks_execution=True,
            )
            session.add(condition_row)
            condition_rows.append(condition_row)
        session.flush()
        cycle.status = outcome
        cycle.terminal_outcome = outcome
        cycle.closed_at = now
        review.status = outcome
        review.decision = outcome
        review.decision_rationale = rationale
        review.conditions = supplied_json
        review.decision_date = now.astimezone(timezone.utc).replace(tzinfo=None)
        review.review_completed_at = now.astimezone(timezone.utc).replace(tzinfo=None)
        review.decided_by_id = actor.user_id
        session.flush()
        object_ids = {
            "review_cycle_id": cycle.id,
            "review_item_id": review.id,
            "decision_event_id": decision_event.id,
            "condition_ids": [row.id for row in condition_rows],
        }
        return DomainMutationResult(
            object_ids=object_ids,
            response={
                **object_ids,
                "status": outcome,
                "outcome": outcome,
                "conditions": supplied_json,
            },
            outbox_events=(),
        )

    @classmethod
    def _lock_subject_decision(cls, session, actor, cycle_id):
        identity = session.execute(
            select(
                ARBReviewCycle.subject_type,
                ARBReviewCycle.subject_id,
            ).where(
                ARBReviewCycle.id == cycle_id,
                ARBReviewCycle.organization_id == actor.organization_id,
            )
        ).one_or_none()
        if identity is None:
            raise NotFound("arb_review_cycle_not_found")
        session.execute(
            text(
                "SELECT pg_advisory_xact_lock(:lock_key) "
                "/* tenancy-ok: deterministic key includes organization_id */"
            ),
            {
                "lock_key": TypedARBSubmissionService._subject_lock_key(
                    actor.organization_id, identity.subject_type, identity.subject_id
                )
            },
        )


__all__ = ["TypedARBDecisionService"]

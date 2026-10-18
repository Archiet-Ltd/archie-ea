"""Replay-safe submission of typed governed subjects to the ARB."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import timezone
import hashlib
import uuid
from typing import Any, Mapping

from flask import has_app_context
from sqlalchemy import select, text

from app import db
from app.models.architecture_review_board import ARBReviewCycle, ARBReviewItem
from app.models.arb_submission_event import ARBSubmissionEvent
from app.models.transformation_execution import (
    CommandIdempotencyRecord,
    CommandMaterialisation,
    OperationResult,
)
from app.middleware.tenant_decorators import is_platform_admin
from app.models.user import User
from app.modules.transformation_room.command_service import CommandService
from app.modules.transformation_room.domain import (
    ActorContext,
    BlockedByEvidence,
    CommandConflict,
    DomainMutationResult,
    GovernedSubject,
    NotAuthorised,
    NotFound,
)


_SUBJECT_COLUMNS = {
    "decision_brief": "decision_brief_id",
    "solution": "solution_id",
    "architecture_model": "architecture_model_id",
    "adr": "adr_id",
}
_EVIDENCE_COLUMNS = {
    "decision_brief": ("decision_brief_version_id", "decision_brief_version"),
    "solution": ("solution_evidence_snapshot_id", "solution_evidence_snapshot"),
    "architecture_model": (
        "subject_evidence_snapshot_id",
        "arb_subject_evidence_snapshot",
    ),
    "adr": ("subject_evidence_snapshot_id", "arb_subject_evidence_snapshot"),
}
# Who may SUBMIT a governed subject (ADR, Architecture Model, Solution,
# Decision Brief) into an ARB review cycle.
#
# Deliberately NOT the same set as _EVIDENCE_CAPTURE_ROLES in
# arb_condition_evidence_service: authoring a submission is a design-authorship
# authority, so every domain-specialist architect holds it, as does
# a real platform admin (the `is_platform_admin` predicate, never the bare
# `platform_admin` role value, which a user can save for themselves and which is
# the column default). Board membership alone (`arb_member`) does not — a board
# member reviews and attests rather than submits.
#
# Membership is governance policy, not a refactor target: changing it changes
# who can act on real governed records. tests/test_typed_arb_submission_routes
# pins it so any edit is deliberate and visible in review.
_SUBJECT_SUBMIT_ROLES = frozenset(
    {
        "chief_architect",
        "enterprise_architect",
        "solution_architect",
        "application_architect",
        "business_architect",
        "data_architect",
        "technology_architect",
        "security_architect",
        "architect",
    }
)


def get_arb_subject_adapter(subject_type):
    """Import lazily so this boundary does not change the legacy model load order."""
    from app.modules.transformation_room.arb_adapters import (
        get_arb_subject_adapter as resolve,
    )

    return resolve(subject_type)


def _required_command_key(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("command_key is required")
    return value.strip()


def _required_subject_id(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("subject_id must be a positive integer")
    return value


@contextmanager
def _adapter_session(session):
    """Make legacy adapter calls participate in CommandService's transaction.

    Subject adapters pre-date the explicit-session command boundary and use the
    Flask-SQLAlchemy scoped session. Temporarily pointing that registry at the
    fenced command session keeps their locks and snapshot insert in the same
    atomic transaction without opening a second persistence authority.
    """

    if not has_app_context():
        # Unit-level command-boundary tests supply an inert session and adapters
        # that do not touch Flask-SQLAlchemy. Production commands require an app
        # context already because CommandService reads current_app configuration.
        yield
        return
    registry = db.session.registry
    had_previous = registry.has()
    previous = registry() if had_previous else None
    registry.set(session)
    try:
        yield
    finally:
        if had_previous:
            registry.set(previous)
        else:
            registry.clear()


class TypedARBSubmissionService:
    """The sole command boundary for new typed ARB review cycles."""

    OPERATION = "arb.submit"

    @classmethod
    def submit(
        cls,
        *,
        actor: ActorContext,
        command_key: str,
        subject_type: str,
        subject_id: int,
        assertions: Mapping[str, Any] | None = None,
    ):
        if not isinstance(actor, ActorContext):
            raise TypeError("actor must be ActorContext")
        command_key = _required_command_key(command_key)
        subject_id = _required_subject_id(subject_id)
        adapter = get_arb_subject_adapter(subject_type)
        supplied_assertions = deepcopy(dict(assertions or {}))

        # Fail closed before creating a durable command receipt for a foreign or
        # malformed identity. The authorizer repeats this check for every replay.
        subject = adapter.load(actor, subject_id)
        cls._validate_loaded_subject(actor, subject, subject_type, subject_id)
        cls.authorise_submit(db.session, actor, subject_type, subject_id)
        cls._authorise_submission_context(
            db.session,
            actor,
            subject_type,
            subject_id,
            supplied_assertions,
        )
        existing_receipt = cls._existing_command_receipt(
            db.session, actor, command_key
        )
        natural_key_prefix = (
            f"arb-submission:{actor.organization_id}:{subject_type}:{subject_id}"
            ":after:"
        )
        if existing_receipt is not None:
            natural_key = existing_receipt.natural_key
            if not natural_key.startswith(natural_key_prefix):
                raise CommandConflict("arb_submission_command_mismatch")
            claimed_anchor = natural_key.removeprefix(natural_key_prefix)
        else:
            claimed_anchor = cls._submission_anchor(
                db.session,
                actor.organization_id,
                subject_type,
                subject_id,
                subject.logical_version_id,
            )
            natural_key = f"{natural_key_prefix}{claimed_anchor}"
        payload = {
            "subject_type": subject_type,
            "subject_id": subject_id,
            "assertions": supplied_assertions,
        }

        def authorize(session, runtime_actor, operation, supplied_key):
            if operation != cls.OPERATION or supplied_key != natural_key:
                raise NotAuthorised("arb_submission_command_mismatch")
            if runtime_actor.organization_id != actor.organization_id:
                raise NotAuthorised("arb_submission_actor_mismatch")
            cls._authorise_submission_context(
                session,
                runtime_actor,
                subject_type,
                subject_id,
                supplied_assertions,
            )
            cls.authorise_submit(
                session, runtime_actor, subject_type, subject_id
            )
            with _adapter_session(session):
                current = adapter.load(runtime_actor, subject_id)
            cls._validate_loaded_subject(
                runtime_actor, current, subject_type, subject_id
            )
            current_anchor = cls._submission_anchor(
                session,
                runtime_actor.organization_id,
                subject_type,
                subject_id,
                current.logical_version_id,
            )
            if str(current_anchor) != str(claimed_anchor):
                if existing_receipt is None or not cls._receipt_proves_submission(
                    session,
                    existing_receipt.id,
                    runtime_actor,
                    subject_type,
                    subject_id,
                    natural_key,
                ):
                    raise CommandConflict("arb_submission_anchor_changed")

        return CommandService.execute(
            actor=actor,
            operation=cls.OPERATION,
            idempotency_key=command_key,
            payload=payload,
            natural_key=natural_key,
            authorizer=authorize,
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._submit_locked(
                session=session,
                actor=actor,
                subject=subject,
                adapter=adapter,
                assertions=supplied_assertions,
                claim=claim,
                claimed_anchor=claimed_anchor,
            ),
        )

    @classmethod
    def _authorise_submission_context(
        cls,
        session,
        actor,
        subject_type,
        subject_id,
        assertions,
    ):
        """Revalidate Solution workspace identity before any replay result."""
        if subject_type != "solution":
            return
        workspace_id = assertions.get("workspace_id")
        if workspace_id is None:
            return
        if type(workspace_id) is not int or workspace_id <= 0:
            raise NotFound("arb_submission_workspace_not_found")

        from app.models.solution_architect_models import SolutionAnalysisSession
        from app.models.solution_models import Solution

        solution = session.execute(
            select(Solution).where(
                Solution.id == subject_id,
                Solution.organization_id == actor.organization_id,
            )
        ).scalar_one_or_none()
        workspace = session.execute(
            select(SolutionAnalysisSession).where(
                SolutionAnalysisSession.id == workspace_id,
                SolutionAnalysisSession.organization_id == actor.organization_id,
            )
        ).scalar_one_or_none()
        if solution is None or workspace is None:
            raise NotFound("arb_submission_workspace_not_found")
        metadata_solution_id = (workspace.custom_metadata or {}).get("solution_id")
        if (
            workspace.created_by_id != actor.user_id
            or (
                solution.analysis_session_id != workspace.id
                and metadata_solution_id != solution.id
            )
        ):
            raise NotFound("arb_submission_workspace_not_found")

    @classmethod
    def authorise_submit(cls, session, actor, subject_type, subject_id):
        """Authorize from current server rows, never caller-supplied role claims."""
        user = session.execute(
            select(User).where(
                User.id == actor.user_id,
                User.organization_id == actor.organization_id,
            )
        ).scalar_one_or_none()
        if user is None:
            raise NotAuthorised("arb_submission_not_authorised")
        if (
            user.is_org_admin
            or is_platform_admin(user)
            or user.enterprise_role in _SUBJECT_SUBMIT_ROLES
        ):
            return
        if subject_type == "solution":
            from app.models.solution_models import Solution
            from app.modules.solutions_strategic.v2.services.arb_submission_service import (
                ARBSubmissionService,
            )

            solution = session.execute(
                select(Solution).where(
                    Solution.id == subject_id,
                    Solution.organization_id == actor.organization_id,
                )
            ).scalar_one_or_none()
            if solution is not None and ARBSubmissionService._actor_can_access(
                user, solution
            ):
                return
        raise NotAuthorised("arb_submission_not_authorised")

    @classmethod
    def submit_legacy_solution(
        cls,
        *,
        actor: ActorContext,
        command_key: str,
        solution_id: int,
        workspace_id: int | None = None,
        assertions: Mapping[str, Any] | None = None,
    ):
        supplied = deepcopy(dict(assertions or {}))
        if workspace_id is not None:
            supplied["workspace_id"] = workspace_id
        return cls.submit(
            actor=actor,
            command_key=command_key,
            subject_type="solution",
            subject_id=solution_id,
            assertions=supplied,
        )

    @classmethod
    def _submit_locked(
        cls,
        *,
        session,
        actor,
        subject,
        adapter,
        assertions,
        claim,
        claimed_anchor,
    ):
        cls._lock_subject_submission(session, actor, subject)
        cls._authorise_submission_context(
            session,
            actor,
            subject.subject_type,
            subject.subject_id,
            assertions,
        )
        # A natural-key contender may have claimed while the first command was
        # still running. Re-check after the subject lock, when the winner's
        # immutable materialisation is visible, before evaluating or snapshotting.
        materialised = CommandService.resolve_materialisation(
            session,
            actor=actor,
            operation=cls.OPERATION,
            claim=claim,
        )
        if materialised is not None:
            return materialised
        current_anchor = cls._submission_anchor(
            session,
            actor.organization_id,
            subject.subject_type,
            subject.subject_id,
            subject.logical_version_id,
        )
        if str(current_anchor) != str(claimed_anchor):
            raise CommandConflict("arb_submission_anchor_changed")
        with _adapter_session(session):
            # Adapter.snapshot performs the subject-specific FOR UPDATE and then
            # re-evaluates. The initial evaluation supplies its comparison input.
            readiness = adapter.evaluate(actor, subject, assertions)
            if not readiness.ready:
                raise BlockedByEvidence(
                    "arb_subject_not_ready",
                    reason_codes=list(readiness.reason_codes),
                    missing_evidence=list(readiness.missing_evidence),
                )
            review_item_id = (
                cls._reserve_review_item_id(session)
                if subject.subject_type == "solution"
                else None
            )
            pinned = adapter.snapshot(
                actor,
                subject,
                readiness,
                review_item_id=review_item_id,
            )
            return cls._insert_submission_graph(
                session=session,
                actor=actor,
                subject=subject,
                adapter=adapter,
                pinned_evidence=pinned,
                review_item_id=review_item_id,
                claim=claim,
            )

    @staticmethod
    def _reserve_review_item_id(session):
        return session.scalar(
            text(
                "SELECT nextval(pg_get_serial_sequence("
                "'arb_review_items', 'id'))"
            )
        )

    @staticmethod
    def _subject_lock_key(organization_id, subject_type, subject_id):
        identity = f"{organization_id}:{subject_type}:{subject_id}".encode("utf-8")
        return int.from_bytes(
            hashlib.sha256(identity).digest()[:8], byteorder="big", signed=True
        )

    @classmethod
    def _submission_anchor(
        cls,
        session,
        organization_id,
        subject_type,
        subject_id,
        logical_version_id=None,
    ):
        latest = session.execute(
            select(ARBReviewCycle)
            .where(
                ARBReviewCycle.organization_id == organization_id,
                ARBReviewCycle.subject_type == subject_type,
                ARBReviewCycle.subject_id == subject_id,
            )
            .order_by(
                ARBReviewCycle.cycle_number.desc(),
                ARBReviewCycle.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()
        if latest is None:
            return "root"
        if latest.closed_at is None:
            return latest.predecessor_cycle_id or "root"
        if (
            subject_type == "decision_brief"
            and latest.decision_brief_version_id == logical_version_id
        ):
            return latest.predecessor_cycle_id or "root"
        return latest.id

    @classmethod
    def _existing_command_receipt(cls, session, actor, command_key):
        return session.execute(
            select(CommandIdempotencyRecord).where(
                CommandIdempotencyRecord.organization_id == actor.organization_id,
                CommandIdempotencyRecord.actor_id == actor.user_id,
                CommandIdempotencyRecord.operation == cls.OPERATION,
                CommandIdempotencyRecord.idempotency_key == command_key,
            )
        ).scalar_one_or_none()

    @classmethod
    def _receipt_proves_submission(
        cls,
        session,
        receipt_id,
        actor,
        subject_type,
        subject_id,
        natural_key,
    ):
        receipt = session.execute(
            select(CommandIdempotencyRecord).where(
                CommandIdempotencyRecord.id == receipt_id,
                CommandIdempotencyRecord.organization_id == actor.organization_id,
                CommandIdempotencyRecord.actor_id == actor.user_id,
                CommandIdempotencyRecord.operation == cls.OPERATION,
                CommandIdempotencyRecord.natural_key == natural_key,
                CommandIdempotencyRecord.status == "succeeded",
                CommandIdempotencyRecord.completed_at.is_not(None),
                CommandIdempotencyRecord.operation_result_id.is_not(None),
            )
        ).scalar_one_or_none()
        if receipt is None:
            return False
        result = session.execute(
            select(OperationResult).where(
                OperationResult.id == receipt.operation_result_id,
                OperationResult.organization_id == actor.organization_id,
                OperationResult.receipt_id == receipt.id,
                OperationResult.operation == cls.OPERATION,
                OperationResult.natural_key == natural_key,
            )
        ).scalar_one_or_none()
        materialisation = session.execute(
            select(CommandMaterialisation).where(
                CommandMaterialisation.organization_id == actor.organization_id,
                CommandMaterialisation.receipt_id == receipt.id,
                CommandMaterialisation.operation == cls.OPERATION,
                CommandMaterialisation.natural_key == natural_key,
            )
        ).scalar_one_or_none()
        if result is None or materialisation is None:
            return False
        if result.object_ids != materialisation.object_ids:
            return False
        cycle_id = result.object_ids.get("review_cycle_id")
        if not isinstance(cycle_id, int):
            return False
        return session.execute(
            select(ARBReviewCycle.id).where(
                ARBReviewCycle.id == cycle_id,
                ARBReviewCycle.organization_id == actor.organization_id,
                ARBReviewCycle.subject_type == subject_type,
                ARBReviewCycle.subject_id == subject_id,
            )
        ).scalar_one_or_none() is not None

    @classmethod
    def _lock_subject_submission(cls, session, actor, subject):
        """Serialize first and successor cycle allocation for one typed subject."""
        session.execute(
            text(
                "SELECT pg_advisory_xact_lock(:lock_key) "
                "/* tenancy-ok: deterministic key includes organization_id */"
            ),
            {
                "lock_key": cls._subject_lock_key(
                    actor.organization_id, subject.subject_type, subject.subject_id
                )
            },
        )

    @classmethod
    def _insert_submission_graph(
        cls,
        *,
        session,
        actor,
        subject,
        adapter,
        pinned_evidence,
        review_item_id=None,
        claim,
    ) -> DomainMutationResult:
        subject_column = _SUBJECT_COLUMNS[subject.subject_type]
        evidence_column, expected_evidence_type = _EVIDENCE_COLUMNS[
            subject.subject_type
        ]
        if pinned_evidence.evidence_type != expected_evidence_type:
            raise CommandConflict("arb_evidence_type_mismatch")

        history = list(
            session.execute(
                select(ARBReviewCycle)
                .where(
                    ARBReviewCycle.organization_id == actor.organization_id,
                    ARBReviewCycle.subject_type == subject.subject_type,
                    ARBReviewCycle.subject_id == subject.subject_id,
                )
                .order_by(ARBReviewCycle.cycle_number.desc(), ARBReviewCycle.id.desc())
                .with_for_update()
            ).scalars()
        )
        if history and history[0].closed_at is None:
            raise CommandConflict("arb_subject_already_has_open_cycle")
        predecessor = history[0] if history else None
        if (
            predecessor is not None
            and subject.subject_type == "decision_brief"
            and predecessor.decision_brief_version_id == subject.logical_version_id
        ):
            raise CommandConflict("arb_decision_brief_version_already_reviewed")
        cycle_number = predecessor.cycle_number + 1 if predecessor else 1
        now = CommandService._database_now(session)
        review_number = f"REV-{now:%Y}-{uuid.uuid4().hex[:12].upper()}"
        typed_values = {
            "organization_id": actor.organization_id,
            "subject_type": subject.subject_type,
            "subject_id": subject.subject_id,
            subject_column: subject.subject_id,
            evidence_column: pinned_evidence.evidence_id,
        }
        cycle = ARBReviewCycle(
            **typed_values,
            review_number=review_number,
            cycle_number=cycle_number,
            predecessor_cycle_id=predecessor.id if predecessor else None,
            status="submitted",
            opened_at=now,
        )
        session.add(cycle)
        session.flush()
        review = ARBReviewItem(
            **typed_values,
            id=review_item_id,
            review_cycle_id=cycle.id,
            review_number=review_number,
            title=f"{subject.title} ARB review",
            description=f"Governed {subject.subject_type} submission",
            review_type=getattr(adapter, "review_type", "architecture_change"),
            priority="medium",
            status="submitted",
            submitter_id=actor.user_id,
            submitted_at=now.astimezone(timezone.utc).replace(tzinfo=None),
        )
        session.add(review)
        session.flush()
        session.add(
            ARBSubmissionEvent(
                **typed_values,
                review_cycle_id=cycle.id,
                review_item_id=review.id,
                event_type="submitted",
                actor_id=actor.user_id,
                command_receipt_id=claim.receipt_id,
                command_generation=claim.generation,
            )
        )
        session.flush()
        response = {
            "review_cycle_id": cycle.id,
            "review_item_id": review.id,
            "evidence_id": pinned_evidence.evidence_id,
            "review_number": review_number,
            "cycle_number": cycle_number,
            "subject_type": subject.subject_type,
            "subject_id": subject.subject_id,
            "status": "submitted",
            "canonical_url": adapter.canonical_url(subject),
        }
        return DomainMutationResult(
            object_ids={
                "review_cycle_id": cycle.id,
                "review_item_id": review.id,
                "evidence_id": pinned_evidence.evidence_id,
            },
            response=response,
            outbox_events=(),
        )

    @staticmethod
    def _validate_loaded_subject(actor, subject, subject_type, subject_id):
        if (
            not isinstance(subject, GovernedSubject)
            or subject.subject_type != subject_type
            or subject.subject_id != subject_id
            or subject.organization_id != actor.organization_id
        ):
            raise NotAuthorised("arb_subject_outside_actor_tenant")


__all__ = ["TypedARBSubmissionService"]

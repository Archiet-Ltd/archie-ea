"""Tenant-scoped read boundary for the typed ARB governance workspace.

This module is the *only* place the visible typed ARB surfaces are allowed to
assemble the ``ARBReviewCycle``/``ARBReviewItem`` graph.  Routes and templates
consume the two projections built here and never query the graph themselves.

Four rules are load-bearing and are enforced by
``tests/test_typed_arb_read_models.py``:

1. Every typed row is loaded with an explicit ``(id, organization_id)``
   predicate.  ``Query.get()``/``Session.get()`` are never used: they are
   tenant-scoped only on an identity-map *miss*, so a warm session hands back
   another tenant's object with no SQL and therefore no tenant filter.
2. Nothing is invented.  A value that is not persisted is ``None`` — never a
   zero, never a substituted live-subject value, never a computed readiness,
   risk or compliance score.  A failed read yields ``state="failed"`` with
   nullable pagination numbers rather than zero-filled ones.
3. Snapshot/version membership *and* content hash are verified before any
   dossier is exposed.  A hash mismatch is a destructive integrity state with
   no mutations offered, never a partial live-subject fallback.
4. ``allowed_actions`` is a projection of the current server-side authorisers.
   It is never derived from ``current_user.enterprise_role`` here and never
   from a client claim.  The services enforce it again on write.
"""

from __future__ import annotations

import uuid
from typing import Any, Iterable, Mapping

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.models.architecture_decision import ArchitectureDecision
from app.models.models import ArchitectureModel
from app.models.arb_condition_event import ARBConditionEvent
from app.models.arb_condition_evidence import ARBConditionEvidenceRecord
from app.models.arb_decision_event import ARBCondition, ARBDecisionEvent
from app.models.arb_submission_event import ARBSubmissionEvent
from app.models.arb_submission_evidence import ARBSubmissionEvidenceSnapshot
from app.models.architecture_review_board import (
    ARBReviewComment,
    ARBReviewCycle,
    ARBReviewItem,
)
from app.models.solution_models import Solution
from app.models.transformation_decision import (
    ARBSubjectEvidenceSnapshot,
    DecisionBrief,
    DecisionBriefVersion,
)
from app.models.transformation_programme import ProgrammeWorkstream
from app.models.user import User
from app.modules.transformation_room.arb_condition_evidence_service import (
    TypedARBConditionEvidenceService,
)
from app.modules.transformation_room.arb_condition_lifecycle_service import (
    TypedARBConditionLifecycleService,
)
from app.modules.transformation_room.arb_decision_service import TypedARBDecisionService
from app.modules.transformation_room.domain import ActorContext, TransformationError


SUBJECT_TYPES = ("decision_brief", "solution", "architecture_model", "adr")

SUBJECT_LABELS = {
    "decision_brief": "Decision Brief",
    "solution": "Solution",
    "architecture_model": "Architecture Model",
    "adr": "ADR",
}

# Lucide icon names; the shared icon macro resolves them.
SUBJECT_ICONS = {
    "decision_brief": "file-check",
    "solution": "layout-grid",
    "architecture_model": "network",
    "adr": "scroll-text",
}

OPEN_STATUSES = frozenset(TypedARBDecisionService.OPEN_STATUSES)

HISTORICAL_UNVERIFIED = "historical_unverified"

QUEUE_STATE_OPTIONS = ("open", "decided", "historical")

DEFAULT_PAGE_SIZE = 25

# Stable, actor-safe explanations.  They never name another tenant's data and
# never reveal which specific authority rule was missing beyond its class.
DENIAL_MESSAGES = {
    "arb_decision_separation_of_duties": (
        "You submitted this review. A separate authorised decision maker must "
        "record the outcome."
    ),
    "arb_condition_verification_separation_required": (
        "You submitted this review or this evidence. A separate authorised "
        "person must verify it."
    ),
    "arb_decision_not_authorised": (
        "You are not an authorised decision maker for this review."
    ),
    "arb_condition_transition_not_authorised": (
        "You are not authorised to act on this condition."
    ),
    "arb_condition_evidence_not_authorised": (
        "You are not authorised to record evidence for this condition."
    ),
}

_GENERIC_DENIAL = "You are not authorised to perform this action."

_CYCLE_CLOSED_DENIAL = "This review cycle is closed. Its outcome is immutable."

_CONDITION_STATE_DENIAL = "This condition is not in a state that allows this action."


def _denial(reason: str | None) -> str:
    if reason is None:
        return _GENERIC_DENIAL
    return DENIAL_MESSAGES.get(reason, _GENERIC_DENIAL)


def _display_name(user: User | None) -> str | None:
    """Return a human label for a user, or ``None`` when it is not persisted."""
    if user is None:
        return None
    name = user.full_name() if callable(getattr(user, "full_name", None)) else None
    if isinstance(name, str) and name.strip():
        return name.strip()
    email = getattr(user, "email", None)
    if isinstance(email, str) and email.strip():
        return email.strip()
    return None


def _new_command_key() -> str:
    return str(uuid.uuid4())


class TypedARBReadModel:
    """Build the two typed ARB projections for one authenticated actor."""

    QUEUE_STATE_OPTIONS = QUEUE_STATE_OPTIONS
    SUBJECT_TYPES = SUBJECT_TYPES
    DEFAULT_PAGE_SIZE = DEFAULT_PAGE_SIZE

    # ------------------------------------------------------------------
    # tenant-scoped loaders
    # ------------------------------------------------------------------

    @staticmethod
    def _one(session, model, *predicates):
        """Load exactly one row under an explicit predicate, or ``None``.

        Deliberately ``select()``-based: ``Session.get()`` skips SQL — and so
        skips the tenant filter — whenever the object is already in the
        identity map.
        """
        return session.execute(select(model).where(*predicates)).scalar_one_or_none()

    @classmethod
    def _load_review(cls, session, actor: ActorContext, review_item_id: int):
        return cls._one(
            session,
            ARBReviewItem,
            ARBReviewItem.id == review_item_id,
            ARBReviewItem.organization_id == actor.organization_id,
        )

    @classmethod
    def _load_cycle(cls, session, actor: ActorContext, cycle_id: int):
        return cls._one(
            session,
            ARBReviewCycle,
            ARBReviewCycle.id == cycle_id,
            ARBReviewCycle.organization_id == actor.organization_id,
        )

    @classmethod
    def _load_user(cls, session, actor: ActorContext, user_id: int | None):
        if user_id is None:
            return None
        return cls._one(
            session,
            User,
            User.id == user_id,
            User.organization_id == actor.organization_id,
        )

    # ------------------------------------------------------------------
    # subject identity
    # ------------------------------------------------------------------

    @classmethod
    def _subject_title(cls, session, actor: ActorContext, subject_type, subject_id):
        """Return the real persisted subject title, or ``None``."""
        if subject_type is None or subject_id is None:
            return None
        if subject_type == "decision_brief":
            row = cls._one(
                session,
                DecisionBrief,
                DecisionBrief.id == subject_id,
                DecisionBrief.organization_id == actor.organization_id,
            )
            return getattr(row, "title", None)
        if subject_type == "solution":
            row = cls._one(
                session,
                Solution,
                Solution.id == subject_id,
                Solution.organization_id == actor.organization_id,
            )
            return getattr(row, "name", None)
        if subject_type == "architecture_model":
            row = cls._one(
                session,
                ArchitectureModel,
                ArchitectureModel.id == subject_id,
                ArchitectureModel.organization_id == actor.organization_id,
            )
            return getattr(row, "name", None)
        if subject_type == "adr":
            row = cls._one(
                session,
                ArchitectureDecision,
                ArchitectureDecision.id == subject_id,
                ArchitectureDecision.organization_id == actor.organization_id,
            )
            return getattr(row, "title", None)
        return None

    @classmethod
    def _canonical_url(cls, session, actor: ActorContext, cycle):
        """Return the canonical subject URL, or ``None`` when unresolvable."""
        subject_type = cycle.subject_type
        subject_id = cycle.subject_id
        if subject_type is None or subject_id is None:
            return None
        if subject_type == "solution":
            return f"/solutions/{subject_id}?tab=governance"
        if subject_type == "architecture_model":
            return "/architecture/models"
        if subject_type == "adr":
            # E2E-H: was /architecture/adrs/records/<id>, a JSON-only
            # endpoint reading ArchitectureDecisionRecord (0 rows in
            # production). The real page is arch_decisions.view_decision.
            return f"/architecture/decisions/{subject_id}"
        if subject_type == "decision_brief":
            version = cls._pinned_decision_brief_version(session, actor, cycle)
            if version is None:
                return None
            workstream = cls._one(
                session,
                ProgrammeWorkstream,
                ProgrammeWorkstream.id == version.workstream_id,
                ProgrammeWorkstream.organization_id == actor.organization_id,
            )
            if workstream is None or workstream.programme_id is None:
                return None
            return (
                f"/solutions/programmes/{workstream.programme_id}"
                f"/workstreams/{workstream.id}/decision"
            )
        return None

    # ------------------------------------------------------------------
    # queue
    # ------------------------------------------------------------------

    @classmethod
    def queue_view(
        cls,
        *,
        actor: ActorContext,
        filters: Mapping[str, Any] | None = None,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        applied = cls._queue_filters(filters, page)
        try:
            return cls._queue_view(actor, applied, page_size)
        except (SQLAlchemyError, TransformationError, ValueError, TypeError):
            return cls._failed_queue(applied, "arb_queue_unavailable")

    @staticmethod
    def _queue_filters(filters: Mapping[str, Any] | None, page: int) -> dict[str, Any]:
        source = filters or {}
        state = source.get("state")
        if state not in QUEUE_STATE_OPTIONS:
            state = None
        subject_type = source.get("subject_type")
        if subject_type not in SUBJECT_TYPES:
            subject_type = None
        query = source.get("q")
        query = query.strip() if isinstance(query, str) and query.strip() else None
        try:
            resolved_page = int(page)
        except (TypeError, ValueError):
            resolved_page = 1
        if resolved_page < 1:
            resolved_page = 1
        return {
            "state": state,
            "subject_type": subject_type,
            "q": query,
            "page": resolved_page,
        }

    @staticmethod
    def _filter_options() -> dict[str, Any]:
        return {
            "state": [
                {"value": "open", "label": "Open"},
                {"value": "decided", "label": "Decided"},
                {"value": "historical", "label": "Historical"},
            ],
            "subject_type": [
                {"value": value, "label": SUBJECT_LABELS[value]}
                for value in SUBJECT_TYPES
            ],
        }

    @classmethod
    def _failed_queue(cls, applied: Mapping[str, Any], reason: str) -> dict[str, Any]:
        return {
            "state": "failed",
            "reason": reason,
            "filters": dict(applied),
            "filter_options": cls._filter_options(),
            "items": [],
            "page": None,
            "page_size": None,
            "total_items": None,
            "total_pages": None,
        }

    @classmethod
    def _queue_view(cls, actor, applied, page_size) -> dict[str, Any]:
        session = db.session
        predicates = [
            ARBReviewCycle.organization_id == actor.organization_id,
            ARBReviewItem.organization_id == actor.organization_id,
            ARBReviewItem.review_cycle_id == ARBReviewCycle.id,
            ARBReviewCycle.subject_type.in_(SUBJECT_TYPES),
        ]
        state = applied["state"]
        if state == "open":
            predicates.append(ARBReviewCycle.closed_at.is_(None))
            predicates.append(ARBReviewCycle.status != HISTORICAL_UNVERIFIED)
        elif state == "decided":
            predicates.append(ARBReviewCycle.closed_at.isnot(None))
            predicates.append(ARBReviewCycle.status != HISTORICAL_UNVERIFIED)
        elif state == "historical":
            predicates.append(ARBReviewCycle.status == HISTORICAL_UNVERIFIED)
        if applied["subject_type"]:
            predicates.append(ARBReviewCycle.subject_type == applied["subject_type"])
        if applied["q"]:
            pattern = f"%{applied['q']}%"
            predicates.append(
                or_(
                    ARBReviewItem.title.ilike(pattern),
                    ARBReviewCycle.review_number.ilike(pattern),
                )
            )

        total_items = session.execute(
            select(func.count())
            .select_from(ARBReviewCycle)
            .join(ARBReviewItem, ARBReviewItem.review_cycle_id == ARBReviewCycle.id)
            .where(*predicates)
        ).scalar_one()
        total_pages = (total_items + page_size - 1) // page_size if total_items else 0
        page = applied["page"]
        rows = session.execute(
            select(ARBReviewCycle, ARBReviewItem)
            .join(ARBReviewItem, ARBReviewItem.review_cycle_id == ARBReviewCycle.id)
            .where(*predicates)
            .order_by(ARBReviewCycle.opened_at.desc(), ARBReviewCycle.id.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        ).all()

        items = [cls._queue_item(session, actor, cycle, review) for cycle, review in rows]
        return {
            "state": "available" if total_items else "empty",
            "reason": None if total_items else "arb_queue_empty",
            "filters": dict(applied),
            "filter_options": cls._filter_options(),
            "items": items,
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": total_pages,
        }

    @classmethod
    def _queue_item(cls, session, actor, cycle, review) -> dict[str, Any]:
        historical = cycle.status == HISTORICAL_UNVERIFIED
        action_label, action_anchor = cls._required_action(cycle, historical)
        return {
            "review_item_id": review.id,
            "review_cycle_id": cycle.id,
            "review_number": cycle.review_number,
            "cycle_number": cycle.cycle_number,
            "subject_type": cycle.subject_type,
            "subject_id": cycle.subject_id,
            "subject_label": SUBJECT_LABELS.get(cycle.subject_type),
            "subject_icon": SUBJECT_ICONS.get(cycle.subject_type),
            "subject_title": cls._subject_title(
                session, actor, cycle.subject_type, cycle.subject_id
            ),
            "canonical_url": cls._canonical_url(session, actor, cycle),
            "projection_status": cycle.status,
            "opened_at": cycle.opened_at,
            "submitted_at": review.submitted_at,
            "submitter_display": _display_name(
                cls._load_user(session, actor, review.submitter_id)
            ),
            "required_action_label": action_label,
            "required_action_anchor": action_anchor,
            "is_historical_unverified": historical,
        }

    @staticmethod
    def _required_action(cycle, historical: bool):
        if historical:
            return "Locked historical review", None
        if cycle.status in OPEN_STATUSES:
            return "Record a decision", "#decision"
        if cycle.status == "approved_with_conditions":
            return "Resolve blocking conditions", "#conditions"
        if cycle.closed_at is not None:
            return "No action required", None
        return None, None

    # ------------------------------------------------------------------
    # review
    # ------------------------------------------------------------------

    @classmethod
    def review_view(cls, *, actor: ActorContext, review_item_id: int) -> dict[str, Any]:
        try:
            return cls._review_view(actor, review_item_id)
        except (SQLAlchemyError, TransformationError, ValueError, TypeError):
            return cls._failed_review("arb_review_unavailable")

    @staticmethod
    def _empty_identity() -> dict[str, Any]:
        return {
            "review_item_id": None,
            "review_cycle_id": None,
            "review_number": None,
            "cycle_number": None,
            "subject_type": None,
            "subject_id": None,
            "predecessor_cycle_id": None,
            "successor_cycle_id": None,
        }

    @staticmethod
    def _no_actions() -> dict[str, Any]:
        return {
            "can_decide": False,
            "decision_denial_reason": None,
            "decision_outcomes": [],
            "conditions": {},
        }

    @classmethod
    def _failed_review(cls, reason: str, identity=None, evidence=None) -> dict[str, Any]:
        return {
            "state": "failed",
            "reason": reason,
            "identity": identity or cls._empty_identity(),
            "subject": {
                "type": None,
                "label": None,
                "icon": None,
                "title": None,
                "canonical_url": None,
            },
            "evidence": evidence,
            "decision": None,
            "conditions": [],
            "history": [],
            "comments": [],
            "allowed_actions": cls._no_actions(),
            "command_keys": {},
        }

    @classmethod
    def _review_view(cls, actor, review_item_id) -> dict[str, Any]:
        session = db.session
        review = cls._load_review(session, actor, review_item_id)
        if review is None:
            return cls._failed_review("arb_review_not_found")
        if review.review_cycle_id is None:
            return cls._legacy_generic(review)
        cycle = cls._load_cycle(session, actor, review.review_cycle_id)
        if cycle is None or review.organization_id != cycle.organization_id:
            return cls._failed_review("arb_review_cycle_not_found")

        identity = cls._identity(session, actor, cycle, review)
        subject = {
            "type": cycle.subject_type,
            "label": SUBJECT_LABELS.get(cycle.subject_type),
            "icon": SUBJECT_ICONS.get(cycle.subject_type),
            "title": cls._subject_title(
                session, actor, cycle.subject_type, cycle.subject_id
            ),
            "canonical_url": cls._canonical_url(session, actor, cycle),
        }
        history = cls._history(session, actor, cycle)
        comments = cls._comments(session, actor, review)

        if cycle.status == HISTORICAL_UNVERIFIED:
            return {
                "state": HISTORICAL_UNVERIFIED,
                "reason": cycle.migration_gap_reason,
                "identity": identity,
                "subject": subject,
                "evidence": {
                    "evidence_type": None,
                    "evidence_id": None,
                    "version": None,
                    "schema_version": None,
                    "policy_version": None,
                    "captured_by_display": None,
                    "captured_at": None,
                    "content_hash": None,
                    "hash_state": "unavailable",
                    "sections": [],
                    "legacy_source_type": cycle.legacy_source_type,
                    "legacy_source_id": cycle.legacy_source_id,
                    "migration_gap_reason": cycle.migration_gap_reason,
                },
                "decision": {
                    "event": None,
                    "projection": cls._projection(cycle, review, None),
                    "recorded_historical_outcome": cycle.terminal_outcome,
                },
                "conditions": [],
                "history": history,
                "comments": comments,
                "allowed_actions": cls._no_actions(),
                "command_keys": {},
            }

        evidence = cls._evidence(session, actor, cycle)
        if evidence["hash_state"] != "verified":
            return cls._failed_review(
                "arb_evidence_integrity_failed", identity=identity, evidence=evidence
            )

        decision_event = cls._one(
            session,
            ARBDecisionEvent,
            ARBDecisionEvent.review_cycle_id == cycle.id,
            ARBDecisionEvent.organization_id == actor.organization_id,
        )
        if decision_event is not None and decision_event.review_item_id != review.id:
            return cls._failed_review(
                "arb_cycle_review_projection_mismatch", identity=identity
            )
        conditions = cls._conditions(session, actor, cycle, decision_event)
        allowed_actions = cls._allowed_actions(
            session, actor, cycle, decision_event, conditions
        )
        return {
            "state": "available",
            "reason": None,
            "identity": identity,
            "subject": subject,
            "evidence": evidence,
            "decision": {
                "event": cls._decision_event(session, actor, decision_event),
                "projection": cls._projection(cycle, review, conditions),
                "recorded_historical_outcome": None,
            },
            "conditions": conditions,
            "history": history,
            "comments": comments,
            "allowed_actions": allowed_actions,
            "command_keys": cls._command_keys(allowed_actions),
        }

    @classmethod
    def _legacy_generic(cls, review) -> dict[str, Any]:
        identity = cls._empty_identity()
        identity["review_item_id"] = review.id
        identity["review_number"] = review.review_number
        return {
            "state": "legacy_generic",
            "reason": "arb_review_is_legacy_generic",
            "identity": identity,
            "subject": {
                "type": None,
                "label": None,
                "icon": None,
                "title": review.title,
                "canonical_url": None,
            },
            "evidence": None,
            "decision": None,
            "conditions": [],
            "history": [],
            "comments": [],
            "allowed_actions": cls._no_actions(),
            "command_keys": {},
        }

    @classmethod
    def _identity(cls, session, actor, cycle, review) -> dict[str, Any]:
        successor = cls._one(
            session,
            ARBReviewCycle,
            ARBReviewCycle.predecessor_cycle_id == cycle.id,
            ARBReviewCycle.organization_id == actor.organization_id,
        )
        successor_cycle_id = successor.id if successor is not None else None
        return {
            "review_item_id": review.id,
            "review_cycle_id": cycle.id,
            "review_number": cycle.review_number,
            "cycle_number": cycle.cycle_number,
            "subject_type": cycle.subject_type,
            "subject_id": cycle.subject_id,
            "predecessor_cycle_id": cycle.predecessor_cycle_id,
            "predecessor_review_item_id": cls._review_item_id_for_cycle(
                session, actor, cycle.predecessor_cycle_id
            ),
            "successor_cycle_id": successor_cycle_id,
            "successor_review_item_id": cls._review_item_id_for_cycle(
                session, actor, successor_cycle_id
            ),
        }

    @classmethod
    def _review_item_id_for_cycle(cls, session, actor, cycle_id):
        """Return the addressable review item for a linked cycle, or ``None``.

        ``/arb/reviews/<review_item_id>`` cannot be addressed by a cycle ID, so
        a predecessor/successor link needs the review item.  A cycle in another
        tenant, or one whose review item is missing, resolves to ``None``: the
        cycle ID is never substituted as a stand-in.
        """
        if cycle_id is None:
            return None
        review = cls._one(
            session,
            ARBReviewItem,
            ARBReviewItem.review_cycle_id == cycle_id,
            ARBReviewItem.organization_id == actor.organization_id,
        )
        return review.id if review is not None else None

    # ------------------------------------------------------------------
    # evidence
    # ------------------------------------------------------------------

    @classmethod
    def _pinned_decision_brief_version(cls, session, actor, cycle):
        if cycle.decision_brief_version_id is None or cycle.decision_brief_id is None:
            return None
        return cls._one(
            session,
            DecisionBriefVersion,
            DecisionBriefVersion.id == cycle.decision_brief_version_id,
            DecisionBriefVersion.brief_id == cycle.decision_brief_id,
            DecisionBriefVersion.organization_id == actor.organization_id,
        )

    @staticmethod
    def _blank_evidence(evidence_type, reason) -> dict[str, Any]:
        return {
            "evidence_type": evidence_type,
            "evidence_id": None,
            "version": None,
            "schema_version": None,
            "policy_version": None,
            "captured_by_display": None,
            "captured_at": None,
            "content_hash": None,
            "hash_state": "unavailable",
            "hash_reason": reason,
            "sections": [],
        }

    @classmethod
    def _evidence(cls, session, actor, cycle) -> dict[str, Any]:
        subject_type = cycle.subject_type
        if subject_type == "decision_brief":
            return cls._decision_brief_evidence(session, actor, cycle)
        if subject_type == "solution":
            return cls._solution_evidence(session, actor, cycle)
        if subject_type in ("architecture_model", "adr"):
            return cls._subject_snapshot_evidence(session, actor, cycle)
        return cls._blank_evidence(None, "arb_subject_type_unknown")

    @classmethod
    def _decision_brief_evidence(cls, session, actor, cycle) -> dict[str, Any]:
        from app.modules.transformation_room.decision_service import DecisionBriefService

        version = cls._pinned_decision_brief_version(session, actor, cycle)
        if version is None:
            return cls._blank_evidence(
                "decision_brief_version", "arb_pinned_evidence_missing"
            )
        verified = False
        try:
            verified = bool(DecisionBriefService.verify_hash(version))
        except (SQLAlchemyError, TransformationError, ValueError, TypeError):
            verified = False
        payload = version.frozen_payload if isinstance(version.frozen_payload, Mapping) else {}
        return {
            "evidence_type": "decision_brief_version",
            "evidence_id": version.id,
            "version": version.version,
            "schema_version": None,
            "policy_version": version.policy_version,
            "captured_by_display": _display_name(
                cls._load_user(session, actor, version.created_by_id)
            ),
            "captured_at": version.created_at,
            "content_hash": version.content_hash,
            "hash_state": "verified" if verified else "mismatch",
            "hash_reason": None if verified else "arb_evidence_hash_mismatch",
            "sections": cls._sections(
                payload,
                (
                    ("objective", "Objective"),
                    ("scope", "Scope"),
                    ("candidate", "Candidate"),
                    ("options", "Option versions"),
                    ("recommendation", "Recommendation"),
                    ("expected_outcomes", "Expected outcomes"),
                    ("measures", "Measures"),
                    ("cited_evidence", "Cited evidence"),
                    ("unknowns", "Unknowns"),
                    ("conflicts", "Conflicts"),
                    ("expected_impacts", "Expected impacts"),
                    ("human_assertions", "Human assertions"),
                    ("exception", "Exception and authority"),
                ),
            )
            if verified
            else [],
        }

    @classmethod
    def _solution_evidence(cls, session, actor, cycle) -> dict[str, Any]:
        if cycle.solution_evidence_snapshot_id is None:
            return cls._blank_evidence(
                "solution_evidence_snapshot", "arb_pinned_evidence_missing"
            )
        snapshot = cls._one(
            session,
            ARBSubmissionEvidenceSnapshot,
            ARBSubmissionEvidenceSnapshot.id == cycle.solution_evidence_snapshot_id,
            ARBSubmissionEvidenceSnapshot.organization_id == actor.organization_id,
        )
        if snapshot is None or snapshot.solution_id != cycle.solution_id:
            return cls._blank_evidence(
                "solution_evidence_snapshot", "arb_evidence_membership_mismatch"
            )
        verified = bool(
            snapshot.content_hash
            and snapshot.content_hash == snapshot.recompute_content_hash()
        )
        payload = {
            "workflow_type": snapshot.workflow_type,
            "checks": snapshot.checks,
            "artifacts": snapshot.artifacts,
            "governance_result": snapshot.governance_result,
            "request_assertions": snapshot.request_assertions,
        }
        return {
            "evidence_type": "solution_evidence_snapshot",
            "evidence_id": snapshot.id,
            "version": None,
            "schema_version": snapshot.schema_version,
            "policy_version": None,
            "captured_by_display": _display_name(
                cls._load_user(session, actor, snapshot.actor_id)
            ),
            "captured_at": snapshot.captured_at,
            "content_hash": snapshot.content_hash,
            "hash_state": "verified" if verified else "mismatch",
            "hash_reason": None if verified else "arb_evidence_hash_mismatch",
            "sections": cls._sections(
                payload,
                (
                    ("workflow_type", "Workflow type"),
                    ("checks", "Server checks"),
                    ("artifacts", "Artefacts"),
                    ("governance_result", "Governance result"),
                    ("request_assertions", "Request assertion"),
                ),
            )
            if verified
            else [],
        }

    @classmethod
    def _subject_snapshot_evidence(cls, session, actor, cycle) -> dict[str, Any]:
        if cycle.subject_evidence_snapshot_id is None:
            return cls._blank_evidence(
                "arb_subject_evidence_snapshot", "arb_pinned_evidence_missing"
            )
        snapshot = cls._one(
            session,
            ARBSubjectEvidenceSnapshot,
            ARBSubjectEvidenceSnapshot.id == cycle.subject_evidence_snapshot_id,
            ARBSubjectEvidenceSnapshot.organization_id == actor.organization_id,
        )
        if (
            snapshot is None
            or snapshot.subject_type != cycle.subject_type
            or snapshot.subject_id != cycle.subject_id
        ):
            return cls._blank_evidence(
                "arb_subject_evidence_snapshot", "arb_evidence_membership_mismatch"
            )
        verified = bool(
            snapshot.content_hash
            and snapshot.content_hash == snapshot.recompute_content_hash()
        )
        payload = snapshot.payload if isinstance(snapshot.payload, Mapping) else {}
        sections_spec = (
            (
                ("name", "Name"),
                ("version", "Version"),
                ("elements", "Immutable element citations"),
                ("relationships", "Immutable relationship citations"),
                ("validator_result", "ArchiMate validator result"),
                ("policy_catalogue", "Mandatory standard policy catalogue"),
                ("pending_obligations", "Pending review obligations"),
            )
            if cycle.subject_type == "architecture_model"
            else (
                ("title", "Title"),
                ("context", "Context"),
                ("decision", "Decision"),
                ("rationale", "Rationale"),
                ("consequences", "Consequences"),
                ("resolved_links", "Linked-subject evidence"),
                ("policy_catalogue", "Applicable policy and standard citations"),
                ("pending_obligations", "Pending obligations"),
            )
        )
        return {
            "evidence_type": "arb_subject_evidence_snapshot",
            "evidence_id": snapshot.id,
            "version": None,
            "schema_version": snapshot.schema_version,
            "policy_version": snapshot.policy_version,
            "captured_by_display": _display_name(
                cls._load_user(session, actor, snapshot.captured_by_id)
            ),
            "captured_at": snapshot.captured_at,
            "content_hash": snapshot.content_hash,
            "hash_state": "verified" if verified else "mismatch",
            "hash_reason": None if verified else "arb_evidence_hash_mismatch",
            "sections": (
                cls._sections(payload, sections_spec)
                + cls._sections({"citations": snapshot.citations}, (("citations", "Citations"),))
            )
            if verified
            else [],
        }

    @staticmethod
    def _sections(payload: Mapping[str, Any], spec: Iterable[tuple[str, str]]):
        """Name every dossier section; an absent key stays ``None`` (em dash)."""
        source = payload if isinstance(payload, Mapping) else {}
        return [
            {"key": key, "label": label, "value": source.get(key)}
            for key, label in spec
        ]

    # ------------------------------------------------------------------
    # decision, conditions, history
    # ------------------------------------------------------------------

    @classmethod
    def _decision_event(cls, session, actor, event) -> dict[str, Any] | None:
        if event is None:
            return None
        return {
            "decision_event_id": event.id,
            "outcome": event.outcome,
            "from_state": event.from_state,
            "to_state": event.to_state,
            "rationale": event.rationale,
            "actor_display": _display_name(
                cls._load_user(session, actor, event.actor_id)
            ),
            "recorded_at": event.created_at,
        }

    @staticmethod
    def _projection(cycle, review, conditions) -> dict[str, Any]:
        if conditions is None:
            blocking = None
            condition_count = None
        else:
            condition_count = len(conditions)
            blocking = sum(
                1
                for condition in conditions
                if condition["blocks_execution"]
                and condition["status"] not in ("fulfilled", "waived")
            )
        return {
            "status": cycle.status,
            "terminal_outcome": cycle.terminal_outcome,
            "closed_at": cycle.closed_at,
            "condition_projection_revision": cycle.condition_projection_revision,
            "review_status": review.status,
            "condition_count": condition_count,
            "blocking_condition_count": blocking,
        }

    @classmethod
    def _conditions(cls, session, actor, cycle, decision_event):
        if decision_event is None:
            return []
        rows = (
            session.execute(
                select(ARBCondition)
                .where(
                    ARBCondition.review_cycle_id == cycle.id,
                    ARBCondition.decision_event_id == decision_event.id,
                    ARBCondition.organization_id == actor.organization_id,
                )
                .order_by(ARBCondition.id)
            )
            .scalars()
            .all()
        )
        return [cls._condition(session, actor, cycle, condition) for condition in rows]

    @classmethod
    def _condition(cls, session, actor, cycle, condition) -> dict[str, Any]:
        evidence = None
        if condition.submitted_evidence_id is not None:
            record = cls._one(
                session,
                ARBConditionEvidenceRecord,
                ARBConditionEvidenceRecord.id == condition.submitted_evidence_id,
                ARBConditionEvidenceRecord.condition_id == condition.id,
                ARBConditionEvidenceRecord.organization_id == actor.organization_id,
            )
            if record is not None:
                evidence = {
                    "condition_evidence_id": record.id,
                    "condition_revision": record.condition_revision,
                    "source_identity": record.source_identity,
                    "source_type": record.source_type,
                    "source_version": record.source_version,
                    "content_hash": record.content_hash,
                    "observed_at": record.observed_at,
                    "freshness_status": record.freshness_status,
                    "freshness_rule_version": record.freshness_rule_version,
                    "submitted_by_display": _display_name(
                        cls._load_user(session, actor, condition.evidence_submitted_by_id)
                    ),
                    "submitted_at": condition.evidence_submitted_at,
                }
        waiver = None
        if condition.waived_at is not None:
            waiver = {
                "reason": condition.waiver_reason,
                "expires_at": condition.waiver_expires_at,
                "scope": condition.waiver_scope_json,
                "compensating_control": condition.compensating_control,
                "approved_by_display": _display_name(
                    cls._load_user(session, actor, condition.waived_by_id)
                ),
                "waived_at": condition.waived_at,
                "prior_status": condition.waiver_prior_status,
            }
        events = (
            session.execute(
                select(ARBConditionEvent)
                .where(
                    ARBConditionEvent.condition_id == condition.id,
                    ARBConditionEvent.review_cycle_id == cycle.id,
                    ARBConditionEvent.organization_id == actor.organization_id,
                )
                .order_by(ARBConditionEvent.id)
            )
            .scalars()
            .all()
        )
        return {
            "condition_id": condition.id,
            "anchor": f"condition-{condition.id}",
            "condition_number": condition.condition_number,
            "description": condition.description,
            "category": condition.category,
            "due_date": condition.due_date,
            "blocks_execution": bool(condition.blocks_execution),
            "status": condition.status,
            "revision": condition.revision,
            "responsible_display": _display_name(
                cls._load_user(session, actor, condition.responsible_id)
            ),
            "verified_by_display": _display_name(
                cls._load_user(session, actor, condition.verified_by_id)
            ),
            "verified_at": condition.verified_at,
            "evidence": evidence,
            "waiver": waiver,
            "events": [
                {
                    "condition_event_id": event.id,
                    "event_type": event.event_type,
                    "from_state": event.from_state,
                    "to_state": event.to_state,
                    "condition_revision": event.condition_revision,
                    "projection_status": event.projection_status,
                    "actor_display": _display_name(
                        cls._load_user(session, actor, event.actor_id)
                    ),
                    "recorded_at": event.created_at,
                }
                for event in events
            ],
        }

    @classmethod
    def _history(cls, session, actor, cycle):
        entries = []
        submissions = (
            session.execute(
                select(ARBSubmissionEvent)
                .where(
                    ARBSubmissionEvent.review_cycle_id == cycle.id,
                    ARBSubmissionEvent.organization_id == actor.organization_id,
                )
                .order_by(ARBSubmissionEvent.id)
            )
            .scalars()
            .all()
        )
        for event in submissions:
            entries.append(
                {
                    "kind": "submission",
                    "event_id": event.id,
                    "event_type": event.event_type,
                    "from_state": None,
                    "to_state": None,
                    "actor_display": _display_name(
                        cls._load_user(session, actor, event.actor_id)
                    ),
                    "recorded_at": event.created_at,
                    "object_ids": {
                        "review_cycle_id": event.review_cycle_id,
                        "review_item_id": event.review_item_id,
                    },
                }
            )
        decision = cls._one(
            session,
            ARBDecisionEvent,
            ARBDecisionEvent.review_cycle_id == cycle.id,
            ARBDecisionEvent.organization_id == actor.organization_id,
        )
        if decision is not None:
            entries.append(
                {
                    "kind": "decision",
                    "event_id": decision.id,
                    "event_type": "decided",
                    "from_state": decision.from_state,
                    "to_state": decision.to_state,
                    "actor_display": _display_name(
                        cls._load_user(session, actor, decision.actor_id)
                    ),
                    "recorded_at": decision.created_at,
                    "rationale": decision.rationale,
                    "object_ids": {"decision_event_id": decision.id},
                }
            )
        condition_events = (
            session.execute(
                select(ARBConditionEvent)
                .where(
                    ARBConditionEvent.review_cycle_id == cycle.id,
                    ARBConditionEvent.organization_id == actor.organization_id,
                )
                .order_by(ARBConditionEvent.id)
            )
            .scalars()
            .all()
        )
        for event in condition_events:
            entries.append(
                {
                    "kind": "condition",
                    "event_id": event.id,
                    "event_type": event.event_type,
                    "from_state": event.from_state,
                    "to_state": event.to_state,
                    "actor_display": _display_name(
                        cls._load_user(session, actor, event.actor_id)
                    ),
                    "recorded_at": event.created_at,
                    "object_ids": {
                        "condition_id": event.condition_id,
                        "condition_event_id": event.id,
                        "submitted_evidence_id": event.submitted_evidence_id,
                    },
                }
            )
        return entries

    @classmethod
    def _comments(cls, session, actor, review):
        """Discussion comments on this review item, oldest first.

        Tenant-scoped the same way as every other loader in this module: an
        explicit ``(review_item_id, organization_id)`` predicate, never
        ``Session.get()``. Comments are written by the existing
        ``arb.add_comment`` route, which this read model does not replace —
        it only exposes what that route already persists so the typed
        workspace can display it again.
        """
        rows = (
            session.execute(
                select(ARBReviewComment)
                .where(
                    ARBReviewComment.review_item_id == review.id,
                    ARBReviewComment.organization_id == actor.organization_id,
                )
                .order_by(ARBReviewComment.id)
            )
            .scalars()
            .all()
        )
        return [
            {
                "comment_id": row.id,
                "comment_type": row.comment_type,
                "content": row.content,
                "actor_display": _display_name(
                    cls._load_user(session, actor, row.user_id)
                ),
                "created_at": row.created_at,
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # authority
    # ------------------------------------------------------------------

    @staticmethod
    def _authorise(callable_, *args, **kwargs):
        """Run a server-side authoriser; return ``(allowed, reason)``."""
        try:
            callable_(*args, **kwargs)
        except TransformationError as error:
            return False, getattr(error, "reason", None) or getattr(error, "code", None)
        except SQLAlchemyError:
            return False, None
        return True, None

    @classmethod
    def _allowed_actions(cls, session, actor, cycle, decision_event, conditions):
        cycle_open = cycle.status in OPEN_STATUSES and cycle.closed_at is None
        if not cycle_open or decision_event is not None:
            can_decide, decide_reason = False, None
            decide_denial = _CYCLE_CLOSED_DENIAL
        else:
            can_decide, decide_reason = cls._authorise(
                TypedARBDecisionService.authorise_decision, session, actor, cycle.id
            )
            decide_denial = None if can_decide else _denial(decide_reason)
        outcomes = []
        if can_decide:
            outcomes = [
                outcome
                for outcome in (
                    "approved",
                    "approved_with_conditions",
                    "returned_for_evidence",
                    "returned_for_options",
                    "rejected",
                )
                if outcome != "returned_for_options"
                or cycle.subject_type == "decision_brief"
            ]
        condition_actions = {}
        for condition in conditions:
            condition_actions[condition["condition_id"]] = cls._condition_actions(
                session, actor, condition
            )
        return {
            "can_decide": can_decide,
            "decision_denial_reason": decide_denial,
            "decision_outcomes": outcomes,
            "conditions": condition_actions,
        }

    @classmethod
    def _condition_actions(cls, session, actor, condition) -> dict[str, Any]:
        condition_id = condition["condition_id"]
        status = condition["status"]
        capture_ok, capture_reason = cls._authorise(
            TypedARBConditionEvidenceService.authorise_acceptance,
            session,
            actor,
            condition_id,
        )
        verify_ok, verify_reason = cls._authorise(
            TypedARBConditionLifecycleService.authorise_verify,
            session,
            actor,
            condition_id,
        )
        waive_ok, waive_reason = cls._authorise(
            TypedARBConditionLifecycleService.authorise_waive,
            session,
            actor,
            condition_id,
        )
        open_for_evidence = status in ("pending", "evidence_submitted")
        can_capture = capture_ok and status == "pending"
        can_submit = capture_ok and status == "pending"
        can_verify = verify_ok and status == "evidence_submitted"
        can_waive = waive_ok and open_for_evidence
        return {
            "can_capture_evidence": can_capture,
            "can_submit_evidence": can_submit,
            "can_verify": can_verify,
            "can_waive": can_waive,
            "capture_denial_reason": None
            if can_capture
            else (_denial(capture_reason) if not capture_ok else _CONDITION_STATE_DENIAL),
            "submit_denial_reason": None
            if can_submit
            else (_denial(capture_reason) if not capture_ok else _CONDITION_STATE_DENIAL),
            "verify_denial_reason": None
            if can_verify
            else (_denial(verify_reason) if not verify_ok else _CONDITION_STATE_DENIAL),
            "waive_denial_reason": None
            if can_waive
            else (_denial(waive_reason) if not waive_ok else _CONDITION_STATE_DENIAL),
        }

    @staticmethod
    def _command_keys(allowed_actions: Mapping[str, Any]) -> dict[str, str]:
        """One fresh command key per visible mutation form, minted on GET."""
        keys: dict[str, str] = {}
        if allowed_actions["can_decide"]:
            keys["decision"] = _new_command_key()
        for condition_id, actions in allowed_actions["conditions"].items():
            if actions["can_capture_evidence"]:
                keys[f"condition:{condition_id}:capture"] = _new_command_key()
            if actions["can_submit_evidence"]:
                keys[f"condition:{condition_id}:submit"] = _new_command_key()
            if actions["can_verify"]:
                keys[f"condition:{condition_id}:verify"] = _new_command_key()
            if actions["can_waive"]:
                keys[f"condition:{condition_id}:waive"] = _new_command_key()
        return keys


def typed_arb_queue_view(*, actor, filters=None, page=1, page_size=DEFAULT_PAGE_SIZE):
    """Build ``TypedARBQueueView`` for one actor."""
    return TypedARBReadModel.queue_view(
        actor=actor, filters=filters, page=page, page_size=page_size
    )


def typed_arb_review_view(*, actor, review_item_id):
    """Build ``TypedARBReviewView`` for one actor and one review item."""
    return TypedARBReadModel.review_view(actor=actor, review_item_id=review_item_id)


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "QUEUE_STATE_OPTIONS",
    "SUBJECT_ICONS",
    "SUBJECT_LABELS",
    "SUBJECT_TYPES",
    "TypedARBReadModel",
    "typed_arb_queue_view",
    "typed_arb_review_view",
]

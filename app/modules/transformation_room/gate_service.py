"""Pure, versioned Transformation Room lifecycle policy and locked transitions."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.benefit import Benefit
from app.models.implementation_migration import WorkPackage
from app.models.strategic import RoadmapItem, StrategicInitiative
from app.models.transformation_decision import (
    DecisionBrief,
    DecisionBriefVersion,
    TransformationOptionVersion,
)
from app.models.transformation_evidence import (
    CandidateOverlapDisposition,
    CandidateSignal,
    EvidenceClaimHead,
    EvidenceRecord,
    EvidenceRequest,
    TransformationCandidate,
)
from app.models.transformation_execution import CommandMaterialisation
from app.models.transformation_programme import (
    ISO_4217_CURRENCIES,
    MeasureDefinition,
    ProgrammeOutcomeCommitment,
    ProgrammeRoleAssignment,
    ProgrammeWorkstream,
)
from app.models.user import User
from app.modules.transformation_room.command_service import CommandService, OperationAuthorizer
from app.modules.transformation_room.domain import (
    ActorContext,
    BlockedByEvidence,
    CommandConflict,
    CommandResult,
    DomainMutationResult,
    GateBlocker,
    GateResult,
    NotAuthorised,
    NotFound,
)
from app.modules.transformation_room.programme_service import (
    CREATE_ROLES,
    OBJECTIVE_ROLES,
    READ_ROLES,
    TransformationProgrammeService,
)


@dataclass(frozen=True)
class Transition:
    source: str
    target: str


@dataclass(frozen=True)
class PolicySnapshot:
    """All inputs to the r1.1 policy, including not-yet-installed resources."""

    programme: StrategicInitiative
    workstream: ProgrammeWorkstream
    role_assignments: tuple[ProgrammeRoleAssignment, ...]
    outcomes: tuple[ProgrammeOutcomeCommitment, ...]
    measures: tuple[MeasureDefinition, ...]
    accepted_candidates: tuple[object, ...]
    active_evidence_heads: tuple[object, ...]
    evidence_records: tuple[object, ...]
    evidence_requests: tuple[object, ...]
    evidence_waivers: tuple[object, ...]
    option_versions: tuple[object, ...]
    option_exceptions: tuple[object, ...]
    brief_versions: tuple[object, ...]
    arb_cycles: tuple[object, ...]
    arb_conditions: tuple[object, ...]
    approved_actions: tuple[object, ...]
    work_packages: tuple[WorkPackage, ...]
    roadmap_items: tuple[RoadmapItem, ...]
    benefits: tuple[Benefit, ...]
    delivery_records: tuple[object, ...]
    measurements: tuple[object, ...]
    outcome_reviews: tuple[object, ...]
    authorized_waiver_authority_ids: frozenset[int]
    unavailable_resources: frozenset[str]


@dataclass(frozen=True)
class CandidatePolicyProjection:
    """Persisted candidate facts needed by the Discover-to-Evidence policy."""

    id: int
    organization_id: int
    workstream_id: int
    subject_type: str
    subject_id: int
    inclusion_status: str
    subject_exists: bool
    duplicates_resolved: bool


def _value(row: object, field: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(field, default)
    return getattr(row, field, default)


def _text(row: object, field: str) -> str:
    value = _value(row, field)
    return value.strip() if isinstance(value, str) else ""


def _finite(value: Any) -> bool:
    if value is None:
        return True
    try:
        return Decimal(str(value)).is_finite()
    except (InvalidOperation, ValueError, TypeError):
        return False


def _current(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, datetime):
        now = datetime.now(timezone.utc)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value > now
    return value > date.today()


class TransformationGateService:
    POLICY_VERSION = "transformation-r1.1"
    TRANSITIONS = frozenset(
        {
            ("objective", "discover"),
            ("discover", "evidence"),
            ("evidence", "options"),
            ("options", "decision_ready"),
            ("decision_ready", "in_governance"),
            ("in_governance", "evidence"),
            ("in_governance", "options"),
            ("in_governance", "approved"),
            ("in_governance", "approved_with_conditions"),
            ("in_governance", "rejected"),
            ("approved_with_conditions", "approved"),
            ("approved", "execute"),
            ("execute", "outcomes"),
            ("outcomes", "completed"),
            ("outcomes", "execute"),
            ("rejected", "options"),
        }
    )
    NEXT_STAGE = {
        "objective": "discover", "discover": "evidence", "evidence": "options",
        "options": "decision_ready", "decision_ready": "in_governance",
        "in_governance": "approved", "approved_with_conditions": "approved",
        "approved": "execute", "rejected": "options", "execute": "outcomes",
        "outcomes": "completed",
    }
    TERMINAL_STAGES = frozenset({"completed"})
    # Lifecycle stages are states; the room has seven pages. The decision page
    # carries every stage from a frozen brief through the governance outcome.
    STAGE_PAGES = {
        "decision_ready": "decision",
        "in_governance": "decision",
        "approved_with_conditions": "decision",
        "approved": "decision",
        "rejected": "decision",
        "completed": "outcomes",
    }
    STAGE_ORDER = {
        "objective": 0,
        "discover": 1,
        "evidence": 2,
        "options": 3,
        "rejected": 3,
        "decision_ready": 4,
        "in_governance": 5,
        "approved_with_conditions": 6,
        "approved": 7,
        "execute": 8,
        "outcomes": 9,
        "completed": 10,
    }
    WAIVER_AUTHORITY_ROLES = CREATE_ROLES | frozenset({"arb_member", "decision_authority"})
    REQUIRED_RESOURCES = {
        ("discover", "evidence"): frozenset({"candidates", "evidence"}),
        ("evidence", "options"): frozenset({"evidence"}),
        ("options", "decision_ready"): frozenset({"options"}),
        ("decision_ready", "in_governance"): frozenset({"briefs", "evidence", "options"}),
        ("in_governance", "evidence"): frozenset({"arb", "briefs"}),
        ("in_governance", "options"): frozenset({"arb", "briefs"}),
        ("in_governance", "approved"): frozenset({"arb", "briefs"}),
        ("in_governance", "approved_with_conditions"): frozenset({"arb", "briefs", "conditions"}),
        ("in_governance", "rejected"): frozenset({"arb", "briefs"}),
        ("approved_with_conditions", "approved"): frozenset({"arb", "conditions", "evidence"}),
        ("approved", "execute"): frozenset({"approved_actions"}),
        ("execute", "outcomes"): frozenset({"delivery", "evidence"}),
        ("outcomes", "completed"): frozenset({"measurements", "outcome_reviews"}),
        ("outcomes", "execute"): frozenset({"delivery"}),
        ("rejected", "options"): frozenset({"arb"}),
    }

    _UNSCOPED_DECISION = object()

    @classmethod
    def evaluate(
        cls,
        *,
        actor: ActorContext,
        workstream_id: int,
        target_stage: str,
        decision_candidate_id=_UNSCOPED_DECISION,
    ) -> GateResult:
        snapshot = cls.load_policy_snapshot(actor=actor, workstream_id=workstream_id)
        if decision_candidate_id is not cls._UNSCOPED_DECISION:
            snapshot = cls.for_decision_scope(snapshot, decision_candidate_id)
        transition = cls.require_valid_transition(snapshot.workstream.lifecycle_stage, target_stage)
        blockers, warnings, evidence_ids = cls.evaluate_requirements(snapshot, transition)
        return GateResult(
            not blockers, transition.source, transition.target, cls.POLICY_VERSION,
            tuple(blockers), tuple(warnings), tuple(sorted(evidence_ids)),
        )

    @classmethod
    def for_decision_scope(cls, snapshot, candidate_id):
        """Project policy inputs to exactly one candidate or workstream decision."""
        roots = tuple(
            row
            for row in snapshot.option_exceptions
            if _value(row, "candidate_id") == candidate_id
        )
        root_ids = {_value(row, "id") for row in roots}
        return replace(
            snapshot,
            option_versions=tuple(
                row
                for row in snapshot.option_versions
                if _value(row, "candidate_id") == candidate_id
            ),
            option_exceptions=roots,
            brief_versions=tuple(
                row
                for row in snapshot.brief_versions
                if _value(row, "brief_id") in root_ids
            ),
        )

    @classmethod
    def transition(cls, *, actor: ActorContext, workstream_id: int, target_stage: str,
                   expected_revision: int, command_key: str) -> CommandResult:
        request = {"workstream_id": workstream_id, "target_stage": target_stage,
                   "expected_revision": expected_revision}
        return CommandService.execute(
            actor=actor, operation="workstream.transition", idempotency_key=command_key,
            payload=request,
            natural_key=f"transition:{workstream_id}:{expected_revision}:{target_stage}",
            authorizer=cls.authorise_transition(workstream_id, target_stage, expected_revision),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._locked_transition(session, actor, request, claim),
        )

    @classmethod
    def load_policy_snapshot(cls, *, actor: ActorContext, workstream_id: int) -> PolicySnapshot:
        with Session(db.engine) as session:
            snapshot = cls._load_policy_snapshot(
                session=session, actor=actor, workstream_id=workstream_id, lock=False
            )
            session.expunge_all()
            return snapshot

    @classmethod
    def _load_policy_snapshot(cls, *, session, actor, workstream_id, lock) -> PolicySnapshot:
        scope = session.execute(
            select(ProgrammeWorkstream).where(
                ProgrammeWorkstream.id == workstream_id,
                ProgrammeWorkstream.organization_id == actor.organization_id,
            )
        ).scalar_one_or_none()
        if scope is None:
            raise NotFound("workstream_not_found")
        programme = cls._programme_statement(
            session, actor, scope.programme_id, lock=lock
        ).scalar_one_or_none()
        if programme is None:
            raise NotFound("programme_not_found")
        TransformationProgrammeService._require_active_programme(programme)
        if lock:
            workstream = session.execute(
                select(ProgrammeWorkstream).where(
                    ProgrammeWorkstream.id == scope.id,
                    ProgrammeWorkstream.programme_id == programme.id,
                    ProgrammeWorkstream.organization_id == actor.organization_id,
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            ).scalar_one()
        else:
            workstream = scope
            TransformationProgrammeService._require_programme_authority(
                session,
                actor,
                programme.id,
                scope.id,
                READ_ROLES,
                "programme_read_not_authorised",
            )
        roles = tuple(session.scalars(select(ProgrammeRoleAssignment).where(
            ProgrammeRoleAssignment.organization_id == actor.organization_id,
            ProgrammeRoleAssignment.programme_id == programme.id,
        )).all())
        tenant_users = tuple(session.scalars(select(User).where(
            User.organization_id == actor.organization_id,
        )).all())
        tenant_user_ids = {row.id for row in tenant_users}
        today = date.today()
        authorized_waiver_authority_ids = {
            row.id
            for row in tenant_users
            if (
                row.enterprise_role in cls.WAIVER_AUTHORITY_ROLES
                or (row.is_org_admin and "organization_admin" in cls.WAIVER_AUTHORITY_ROLES)
                or (row.is_platform_admin and "platform_admin" in cls.WAIVER_AUTHORITY_ROLES)
            )
        }
        authorized_waiver_authority_ids.update(
            row.user_id
            for row in roles
            if row.user_id in tenant_user_ids
            and row.role == "decision_authority"
            and row.effective_from is not None
            and row.effective_from <= today
            and (row.effective_to is None or row.effective_to >= today)
            and (row.workstream_id is None or row.workstream_id == workstream.id)
        )
        outcomes = tuple(session.scalars(select(ProgrammeOutcomeCommitment).where(
            ProgrammeOutcomeCommitment.organization_id == actor.organization_id,
            ProgrammeOutcomeCommitment.programme_id == programme.id,
            (ProgrammeOutcomeCommitment.workstream_id == workstream.id)
            | (ProgrammeOutcomeCommitment.workstream_id.is_(None)),
        )).all())
        outcome_ids = [row.id for row in outcomes]
        measures = tuple(session.scalars(select(MeasureDefinition).where(
            MeasureDefinition.organization_id == actor.organization_id,
            MeasureDefinition.outcome_commitment_id.in_(outcome_ids or [-1]),
        )).all())
        work_packages = tuple(session.scalars(select(WorkPackage).where(
            WorkPackage.organization_id == actor.organization_id,
            WorkPackage.programme_workstream_id == workstream.id,
        )).all())
        roadmap_items = tuple(session.scalars(select(RoadmapItem).where(
            RoadmapItem.organization_id == actor.organization_id,
            RoadmapItem.programme_workstream_id == workstream.id,
        )).all())
        benefits = tuple(session.scalars(select(Benefit).where(
            Benefit.organization_id == actor.organization_id,
            Benefit.programme_workstream_id == workstream.id,
        )).all())
        candidate_rows = tuple(session.scalars(
            select(TransformationCandidate).where(
                TransformationCandidate.organization_id == actor.organization_id,
                TransformationCandidate.workstream_id == workstream.id,
                TransformationCandidate.inclusion_status == "accepted",
            )
        ).all())
        candidate_ids = [row.id for row in candidate_rows]
        candidate_signals = tuple(session.scalars(
            select(CandidateSignal)
            .join(
                TransformationCandidate,
                (TransformationCandidate.id == CandidateSignal.candidate_id)
                & (
                    TransformationCandidate.organization_id
                    == CandidateSignal.organization_id
                ),
            )
            .where(
                CandidateSignal.organization_id == actor.organization_id,
                CandidateSignal.candidate_id.in_(candidate_ids or [-1]),
                TransformationCandidate.organization_id == actor.organization_id,
                TransformationCandidate.workstream_id == workstream.id,
            )
        ).all())
        application_subject_ids = [
            row.subject_id
            for row in candidate_rows
            if row.subject_type == "application"
        ]
        existing_application_ids = frozenset(session.scalars(
            select(ApplicationComponent.id).where(
                ApplicationComponent.organization_id == actor.organization_id,
                ApplicationComponent.id.in_(application_subject_ids or [-1]),
                ApplicationComponent.deleted_at.is_(None),
            )
        ).all())
        signals_by_candidate: dict[int, list[CandidateSignal]] = {}
        for signal in candidate_signals:
            signals_by_candidate.setdefault(signal.candidate_id, []).append(signal)
        overlap_dispositions = tuple(
            session.scalars(
                select(CandidateOverlapDisposition).where(
                    CandidateOverlapDisposition.organization_id
                    == actor.organization_id,
                    CandidateOverlapDisposition.candidate_id.in_(
                        candidate_ids or [-1]
                    ),
                )
            ).all()
        )
        disposition_by_candidate = {
            row.candidate_id: row for row in overlap_dispositions
        }
        from app.modules.transformation_room.discovery_service import (
            RationalisationDiscoveryService,
            _accepted_ruleset_digest,
        )

        expected_rule_versions = {
            rule.code: rule.rule_version
            for rule in RationalisationDiscoveryService.signal_rules()
        }

        def duplicate_resolution_is_current(candidate):
            signals = signals_by_candidate.get(candidate.id, ())
            if (
                candidate.ruleset_version
                != RationalisationDiscoveryService.RULESET_VERSION
                or candidate.ruleset_digest != _accepted_ruleset_digest(signals)
                or {
                    signal.rule_code: signal.rule_version for signal in signals
                }
                != expected_rule_versions
            ):
                return False
            overlap = next(
                (
                    signal
                    for signal in signals
                    if signal.rule_code == "capability_overlap"
                ),
                None,
            )
            if overlap is None or not isinstance(overlap.payload_json, Mapping):
                return False
            values = overlap.payload_json.get("observed_values")
            if (
                overlap.payload_json.get("unknown_code") is not None
                or not isinstance(values, Mapping)
            ):
                return False
            overlap_count = values.get("overlap_count")
            overlap_ids = values.get("overlapping_application_ids")
            if overlap_count == 0 and overlap_ids == []:
                return True
            if not isinstance(overlap_count, int) or overlap_count <= 0:
                return False
            disposition = disposition_by_candidate.get(candidate.id)
            return bool(
                disposition is not None
                and disposition.signal_digest == overlap.content_hash
                and disposition.overlapping_application_ids == overlap_ids
                and disposition.decision
                in {"confirmed_duplicate", "justified_distinct", "merge_repoint"}
                and isinstance(disposition.rationale, str)
                and disposition.rationale.strip()
            )

        accepted_candidates = tuple(
            CandidatePolicyProjection(
                id=row.id,
                organization_id=row.organization_id,
                workstream_id=row.workstream_id,
                subject_type=row.subject_type,
                subject_id=row.subject_id,
                inclusion_status=row.inclusion_status,
                subject_exists=(
                    row.subject_type == "application"
                    and row.subject_id in existing_application_ids
                ),
                duplicates_resolved=duplicate_resolution_is_current(row),
            )
            for row in candidate_rows
        )
        evidence_requests = tuple(session.scalars(
            select(EvidenceRequest)
            .join(
                TransformationCandidate,
                (TransformationCandidate.id == EvidenceRequest.candidate_id)
                & (
                    TransformationCandidate.organization_id
                    == EvidenceRequest.organization_id
                ),
            )
            .where(
                EvidenceRequest.organization_id == actor.organization_id,
                EvidenceRequest.workstream_id == workstream.id,
                EvidenceRequest.candidate_id.in_(candidate_ids or [-1]),
                TransformationCandidate.organization_id == actor.organization_id,
                TransformationCandidate.workstream_id == workstream.id,
            )
        ).all())
        subject_pairs = sorted(
            {(row.subject_type, row.subject_id) for row in candidate_rows}
        )
        active_evidence_heads = tuple(session.scalars(
            select(EvidenceClaimHead)
            .where(
                EvidenceClaimHead.organization_id == actor.organization_id,
                tuple_(
                    EvidenceClaimHead.subject_type,
                    EvidenceClaimHead.subject_id,
                ).in_(subject_pairs or [("", -1)]),
                EvidenceClaimHead.current_record_id.is_not(None),
            )
            .order_by(
                EvidenceClaimHead.subject_type,
                EvidenceClaimHead.subject_id,
                EvidenceClaimHead.claim_key,
                EvidenceClaimHead.source_identity,
                EvidenceClaimHead.id,
            )
        ).all())
        evidence_records = tuple(session.scalars(
            select(EvidenceRecord)
            .where(
                EvidenceRecord.organization_id == actor.organization_id,
                tuple_(EvidenceRecord.subject_type, EvidenceRecord.subject_id).in_(
                    subject_pairs or [("", -1)]
                ),
            )
            .order_by(EvidenceRecord.id)
        ).all())
        option_versions = tuple(session.scalars(
            select(TransformationOptionVersion)
            .where(
                TransformationOptionVersion.organization_id == actor.organization_id,
                TransformationOptionVersion.workstream_id == workstream.id,
            )
            .order_by(
                TransformationOptionVersion.option_id,
                TransformationOptionVersion.version,
            )
        ).all())
        decision_briefs = tuple(session.scalars(
            select(DecisionBrief)
            .where(
                DecisionBrief.organization_id == actor.organization_id,
                DecisionBrief.workstream_id == workstream.id,
            )
            .order_by(DecisionBrief.id)
        ).all())
        brief_versions = tuple(session.scalars(
            select(DecisionBriefVersion)
            .where(
                DecisionBriefVersion.organization_id == actor.organization_id,
                DecisionBriefVersion.workstream_id == workstream.id,
            )
            .order_by(DecisionBriefVersion.version, DecisionBriefVersion.id)
        ).all())
        unavailable = frozenset({
            "arb", "conditions", "approved_actions",
            "delivery", "measurements", "outcome_reviews",
        })
        evidence_waivers = tuple(
            row
            for row in evidence_requests
            if row.waiver_id is not None
            and row.interim_accountable_id in tenant_user_ids
        )
        return PolicySnapshot(
            programme, workstream, roles, outcomes, measures, accepted_candidates,
            active_evidence_heads, evidence_records, evidence_requests, evidence_waivers,
            option_versions, decision_briefs, brief_versions, (), (), (),
            work_packages, roadmap_items, benefits, (), (),
            (), frozenset(authorized_waiver_authority_ids), unavailable,
        )

    @staticmethod
    def _programme_statement(session, actor, programme_id, *, lock):
        statement = select(StrategicInitiative).where(
            StrategicInitiative.id == programme_id,
            StrategicInitiative.organization_id == actor.organization_id,
            StrategicInitiative.record_kind == "transformation_programme",
        )
        if lock:
            statement = statement.with_for_update()
        return session.execute(statement)

    @classmethod
    def require_valid_transition(cls, current_stage: str, target_stage: str) -> Transition:
        if (current_stage, target_stage) not in cls.TRANSITIONS:
            raise CommandConflict("invalid_lifecycle_transition",
                                  current_stage=current_stage, target_stage=target_stage)
        return Transition(current_stage, target_stage)

    @classmethod
    def evaluate_requirements(cls, snapshot, transition):
        blockers: list[GateBlocker] = []
        warnings: list[GateBlocker] = []
        evidence_ids: set[int] = set()
        workstream_id = snapshot.workstream.id
        room = f"/solutions/programmes/{snapshot.programme.id}/workstreams/{workstream_id}"
        edge = (transition.source, transition.target)

        def block(code, message, resource_type=None, resource_id=None, action_url=None):
            blockers.append(GateBlocker(code, message, resource_type, resource_id, action_url))

        missing = sorted(cls.REQUIRED_RESOURCES.get(edge, frozenset()) & snapshot.unavailable_resources)
        if missing:
            for resource in missing:
                block("policy_resource_unavailable",
                      f"The canonical {resource.replace('_', ' ')} resource is not installed.",
                      resource, workstream_id, None)
            return blockers, warnings, evidence_ids

        handlers = {
            ("objective", "discover"): cls._evaluate_objective,
            ("discover", "evidence"): cls._evaluate_discovery,
            ("evidence", "options"): cls._evaluate_evidence,
            ("options", "decision_ready"): cls._evaluate_options,
            ("decision_ready", "in_governance"): cls._evaluate_brief,
            ("approved_with_conditions", "approved"): cls._evaluate_conditions,
            ("approved", "execute"): cls._evaluate_execution,
            ("execute", "outcomes"): cls._evaluate_delivery,
            ("outcomes", "completed"): cls._evaluate_completion,
            ("rejected", "options"): cls._evaluate_reframe,
        }
        if transition.source == "in_governance":
            cls._evaluate_governance(snapshot, transition, block, room)
        elif edge == ("outcomes", "execute"):
            if not any(_value(row, "workstream_id") == workstream_id
                       and _value(row, "corrective_action_approved") is True
                       for row in snapshot.delivery_records):
                block("corrective_action_required", "Record an approved corrective delivery action.",
                      "workstream", workstream_id, f"{room}/outcomes")
        else:
            handlers[edge](snapshot, block, room)
        for head in snapshot.active_evidence_heads:
            current_id = _value(head, "current_record_id")
            if current_id is not None:
                evidence_ids.add(current_id)
        evidence_ids.update(
            _value(request, "accepted_evidence_id")
            for request in snapshot.evidence_requests
            if _value(request, "status") == "accepted"
            and _value(request, "accepted_evidence_id") is not None
        )
        return blockers, warnings, evidence_ids

    @classmethod
    def _evaluate_objective(cls, snapshot, block, room):
        programme_id, workstream_id = snapshot.programme.id, snapshot.workstream.id
        if snapshot.programme.owner_id is None:
            block("programme_owner_required", "Assign a programme owner.", "programme", programme_id, f"{room}/objective")
        if snapshot.workstream.lead_id is None:
            block("workstream_owner_required", "Assign a workstream owner.", "workstream", workstream_id, f"{room}/objective")
        if not _text(snapshot.workstream, "objective"):
            block("objective_required", "Record the business objective.", "workstream", workstream_id, f"{room}/objective")
        valid_outcomes = {row.id for row in snapshot.outcomes
                          if _value(row, "owner_id") and _text(row, "statement")}
        if not snapshot.outcomes:
            block("outcome_commitment_required", "Record an accountable outcome commitment.", "workstream", workstream_id, f"{room}/objective")
        elif not valid_outcomes:
            block("outcome_owner_required", "Name an accountable outcome owner.", "workstream", workstream_id, f"{room}/objective")
        invalid_values = any(not _finite(_value(row, field)) for row in snapshot.measures
                             for field in ("baseline_amount", "baseline_value", "target_amount", "target_value"))
        if invalid_values:
            block("measure_value_invalid", "Measure values must be finite.", "workstream", workstream_id, f"{room}/objective")
        else:
            valid_measures = [row for row in snapshot.measures
                              if _value(row, "outcome_commitment_id") in valid_outcomes
                              and _text(row, "metric_name") and _text(row, "unit")
                              and (_value(row, "target_amount") is not None or _value(row, "target_value") is not None)
                              and (_value(row, "baseline_amount") is not None
                                   or _value(row, "baseline_value") is not None
                                   or _text(row, "unavailable_reason"))]
            if not valid_measures:
                block("measure_definition_required", "Define a valid outcome measure.", "workstream", workstream_id, f"{room}/objective")
        if not _value(snapshot.workstream, "scope_expression"):
            block("scope_required", "Define the workstream scope.", "workstream", workstream_id, f"{room}/objective")
        if _value(snapshot.workstream, "target_date") is None and not _text(snapshot.workstream, "target_date_unavailable_reason"):
            block("target_date_required", "Record a target date or why it is unavailable.", "workstream", workstream_id, f"{room}/objective")

    @classmethod
    def _valid_waiver(cls, snapshot, row):
        authority = _value(row, "approver_id", _value(row, "waiver_authority_id"))
        reason = _text(row, "reason") or _text(row, "waiver_reason")
        expiry = _value(row, "expires_at", _value(row, "waiver_expires_at"))
        return bool(
            authority in snapshot.authorized_waiver_authority_ids
            and _value(row, "organization_id") == snapshot.programme.organization_id
            and reason
            and _current(expiry)
        )

    @classmethod
    def _valid_evidence_waiver(cls, snapshot, row):
        return bool(
            cls._valid_waiver(snapshot, row)
            and _value(row, "waiver_id") == _value(row, "id")
            and _value(row, "status") in {"declined", "expired"}
            and _value(row, "interim_accountable_id")
            and _value(row, "waived_at") is not None
        )

    @staticmethod
    def _effectively_fresh(record):
        expires_at = _value(record, "freshness_expires_at")
        return bool(
            _value(record, "freshness_status") in {"fresh", "not_applicable"}
            and (expires_at is None or _current(expires_at))
        )

    @classmethod
    def _current_evidence_state(cls, snapshot):
        from app.modules.transformation_room.evidence_service import (
            REQUIRED_EVIDENCE_CLAIMS,
            TransformationEvidenceService,
        )

        records_by_id = {
            _value(row, "id"): row
            for row in snapshot.evidence_records
            if _value(row, "id") is not None
        }
        current = tuple(
            records_by_id[current_id]
            for current_id in (
                _value(head, "current_record_id")
                for head in snapshot.active_evidence_heads
            )
            if current_id in records_by_id
        )
        resolutions = tuple(
            row
            for row in current
            if _value(row, "source_type") == "governance_resolution"
        )
        unresolved_conflict_ids = {
            _value(conflict, "id")
            for conflict in current
            if _value(conflict, "classification") == "conflict"
            and not any(
                _value(resolution, "subject_type")
                == _value(conflict, "subject_type")
                and _value(resolution, "subject_id")
                == _value(conflict, "subject_id")
                and _value(resolution, "claim_key")
                == _value(conflict, "claim_key")
                and _value(conflict, "id")
                in set(_value(resolution, "cited_evidence_ids", ()) or ())
                for resolution in resolutions
            )
        }
        head_current_by_key = {
            (
                _value(head, "subject_type"),
                _value(head, "subject_id"),
                _value(head, "claim_key"),
                _value(head, "source_identity"),
            ): _value(head, "current_record_id")
            for head in snapshot.active_evidence_heads
        }
        valid_by_candidate: dict[int, list[object]] = {}
        for request in snapshot.evidence_requests:
            accepted = records_by_id.get(_value(request, "accepted_evidence_id"))
            if (
                _value(request, "status") != "accepted"
                or accepted is None
                or head_current_by_key.get(
                    (
                        _value(accepted, "subject_type"),
                        _value(accepted, "subject_id"),
                        _value(accepted, "claim_key"),
                        _value(accepted, "source_identity"),
                    )
                )
                != _value(accepted, "id")
                or _value(accepted, "classification") == "conflict"
                or _value(accepted, "subject_type") != _value(request, "subject_type")
                or _value(accepted, "subject_id") != _value(request, "subject_id")
                or _value(accepted, "claim_key") != _value(request, "claim_key")
                or (
                    _value(request, "claim_key") in REQUIRED_EVIDENCE_CLAIMS
                    and not TransformationEvidenceService.record_satisfies_contract(
                        request, accepted
                    )
                )
            ):
                continue
            valid_by_candidate.setdefault(
                _value(request, "candidate_id"), []
            ).append(accepted)
        return current, unresolved_conflict_ids, valid_by_candidate

    @classmethod
    def _evaluate_discovery(cls, snapshot, block, room):
        workstream_id = snapshot.workstream.id
        candidates = [row for row in snapshot.accepted_candidates
                      if _value(row, "workstream_id") == workstream_id
                      and _value(row, "organization_id", snapshot.programme.organization_id) == snapshot.programme.organization_id
                      and _value(row, "inclusion_status") == "accepted"]
        if not candidates or any(_value(row, "subject_exists") is not True
                                 or _value(row, "duplicates_resolved") is not True
                                 for row in candidates):
            block("candidate_scope_incomplete", "Accept valid in-tenant candidates and resolve duplicates.", "workstream", workstream_id, f"{room}/discover")
            return
        current_records, unresolved, valid_by_candidate = (
            cls._current_evidence_state(snapshot)
        )
        for candidate in candidates:
            owner_evidence = any(
                _value(row, "claim_key") == "application_owner"
                and cls._effectively_fresh(row)
                and not any(
                    _value(conflict, "id") in unresolved
                    and _value(conflict, "subject_type")
                    == _value(row, "subject_type")
                    and _value(conflict, "subject_id") == _value(row, "subject_id")
                    and _value(conflict, "claim_key") == _value(row, "claim_key")
                    for conflict in current_records
                )
                for row in valid_by_candidate.get(candidate.id, ())
            )
            owner_waiver = any(_value(row, "candidate_id") == candidate.id
                               and _value(row, "claim_key") == "application_owner"
                               and cls._valid_evidence_waiver(snapshot, row)
                               and _value(row, "interim_accountable_id")
                               for row in snapshot.evidence_waivers)
            if not owner_evidence and not owner_waiver:
                block("application_owner_evidence_required", "Resolve application ownership with accepted evidence or a controlled waiver.", "candidate", candidate.id, f"{room}/evidence")

    @classmethod
    def _evaluate_evidence(cls, snapshot, block, room):
        workstream_id = snapshot.workstream.id
        required_claims = {"application_owner", "lifecycle", "cost", "business_criticality",
                           "capability_impact", "dependency_impact", "risk", "source_freshness"}
        candidates = [row for row in snapshot.accepted_candidates if _value(row, "workstream_id") == workstream_id]
        current_records, unresolved_conflict_ids, valid_by_candidate = (
            cls._current_evidence_state(snapshot)
        )
        valid_accepted_ids = {
            _value(row, "id")
            for rows in valid_by_candidate.values()
            for row in rows
        }
        valid_waived_request_ids = {
            _value(row, "id")
            for row in snapshot.evidence_waivers
            if cls._valid_evidence_waiver(snapshot, row)
        }
        requests_complete = bool(snapshot.evidence_requests) and all(
            not _value(row, "required", False)
            or (
                _value(row, "status") == "accepted"
                and _value(row, "accepted_evidence_id") in valid_accepted_ids
            )
            or (
                _value(row, "status") in {"declined", "expired"}
                and _value(row, "id") in valid_waived_request_ids
            )
            for row in snapshot.evidence_requests)
        if not requests_complete:
            block("required_evidence_incomplete", "Complete or explicitly acknowledge every required evidence request.", "workstream", workstream_id, f"{room}/evidence")
        for candidate in candidates:
            records = list(valid_by_candidate.get(candidate.id, ()))
            claims = {_value(row, "claim_key") for row in records}
            if "application_owner" not in claims:
                block("application_owner_evidence_required", "Resolve application ownership.", "candidate", candidate.id, f"{room}/evidence")
            if not required_claims.issubset(claims):
                block("evidence_dimension_incomplete", "Evaluate every required evidence dimension.", "candidate", candidate.id, f"{room}/evidence")
            candidate_subject = (
                _value(candidate, "subject_type"),
                _value(candidate, "subject_id"),
            )
            if any(
                _value(row, "id") in unresolved_conflict_ids
                and (
                    _value(row, "subject_type"),
                    _value(row, "subject_id"),
                )
                == candidate_subject
                for row in current_records
            ):
                block("evidence_conflict_unresolved", "Resolve or explicitly govern evidence conflicts.", "candidate", candidate.id, f"{room}/evidence")
            if any(
                (
                    _value(row, "subject_type"),
                    _value(row, "subject_id"),
                )
                == candidate_subject
                and _value(row, "classification") != "conflict"
                and _value(row, "source_type") != "governance_resolution"
                and not cls._effectively_fresh(row)
                for row in current_records
            ):
                block("evidence_freshness_invalid", "Refresh or acknowledge stale evidence.", "candidate", candidate.id, f"{room}/evidence")

    @classmethod
    def _evaluate_options(cls, snapshot, block, room):
        workstream_id = snapshot.workstream.id
        ordered = sorted(
            (
                row
                for row in snapshot.option_versions
                if _value(row, "workstream_id") == workstream_id
            ),
            key=lambda row: (
                _value(row, "option_id", _value(row, "id")),
                -int(_value(row, "version", 1)),
            ),
        )
        latest = {}
        for row in ordered:
            latest.setdefault(_value(row, "option_id", _value(row, "id")), row)
        options = list(latest.values())
        exception = any(_value(row, "workstream_id") == workstream_id and _text(row, "reason")
                        and _value(row, "authority_id") and _value(row, "constraint_type") in {"policy", "legal"}
                        for row in snapshot.option_exceptions)
        corrupt = []
        for row in options:
            if isinstance(row, TransformationOptionVersion):
                from app.modules.transformation_room.decision_service import (
                    TransformationOptionService,
                )

                if not TransformationOptionService.verify_version_hash(row):
                    corrupt.append(row)
        if corrupt:
            block(
                "option_integrity_invalid",
                "Replace corrupted immutable option versions.",
                "workstream",
                workstream_id,
                f"{room}/options",
            )
            return
        hashes = {
            (
                json.dumps(
                    _value(row, "content_json"),
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if _value(row, "content_json")
                else f"projection:{_value(row, 'content_hash')}"
            )
            for row in options
            if _value(row, "immutable") is True
            and _value(row, "content_hash")
        }
        if len(hashes) < 2 and not (len(hashes) == 1 and exception):
            block("viable_options_required", "Freeze two distinct options or a reasoned policy/legal exception.", "workstream", workstream_id, f"{room}/options")
        required_fields = ("assumptions", "risks", "dependencies", "reversibility",
                           "transition_approach", "affected_capability_ids",
                           "affected_value_stream_ids", "recommendation_rationale")
        incomplete_contract = any(
               _value(row, "immutable") is not True or not _value(row, "content_hash")
               or any(not _value(row, field) for field in required_fields)
               or _value(row, "currency") not in ISO_4217_CURRENCIES
               or _value(row, "technology_required") is None
               or any(_value(row, field) is None for field in ("benefit_min", "benefit_max", "cost_min", "cost_max"))
               for row in options
        )
        if incomplete_contract:
            block("option_contract_incomplete", "Complete every immutable option contract.", "workstream", workstream_id, f"{room}/options")
        elif any(not _finite(_value(row, field)) for row in options
                 for field in ("benefit_min", "benefit_max", "cost_min", "cost_max")):
            block("option_value_invalid", "Option values must be finite.", "workstream", workstream_id, f"{room}/options")
        elif any(_value(row, "benefit_min") > _value(row, "benefit_max")
                 or _value(row, "cost_min") > _value(row, "cost_max") for row in options):
            block("option_value_invalid", "Option ranges must be ordered.", "workstream", workstream_id, f"{room}/options")

    @classmethod
    def _valid_briefs(cls, snapshot):
        evidence_ids = {_value(row, "id") for row in snapshot.evidence_records}
        option_ids = {_value(row, "id") for row in snapshot.option_versions}
        latest = {}
        for row in sorted(
            snapshot.brief_versions,
            key=lambda item: (
                _value(item, "brief_id"),
                -int(_value(item, "version", 1)),
            ),
        ):
            latest.setdefault(_value(row, "brief_id"), row)
        valid = []
        for row in latest.values():
            if isinstance(row, DecisionBriefVersion):
                from app.modules.transformation_room.decision_service import (
                    DecisionBriefService,
                )

                if not DecisionBriefService.verify_hash(row):
                    continue
            if (
                _value(row, "workstream_id") == snapshot.workstream.id
                and _value(row, "immutable") is True
                and _value(row, "content_hash")
                and _value(row, "policy_version") == cls.POLICY_VERSION
                and bool(_value(row, "cited_evidence_ids", ()))
                and set(_value(row, "cited_evidence_ids", ())).issubset(evidence_ids)
                and bool(_value(row, "option_version_ids", ()))
                and set(_value(row, "option_version_ids", ())).issubset(option_ids)
            ):
                valid.append(row)
        return valid

    @classmethod
    def _evaluate_brief(cls, snapshot, block, room):
        workstream_id = snapshot.workstream.id
        briefs = cls._valid_briefs(snapshot)
        if not briefs:
            block("immutable_brief_required", "Freeze a current evidence-cited decision brief.", "workstream", workstream_id, f"{room}/decision")
            return
        brief = briefs[-1]
        if not _value(brief, "submitted_by_id") or _value(brief, "submitter_authorized") is not True:
            block("brief_submitter_not_authorised", "Record an authorised brief submitter.", "brief", brief.id, f"{room}/decision")
        if _value(brief, "human_reviewed_ai") is not True:
            block("human_review_required", "Record explicit human review of AI-authored material.", "brief", brief.id, f"{room}/decision")
        if not _value(brief, "decision_authority_id"):
            block("decision_authority_required", "Name the decision authority.", "brief", brief.id, f"{room}/decision")
        if _value(brief, "blockers_cleared") is not True:
            block("decision_blockers_open", "Clear all decision blockers.", "brief", brief.id, f"{room}/decision")
        if _value(brief, "unknowns_acknowledged") is not True:
            block("decision_unknowns_unacknowledged", "Acknowledge non-blocking unknowns.", "brief", brief.id, f"{room}/decision")

    @classmethod
    def _evaluate_governance(cls, snapshot, transition, block, room):
        workstream_id = snapshot.workstream.id
        briefs = cls._valid_briefs(snapshot)
        brief_ids = {_value(row, "id") for row in briefs}
        subject_ids = {_value(row, "brief_id") for row in briefs}
        decisions = {"evidence": "returned_for_evidence", "options": "returned_for_options",
                     "approved": "approved", "approved_with_conditions": "approved_with_conditions",
                     "rejected": "rejected"}
        matching = [cycle for cycle in snapshot.arb_cycles
                    if _value(cycle, "workstream_id") == workstream_id
                    and _value(cycle, "subject_type") == "decision_brief"
                    and cls._cycle_brief_version_id(cycle) in brief_ids
                    and _value(cycle, "subject_id") in subject_ids
                    and _value(cycle, "brief_id") == _value(cycle, "subject_id")
                    and _value(cycle, "status") in {"decided", "terminal"}
                    and _value(cycle, "decision") == decisions[transition.target]
                    and _value(cycle, "target_stage") == transition.target
                    and _value(cycle, "decision_maker_id") and _text(cycle, "rationale")
                    and _value(cycle, "decided_at")]
        if not matching:
            block("arb_decision_mismatch", "Record a terminal ARB decision for the submitted brief and target stage.", "workstream", workstream_id, f"{room}/governance")
            return
        linked = [row for row in snapshot.arb_conditions if _value(row, "arb_cycle_id") == matching[-1].id]
        if transition.target == "approved" and linked:
            block("arb_decision_mismatch", "A decision with conditions must project to approved with conditions.", "workstream", workstream_id, f"{room}/governance")
        if transition.target == "approved_with_conditions" and not linked:
            block("arb_decision_mismatch", "Conditional approval requires persisted conditions.", "workstream", workstream_id, f"{room}/governance")

    @classmethod
    def _evaluate_conditions(cls, snapshot, block, room):
        workstream_id = snapshot.workstream.id
        cycles = cls._matching_governance_cycles(snapshot, "approved_with_conditions")
        if not cycles:
            block("arb_decision_mismatch", "A matching terminal conditional approval is required.", "workstream", workstream_id, f"{room}/governance")
            return
        if not cls._conditions_resolved(snapshot, cycles[-1]):
            block("arb_conditions_open", "Fulfil or authoritatively waive every ARB condition.", "workstream", workstream_id, f"{room}/governance")

    @classmethod
    def _matching_governance_cycles(cls, snapshot, decision):
        briefs = cls._valid_briefs(snapshot)
        brief_ids = {_value(row, "id") for row in briefs}
        subject_ids = {_value(row, "brief_id") for row in briefs}
        return [
            cycle
            for cycle in snapshot.arb_cycles
            if _value(cycle, "workstream_id") == snapshot.workstream.id
            and _value(cycle, "subject_type") == "decision_brief"
            and cls._cycle_brief_version_id(cycle) in brief_ids
            and _value(cycle, "subject_id") in subject_ids
            and _value(cycle, "brief_id") == _value(cycle, "subject_id")
            and _value(cycle, "status") in {"decided", "terminal"}
            and _value(cycle, "decision") == decision
            and _value(cycle, "target_stage") == decision
            and _value(cycle, "decision_maker_id")
            and _text(cycle, "rationale")
            and _value(cycle, "decided_at")
        ]

    @staticmethod
    def _cycle_brief_version_id(cycle):
        return _value(
            cycle,
            "decision_brief_version_id",
            _value(cycle, "brief_version_id"),
        )

    @classmethod
    def _conditions_resolved(cls, snapshot, cycle):
        accepted_evidence_ids = {
            _value(row, "id")
            for row in snapshot.evidence_records
            if _value(row, "status") == "accepted"
        }
        accepted_evidence_ids.update(
            _value(row, "current_record_id")
            for row in snapshot.active_evidence_heads
            if _value(row, "current_record_id") is not None
        )
        conditions = [
            row
            for row in snapshot.arb_conditions
            if _value(row, "arb_cycle_id") == _value(cycle, "id")
        ]
        return bool(conditions) and all(
            (
                _value(row, "status") == "fulfilled"
                and _value(row, "accepted_evidence_id") in accepted_evidence_ids
            )
            or (
                _value(row, "status") == "waived"
                and cls._valid_condition_waiver(snapshot, row, cycle)
            )
            for row in conditions
        )

    @classmethod
    def _valid_condition_waiver(cls, snapshot, condition, cycle):
        return bool(
            cls._valid_waiver(snapshot, condition)
            and _value(condition, "waiver_condition_id") == _value(condition, "id")
            and _value(condition, "waiver_arb_cycle_id") == _value(cycle, "id")
            and _value(condition, "waiver_subject_type") == _value(cycle, "subject_type")
            and _value(condition, "waiver_subject_id") == _value(cycle, "subject_id")
        )

    @classmethod
    def _evaluate_execution(cls, snapshot, block, room):
        workstream_id = snapshot.workstream.id
        approved_cycles = cls._matching_governance_cycles(snapshot, "approved")
        conditional_cycles = [
            cycle
            for cycle in cls._matching_governance_cycles(snapshot, "approved_with_conditions")
            if _value(snapshot.workstream, "lifecycle_stage") == "approved"
            and cls._conditions_resolved(snapshot, cycle)
        ]
        approved_versions = {
            cls._cycle_brief_version_id(row)
            for row in (*approved_cycles, *conditional_cycles)
        }
        work_packages = {_value(row, "id"): row for row in snapshot.work_packages
                         if _value(row, "programme_workstream_id", _value(row, "workstream_id")) == workstream_id
                         and _value(row, "owner_id")
                         and _value(row, "decision_brief_version_id") in approved_versions}
        roadmap_ids = {_value(row, "id") for row in snapshot.roadmap_items
                       if _value(row, "programme_workstream_id") == workstream_id
                       and _value(row, "work_package_id") in work_packages
                       and _value(row, "decision_brief_version_id") in approved_versions}
        action_ok = bool(snapshot.approved_actions) and all(
            _value(row, "workstream_id") == workstream_id
            and _value(row, "decision_brief_version_id") in approved_versions
            and ((_value(row, "status") == "declined" and _text(row, "decline_reason"))
                 or (_value(row, "status") == "accepted" and _value(row, "owner_id")
                     and _value(row, "work_package_id") in work_packages
                     and (not _value(row, "scheduling_applicable", False)
                          or _value(row, "roadmap_item_id") in roadmap_ids)))
            for row in snapshot.approved_actions)
        if not action_ok:
            block("approved_action_unresolved", "Resolve every approved action into declined rationale or owned scheduled work.", "workstream", workstream_id, f"{room}/execute")
        relevant_benefits = [
            row
            for row in snapshot.benefits
            if _value(row, "programme_workstream_id") == workstream_id
            and _value(row, "strategic_initiative_id") == snapshot.programme.id
        ]
        if any(
            _value(row, field) is not None and not _finite(_value(row, field))
            for row in relevant_benefits
            for field in ("baseline_value", "target_value")
        ):
            block(
                "benefit_value_invalid",
                "Benefit baseline and target values must be finite.",
                "workstream",
                workstream_id,
                f"{room}/outcomes",
            )
            return
        outcome_ids = {
            _value(row, "id")
            for row in snapshot.outcomes
            if _value(row, "owner_id") and _text(row, "statement")
        }
        benefit_outcomes = set()
        for benefit in relevant_benefits:
            outcome_id = _value(benefit, "outcome_commitment_id")
            measures = [
                measure
                for measure in snapshot.measures
                if _value(measure, "outcome_commitment_id") == outcome_id
                and _text(measure, "metric_name") == _text(benefit, "measure")
            ]
            matching_measure = next(
                (
                    measure
                    for measure in measures
                    if _text(measure, "unit")
                    and cls._measure_target(measure) is not None
                    and _finite(cls._measure_target(measure))
                    and (cls._measure_baseline(measure) is not None
                         or _text(measure, "unavailable_reason"))
                    and (
                        (
                            _text(measure, "unit") == _text(benefit, "unit")
                            and cls._measure_target(measure)
                            == _value(benefit, "target_value")
                            and (
                                _value(benefit, "baseline_value") is not None
                                or cls._measure_baseline(measure) is None
                            )
                        )
                        or (
                            _value(benefit, "unit") is None
                            and _value(benefit, "baseline_value") is None
                            and _value(benefit, "target_value") is None
                        )
                    )
                ),
                None,
            )
            if (
                _value(benefit, "programme_workstream_id") == workstream_id
                and _value(benefit, "strategic_initiative_id") == snapshot.programme.id
                and outcome_id in outcome_ids
                and _value(benefit, "decision_brief_version_id") in approved_versions
                and _value(benefit, "work_package_id") in work_packages
                and _value(benefit, "owner_id")
                and matching_measure is not None
                and _text(benefit, "measurement_method")
            ):
                benefit_outcomes.add(outcome_id)
        if not outcome_ids or not outcome_ids.issubset(benefit_outcomes):
            block("benefit_contract_incomplete", "Link each outcome to a complete canonical Benefit measurement contract.", "workstream", workstream_id, f"{room}/outcomes")

    @staticmethod
    def _measure_baseline(measure):
        return (
            _value(measure, "baseline_amount")
            if _value(measure, "baseline_amount") is not None
            else _value(measure, "baseline_value")
        )

    @staticmethod
    def _measure_target(measure):
        return (
            _value(measure, "target_amount")
            if _value(measure, "target_amount") is not None
            else _value(measure, "target_value")
        )

    @classmethod
    def _evaluate_delivery(cls, snapshot, block, room):
        workstream_id = snapshot.workstream.id
        if not any(_value(row, "workstream_id") == workstream_id
                   and _value(row, "completion_evidence_id") and _text(row, "residual_risk")
                   and _value(row, "operational_owner_id") and _text(row, "measurement_schedule")
                   for row in snapshot.delivery_records):
            block("delivery_completion_required", "Accept delivery evidence, residual risk, operational ownership and a measurement schedule.", "workstream", workstream_id, f"{room}/execute")

    @classmethod
    def _evaluate_completion(cls, snapshot, block, room):
        workstream_id = snapshot.workstream.id
        outcome_ids = {_value(row, "id") for row in snapshot.outcomes}
        benefits = [row for row in snapshot.benefits
                    if _value(row, "programme_workstream_id") == workstream_id
                    and _value(row, "outcome_commitment_id") in outcome_ids]
        measured = {(_value(row, "benefit_id"), _value(row, "outcome_commitment_id"))
                    for row in snapshot.measurements
                    if _value(row, "valid") is True and _value(row, "observed_at")
                    and ((_value(row, "value") is not None and _finite(_value(row, "value")))
                         or (_value(row, "value") is None and _text(row, "unavailable_reason")))}
        expected = {(_value(row, "id"), _value(row, "outcome_commitment_id")) for row in benefits}
        if not expected or not expected.issubset(measured):
            block("outcome_measurement_required", "Record a valid actual or explicit not-measurable result for each Benefit.", "workstream", workstream_id, f"{room}/outcomes")
        reviewed = {_value(row, "outcome_commitment_id") for row in snapshot.outcome_reviews
                    if _value(row, "workstream_id") == workstream_id
                    and _value(row, "judgement") in {"realised", "not_realised"}
                    and _text(row, "lessons") and _text(row, "follow_up_decision")}
        if not outcome_ids.issubset(reviewed):
            block("outcome_review_required", "Record judgement, lessons and follow-up for each outcome.", "workstream", workstream_id, f"{room}/outcomes")

    @classmethod
    def _evaluate_reframe(cls, snapshot, block, room):
        workstream_id = snapshot.workstream.id
        if not any(_value(row, "workstream_id") == workstream_id
                   and _value(row, "decision") == "reframe_authorised"
                   and _value(row, "target_stage") == "options"
                   and _value(row, "decision_maker_id") and _text(row, "rationale")
                   and _value(row, "decided_at") for row in snapshot.arb_cycles):
            block("reframe_authorisation_required", "Record decision-authority approval to reframe rejected options.", "workstream", workstream_id, f"{room}/governance")

    @classmethod
    def authorise_transition(cls, workstream_id, target_stage, expected_revision) -> OperationAuthorizer:
        expected_key = f"transition:{workstream_id}:{expected_revision}:{target_stage}"

        def authorize(session, actor, operation, natural_key):
            if operation != "workstream.transition" or natural_key != expected_key:
                raise NotAuthorised("transition_command_mismatch")
            workstream = session.execute(select(ProgrammeWorkstream).where(
                ProgrammeWorkstream.id == workstream_id,
                ProgrammeWorkstream.organization_id == actor.organization_id,
            )).scalar_one_or_none()
            if workstream is None:
                raise NotFound("workstream_not_found")
            programme = cls._programme_statement(
                session, actor, workstream.programme_id, lock=False
            ).scalar_one_or_none()
            if programme is None:
                raise NotFound("programme_not_found")
            TransformationProgrammeService._require_active_programme(programme)
            source_stage = workstream.lifecycle_stage
            materialisation = session.scalar(
                select(CommandMaterialisation).where(
                    CommandMaterialisation.organization_id == actor.organization_id,
                    CommandMaterialisation.operation == "workstream.transition",
                    CommandMaterialisation.natural_key == expected_key,
                )
            )
            if materialisation is not None:
                if materialisation.actor_id != actor.user_id:
                    raise NotAuthorised("transition_result_not_owned")
                source_stage = cls._materialised_transition_source(
                    materialisation,
                    workstream_id=workstream_id,
                    target_stage=target_stage,
                    expected_revision=expected_revision,
                )
            TransformationProgrammeService._require_programme_authority(
                session, actor, workstream.programme_id, workstream.id,
                cls._transition_roles(source_stage, target_stage),
                "transition_not_authorised",
            )
        return authorize

    @classmethod
    def _materialised_transition_source(
        cls,
        materialisation: CommandMaterialisation,
        *,
        workstream_id: int,
        target_stage: str,
        expected_revision: int,
    ) -> str:
        """Recover the immutable edge without trusting the live projection."""
        events = materialisation.outbox_events
        if not isinstance(events, list) or len(events) != 1:
            raise CommandConflict("transition_materialisation_invalid")
        event = events[0]
        payload = event.get("payload") if isinstance(event, Mapping) else None
        if (
            event.get("event_type") != "workstream.transitioned"
            or not isinstance(payload, Mapping)
            or payload.get("workstream_id") != workstream_id
            or payload.get("lifecycle_stage") != target_stage
            or payload.get("before_revision") != expected_revision
        ):
            raise CommandConflict("transition_materialisation_invalid")
        source_stage = payload.get("source_stage")
        if not isinstance(source_stage, str):
            raise CommandConflict("transition_materialisation_invalid")
        transition = cls.require_valid_transition(source_stage, target_stage)
        return transition.source

    @classmethod
    def _transition_roles(cls, source, target):
        if source == "in_governance":
            return CREATE_ROLES | frozenset({"decision_authority", "arb_member"})
        if source in {"approved", "approved_with_conditions", "execute"}:
            return CREATE_ROLES | frozenset({"programme_owner", "delivery_lead", "workstream_lead"})
        if source == "outcomes":
            return CREATE_ROLES | frozenset({"programme_owner", "outcome_owner", "workstream_lead"})
        return OBJECTIVE_ROLES

    @classmethod
    def _locked_transition(cls, session, actor, request, claim):
        snapshot = cls._load_policy_snapshot(
            session=session, actor=actor, workstream_id=request["workstream_id"], lock=True
        )
        return cls.apply_locked_transition(
            session,
            actor,
            snapshot=snapshot,
            target_stage=request["target_stage"],
            expected_revision=request["expected_revision"],
        )

    @classmethod
    def apply_locked_transition(
        cls, session, actor, *, snapshot, target_stage, expected_revision
    ):
        """Apply the canonical gate to a snapshot already locked by its caller."""
        workstream = snapshot.workstream
        if workstream.revision != expected_revision:
            raise CommandConflict("stale_revision")
        transition = cls.require_valid_transition(
            workstream.lifecycle_stage, target_stage
        )
        TransformationProgrammeService._require_programme_authority(
            session, actor, workstream.programme_id, workstream.id,
            cls._transition_roles(transition.source, transition.target),
            "transition_not_authorised",
            lock=True,
        )
        blockers, warnings, evidence_ids = cls.evaluate_requirements(snapshot, transition)
        if blockers:
            raise BlockedByEvidence("gate_requirements_not_met", blockers=tuple(blockers),
                                    warnings=tuple(warnings), policy_version=cls.POLICY_VERSION)
        before_revision = workstream.revision
        workstream.lifecycle_stage = transition.target
        workstream.revision += 1
        session.flush()
        response = {"workstream_id": workstream.id, "lifecycle_stage": workstream.lifecycle_stage,
                    "revision": workstream.revision, "policy_version": cls.POLICY_VERSION}
        return DomainMutationResult(
            response, response,
            ({"event_type": "workstream.transitioned",
              "payload": {**response, "source_stage": transition.source,
                          "before_revision": before_revision,
                          "evidence_ids": sorted(evidence_ids)}},),
        )

    @classmethod
    def page_for_stage(cls, stage: str) -> str:
        """Name the room page that shows a lifecycle stage."""
        return cls.STAGE_PAGES.get(stage, stage)

    @classmethod
    def next_action(cls, *, actor: ActorContext, workstreams: Sequence[ProgrammeWorkstream]):
        for workstream in sorted(
            workstreams,
            key=lambda row: (cls.STAGE_ORDER.get(row.lifecycle_stage, -1), row.id),
        ):
            if workstream.lifecycle_stage in cls.TERMINAL_STAGES:
                continue
            target = cls.NEXT_STAGE.get(workstream.lifecycle_stage)
            if target is None:
                return GateBlocker("lifecycle_action_required", "Choose the next governed lifecycle action.",
                                   "workstream", workstream.id,
                                   f"/solutions/programmes/{workstream.programme_id}/workstreams/{workstream.id}/{cls.page_for_stage(workstream.lifecycle_stage)}")
            gate = cls.evaluate(actor=actor, workstream_id=workstream.id, target_stage=target)
            if gate.blockers:
                return gate.blockers[0]
            # F-07, Capgemini dry-run: this used workstream.lifecycle_stage
            # (the CURRENT stage) here, so "Advance this workstream to X"
            # always linked back to the page the user was already on. `target`
            # is the stage the message actually names.
            return GateBlocker("transition_ready", f"Advance this workstream to {target.replace('_', ' ')}.",
                               "workstream", workstream.id,
                               f"/solutions/programmes/{workstream.programme_id}/workstreams/{workstream.id}/{cls.page_for_stage(target)}")
        return None


__all__ = ["PolicySnapshot", "TransformationGateService", "Transition"]

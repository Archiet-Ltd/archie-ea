"""Tenant-scoped, nullable read projections for the Transformation Room.

These projections deliberately do not turn absent later-wave services into
working features.  They expose the durable Task 1--7 records that exist and
carry a reason beside every value that cannot yet be measured.
"""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import db
from app.models.strategic import StrategicInitiative
from app.models.transformation_decision import (
    DecisionBrief,
    DecisionBriefVersion,
    TransformationOption,
    TransformationOptionVersion,
)
from app.models.transformation_evidence import (
    CandidateSignal,
    EvidenceRecord,
    EvidenceRequest,
    TransformationCandidate,
)
from app.models.transformation_programme import (
    MeasureDefinition,
    ProgrammeOutcomeCommitment,
    ProgrammeWorkstream,
)
from app.models.user import ROLE_CTO, ROLE_ENTERPRISE_ARCHITECT, User
from app.modules.transformation_room.decision_service import (
    DecisionBriefService,
    TransformationOptionService,
)
from app.modules.transformation_room.domain import (
    ActorContext,
    NotAuthorised,
    NotFound,
    StageView,
    TransformationPortfolioView,
    TypedEvidenceValue,
)
from app.modules.transformation_room.evidence_service import sha256_canonical
from app.modules.transformation_room.gate_service import TransformationGateService
from app.modules.transformation_room.programme_service import TransformationProgrammeService


STAGE_ROUTES = (
    "objective",
    "discover",
    "evidence",
    "options",
    "decision",
    "execute",
    "outcomes",
)

STAGE_LABELS = {
    "objective": "Objective",
    "discover": "Discover",
    "evidence": "Evidence",
    "options": "Options",
    "decision": "Decision",
    "execute": "Execute",
    "outcomes": "Outcomes",
}

IMPLEMENTED_STAGES = frozenset({"objective", "discover", "evidence", "options"})
LATER_STAGE_REASON = (
    "The governed decision, execution and outcome services are not available "
    "in this release. This page is a stable, inspectable deep link only."
)


def _json_scalar(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _row(row: Any, *fields: str) -> dict[str, Any]:
    return {field: _json_scalar(getattr(row, field, None)) for field in fields}


class TransformationRoomReadModel:
    """Build one room page without mutating domain state."""

    @staticmethod
    def next_stage(stage: str) -> str | None:
        """Use the gate service as the single transition authority."""
        return TransformationGateService.NEXT_STAGE.get(stage)

    STAGE_RESOURCE_KEYS = {
        "objective": ("outcomes", "measures"),
        "discover": ("candidates", "signals"),
        "evidence": ("requests", "evidence"),
        "options": ("options", "option_versions"),
        "decision": ("briefs", "brief_versions"),
        "execute": (),
        "outcomes": (),
    }

    @classmethod
    def stage_resource_states(cls, stage, *, state, reason):
        """Return the complete shape every stage template may dereference."""
        keys = ("stage", *cls.STAGE_RESOURCE_KEYS[stage])
        return {key: {"state": state, "reason": reason} for key in keys}

    @classmethod
    def empty_stage_resources(cls, stage):
        return {key: () for key in cls.STAGE_RESOURCE_KEYS[stage]}

    @classmethod
    def stage_resource_projection(cls, stage, *, state, reason):
        """Build a structurally complete non-data projection for any stage."""
        keys = ("stage", *cls.STAGE_RESOURCE_KEYS[stage])
        return (
            cls.empty_stage_resources(stage),
            cls.stage_resource_states(stage, state=state, reason=reason),
            {key: reason for key in keys},
        )

    @classmethod
    def stage(
        cls,
        *,
        actor: ActorContext,
        programme_id: int,
        workstream_id: int,
        stage: str,
    ) -> StageView:
        if stage not in STAGE_ROUTES:
            raise NotFound("stage_not_found")
        programme = TransformationProgrammeService.get_programme(
            actor=actor, programme_id=programme_id
        )
        if workstream_id not in programme.workstream_ids:
            raise NotFound("workstream_not_found")
        try:
            resources, resource_states = cls.load_stage_resources(
                actor=actor, workstream_id=workstream_id, stage=stage
            )
        except SQLAlchemyError:
            reason = "Stage resources could not be loaded. Try again later."
            resources, resource_states, unavailable_reasons = (
                cls.stage_resource_projection(stage, state="failed", reason=reason)
            )
            return StageView(
                programme=programme,
                workstream_id=workstream_id,
                stage=stage,
                gate=None,
                resources=resources,
                resource_states=resource_states,
                unavailable_reasons=unavailable_reasons,
            )
        current_stage = resources["workstream"][0]["lifecycle_stage"]
        gate = None
        target_stage = cls.next_stage(stage)
        if stage in IMPLEMENTED_STAGES and current_stage == stage and target_stage:
            gate = TransformationGateService.evaluate(
                actor=actor,
                workstream_id=workstream_id,
                target_stage=target_stage,
            )
        return StageView(
            programme=programme,
            workstream_id=workstream_id,
            stage=stage,
            gate=gate,
            resources=resources,
            resource_states=resource_states,
            unavailable_reasons=cls.unavailable_reasons(
                stage, resources, resource_states
            ),
        )

    @classmethod
    def load_stage_resources(
        cls, *, actor: ActorContext, workstream_id: int, stage: str
    ) -> tuple[
        Mapping[str, tuple[Mapping[str, Any], ...]],
        Mapping[str, Mapping[str, str | None]],
    ]:
        """Dispatch through a closed map; every query repeats tenant membership."""
        loaders = {
            "objective": cls._objective_resources,
            "discover": cls._discover_resources,
            "evidence": cls._evidence_resources,
            "options": cls._options_resources,
            "decision": cls._decision_resources,
            "execute": cls._later_resources,
            "outcomes": cls._later_resources,
        }
        try:
            loader = loaders[stage]
        except KeyError as exc:
            raise NotFound("stage_not_found") from exc
        with Session(db.engine) as session:
            workstream = session.scalar(
                select(ProgrammeWorkstream).where(
                    ProgrammeWorkstream.id == workstream_id,
                    ProgrammeWorkstream.organization_id == actor.organization_id,
                )
            )
            if workstream is None:
                raise NotFound("workstream_not_found")
            programme = session.scalar(
                select(StrategicInitiative).where(
                    StrategicInitiative.id == workstream.programme_id,
                    StrategicInitiative.organization_id == actor.organization_id,
                    StrategicInitiative.record_kind == "transformation_programme",
                )
            )
            if programme is None:
                raise NotFound("programme_not_found")
            loaded = loader(session, actor, workstream)
            explicit_states = loaded.pop("_resource_states", {})
            resources = {
                "workstream": (
                    _row(
                        workstream,
                        "id",
                        "programme_id",
                        "workstream_type",
                        "objective",
                        "scope_expression",
                        "lifecycle_stage",
                        "lead_id",
                        "target_date",
                        "target_date_unavailable_reason",
                        "revision",
                        "name",
                        "archimate_element_id",
                    ),
                ),
                **loaded,
            }
            return resources, cls.resource_states(
                stage, resources, explicit_states
            )

    @staticmethod
    def _objective_resources(session, actor, workstream):
        outcomes = session.scalars(
            select(ProgrammeOutcomeCommitment)
            .where(
                ProgrammeOutcomeCommitment.organization_id == actor.organization_id,
                ProgrammeOutcomeCommitment.programme_id == workstream.programme_id,
                ProgrammeOutcomeCommitment.workstream_id == workstream.id,
            )
            .order_by(ProgrammeOutcomeCommitment.id)
        ).all()
        outcome_ids = [row.id for row in outcomes]
        measures = session.scalars(
            select(MeasureDefinition)
            .where(
                MeasureDefinition.organization_id == actor.organization_id,
                MeasureDefinition.outcome_commitment_id.in_(outcome_ids or [-1]),
            )
            .order_by(MeasureDefinition.id)
        ).all()
        return {
            "outcomes": tuple(
                _row(
                    row,
                    "id",
                    "statement",
                    "owner_id",
                    "improvement_direction",
                    "target_date",
                    "lifecycle",
                )
                for row in outcomes
            ),
            "measures": tuple(
                {
                    **_row(
                        row,
                        "id",
                        "outcome_commitment_id",
                        "metric_name",
                        "unit",
                        "currency",
                        "aggregation",
                        "unavailable_reason",
                        "target_date",
                    ),
                    "baseline_value": _json_scalar(
                        row.baseline_amount
                        if row.currency and row.baseline_amount is not None
                        else row.baseline_value
                    ),
                    "target_value": _json_scalar(
                        row.target_amount
                        if row.currency and row.target_amount is not None
                        else row.target_value
                    ),
                }
                for row in measures
            ),
        }

    @staticmethod
    def _discover_resources(session, actor, workstream):
        candidates = session.scalars(
            select(TransformationCandidate)
            .where(
                TransformationCandidate.organization_id == actor.organization_id,
                TransformationCandidate.workstream_id == workstream.id,
            )
            .order_by(TransformationCandidate.id)
        ).all()
        candidate_ids = [row.id for row in candidates]
        signals = session.scalars(
            select(CandidateSignal)
            .where(
                CandidateSignal.organization_id == actor.organization_id,
                CandidateSignal.candidate_id.in_(candidate_ids or [-1]),
            )
            .order_by(CandidateSignal.id)
        ).all()
        return {
            "candidates": tuple(
                _row(
                    row,
                    "id",
                    "subject_type",
                    "subject_id",
                    "inclusion_status",
                    "inclusion_reason",
                    "accepted_at",
                )
                for row in candidates
            ),
            "signals": tuple(
                _row(row, "id", "candidate_id", "rule_code", "evaluated_at")
                for row in signals
            ),
        }

    @staticmethod
    def _evidence_resources(session, actor, workstream):
        requests = session.scalars(
            select(EvidenceRequest)
            .where(
                EvidenceRequest.organization_id == actor.organization_id,
                EvidenceRequest.workstream_id == workstream.id,
            )
            .order_by(EvidenceRequest.id)
        ).all()
        evidence = session.scalars(
            select(EvidenceRecord)
            .where(
                EvidenceRecord.organization_id == actor.organization_id,
                EvidenceRecord.workstream_id == workstream.id,
            )
            .order_by(EvidenceRecord.id)
        ).all()
        evidence_rows, evidence_state = TransformationRoomReadModel.project_evidence(
            evidence
        )
        return {
            "requests": tuple(
                _row(
                    row,
                    "id",
                    "claim_key",
                    "assigned_to_id",
                    "required",
                    "status",
                    "due_at",
                )
                for row in requests
            ),
            "evidence": evidence_rows,
            "_resource_states": {"evidence": evidence_state},
        }

    @staticmethod
    def _options_resources(session, actor, workstream):
        options = session.scalars(
            select(TransformationOption)
            .where(
                TransformationOption.organization_id == actor.organization_id,
                TransformationOption.workstream_id == workstream.id,
            )
            .order_by(TransformationOption.id)
        ).all()
        versions = session.scalars(
            select(TransformationOptionVersion)
            .where(
                TransformationOptionVersion.organization_id == actor.organization_id,
                TransformationOptionVersion.workstream_id == workstream.id,
            )
            .order_by(TransformationOptionVersion.id)
        ).all()
        version_rows, version_state = (
            TransformationRoomReadModel.project_verified_versions(
                versions,
                fields=(
                    "id",
                    "option_id",
                    "version",
                    "currency",
                    "technology_required",
                ),
                verifier=TransformationOptionService.verify_version_hash,
                resource_label="option versions",
            )
        )
        return {
            "options": tuple(
                _row(
                    row,
                    "id",
                    "title",
                    "action_type",
                    "description",
                    "technology_required",
                    "revision",
                )
                for row in options
            ),
            "option_versions": version_rows,
            "_resource_states": {"option_versions": version_state},
        }

    @staticmethod
    def _decision_resources(session, actor, workstream):
        briefs = session.scalars(
            select(DecisionBrief)
            .where(
                DecisionBrief.organization_id == actor.organization_id,
                DecisionBrief.workstream_id == workstream.id,
            )
            .order_by(DecisionBrief.id)
        ).all()
        versions = session.scalars(
            select(DecisionBriefVersion)
            .where(
                DecisionBriefVersion.organization_id == actor.organization_id,
                DecisionBriefVersion.workstream_id == workstream.id,
            )
            .order_by(DecisionBriefVersion.id)
        ).all()
        version_rows, version_state = (
            TransformationRoomReadModel.project_verified_versions(
                versions,
                fields=("id", "brief_id", "version", "created_at"),
                verifier=DecisionBriefService.verify_hash,
                resource_label="decision brief versions",
            )
        )
        return {
            "briefs": tuple(
                _row(row, "id", "title", "status", "decision_authority_id", "revision")
                for row in briefs
            ),
            "brief_versions": version_rows,
            "_resource_states": {"brief_versions": version_state},
        }

    @staticmethod
    def _later_resources(_session, _actor, _workstream):
        return {}

    @staticmethod
    def project_evidence(rows):
        projected = []
        for row in rows:
            value = TypedEvidenceValue(
                row.value_type, row.value_json, row.unit, row.currency
            )
            if sha256_canonical(value) != row.source_checksum.lower():
                return (), {
                    "state": "unavailable",
                    "reason": (
                        "Evidence integrity verification failed; compromised "
                        "records are hidden."
                    ),
                }
            projected.append(
                {
                    **_row(
                        row,
                        "id",
                        "claim_key",
                        "classification",
                        "freshness_status",
                        "observed_at",
                        "source_system",
                    ),
                    "verification": "integrity_verified",
                }
            )
        state = "available" if projected else "empty"
        reason = None if projected else "No evidence records are available."
        return tuple(projected), {"state": state, "reason": reason}

    @staticmethod
    def project_verified_versions(rows, *, fields, verifier, resource_label):
        rows = tuple(rows)
        if any(not verifier(row) for row in rows):
            return (), {
                "state": "unavailable",
                "reason": (
                    f"Integrity verification failed for {resource_label}; "
                    "compromised records are hidden."
                ),
            }
        projected = tuple(
            {**_row(row, *fields), "hash_verified": True} for row in rows
        )
        return projected, {
            "state": "available" if projected else "empty",
            "reason": None if projected else f"No {resource_label} are recorded.",
        }

    @staticmethod
    def resource_states(stage, resources, explicit_states=None):
        explicit_states = explicit_states or {}
        if stage in {"execute", "outcomes"}:
            return {
                "stage": {
                    "state": "unknown",
                    "reason": LATER_STAGE_REASON,
                }
            }
        states = {}
        for key, rows in resources.items():
            if key == "workstream":
                continue
            states[key] = explicit_states.get(key) or {
                "state": "available" if rows else "empty",
                "reason": None,
            }
        if stage == "decision":
            states["stage"] = {"state": "unknown", "reason": LATER_STAGE_REASON}
        return states

    @staticmethod
    def unavailable_reasons(stage, resources, resource_states=None):
        reasons: dict[str, str] = {}
        if stage in {"decision", "execute", "outcomes"}:
            reasons["stage"] = LATER_STAGE_REASON
        empty_copy = {
            "outcomes": "No outcome commitments are recorded.",
            "measures": "No measurement definitions are recorded.",
            "candidates": "No candidates have been accepted into this workstream.",
            "signals": "No accepted candidate signals are recorded.",
            "requests": "No evidence requests have been planned.",
            "evidence": "No evidence records are available.",
            "options": "No transformation options are recorded.",
            "option_versions": "No immutable option versions are recorded.",
            "briefs": "No decision brief is recorded.",
            "brief_versions": "No immutable decision brief version is recorded.",
        }
        for key, reason in empty_copy.items():
            if key in resources and not resources[key]:
                reasons[key] = reason
        for key, state in (resource_states or {}).items():
            if state.get("reason"):
                reasons[key] = state["reason"]
        return reasons

    @classmethod
    def room(
        cls,
        *,
        actor: ActorContext,
        programme_id: int,
        workstream_id: int | None = None,
        stage: str | None = None,
    ) -> dict[str, Any]:
        programme_view = TransformationProgrammeService.get_programme(
            actor=actor, programme_id=programme_id
        )
        with Session(db.engine) as session:
            programme = session.scalar(
                select(StrategicInitiative).where(
                    StrategicInitiative.id == programme_id,
                    StrategicInitiative.organization_id == actor.organization_id,
                    StrategicInitiative.record_kind == "transformation_programme",
                )
            )
            if programme is None:
                raise NotFound("programme_not_found")
            workstreams = session.scalars(
                select(ProgrammeWorkstream)
                .where(
                    ProgrammeWorkstream.programme_id == programme_id,
                    ProgrammeWorkstream.organization_id == actor.organization_id,
                )
                .order_by(ProgrammeWorkstream.id)
            ).all()
            if workstream_id is not None:
                selected = next(
                    (row for row in workstreams if row.id == workstream_id), None
                )
                if selected is None:
                    raise NotFound("workstream_not_found")
            else:
                selected = workstreams[0] if workstreams else None
            owner = session.scalar(
                select(User).where(
                    User.id == programme.owner_id,
                    User.organization_id == actor.organization_id,
                )
            )
            outcomes = session.scalars(
                select(ProgrammeOutcomeCommitment)
                .where(
                    ProgrammeOutcomeCommitment.organization_id == actor.organization_id,
                    ProgrammeOutcomeCommitment.programme_id == programme_id,
                    ProgrammeOutcomeCommitment.workstream_id
                    == (selected.id if selected else -1),
                )
                .order_by(ProgrammeOutcomeCommitment.id)
            ).all()
            outcome_ids = [row.id for row in outcomes]
            measures = session.scalars(
                select(MeasureDefinition)
                .where(
                    MeasureDefinition.organization_id == actor.organization_id,
                    MeasureDefinition.outcome_commitment_id.in_(outcome_ids or [-1]),
                )
                .order_by(MeasureDefinition.id)
            ).all()
            requests = session.scalars(
                select(EvidenceRequest).where(
                    EvidenceRequest.organization_id == actor.organization_id,
                    EvidenceRequest.workstream_id == (selected.id if selected else -1),
                    EvidenceRequest.required.is_(True),
                )
            ).all()

            expected_outcome = outcomes[0].statement if outcomes else None
            first_measure = measures[0] if measures else None
            open_requests = [
                row
                for row in requests
                if row.status not in {"accepted", "cancelled"}
            ]
            evidence_posture = (
                f"{len(open_requests)} of {len(requests)} required requests open"
                if requests
                else None
            )
            selected_data = (
                _row(
                    selected,
                    "id",
                    "workstream_type",
                    "objective",
                    "scope_expression",
                    "lifecycle_stage",
                    "target_date",
                    "target_date_unavailable_reason",
                    "revision",
                    "name",
                    "archimate_element_id",
                )
                if selected
                else None
            )
            room = {
                "programme": {
                    **_row(
                        programme,
                        "id",
                        "name",
                        "description",
                        "status",
                        "target_completion_date",
                        "revision",
                    ),
                    "owner_name": owner.full_name() if owner else None,
                },
                "workstreams": tuple(
                    _row(
                        row,
                        "id",
                        "workstream_type",
                        "objective",
                        "lifecycle_stage",
                        "target_date",
                    )
                    for row in workstreams
                ),
                "workstream": selected_data,
                "header": {
                    "objective": selected.objective if selected else programme.description,
                    "lifecycle": selected.lifecycle_stage if selected else programme.status,
                    "owner": owner.full_name() if owner else None,
                    "next_action": (
                        programme_view.next_action.message
                        if programme_view.next_action
                        else None
                    ),
                    "next_action_url": (
                        programme_view.next_action.action_url
                        if (
                            programme_view.next_action
                            and programme_view.next_action.action_url
                            and programme_view.next_action.action_url.startswith(
                                "/solutions/"
                            )
                        )
                        else None
                    ),
                    "evidence_posture": evidence_posture,
                    "evidence_reason": (
                        None
                        if evidence_posture
                        else "No evidence requests have been planned."
                    ),
                    "expected_outcome": expected_outcome,
                    "expected_measure": (
                        first_measure.metric_name if first_measure else None
                    ),
                    "baseline": (
                        _json_scalar(
                            first_measure.baseline_amount
                            if first_measure and first_measure.currency
                            else first_measure.baseline_value
                        )
                        if first_measure
                        else None
                    ),
                    "baseline_reason": (
                        first_measure.unavailable_reason if first_measure else None
                    ),
                },
                "stage": stage,
                "stage_label": STAGE_LABELS.get(stage, "Overview"),
                "stage_routes": tuple(
                    {"key": key, "label": STAGE_LABELS[key]} for key in STAGE_ROUTES
                ),
            }
        if selected and stage:
            room["stage_view"] = cls.stage(
                actor=actor,
                programme_id=programme_id,
                workstream_id=selected.id,
                stage=stage,
            )
        else:
            room["stage_view"] = None
        return room


class ChiefArchitectTransformationReadModel:
    """Portfolio projection that keeps unlike measures separate and nullable."""

    ALLOWED_ROLES = frozenset(
        {ROLE_ENTERPRISE_ARCHITECT, ROLE_CTO, "chief_architect", "platform_admin"}
    )

    @classmethod
    def require_chief_or_enterprise_architect(cls, actor: ActorContext) -> None:
        with Session(db.engine) as session:
            user = session.scalar(
                select(User).where(
                    User.id == actor.user_id,
                    User.organization_id == actor.organization_id,
                )
            )
            if user is None:
                raise NotFound("actor_not_found")
            roles = {
                user.enterprise_role,
                "platform_admin" if user.is_platform_admin else None,
            }
            if not roles.intersection(cls.ALLOWED_ROLES):
                raise NotAuthorised("transformation_portfolio_not_authorised")

    @classmethod
    def portfolio(cls, *, actor: ActorContext) -> TransformationPortfolioView:
        cls.require_chief_or_enterprise_architect(actor)
        programmes = cls.load_programmes(actor)
        return TransformationPortfolioView(
            programmes=tuple(programmes),
            evidence_debt=cls.evidence_debt(actor, programmes),
            decision_ageing=cls.decision_ageing(actor, programmes),
            cross_domain_dependencies=cls.cross_domain_dependencies(actor, programmes),
            delivery_confidence=cls.delivery_confidence(actor, programmes),
            outcome_variance=cls.outcome_variance(actor, programmes),
        )

    @staticmethod
    def load_programmes(actor):
        with Session(db.engine) as session:
            ids = session.scalars(
                select(StrategicInitiative.id)
                .where(
                    StrategicInitiative.organization_id == actor.organization_id,
                    StrategicInitiative.record_kind == "transformation_programme",
                    StrategicInitiative.archived_at.is_(None),
                )
                .order_by(StrategicInitiative.id)
            ).all()
        return tuple(
            TransformationProgrammeService.get_programme(
                actor=actor, programme_id=programme_id
            )
            for programme_id in ids
        )

    @staticmethod
    def evidence_debt(actor, programmes):
        workstream_ids = {
            workstream_id
            for programme in programmes
            for workstream_id in programme.workstream_ids
        }
        if not workstream_ids:
            return {"value": None, "reason": "No programme workstreams are in scope."}
        with Session(db.engine) as session:
            requests = session.scalars(
                select(EvidenceRequest).where(
                    EvidenceRequest.organization_id == actor.organization_id,
                    EvidenceRequest.workstream_id.in_(workstream_ids),
                    EvidenceRequest.required.is_(True),
                )
            ).all()
        if not requests:
            return {"value": None, "reason": "No evidence requests have been planned."}
        return {
            "value": sum(
                row.status not in {"accepted", "cancelled"} for row in requests
            ),
            "reason": None,
        }

    @staticmethod
    def decision_ageing(_actor, _programmes):
        return {
            "value": None,
            "reason": "Typed ARB decision cycles are not available in this release.",
        }

    @staticmethod
    def cross_domain_dependencies(_actor, _programmes):
        return {
            "value": None,
            "reason": (
                "Dependencies are not yet classified by transformation domain; "
                "a cross-domain count is unavailable."
            ),
        }

    @staticmethod
    def delivery_confidence(_actor, _programmes):
        return {
            "value": None,
            "reason": "Canonical execution materialisation is not available in this release.",
        }

    @staticmethod
    def outcome_variance(_actor, _programmes):
        return {
            "value": None,
            "reason": "Outcome measurement is not available in this release.",
        }

    @staticmethod
    def to_template(view: TransformationPortfolioView) -> dict[str, Any]:
        data = asdict(view)
        data["state"] = "available" if view.programmes else "empty"
        data["programme_count"] = len(view.programmes)
        data["non_solution_programmes"] = len(view.programmes)
        return data


__all__ = [
    "ChiefArchitectTransformationReadModel",
    "IMPLEMENTED_STAGES",
    "LATER_STAGE_REASON",
    "STAGE_LABELS",
    "STAGE_ROUTES",
    "TransformationRoomReadModel",
]

"""Canonical execution materialisation for approved Transformation Room decisions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Mapping, Sequence
import time
import unicodedata

from flask import current_app
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import db
from app.models.architecture_review_board import ARBReviewCycle, ARBReviewItem
from app.models.arb_decision_event import ARBCondition
from app.models.benefit import Benefit
from app.models.implementation_migration import WorkPackage
from app.models.solution_models import Solution
from app.models.strategic import RoadmapItem, StrategicInitiative
from app.models.transformation_decision import (
    DecisionBrief,
    DecisionBriefVersion,
    TransformationOptionVersion,
)
from app.models.transformation_execution import DeliveryExportAttempt
from app.models.transformation_programme import (
    MeasureDefinition,
    ProgrammeOutcomeCommitment,
    ProgrammeWorkstream,
)
from app.models.user import User
from app.modules.solutions_strategic.v2.services.strategic_service import StrategicService
from app.modules.transformation_room.command_service import (
    CommandService,
    OperationAuthorizer,
    canonical_request_digest,
)
from app.modules.transformation_room.decision_service import (
    DecisionBriefService,
    TransformationOptionService,
)
from app.modules.transformation_room.domain import (
    ActorContext,
    ApprovedAction,
    CommandConflict,
    CommandResult,
    DomainMutationResult,
    KnownPreCommitTransient,
    NotAuthorised,
    NotFound,
)
from app.modules.transformation_room.gate_service import TransformationGateService
from app.modules.transformation_room.programme_service import (
    CREATE_ROLES,
    TransformationProgrammeService,
)


EXECUTION_ROLES = CREATE_ROLES | frozenset(
    {
        "programme_owner",
        "workstream_lead",
        "delivery_lead",
        "application_architect",
        "solution_architect",
    }
)


@dataclass(frozen=True)
class _ExecutionGraph:
    version: DecisionBriefVersion
    brief: DecisionBrief
    programme: StrategicInitiative
    workstream: ProgrammeWorkstream
    cycle: ARBReviewCycle
    review: ARBReviewItem
    conditions: tuple[ARBCondition, ...]
    options: Mapping[int, TransformationOptionVersion]
    outcomes: tuple[ProgrammeOutcomeCommitment, ...]
    measures: tuple[MeasureDefinition, ...]


def _positive_id(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _required_text(value: Any, field: str, limit: int) -> str:
    normalized = (
        unicodedata.normalize("NFC", value).strip() if isinstance(value, str) else ""
    )
    if (
        not normalized
        or len(normalized) > limit
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise ValueError(f"invalid {field}")
    return normalized


def _bounded_display_text(value: Any, field: str, limit: int) -> str:
    """Return honest display text that cannot overflow a bounded projection."""
    normalized = (
        unicodedata.normalize("NFC", value).strip() if isinstance(value, str) else ""
    )
    if (
        not normalized
        or limit < 2
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise ValueError(f"invalid {field}")
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1]}…"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _fits_legacy_benefit_numeric(value: Any) -> bool:
    """Return whether Numeric(18,2) can store ``value`` without changing it."""
    if value is None:
        return True
    parsed = Decimal(str(value)).normalize()
    fractional_digits = max(-parsed.as_tuple().exponent, 0)
    integer_digits = max(parsed.adjusted() + 1, 0) if parsed else 0
    return fractional_digits <= 2 and integer_digits <= 16


class TransformationExecutionService:
    """Materialise approved work without introducing another planning aggregate."""

    OPERATION = "execution.materialise"
    SOLUTION_OPERATION = "execution.create_solution"
    EXPORT_OPERATION = "execution.delivery_export.prepare"
    EXPORT_RECOVER_OPERATION = "execution.delivery_export.recover"
    EXPORT_FINALISE_OPERATION = "execution.delivery_export.finalise"

    @classmethod
    def materialise(
        cls,
        *,
        actor: ActorContext,
        decision_brief_version_id: int,
        actions: Sequence[ApprovedAction],
        command_key: str,
    ) -> CommandResult:
        command_key = _required_text(command_key, "command_key", 255)
        request = cls.validate_materialisation_request(
            actor, decision_brief_version_id, actions
        )
        natural_key = (
            f"materialise:{decision_brief_version_id}:"
            f"{canonical_request_digest(request)}"
        )
        return CommandService.execute(
            actor=actor,
            operation=cls.OPERATION,
            idempotency_key=command_key,
            payload=request,
            natural_key=natural_key,
            authorizer=cls.authorise_materialisation(
                decision_brief_version_id, request
            ),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._materialise_locked(
                session, actor, request, claim
            ),
        )

    @classmethod
    def create_technology_solution(
        cls,
        *,
        actor: ActorContext,
        decision_brief_version_id: int,
        option_version_id: int,
        command_key: str,
    ) -> CommandResult:
        decision_brief_version_id = _positive_id(
            decision_brief_version_id, "decision_brief_version_id"
        )
        option_version_id = _positive_id(option_version_id, "option_version_id")
        command_key = _required_text(command_key, "command_key", 255)
        request = {
            "decision_brief_version_id": decision_brief_version_id,
            "option_version_id": option_version_id,
            "explicit_action": "create_technology_solution",
        }
        return CommandService.execute(
            actor=actor,
            operation=cls.SOLUTION_OPERATION,
            idempotency_key=command_key,
            payload=request,
            natural_key=(
                f"solution:{decision_brief_version_id}:{option_version_id}"
            ),
            authorizer=cls.authorise_solution_creation(
                decision_brief_version_id, option_version_id
            ),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._create_solution_if_required(
                session, actor, request, claim
            ),
        )

    @classmethod
    def validate_materialisation_request(
        cls,
        actor: ActorContext,
        decision_brief_version_id: int,
        actions: Sequence[ApprovedAction],
    ) -> Mapping[str, Any]:
        if not isinstance(actor, ActorContext):
            raise TypeError("actor must be ActorContext")
        decision_brief_version_id = _positive_id(
            decision_brief_version_id, "decision_brief_version_id"
        )
        graph = cls._load_execution_graph(
            db.session, actor, decision_brief_version_id, lock=False
        )
        cls._require_execution_authority(db.session, actor, graph, lock=False)
        canonical_actions = cls._canonical_actions(db.session, actor, graph, actions)
        return {
            "decision_brief_version_id": decision_brief_version_id,
            "actions": canonical_actions,
            "outcome_ids": [row.id for row in graph.outcomes],
            "measure_ids": [row.id for row in graph.measures],
        }

    @classmethod
    def authorise_materialisation(
        cls, decision_brief_version_id: int, request: Mapping[str, Any]
    ) -> OperationAuthorizer:
        expected_key = (
            f"materialise:{decision_brief_version_id}:"
            f"{canonical_request_digest(request)}"
        )

        def authorize(session, actor, operation, natural_key):
            if operation != cls.OPERATION or natural_key != expected_key:
                raise NotAuthorised("execution_materialisation_command_mismatch")
            graph = cls._load_execution_graph(
                session, actor, decision_brief_version_id, lock=False
            )
            cls._require_execution_authority(session, actor, graph, lock=False)

        return authorize

    @classmethod
    def authorise_solution_creation(
        cls, decision_brief_version_id: int, option_version_id: int
    ) -> OperationAuthorizer:
        expected_key = f"solution:{decision_brief_version_id}:{option_version_id}"

        def authorize(session, actor, operation, natural_key):
            if operation != cls.SOLUTION_OPERATION or natural_key != expected_key:
                raise NotAuthorised("technology_solution_command_mismatch")
            graph = cls._load_execution_graph(
                session, actor, decision_brief_version_id, lock=False
            )
            if option_version_id not in graph.options:
                raise NotFound("option_version_not_found")
            cls._require_execution_authority(session, actor, graph, lock=False)

        return authorize

    @classmethod
    def _materialise_locked(cls, session, actor, request, _claim):
        graph = cls._load_execution_graph(
            session,
            actor,
            request["decision_brief_version_id"],
            lock=True,
        )
        cls._require_execution_authority(session, actor, graph, lock=True)
        reconciled = CommandService.resolve_materialisation(
            session,
            actor=actor,
            operation=cls.OPERATION,
            claim=_claim,
        )
        if reconciled is not None:
            return reconciled
        cls._assert_active_aggregate(graph)
        cls._assert_snapshot_integrity(graph)
        now = CommandService._database_now(session)
        cls._assert_governance_ready(
            graph.cycle, graph.review, graph.conditions, now
        )
        if graph.workstream.lifecycle_stage != "approved":
            raise CommandConflict("execution_aggregate_not_approved")
        actions = cls._canonical_actions(
            session,
            actor,
            graph,
            tuple(
                ApprovedAction(
                    action_key=row["action_key"],
                    option_version_id=row["option_version_id"],
                    title=row["title"],
                    owner_id=row["owner_id"],
                    start_date=(
                        date.fromisoformat(row["start_date"])
                        if row["start_date"]
                        else None
                    ),
                    target_date=(
                        date.fromisoformat(row["target_date"])
                        if row["target_date"]
                        else None
                    ),
                    scheduling_applicable=row["scheduling_applicable"],
                )
                for row in request["actions"]
            ),
        )
        existing = session.scalar(
            select(WorkPackage.id).where(
                WorkPackage.organization_id == actor.organization_id,
                WorkPackage.decision_brief_version_id == graph.version.id,
                WorkPackage.materialisation_key.is_not(None),
            )
        )
        if existing is not None:
            raise CommandConflict("decision_execution_already_materialised")

        work_packages = []
        roadmap_items = []
        condition_snapshot = [
            {
                "condition_id": row.id,
                "status": row.status,
                "submitted_evidence_id": row.submitted_evidence_id,
                "fulfilment_evidence_id": row.fulfilment_evidence_id,
                "waiver_expires_at": (
                    _utc(row.waiver_expires_at).isoformat()
                    if row.waiver_expires_at
                    else None
                ),
            }
            for row in graph.conditions
        ]
        for ordinal, action in enumerate(actions, start=1):
            option = graph.options[action["option_version_id"]]
            content = option.content_json or {}
            materialisation_key = cls._materialisation_key(
                actor.organization_id,
                graph.version.id,
                "work_package",
                action["action_key"],
                action["option_version_id"],
            )
            work_package = StrategicService.create_transformation_work_package(
                session,
                organization_id=actor.organization_id,
                programme_id=graph.programme.id,
                workstream_id=graph.workstream.id,
                decision_brief_version_id=graph.version.id,
                materialisation_key=materialisation_key,
                name=action["title"],
                description=content.get("description"),
                owner_id=action["owner_id"],
                start_date=(
                    date.fromisoformat(action["start_date"])
                    if action["start_date"]
                    else None
                ),
                target_date=(
                    date.fromisoformat(action["target_date"])
                    if action["target_date"]
                    else None
                ),
                dependencies=None,
                provenance={
                    "source": "transformation_room",
                    "decision_brief_version_id": graph.version.id,
                    "option_version_id": option.id,
                    "action_key": action["action_key"],
                    "cited_evidence_ids": list(
                        graph.version.cited_evidence_ids or ()
                    ),
                    "conditions": condition_snapshot,
                    "unresolved_dependency_claims": list(
                        content.get("dependencies") or ()
                    ),
                },
            )
            work_package.sequence_order = ordinal
            work_packages.append(work_package)
            if action["scheduling_applicable"]:
                roadmap_items.append(
                    cls._create_roadmap_item(
                        session, actor, graph, action, work_package
                    )
                )

        benefits = cls._create_benefits(
            session, actor, graph, work_packages[0]
        )
        session.flush()
        gate_result = cls._apply_execution_gate(
            session,
            actor,
            graph,
            actions,
            work_packages,
            roadmap_items,
        )
        object_ids = {
            "decision_brief_version_id": graph.version.id,
            "work_package_ids": [row.id for row in work_packages],
            "roadmap_item_ids": [row.id for row in roadmap_items],
            "benefit_ids": [row.id for row in benefits],
            "solution_ids": [],
        }
        response = {
            **object_ids,
            "lifecycle_stage": gate_result.response["lifecycle_stage"],
        }
        return DomainMutationResult(
            object_ids,
            response,
            (*gate_result.outbox_events,
                {
                    "event_type": "transformation.execution_materialised",
                    "payload": response,
                },
            ),
        )

    @classmethod
    def _create_solution_if_required(cls, session, actor, request, _claim):
        if request.get("explicit_action") != "create_technology_solution":
            raise CommandConflict("technology_solution_explicit_action_required")
        graph = cls._load_execution_graph(
            session,
            actor,
            request["decision_brief_version_id"],
            lock=True,
        )
        cls._require_execution_authority(session, actor, graph, lock=True)
        cls._assert_active_aggregate(graph, allowed_stages={"approved", "execute", "outcomes"})
        cls._assert_snapshot_integrity(graph)
        cls._assert_governance_ready(
            graph.cycle,
            graph.review,
            graph.conditions,
            CommandService._database_now(session),
        )
        reconciled = CommandService.resolve_materialisation(
            session,
            actor=actor,
            operation=cls.SOLUTION_OPERATION,
            claim=_claim,
        )
        if reconciled is not None:
            return reconciled
        option = graph.options.get(request["option_version_id"])
        if option is None:
            raise NotFound("option_version_not_found")
        if option.id != graph.version.recommendation_option_version_id:
            raise CommandConflict("option_version_not_approved")
        if option.technology_required is not True:
            raise CommandConflict("technology_solution_not_required")
        existing = tuple(
            session.scalars(
                select(Solution)
                .where(
                    Solution.organization_id == actor.organization_id,
                    Solution.workstream_id == graph.workstream.id,
                )
                .with_for_update(of=Solution)
            ).all()
        )
        if any(
            (row.journey_state or {}).get("decision_brief_version_id")
            == graph.version.id
            and (row.journey_state or {}).get("option_version_id") == option.id
            for row in existing
        ):
            raise CommandConflict("technology_solution_already_created")
        frozen = graph.version.frozen_payload or {}
        content = option.content_json or {}
        journey_state = {
            "source": "transformation_room",
            "decision_brief_version_id": graph.version.id,
            "option_version_id": option.id,
            "constraints": list(frozen.get("conflicts") or ()),
            "cited_evidence_ids": list(graph.version.cited_evidence_ids or ()),
            "scope_expression": frozen.get("scope_expression"),
        }
        solution = Solution(
            organization_id=actor.organization_id,
            name=_required_text(content.get("title"), "option title", 255),
            description=content.get("description"),
            scope_description=frozen.get("objective"),
            journey_state=journey_state,
            status="planned",
            governance_status="draft",
            created_by_id=actor.user_id,
            initiative_id=graph.programme.id,
            workstream_id=graph.workstream.id,
        )
        session.add(solution)
        session.flush()
        object_ids = {
            "solution_id": solution.id,
            "decision_brief_version_id": graph.version.id,
            "option_version_id": option.id,
        }
        return DomainMutationResult(
            object_ids,
            {**object_ids, "created_technology_solution": True},
            (
                {
                    "event_type": "transformation.technology_solution_created",
                    "payload": object_ids,
                },
            ),
        )

    @classmethod
    def export_work_package(
        cls,
        *,
        actor: ActorContext,
        work_package_id: int,
        provider_key: str,
        request: Mapping[str, Any],
        exporter: Callable[
            [WorkPackage, Mapping[str, Any], str], Mapping[str, Any]
        ],
        command_key: str,
        predecessor_attempt_id: int | None = None,
    ) -> CommandResult:
        work_package_id = _positive_id(work_package_id, "work_package_id")
        provider_key = _required_text(
            _required_text(provider_key, "provider_key", 120).lower(),
            "provider_key",
            120,
        )
        command_key = _required_text(command_key, "command_key", 255)
        if predecessor_attempt_id is not None:
            predecessor_attempt_id = _positive_id(
                predecessor_attempt_id, "predecessor_attempt_id"
            )
        if not isinstance(request, Mapping):
            raise TypeError("request must be a mapping")
        if not callable(exporter):
            raise TypeError("exporter must be callable")
        payload = {
            "work_package_id": work_package_id,
            "provider_key": provider_key,
            "predecessor_attempt_id": predecessor_attempt_id,
            "request": dict(request),
        }
        provider_idempotency_key = canonical_request_digest(
            {
                "organization_id": actor.organization_id,
                "work_package_id": work_package_id,
                "provider_key": provider_key,
                "request": dict(request),
            }
        )
        payload["provider_idempotency_key"] = provider_idempotency_key
        attempt_key = canonical_request_digest(
            {
                "provider_idempotency_key": provider_idempotency_key,
                "predecessor_attempt_id": predecessor_attempt_id,
            }
        )
        natural_key = (
            f"delivery-export:{work_package_id}:{provider_key}:"
            f"{predecessor_attempt_id or 'root'}:{attempt_key}"
        )
        prepared = CommandService.execute(
            actor=actor,
            operation=cls.EXPORT_OPERATION,
            idempotency_key=command_key,
            payload=payload,
            natural_key=natural_key,
            authorizer=cls.authorise_delivery_export(
                work_package_id,
                provider_key,
                predecessor_attempt_id,
                attempt_key,
            ),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._prepare_export_locked(
                session,
                actor,
                payload,
                attempt_key,
                claim,
            ),
        )
        attempt_id = prepared.object_ids["delivery_export_attempt_id"]
        if prepared.created:
            attempt = cls._load_export_attempt(actor, attempt_id)
            final_payload = cls._dispatch_export_attempt(
                actor=actor,
                attempt=attempt,
                exporter=exporter,
            )
        else:
            attempt, dispatch_owned = cls._await_or_recover_export_attempt(
                actor=actor,
                attempt_id=attempt_id,
            )
            final_payload = (
                cls._dispatch_export_attempt(
                    actor=actor,
                    attempt=attempt,
                    exporter=exporter,
                )
                if dispatch_owned
                else cls._persisted_export_outcome(attempt)
            )
        return cls._finalise_export_attempt(
            actor=actor,
            payload=final_payload,
            originating_command_key=command_key,
        )

    @staticmethod
    def _dispatch_lease_seconds():
        configured = current_app.config.get(
            "TRANSFORMATION_EXPORT_DISPATCH_LEASE_SECONDS", 30.0
        )
        try:
            seconds = float(configured)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "TRANSFORMATION_EXPORT_DISPATCH_LEASE_SECONDS must be positive"
            ) from error
        if seconds <= 0 or seconds > 60:
            raise ValueError(
                "TRANSFORMATION_EXPORT_DISPATCH_LEASE_SECONDS must be between 0 and 60"
            )
        return seconds

    @classmethod
    def _load_export_attempt(cls, actor, attempt_id):
        attempt = db.session.scalar(
            select(DeliveryExportAttempt)
            .where(
                DeliveryExportAttempt.id == attempt_id,
                DeliveryExportAttempt.organization_id == actor.organization_id,
            )
            .execution_options(populate_existing=True)
        )
        if attempt is None:
            raise RuntimeError("durable delivery export preparation is missing")
        return attempt

    @classmethod
    def _dispatch_export_attempt(cls, *, actor, attempt, exporter):
        if attempt.status != "in_progress":
            return cls._persisted_export_outcome(attempt)
        work_package = db.session.scalar(
            select(WorkPackage).where(
                WorkPackage.id == attempt.work_package_id,
                WorkPackage.organization_id == actor.organization_id,
            )
        )
        if work_package is None:
            raise RuntimeError("durable delivery export preparation is missing")
        provider_idempotency_key = (
            attempt.provider_idempotency_key or attempt.attempt_key
        )
        try:
            provider_response = exporter(
                work_package,
                dict(attempt.request_json),
                provider_idempotency_key,
            )
            if not isinstance(provider_response, Mapping):
                raise ValueError("provider response must be a mapping")
            external_key = _required_text(
                provider_response.get("external_key"), "external_key", 512
            )
            final_payload = {
                "delivery_export_attempt_id": attempt.id,
                "attempt_key": attempt.attempt_key,
                "status": "succeeded",
                "external_key": external_key,
                "response_digest": canonical_request_digest(dict(provider_response)),
                "error_class": None,
                "error_message": None,
            }
        except Exception as error:  # provider boundary: persist honest failure
            error_message = " ".join(
                unicodedata.normalize("NFC", str(error)).split()
            )
            final_payload = {
                "delivery_export_attempt_id": attempt.id,
                "attempt_key": attempt.attempt_key,
                "status": "failed",
                "external_key": None,
                "response_digest": None,
                "error_class": _bounded_display_text(
                    type(error).__name__, "provider error class", 255
                ),
                "error_message": (error_message or type(error).__name__)[:4000],
            }
        cls._after_provider_before_finalise(attempt.id, final_payload)
        return final_payload

    @classmethod
    def _await_or_recover_export_attempt(cls, *, actor, attempt_id):
        """Wait for the dispatch owner or durably fence an uncertain retry."""
        while True:
            attempt = cls._load_export_attempt(actor, attempt_id)
            if attempt.status in {"succeeded", "failed"}:
                return attempt, False
            if attempt.status == "indeterminate":
                with Session(db.engine) as session:
                    successor_id = session.scalar(
                        select(DeliveryExportAttempt.id).where(
                            DeliveryExportAttempt.organization_id
                            == actor.organization_id,
                            DeliveryExportAttempt.predecessor_attempt_id
                            == attempt.id,
                        )
                    )
                if successor_id is not None:
                    attempt_id = successor_id
                    continue
            now = _utc(CommandService._database_now(db.session))
            lease_expires_at = (
                _utc(attempt.dispatch_lease_expires_at)
                if attempt.dispatch_lease_expires_at is not None
                else None
            )
            if (
                attempt.status == "in_progress"
                and lease_expires_at is not None
                and lease_expires_at > now
            ):
                time.sleep(min(0.01, (lease_expires_at - now).total_seconds()))
                continue
            recovered = cls._recover_export_attempt(actor=actor, attempt=attempt)
            successor = cls._load_export_attempt(
                actor, recovered.object_ids["delivery_export_attempt_id"]
            )
            if recovered.created and recovered.response.get("dispatch_required"):
                return successor, True
            attempt_id = successor.id

    @classmethod
    def _recover_export_attempt(cls, *, actor, attempt):
        provider_idempotency_key = (
            attempt.provider_idempotency_key or attempt.attempt_key
        )
        successor_key = canonical_request_digest(
            {
                "provider_idempotency_key": provider_idempotency_key,
                "predecessor_attempt_id": attempt.id,
            }
        )
        payload = {
            "predecessor_attempt_id": attempt.id,
            "work_package_id": attempt.work_package_id,
            "provider_key": attempt.provider_key,
            "provider_idempotency_key": provider_idempotency_key,
            "attempt_key": successor_key,
            "request": dict(attempt.request_json),
        }
        natural_key = f"delivery-export-recover:{attempt.id}:{successor_key}"
        return CommandService.execute(
            actor=actor,
            operation=cls.EXPORT_RECOVER_OPERATION,
            idempotency_key=f"delivery-export-recover:{attempt.id}",
            payload=payload,
            natural_key=natural_key,
            authorizer=cls.authorise_delivery_export_recovery(
                attempt.id, natural_key
            ),
            handler=lambda session, claim: cls._recover_export_locked(
                session, actor, payload, claim
            ),
        )

    @classmethod
    def authorise_delivery_export_recovery(
        cls, predecessor_attempt_id, expected_natural_key
    ) -> OperationAuthorizer:
        def authorize(session, actor, operation, natural_key):
            if (
                operation != cls.EXPORT_RECOVER_OPERATION
                or natural_key != expected_natural_key
            ):
                raise NotAuthorised("delivery_export_recovery_command_mismatch")
            attempt = session.scalar(
                select(DeliveryExportAttempt).where(
                    DeliveryExportAttempt.id == predecessor_attempt_id,
                    DeliveryExportAttempt.organization_id == actor.organization_id,
                )
            )
            if attempt is None:
                raise NotFound("delivery_export_attempt_not_found")
            work_package = cls._load_work_package(
                session, actor, attempt.work_package_id, lock=False
            )
            cls._require_work_package_authority(
                session, actor, work_package, lock=False
            )

        return authorize

    @classmethod
    def _recover_export_locked(cls, session, actor, payload, claim):
        work_package = cls._load_work_package(
            session, actor, payload["work_package_id"], lock=True
        )
        cls._require_work_package_authority(
            session, actor, work_package, lock=True
        )
        predecessor = session.scalar(
            select(DeliveryExportAttempt)
            .where(
                DeliveryExportAttempt.id == payload["predecessor_attempt_id"],
                DeliveryExportAttempt.organization_id == actor.organization_id,
                DeliveryExportAttempt.work_package_id == work_package.id,
                DeliveryExportAttempt.provider_key == payload["provider_key"],
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if predecessor is None:
            raise NotFound("delivery_export_attempt_not_found")
        reconciled = CommandService.resolve_materialisation(
            session,
            actor=actor,
            operation=cls.EXPORT_RECOVER_OPERATION,
            claim=claim,
        )
        if reconciled is not None:
            return reconciled
        prior_successor = session.scalar(
            select(DeliveryExportAttempt).where(
                DeliveryExportAttempt.organization_id == actor.organization_id,
                DeliveryExportAttempt.predecessor_attempt_id == predecessor.id,
            )
        )
        if prior_successor is not None:
            return cls._recovery_mutation(prior_successor, dispatch_required=False)
        now = CommandService._database_now(session)
        if predecessor.status == "in_progress":
            lease_expires_at = (
                _utc(predecessor.dispatch_lease_expires_at)
                if predecessor.dispatch_lease_expires_at is not None
                else None
            )
            if lease_expires_at is not None and lease_expires_at > _utc(now):
                raise KnownPreCommitTransient(
                    "delivery_export_dispatch_still_owned"
                )
            predecessor.status = "indeterminate"
            predecessor.dispatch_lease_expires_at = None
            predecessor.error_class = "DispatchOutcomeUnknown"
            predecessor.error_message = (
                "Provider call outcome was not durably finalised before dispatch lease expiry"
            )
            predecessor.completed_at = now
        elif predecessor.status != "indeterminate":
            return cls._recovery_mutation(predecessor, dispatch_required=False)
        successor = DeliveryExportAttempt(
            organization_id=actor.organization_id,
            work_package_id=work_package.id,
            predecessor_attempt_id=predecessor.id,
            provider_key=payload["provider_key"],
            attempt_key=payload["attempt_key"],
            provider_idempotency_key=payload["provider_idempotency_key"],
            request_json=dict(payload["request"]),
            status="in_progress",
            dispatch_lease_expires_at=now
            + timedelta(seconds=cls._dispatch_lease_seconds()),
            attempted_by_id=actor.user_id,
        )
        session.add(successor)
        session.flush()
        return cls._recovery_mutation(successor, dispatch_required=True)

    @staticmethod
    def _recovery_mutation(attempt, *, dispatch_required):
        object_ids = {
            "work_package_id": attempt.work_package_id,
            "delivery_export_attempt_id": attempt.id,
        }
        response = {
            **object_ids,
            "status": attempt.status,
            "dispatch_required": dispatch_required,
            "provider_idempotency_key": (
                attempt.provider_idempotency_key or attempt.attempt_key
            ),
        }
        return DomainMutationResult(
            object_ids,
            response,
            (
                {
                    "event_type": "transformation.delivery_export_requested",
                    "payload": response,
                },
            )
            if dispatch_required
            else (),
        )

    @classmethod
    def authorise_delivery_export(
        cls,
        work_package_id: int,
        provider_key: str,
        predecessor_attempt_id: int | None,
        attempt_key: str,
    ) -> OperationAuthorizer:
        expected_key = (
            f"delivery-export:{work_package_id}:{provider_key}:"
            f"{predecessor_attempt_id or 'root'}:{attempt_key}"
        )

        def authorize(session, actor, operation, natural_key):
            if operation != cls.EXPORT_OPERATION or natural_key != expected_key:
                raise NotAuthorised("delivery_export_command_mismatch")
            work_package = cls._load_work_package(
                session, actor, work_package_id, lock=False
            )
            cls._require_work_package_authority(
                session, actor, work_package, lock=False
            )

        return authorize

    @classmethod
    def _prepare_export_locked(cls, session, actor, payload, attempt_key, claim):
        work_package = cls._load_work_package(
            session, actor, payload["work_package_id"], lock=True
        )
        cls._require_work_package_authority(session, actor, work_package, lock=True)
        reconciled = CommandService.resolve_materialisation(
            session,
            actor=actor,
            operation=cls.EXPORT_OPERATION,
            claim=claim,
        )
        if reconciled is not None:
            return reconciled
        predecessor = None
        if payload["predecessor_attempt_id"] is not None:
            predecessor = session.scalar(
                select(DeliveryExportAttempt)
                .where(
                    DeliveryExportAttempt.id == payload["predecessor_attempt_id"],
                    DeliveryExportAttempt.organization_id == actor.organization_id,
                    DeliveryExportAttempt.work_package_id == work_package.id,
                    DeliveryExportAttempt.provider_key == payload["provider_key"],
                )
                .with_for_update()
            )
            if predecessor is None:
                raise NotFound("delivery_export_attempt_not_found")
            if predecessor.status != "failed":
                raise CommandConflict("delivery_export_retry_requires_failed_attempt")
            if (
                dict(predecessor.request_json) != dict(payload["request"])
                or (predecessor.provider_idempotency_key or predecessor.attempt_key)
                != payload["provider_idempotency_key"]
            ):
                raise CommandConflict("delivery_export_retry_payload_conflict")
        else:
            prior_root = session.scalar(
                select(DeliveryExportAttempt.id).where(
                    DeliveryExportAttempt.organization_id == actor.organization_id,
                    DeliveryExportAttempt.work_package_id == work_package.id,
                    DeliveryExportAttempt.provider_key == payload["provider_key"],
                    DeliveryExportAttempt.predecessor_attempt_id.is_(None),
                )
            )
            if prior_root is not None:
                raise CommandConflict("delivery_export_root_already_attempted")
        attempt = DeliveryExportAttempt(
            organization_id=actor.organization_id,
            work_package_id=work_package.id,
            predecessor_attempt_id=predecessor.id if predecessor else None,
            provider_key=payload["provider_key"],
            attempt_key=attempt_key,
            provider_idempotency_key=payload["provider_idempotency_key"],
            request_json=dict(payload["request"]),
            status="in_progress",
            dispatch_lease_expires_at=CommandService._database_now(session)
            + timedelta(seconds=cls._dispatch_lease_seconds()),
            attempted_by_id=actor.user_id,
        )
        session.add(attempt)
        session.flush()
        object_ids = {
            "work_package_id": work_package.id,
            "delivery_export_attempt_id": attempt.id,
        }
        response = {
            **object_ids,
            "exported": False,
            "status": attempt.status,
            "external_key": None,
            "provider_idempotency_key": attempt.provider_idempotency_key,
        }
        return DomainMutationResult(
            object_ids,
            response,
            ({
                "event_type": "transformation.delivery_export_requested",
                "payload": response,
            },),
        )

    @staticmethod
    def _after_provider_before_finalise(_attempt_id, _payload):
        """Explicit crash-injection boundary after I/O and before local finalisation."""

    @staticmethod
    def _persisted_export_outcome(attempt):
        return {
            "delivery_export_attempt_id": attempt.id,
            "attempt_key": attempt.attempt_key,
            "status": attempt.status,
            "external_key": attempt.external_key,
            "response_digest": attempt.response_digest,
            "error_class": attempt.error_class,
            "error_message": attempt.error_message,
        }

    @classmethod
    def _finalise_export_attempt(cls, *, actor, payload, originating_command_key):
        attempt_id = payload["delivery_export_attempt_id"]
        natural_key = f"delivery-export-finalise:{attempt_id}"
        finalisation_key = "delivery-export-finalise:" + canonical_request_digest(
            {
                "attempt_key": payload["attempt_key"],
                "originating_command_key": originating_command_key,
            }
        )
        return CommandService.execute(
            actor=actor,
            operation=cls.EXPORT_FINALISE_OPERATION,
            idempotency_key=finalisation_key,
            payload=payload,
            natural_key=natural_key,
            authorizer=cls.authorise_delivery_export_finalisation(
                attempt_id, natural_key
            ),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._finalise_export_locked(
                session, actor, payload, claim
            ),
        )

    @classmethod
    def authorise_delivery_export_finalisation(
        cls, attempt_id, expected_natural_key
    ) -> OperationAuthorizer:
        def authorize(session, actor, operation, natural_key):
            if (
                operation != cls.EXPORT_FINALISE_OPERATION
                or natural_key != expected_natural_key
            ):
                raise NotAuthorised("delivery_export_finalisation_command_mismatch")
            attempt = session.scalar(
                select(DeliveryExportAttempt).where(
                    DeliveryExportAttempt.id == attempt_id,
                    DeliveryExportAttempt.organization_id
                    == actor.organization_id,
                )
            )
            if attempt is None:
                raise NotFound("delivery_export_attempt_not_found")
            work_package = cls._load_work_package(
                session, actor, attempt.work_package_id, lock=False
            )
            cls._require_work_package_authority(
                session, actor, work_package, lock=False
            )

        return authorize

    @classmethod
    def _finalise_export_locked(cls, session, actor, payload, claim):
        attempt = session.scalar(
            select(DeliveryExportAttempt)
            .where(
                DeliveryExportAttempt.id == payload["delivery_export_attempt_id"],
                DeliveryExportAttempt.organization_id == actor.organization_id,
                DeliveryExportAttempt.attempt_key == payload["attempt_key"],
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if attempt is None:
            raise NotFound("delivery_export_attempt_not_found")
        reconciled = CommandService.resolve_materialisation(
            session,
            actor=actor,
            operation=cls.EXPORT_FINALISE_OPERATION,
            claim=claim,
        )
        if reconciled is not None:
            return reconciled
        if attempt.status != "in_progress":
            raise CommandConflict("delivery_export_attempt_already_completed")
        completed_now = CommandService._database_now(session)
        attempt.status = payload["status"]
        attempt.external_key = payload["external_key"]
        attempt.response_digest = payload["response_digest"]
        attempt.error_class = payload["error_class"]
        attempt.error_message = payload["error_message"]
        attempt.dispatch_lease_expires_at = None
        attempt.completed_at = completed_now
        session.flush()
        object_ids = {
            "work_package_id": attempt.work_package_id,
            "delivery_export_attempt_id": attempt.id,
        }
        response = {
            **object_ids,
            "exported": attempt.status == "succeeded",
            "status": attempt.status,
            "external_key": attempt.external_key,
        }
        return DomainMutationResult(
            object_ids,
            response,
            ({
                "event_type": (
                    "transformation.delivery_export_succeeded"
                    if attempt.status == "succeeded"
                    else "transformation.delivery_export_failed"
                ),
                "payload": response,
            },),
        )

    @classmethod
    def _load_execution_graph(
        cls, session, actor, decision_brief_version_id, *, lock
    ) -> _ExecutionGraph:
        def one(model, reason, *criteria):
            statement = select(model).where(*criteria)
            if lock:
                statement = statement.execution_options(
                    populate_existing=True
                ).with_for_update()
            row = session.scalar(statement)
            if row is None:
                raise NotFound(reason)
            return row

        version = one(
            DecisionBriefVersion,
            "decision_brief_version_not_found",
            DecisionBriefVersion.id == decision_brief_version_id,
            DecisionBriefVersion.organization_id == actor.organization_id,
        )
        brief = one(
            DecisionBrief,
            "decision_brief_not_found",
            DecisionBrief.id == version.brief_id,
            DecisionBrief.organization_id == actor.organization_id,
            DecisionBrief.workstream_id == version.workstream_id,
        )
        if lock:
            workstream_scope = session.scalar(
                select(ProgrammeWorkstream).where(
                    ProgrammeWorkstream.id == version.workstream_id,
                    ProgrammeWorkstream.organization_id == actor.organization_id,
                )
            )
            if workstream_scope is None:
                raise NotFound("workstream_not_found")
        else:
            workstream_scope = one(
                ProgrammeWorkstream,
                "workstream_not_found",
                ProgrammeWorkstream.id == version.workstream_id,
                ProgrammeWorkstream.organization_id == actor.organization_id,
            )
        programme = one(
            StrategicInitiative,
            "programme_not_found",
            StrategicInitiative.id == workstream_scope.programme_id,
            StrategicInitiative.organization_id == actor.organization_id,
            StrategicInitiative.record_kind == "transformation_programme",
        )
        workstream = (
            one(
                ProgrammeWorkstream,
                "workstream_not_found",
                ProgrammeWorkstream.id == version.workstream_id,
                ProgrammeWorkstream.organization_id == actor.organization_id,
                ProgrammeWorkstream.programme_id == programme.id,
            )
            if lock
            else workstream_scope
        )
        cycle_statement = (
            select(ARBReviewCycle)
            .where(
                ARBReviewCycle.organization_id == actor.organization_id,
                ARBReviewCycle.subject_type == "decision_brief",
                ARBReviewCycle.subject_id == brief.id,
                ARBReviewCycle.decision_brief_id == brief.id,
                ARBReviewCycle.decision_brief_version_id == version.id,
            )
            .order_by(ARBReviewCycle.cycle_number.desc(), ARBReviewCycle.id.desc())
            .limit(1)
        )
        if lock:
            cycle_statement = cycle_statement.execution_options(
                populate_existing=True
            ).with_for_update()
        cycle = session.scalar(cycle_statement)
        if cycle is None:
            raise NotFound("approved_arb_cycle_not_found")
        review = one(
            ARBReviewItem,
            "approved_arb_review_not_found",
            ARBReviewItem.organization_id == actor.organization_id,
            ARBReviewItem.review_cycle_id == cycle.id,
            ARBReviewItem.decision_brief_version_id == version.id,
        )

        def many(model, *criteria, order_by):
            statement = select(model).where(*criteria).order_by(order_by)
            if lock:
                statement = statement.execution_options(
                    populate_existing=True
                ).with_for_update()
            return tuple(session.scalars(statement).all())

        conditions = many(
            ARBCondition,
            ARBCondition.organization_id == actor.organization_id,
            ARBCondition.review_cycle_id == cycle.id,
            order_by=ARBCondition.id,
        )
        option_ids = tuple(version.option_version_ids or ())
        options = many(
            TransformationOptionVersion,
            TransformationOptionVersion.organization_id == actor.organization_id,
            TransformationOptionVersion.workstream_id == workstream.id,
            TransformationOptionVersion.id.in_(option_ids or (-1,)),
            order_by=TransformationOptionVersion.id,
        )
        if {row.id for row in options} != set(option_ids):
            raise NotFound("option_version_not_found")
        outcome_ids = tuple(version.outcome_ids or ())
        outcomes = many(
            ProgrammeOutcomeCommitment,
            ProgrammeOutcomeCommitment.organization_id == actor.organization_id,
            ProgrammeOutcomeCommitment.programme_id == programme.id,
            or_(
                ProgrammeOutcomeCommitment.workstream_id == workstream.id,
                ProgrammeOutcomeCommitment.workstream_id.is_(None),
            ),
            ProgrammeOutcomeCommitment.id.in_(outcome_ids or (-1,)),
            order_by=ProgrammeOutcomeCommitment.id,
        )
        if {row.id for row in outcomes} != set(outcome_ids):
            raise NotFound("outcome_commitment_not_found")
        measure_ids = tuple(version.measure_ids or ())
        measures = many(
            MeasureDefinition,
            MeasureDefinition.organization_id == actor.organization_id,
            MeasureDefinition.outcome_commitment_id.in_(outcome_ids or (-1,)),
            MeasureDefinition.id.in_(measure_ids or (-1,)),
            order_by=MeasureDefinition.id,
        )
        if {row.id for row in measures} != set(measure_ids):
            raise NotFound("measure_definition_not_found")
        return _ExecutionGraph(
            version,
            brief,
            programme,
            workstream,
            cycle,
            review,
            conditions,
            {row.id: row for row in options},
            outcomes,
            measures,
        )

    @classmethod
    def _canonical_actions(cls, session, actor, graph, actions):
        if isinstance(actions, (str, bytes)) or not isinstance(actions, Sequence):
            raise TypeError("actions must be a sequence")
        if len(actions) != 1:
            raise CommandConflict("approved_action_not_frozen")
        approved_option_id = graph.version.recommendation_option_version_id
        approved_option = graph.options.get(approved_option_id)
        if approved_option is None:
            raise CommandConflict("approved_action_not_frozen")
        expected_key = f"approved-option:{approved_option_id}"
        expected_title = _required_text(
            (approved_option.content_json or {}).get("title"), "option title", 255
        )
        canonical = []
        seen = set()
        for action in actions:
            if not isinstance(action, ApprovedAction):
                raise TypeError("actions must contain ApprovedAction values")
            action_key = _required_text(action.action_key, "action_key", 120)
            if action_key in seen:
                raise ValueError("action keys must be unique")
            seen.add(action_key)
            option_version_id = _positive_id(
                action.option_version_id, "option_version_id"
            )
            if (
                option_version_id not in graph.options
                or option_version_id
                != graph.version.recommendation_option_version_id
            ):
                raise CommandConflict("option_version_not_approved")
            if (
                action_key != expected_key
                or _required_text(action.title, "action title", 255)
                != expected_title
            ):
                raise CommandConflict("approved_action_not_frozen")
            owner_id = _positive_id(action.owner_id, "owner_id")
            owner = session.scalar(
                select(User.id).where(
                    User.id == owner_id,
                    User.organization_id == actor.organization_id,
                )
            )
            if owner is None:
                raise NotFound("action_owner_not_found")
            if action.start_date is not None and (
                not isinstance(action.start_date, date)
                or isinstance(action.start_date, datetime)
            ):
                raise ValueError("start_date must be a date")
            if action.target_date is not None and (
                not isinstance(action.target_date, date)
                or isinstance(action.target_date, datetime)
            ):
                raise ValueError("target_date must be a date")
            if (
                action.start_date
                and action.target_date
                and action.target_date < action.start_date
            ):
                raise ValueError("target_date must not precede start_date")
            if not isinstance(action.scheduling_applicable, bool):
                raise ValueError("scheduling_applicable must be boolean")
            if action.scheduling_applicable and not (
                action.start_date or action.target_date
            ):
                raise ValueError("scheduled actions require a date")
            canonical.append(
                {
                    "action_key": expected_key,
                    "option_version_id": option_version_id,
                    "title": expected_title,
                    "owner_id": owner_id,
                    "start_date": (
                        action.start_date.isoformat() if action.start_date else None
                    ),
                    "target_date": (
                        action.target_date.isoformat() if action.target_date else None
                    ),
                    "scheduling_applicable": action.scheduling_applicable,
                }
            )
        canonical.sort(key=lambda row: row["action_key"])
        return canonical

    @staticmethod
    def _assert_active_aggregate(graph, allowed_stages=None):
        try:
            TransformationProgrammeService._require_active_programme(graph.programme)
        except CommandConflict as error:
            raise CommandConflict("execution_aggregate_not_approved") from error
        allowed = allowed_stages or {
            "approved",
            "execute",
            "outcomes",
        }
        if graph.workstream.archived_at is not None or graph.workstream.lifecycle_stage not in allowed:
            raise CommandConflict("execution_aggregate_not_approved")

    @staticmethod
    def _assert_snapshot_integrity(graph):
        if not DecisionBriefService.verify_hash(graph.version) or any(
            not TransformationOptionService.verify_version_hash(row)
            for row in graph.options.values()
        ):
            raise CommandConflict("decision_snapshot_hash_invalid")

    @classmethod
    def _apply_execution_gate(
        cls, session, actor, graph, actions, work_packages, roadmap_items
    ):
        snapshot = TransformationGateService._load_policy_snapshot(
            session=session,
            actor=actor,
            workstream_id=graph.workstream.id,
            lock=True,
        )
        roadmap_by_work_package = {
            row.work_package_id: row for row in roadmap_items
        }
        action_rows = tuple(
            {
                "workstream_id": graph.workstream.id,
                "decision_brief_version_id": graph.version.id,
                "status": "accepted",
                "owner_id": action["owner_id"],
                "work_package_id": work_package.id,
                "scheduling_applicable": action["scheduling_applicable"],
                "roadmap_item_id": (
                    roadmap_by_work_package[work_package.id].id
                    if work_package.id in roadmap_by_work_package
                    else None
                ),
            }
            for action, work_package in zip(actions, work_packages, strict=True)
        )
        cycle_decision = graph.cycle.terminal_outcome
        cycle_row = {
            "id": graph.cycle.id,
            "workstream_id": graph.workstream.id,
            "subject_type": "decision_brief",
            "subject_id": graph.brief.id,
            "brief_id": graph.brief.id,
            "decision_brief_version_id": graph.version.id,
            "status": "terminal",
            "decision": cycle_decision,
            "target_stage": cycle_decision,
            "decision_maker_id": graph.review.decided_by_id,
            "rationale": graph.review.decision_rationale,
            "decided_at": graph.review.decision_date,
        }
        condition_rows = tuple(
            {
                "id": row.id,
                "organization_id": actor.organization_id,
                "arb_cycle_id": row.review_cycle_id,
                "status": row.status,
                "accepted_evidence_id": (
                    row.fulfilment_evidence_id or row.submitted_evidence_id
                ),
                "approver_id": row.waived_by_id,
                "reason": row.waiver_reason,
                "expires_at": row.waiver_expires_at,
                "waiver_condition_id": row.id,
                "waiver_arb_cycle_id": row.review_cycle_id,
                "waiver_subject_type": "decision_brief",
                "waiver_subject_id": graph.brief.id,
            }
            for row in graph.conditions
        )
        accepted_condition_evidence_ids = {
            row["accepted_evidence_id"]
            for row in condition_rows
            if row["accepted_evidence_id"] is not None
        }
        projected_evidence_ids = set(graph.version.cited_evidence_ids or ()) | (
            accepted_condition_evidence_ids
        )
        evidence_rows = tuple(snapshot.evidence_records) + tuple(
            {"id": evidence_id, "status": "accepted"}
            for evidence_id in projected_evidence_ids
            if evidence_id
            not in {getattr(row, "id", None) for row in snapshot.evidence_records}
        )
        projected = replace(
            snapshot,
            evidence_records=evidence_rows,
            arb_cycles=(cycle_row,),
            arb_conditions=condition_rows,
            approved_actions=action_rows,
            unavailable_resources=snapshot.unavailable_resources
            - {"arb", "conditions", "approved_actions"},
        )
        return TransformationGateService.apply_locked_transition(
            session,
            actor,
            snapshot=projected,
            target_stage="execute",
            expected_revision=graph.workstream.revision,
        )

    @staticmethod
    def _assert_governance_ready(cycle, review, conditions, now):
        if cycle.status != "approved" or review.status != "approved":
            raise CommandConflict("execution_not_approved")
        if cycle.terminal_outcome not in {"approved", "approved_with_conditions"}:
            raise CommandConflict("execution_not_approved")
        if review.decision not in {"approved", "approved_with_conditions"}:
            raise CommandConflict("execution_not_approved")
        now = _utc(now)
        for condition in conditions:
            if condition.status == "fulfilled":
                continue
            if condition.status == "waived" and condition.waiver_expires_at:
                if _utc(condition.waiver_expires_at) > now:
                    continue
            raise CommandConflict("execution_conditions_unresolved")

    @classmethod
    def _require_execution_authority(cls, session, actor, graph, *, lock):
        TransformationProgrammeService._require_programme_authority(
            session,
            actor,
            graph.programme.id,
            graph.workstream.id,
            EXECUTION_ROLES,
            "execution_not_authorised",
            lock=lock,
        )

    @staticmethod
    def _materialisation_key(
        organization_id, decision_brief_version_id, artifact, natural_id, option_id
    ):
        return canonical_request_digest(
            {
                "organization_id": organization_id,
                "decision_brief_version_id": decision_brief_version_id,
                "artifact": artifact,
                "natural_id": natural_id,
                "option_version_id": option_id,
            }
        )

    @classmethod
    def _create_roadmap_item(cls, session, actor, graph, action, work_package):
        schedule_date = action["target_date"] or action["start_date"]
        scheduled = date.fromisoformat(schedule_date)
        row = RoadmapItem(
            organization_id=actor.organization_id,
            initiative_id=graph.programme.id,
            programme_workstream_id=graph.workstream.id,
            work_package_id=work_package.id,
            decision_brief_version_id=graph.version.id,
            materialisation_key=cls._materialisation_key(
                actor.organization_id,
                graph.version.id,
                "roadmap_item",
                action["action_key"],
                action["option_version_id"],
            ),
            title=action["title"],
            description=work_package.description,
            category=None,
            lane=None,
            quarter=f"Q{((scheduled.month - 1) // 3) + 1}",
            year=scheduled.year,
            status="planned",
            effort_estimate=None,
        )
        session.add(row)
        session.flush()
        return row

    @classmethod
    def _create_benefits(cls, session, actor, graph, work_package):
        outcomes = {row.id: row for row in graph.outcomes}
        benefits = []
        for measure in graph.measures:
            outcome = outcomes[measure.outcome_commitment_id]
            baseline = (
                measure.baseline_amount
                if measure.currency and measure.baseline_amount is not None
                else measure.baseline_value
            )
            target = (
                measure.target_amount
                if measure.currency and measure.target_amount is not None
                else measure.target_value
            )
            compatibility_available = bool(
                measure.unit
                and len(measure.unit) <= 40
                and _fits_legacy_benefit_numeric(baseline)
                and _fits_legacy_benefit_numeric(target)
            )
            source_parts = [
                value
                for value in (measure.source_adapter, measure.source_key)
                if value
            ]
            row = Benefit(
                organization_id=actor.organization_id,
                name=_bounded_display_text(outcome.statement, "outcome statement", 255),
                description=outcome.statement,
                benefit_type=None,
                status="planned",
                measure=measure.metric_name,
                unit=measure.unit if compatibility_available else None,
                baseline_value=baseline if compatibility_available else None,
                baseline_date=measure.baseline_date,
                target_value=target if compatibility_available else None,
                target_date=measure.target_date or outcome.target_date,
                owner_id=outcome.owner_id,
                measurement_method=":".join(source_parts) if source_parts else None,
                measurement_frequency=measure.cadence,
                strategic_initiative_id=graph.programme.id,
                programme_workstream_id=graph.workstream.id,
                outcome_commitment_id=outcome.id,
                decision_brief_version_id=graph.version.id,
                materialisation_key=cls._materialisation_key(
                    actor.organization_id,
                    graph.version.id,
                    "benefit",
                    f"{outcome.id}:{measure.id}",
                    graph.version.recommendation_option_version_id,
                ),
                work_package_id=work_package.id,
            )
            session.add(row)
            benefits.append(row)
        session.flush()
        return benefits

    @classmethod
    def _load_work_package(cls, session, actor, work_package_id, *, lock):
        statement = select(WorkPackage).where(
            WorkPackage.id == work_package_id,
            WorkPackage.organization_id == actor.organization_id,
            WorkPackage.strategic_initiative_id.is_not(None),
            WorkPackage.programme_workstream_id.is_not(None),
        )
        if lock:
            statement = statement.execution_options(
                populate_existing=True
            ).with_for_update()
        row = session.scalar(statement)
        if row is None:
            raise NotFound("work_package_not_found")
        return row

    @classmethod
    def _require_work_package_authority(
        cls, session, actor, work_package, *, lock
    ):
        TransformationProgrammeService._require_programme_authority(
            session,
            actor,
            work_package.strategic_initiative_id,
            work_package.programme_workstream_id,
            EXECUTION_ROLES,
            "delivery_export_not_authorised",
            lock=lock,
        )


__all__ = ["EXECUTION_ROLES", "TransformationExecutionService"]

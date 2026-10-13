"""Server-derived option comparisons and immutable decision-brief snapshots."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence
from types import SimpleNamespace

from sqlalchemy import func, or_, select, text, tuple_
from sqlalchemy.orm import Session

from app import db
from app.models.business_capabilities import BusinessCapability
from app.models.transformation_decision import (
    OPTION_EXCEPTION_TYPES,
    DecisionBrief,
    DecisionBriefVersion,
    DecisionEvent,
    TransformationOption,
    TransformationOptionVersion,
)
from app.models.transformation_evidence import (
    EvidenceClaimHead,
    EvidenceRecord,
    EvidenceRequest,
    TransformationCandidate,
)
from app.models.transformation_programme import (
    ISO_4217_CURRENCIES,
    MeasureDefinition,
    ProgrammeOutcomeCommitment,
    ProgrammeRoleAssignment,
    ProgrammeWorkstream,
)
from app.models.user import User
from app.models.unified_capability import ValueStream
from app.modules.transformation_room.command_service import (
    CommandService,
    OperationAuthorizer,
    canonical_request_document,
)
from app.modules.transformation_room.domain import (
    ActorContext,
    BlockedByEvidence,
    BriefReadiness,
    CommandConflict,
    CommandResult,
    DomainMutationResult,
    HumanAssertions,
    NotAuthorised,
    NotFound,
    OptionComparison,
)
from app.modules.transformation_room.gate_service import TransformationGateService
from app.modules.transformation_room.programme_service import (
    OBJECTIVE_ROLES,
    READ_ROLES,
    TransformationProgrammeService,
)


OPTION_DRAFT_ROLES = OBJECTIVE_ROLES
BRIEF_FREEZE_ROLES = OBJECTIVE_ROLES | frozenset({"decision_authority"})
DECISION_AUTHORITY_ROLES = frozenset(
    {
        "chief_architect",
        "cto",
        "enterprise_architect",
        "platform_admin",
        "organization_admin",
        "administrator",
        "decision_authority",
    }
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _decimal_string(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("canonical decimal must be finite")
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, Decimal):
        return _decimal_string(value)
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical number must be finite")
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"{type(value).__name__} is not canonical JSON")


def _canonical_json(value: Mapping[str, Any] | Sequence[Any]) -> str:
    return json.dumps(
        _canonical_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256_canonical(value: Mapping[str, Any] | Sequence[Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _positive_id(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must contain positive integer IDs")
    return value


def _id_sequence(values: Sequence[int], field: str, *, required: bool = True) -> tuple[int, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{field} must be a sequence")
    normalized = tuple(_positive_id(value, field) for value in values)
    if required and not normalized:
        raise ValueError(f"{field} is required")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"duplicate {field}")
    return normalized


def _required_text(value: Any, field: str) -> str:
    normalized = value.strip() if isinstance(value, str) else ""
    if not normalized:
        raise ValueError(f"{field} is required")
    return normalized


def _required_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"{field} is required")
    canonical = _canonical_value(value)
    if not isinstance(canonical, list) or not canonical:
        raise ValueError(f"{field} is required")
    return canonical


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{field} is required")
    if isinstance(value, float):
        raise ValueError(f"{field} must use Decimal or an exact decimal string")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field} must be numeric") from error
    if not parsed.is_finite():
        raise ValueError(f"{field} must be finite")
    return parsed


def _programme_for_workstream(session, actor, workstream_id, *, lock):
    scope = session.scalar(
        select(ProgrammeWorkstream).where(
            ProgrammeWorkstream.id == workstream_id,
            ProgrammeWorkstream.organization_id == actor.organization_id,
        )
    )
    if scope is None:
        raise NotFound("workstream_not_found")
    programme = TransformationProgrammeService._programme_query(
        session, actor, scope.programme_id, lock=lock
    ).scalar_one_or_none()
    if programme is None:
        raise NotFound("programme_not_found")
    TransformationProgrammeService._require_active_programme(programme)
    if lock:
        workstream = session.scalar(
            select(ProgrammeWorkstream)
            .where(
                ProgrammeWorkstream.id == scope.id,
                ProgrammeWorkstream.programme_id == programme.id,
                ProgrammeWorkstream.organization_id == actor.organization_id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if workstream is None:
            raise NotFound("workstream_not_found")
    else:
        workstream = scope
    return programme, workstream


class TransformationOptionService:
    """Freeze complete option drafts and compare only persisted versions."""

    @classmethod
    def create_draft(
        cls,
        *,
        actor: ActorContext,
        workstream_id: int,
        candidate_id: int | None,
        draft: Mapping[str, Any],
        command_key: str,
    ) -> CommandResult:
        workstream_id = _positive_id(workstream_id, "workstream_id")
        if candidate_id is not None:
            candidate_id = _positive_id(candidate_id, "candidate_id")
        if not isinstance(draft, Mapping):
            raise TypeError("draft must be an object")
        forbidden = {
            "id",
            "organization_id",
            "workstream_id",
            "candidate_id",
            "revision",
            "created_at",
            "updated_at",
        }
        supplied_forbidden = forbidden.intersection(draft)
        if supplied_forbidden:
            raise ValueError("server-owned option fields are not accepted")
        probe = SimpleNamespace(
            id=0,
            workstream_id=workstream_id,
            candidate_id=candidate_id,
            **dict(draft),
        )
        content = cls.canonical_option_payload(probe, 1)["content"]
        payload = {
            "workstream_id": workstream_id,
            "candidate_id": candidate_id,
            "content": content,
        }
        content_digest = _sha256_canonical(payload)
        natural_key = (
            f"option-draft:{workstream_id}:{candidate_id or 'workstream'}:"
            f"{content_digest}"
        )

        def authorize(session, runtime_actor, operation, supplied_key):
            if operation != "option.create" or supplied_key != natural_key:
                raise NotAuthorised("option_create_command_mismatch")
            programme, workstream = _programme_for_workstream(
                session, runtime_actor, workstream_id, lock=False
            )
            if workstream.lifecycle_stage != "options":
                raise CommandConflict("option_create_requires_options_stage")
            TransformationProgrammeService._require_programme_authority(
                session,
                runtime_actor,
                programme.id,
                workstream.id,
                OPTION_DRAFT_ROLES,
                "option_create_not_authorised",
            )

        return CommandService.execute(
            actor=actor,
            operation="option.create",
            idempotency_key=_required_text(command_key, "command_key"),
            payload=payload,
            natural_key=natural_key,
            authorizer=authorize,
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._create_draft_locked(
                session, actor, payload, claim
            ),
        )

    @classmethod
    def _create_draft_locked(cls, session, actor, payload, claim):
        programme, workstream = _programme_for_workstream(
            session, actor, payload["workstream_id"], lock=True
        )
        if workstream.lifecycle_stage != "options":
            raise CommandConflict("option_create_requires_options_stage")
        candidate_id = payload["candidate_id"]
        if candidate_id is not None:
            candidate = session.scalar(
                select(TransformationCandidate.id).where(
                    TransformationCandidate.id == candidate_id,
                    TransformationCandidate.organization_id == actor.organization_id,
                    TransformationCandidate.workstream_id == workstream.id,
                    TransformationCandidate.inclusion_status == "accepted",
                )
            )
            if candidate is None:
                raise NotFound("option_candidate_not_found")
        TransformationProgrammeService._require_programme_authority(
            session,
            actor,
            programme.id,
            workstream.id,
            OPTION_DRAFT_ROLES,
            "option_create_not_authorised",
            lock=True,
        )
        content = dict(payload["content"])
        cls._lock_reference_entities(session, actor, content)
        option = TransformationOption(
            organization_id=actor.organization_id,
            workstream_id=workstream.id,
            candidate_id=candidate_id,
            **content,
            revision=1,
        )
        session.add(option)
        session.flush()
        response = {
            "option_id": option.id,
            "workstream_id": workstream.id,
            "candidate_id": candidate_id,
            "revision": option.revision,
        }
        return DomainMutationResult(
            {"option_id": option.id},
            response,
            (
                {
                    "event_type": "transformation.option_created",
                    "payload": {
                        **response,
                        "command_receipt_id": claim.receipt_id,
                        "command_generation": claim.generation,
                    },
                },
            ),
        )

    @classmethod
    def freeze_version(
        cls,
        *,
        actor: ActorContext,
        option_id: int,
        expected_revision: int,
        command_key: str,
    ) -> CommandResult:
        option_id = _positive_id(option_id, "option_id")
        expected_revision = _positive_id(expected_revision, "expected_revision")
        option = cls.load_option_for_tenant(actor, option_id)
        cls.authorise_draft(actor, option)
        payload = cls.canonical_option_payload(option, expected_revision)
        return CommandService.execute(
            actor=actor,
            operation="option.freeze",
            idempotency_key=command_key,
            payload=payload,
            natural_key=f"option:{option.id}:version:{expected_revision}",
            authorizer=cls.authorise_option_freeze(option.id, expected_revision),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._lock_validate_and_insert_version(
                session, actor, option.id, payload, claim
            ),
        )

    @classmethod
    def load_option_for_tenant(cls, actor: ActorContext, option_id: int):
        with Session(db.engine) as session:
            option = session.scalar(
                select(TransformationOption).where(
                    TransformationOption.id == option_id,
                    TransformationOption.organization_id == actor.organization_id,
                )
            )
            if option is None:
                raise NotFound("option_not_found")
            session.expunge(option)
            return option

    @classmethod
    def authorise_draft(cls, actor, option, *, session=None):
        owns_session = session is None
        session = session or Session(db.engine)
        try:
            programme, workstream = _programme_for_workstream(
                session, actor, option.workstream_id, lock=False
            )
            TransformationProgrammeService._require_programme_authority(
                session,
                actor,
                programme.id,
                workstream.id,
                OPTION_DRAFT_ROLES,
                "option_freeze_not_authorised",
            )
        finally:
            if owns_session:
                session.close()

    @classmethod
    def canonical_option_payload(cls, option, expected_revision):
        currency = (
            option.currency.strip().upper()
            if isinstance(option.currency, str)
            else option.currency
        )
        if currency not in ISO_4217_CURRENCIES:
            raise ValueError("currency must be an ISO 4217 code")
        if not isinstance(option.technology_required, bool):
            raise ValueError("technology_required is required")
        cost_min = _decimal(option.cost_min, "cost_min")
        cost_max = _decimal(option.cost_max, "cost_max")
        benefit_min = _decimal(option.benefit_min, "benefit_min")
        benefit_max = _decimal(option.benefit_max, "benefit_max")
        risk_min = _decimal(option.risk_min, "risk_min")
        risk_max = _decimal(option.risk_max, "risk_max")
        for field, minimum, maximum in (
            ("cost", cost_min, cost_max),
            ("benefit", benefit_min, benefit_max),
            ("risk", risk_min, risk_max),
        ):
            if minimum > maximum:
                raise ValueError(f"{field} range must be ordered")
        content = {
            "title": _required_text(option.title, "title"),
            "action_type": _required_text(option.action_type, "action_type"),
            "description": _required_text(option.description, "description"),
            "assumptions": _required_list(option.assumptions, "assumptions"),
            "dependencies": _required_list(option.dependencies, "dependencies"),
            "impacts": _required_list(option.impacts, "impacts"),
            "risks": _required_list(option.risks, "risks"),
            "reversibility": _required_text(option.reversibility, "reversibility"),
            "transition_approach": _required_text(
                option.transition_approach, "transition_approach"
            ),
            "affected_capability_ids": _id_sequence(
                option.affected_capability_ids,
                "affected_capability_ids",
            ),
            "affected_value_stream_ids": _id_sequence(
                option.affected_value_stream_ids,
                "affected_value_stream_ids",
            ),
            "recommendation_rationale": _required_text(
                option.recommendation_rationale, "recommendation_rationale"
            ),
            "cost_min": cost_min,
            "cost_max": cost_max,
            "benefit_min": benefit_min,
            "benefit_max": benefit_max,
            "risk_min": risk_min,
            "risk_max": risk_max,
            "currency": currency,
            "technology_required": option.technology_required,
        }
        return _canonical_value(
            {
                "option_id": option.id,
                "workstream_id": option.workstream_id,
                "candidate_id": option.candidate_id,
                "expected_revision": expected_revision,
                "content": content,
            }
        )

    @classmethod
    def authorise_option_freeze(
        cls, option_id: int, expected_revision: int
    ) -> OperationAuthorizer:
        expected_key = f"option:{option_id}:version:{expected_revision}"

        def authorize(session, actor, operation, natural_key):
            if operation != "option.freeze" or natural_key != expected_key:
                raise NotAuthorised("option_freeze_command_mismatch")
            option = session.scalar(
                select(TransformationOption).where(
                    TransformationOption.id == option_id,
                    TransformationOption.organization_id == actor.organization_id,
                )
            )
            if option is None:
                raise NotFound("option_not_found")
            cls.authorise_draft(actor, option, session=session)

        return authorize

    @classmethod
    def _lock_validate_and_insert_version(
        cls, session, actor, option_id, payload, claim
    ):
        scope = session.scalar(
            select(TransformationOption).where(
                TransformationOption.id == option_id,
                TransformationOption.organization_id == actor.organization_id,
            )
        )
        if scope is None:
            raise NotFound("option_not_found")
        programme, workstream = _programme_for_workstream(
            session, actor, scope.workstream_id, lock=True
        )
        option = session.scalar(
            select(TransformationOption)
            .where(
                TransformationOption.id == option_id,
                TransformationOption.workstream_id == workstream.id,
                TransformationOption.organization_id == actor.organization_id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if option is None:
            raise NotFound("option_not_found")
        expected_revision = payload["expected_revision"]
        if option.revision != expected_revision:
            raise CommandConflict("stale_revision")
        if option.candidate_id is not None:
            candidate = session.scalar(
                select(TransformationCandidate.id).where(
                    TransformationCandidate.id == option.candidate_id,
                    TransformationCandidate.organization_id == actor.organization_id,
                    TransformationCandidate.workstream_id == workstream.id,
                    TransformationCandidate.inclusion_status == "accepted",
                )
            )
            if candidate is None:
                raise CommandConflict("option_candidate_scope_changed")
        TransformationProgrammeService._require_programme_authority(
            session,
            actor,
            programme.id,
            workstream.id,
            OPTION_DRAFT_ROLES,
            "option_freeze_not_authorised",
            lock=True,
        )
        locked_payload = cls.canonical_option_payload(option, expected_revision)
        if locked_payload != payload:
            raise CommandConflict("option_draft_changed")
        next_version = (
            session.scalar(
                select(func.max(TransformationOptionVersion.version)).where(
                    TransformationOptionVersion.organization_id == actor.organization_id,
                    TransformationOptionVersion.option_id == option.id,
                )
            )
            or 0
        ) + 1
        captured_at = CommandService._database_now(session)
        content = locked_payload["content"]
        version = TransformationOptionVersion(
            organization_id=actor.organization_id,
            option_id=option.id,
            workstream_id=workstream.id,
            candidate_id=option.candidate_id,
            version=next_version,
            source_revision=expected_revision,
            content_json=content,
            cost_min=_decimal(content["cost_min"], "cost_min"),
            cost_max=_decimal(content["cost_max"], "cost_max"),
            benefit_min=_decimal(content["benefit_min"], "benefit_min"),
            benefit_max=_decimal(content["benefit_max"], "benefit_max"),
            risk_min=_decimal(content["risk_min"], "risk_min"),
            risk_max=_decimal(content["risk_max"], "risk_max"),
            currency=content["currency"],
            technology_required=content["technology_required"],
            captured_by_id=actor.user_id,
            captured_at=captured_at,
            content_hash="0" * 64,
        )
        cls._lock_reference_entities(session, actor, content)
        version.content_hash = _sha256_canonical(
            cls.reconstruct_canonical_version(version)
        )
        session.add(version)
        option.revision += 1
        option.updated_at = captured_at
        session.flush()
        response = {
            "option_id": option.id,
            "option_version_id": version.id,
            "version": version.version,
            "source_revision": version.source_revision,
            "content_hash": version.content_hash,
        }
        return DomainMutationResult(
            {"option_id": option.id, "option_version_id": version.id},
            response,
            (
                {
                    "event_type": "transformation.option_version_frozen",
                    "payload": {
                        **response,
                        "workstream_id": workstream.id,
                        "command_receipt_id": claim.receipt_id,
                        "command_generation": claim.generation,
                    },
                },
            ),
        )

    @classmethod
    def reconstruct_canonical_version(cls, version: TransformationOptionVersion):
        return {
            "schema_version": "transformation-option-r1.1",
            "organization_id": version.organization_id,
            "option_id": version.option_id,
            "workstream_id": version.workstream_id,
            "candidate_id": version.candidate_id,
            "version": version.version,
            "source_revision": version.source_revision,
            "captured_by_id": version.captured_by_id,
            "captured_at": version.captured_at,
            "content": version.content_json,
            "comparison": {
                "cost_min": version.cost_min,
                "cost_max": version.cost_max,
                "benefit_min": version.benefit_min,
                "benefit_max": version.benefit_max,
                "risk_min": version.risk_min,
                "risk_max": version.risk_max,
                "currency": version.currency,
                "technology_required": version.technology_required,
            },
        }

    @classmethod
    def verify_version_hash(cls, version: TransformationOptionVersion) -> bool:
        return hmac.compare_digest(
            version.content_hash,
            _sha256_canonical(cls.reconstruct_canonical_version(version)),
        )

    @staticmethod
    def _lock_reference_entities(session, actor, content):
        capability_ids = tuple(content["affected_capability_ids"])
        value_stream_ids = tuple(content["affected_value_stream_ids"])
        capabilities = tuple(
            session.scalars(
                select(BusinessCapability.id)
                .where(
                    BusinessCapability.organization_id == actor.organization_id,
                    BusinessCapability.id.in_(capability_ids),
                )
                .order_by(BusinessCapability.id)
                .with_for_update()
            ).all()
        )
        if set(capabilities) != set(capability_ids):
            raise NotFound("affected_capabilities_not_found")
        value_streams = tuple(
            session.scalars(
                select(ValueStream.id)
                .where(
                    ValueStream.organization_id == actor.organization_id,
                    ValueStream.id.in_(value_stream_ids),
                )
                .order_by(ValueStream.id)
                .with_for_update()
            ).all()
        )
        if set(value_streams) != set(value_stream_ids):
            raise NotFound("affected_value_streams_not_found")

    @classmethod
    def compare(
        cls,
        *,
        actor: ActorContext,
        option_version_ids: Sequence[int],
    ) -> OptionComparison:
        versions = cls.load_versions_for_tenant(actor, option_version_ids)
        cls.require_same_decision_scope(versions)
        currency = cls.single_currency_or_none(versions)
        return OptionComparison(
            tuple(row.id for row in versions),
            currency,
            cls.aggregate_range(versions, "cost", currency),
            cls.aggregate_range(versions, "benefit", currency),
            tuple(cls.comparison_conflicts(versions, currency)),
        )

    @classmethod
    def load_versions_for_tenant(cls, actor, option_version_ids):
        requested = _id_sequence(option_version_ids, "option_version_ids")
        with Session(db.engine) as session:
            rows = session.scalars(
                select(TransformationOptionVersion).where(
                    TransformationOptionVersion.organization_id == actor.organization_id,
                    TransformationOptionVersion.id.in_(requested),
                )
            ).all()
            by_id = {row.id: row for row in rows}
            if len(by_id) != len(requested):
                raise NotFound("option_versions_not_found")
            versions = tuple(by_id[row_id] for row_id in requested)
            cls.require_same_decision_scope(versions)
            if any(not cls.verify_version_hash(row) for row in versions):
                raise CommandConflict("option_version_hash_invalid")
            programme, workstream = _programme_for_workstream(
                session, actor, versions[0].workstream_id, lock=False
            )
            TransformationProgrammeService._require_programme_authority(
                session,
                actor,
                programme.id,
                workstream.id,
                READ_ROLES,
                "option_read_not_authorised",
            )
            session.expunge_all()
            return versions

    @staticmethod
    def require_same_decision_scope(versions):
        scopes = {(row.workstream_id, row.candidate_id) for row in versions}
        if len(scopes) != 1:
            raise ValueError("option versions must share one decision scope")

    @staticmethod
    def single_currency_or_none(versions):
        currencies = {row.currency for row in versions}
        return next(iter(currencies)) if len(currencies) == 1 else None

    @staticmethod
    def aggregate_range(versions, prefix, currency):
        if currency is None:
            return None
        minima = [getattr(row, f"{prefix}_min") for row in versions]
        maxima = [getattr(row, f"{prefix}_max") for row in versions]
        if any(value is None or not value.is_finite() for value in (*minima, *maxima)):
            return None
        return min(minima), max(maxima)

    @staticmethod
    def comparison_conflicts(_versions, currency):
        return () if currency is not None else ("currency_mismatch",)


class DecisionBriefService:
    """Evaluate and freeze exact option/evidence/outcome decision dossiers."""

    @classmethod
    def create_brief(
        cls,
        *,
        actor: ActorContext,
        workstream_id: int,
        candidate_id: int | None,
        title: str,
        recommendation_option_id: int,
        decision_authority_id: int,
        unknown_codes: Sequence[str],
        conflicts: Sequence[str],
        expected_impacts: Sequence[str],
        command_key: str,
        option_exception: Mapping[str, Any] | None = None,
    ) -> CommandResult:
        """Create one governed, tenant-scoped draft through the DB boundary."""
        workstream_id = _positive_id(workstream_id, "workstream_id")
        if candidate_id is not None:
            candidate_id = _positive_id(candidate_id, "candidate_id")
        recommendation_option_id = _positive_id(
            recommendation_option_id, "recommendation_option_id"
        )
        decision_authority_id = _positive_id(
            decision_authority_id, "decision_authority_id"
        )

        def text_values(values, field):
            if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
                raise ValueError(f"{field} must be a sequence")
            normalized = tuple(_required_text(value, field) for value in values)
            if len(set(normalized)) != len(normalized):
                raise ValueError(f"duplicate {field}")
            return normalized

        exception = None
        if option_exception is not None:
            if not isinstance(option_exception, Mapping):
                raise ValueError("option_exception must be a mapping")
            if set(option_exception) != {"type", "name", "reason", "authority_id"}:
                raise ValueError("option_exception fields are invalid")
            exception_type = _required_text(
                option_exception.get("type"), "option_exception.type"
            )
            if exception_type not in OPTION_EXCEPTION_TYPES:
                raise ValueError("option_exception.type is invalid")
            exception = {
                "type": exception_type,
                "name": _required_text(
                    option_exception.get("name"), "option_exception.name"
                ),
                "reason": _required_text(
                    option_exception.get("reason"), "option_exception.reason"
                ),
                "authority_id": _positive_id(
                    option_exception.get("authority_id"),
                    "option_exception.authority_id",
                ),
            }
        request = _canonical_value(
            {
                "workstream_id": workstream_id,
                "candidate_id": candidate_id,
                "title": _required_text(title, "title"),
                "recommendation_option_id": recommendation_option_id,
                "decision_authority_id": decision_authority_id,
                "unknown_codes": text_values(unknown_codes, "unknown_codes"),
                "conflicts": text_values(conflicts, "conflicts"),
                "expected_impacts": text_values(expected_impacts, "expected_impacts"),
                "option_exception": exception,
            }
        )
        natural_key = (
            f"brief:workstream:{workstream_id}:candidate:"
            f"{candidate_id if candidate_id is not None else 'all'}"
        )
        return CommandService.execute(
            actor=actor,
            operation="brief.create",
            idempotency_key=command_key,
            payload=request,
            natural_key=natural_key,
            authorizer=cls.authorise_brief_create(
                workstream_id, candidate_id, recommendation_option_id,
                decision_authority_id, exception
            ),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._create_locked_draft(
                session, actor, request, claim
            ),
        )

    @classmethod
    def authorise_brief_create(
        cls,
        workstream_id,
        candidate_id,
        recommendation_option_id,
        decision_authority_id,
        option_exception,
    ) -> OperationAuthorizer:
        expected_key = (
            f"brief:workstream:{workstream_id}:candidate:"
            f"{candidate_id if candidate_id is not None else 'all'}"
        )

        def authorize(session, actor, operation, natural_key):
            if operation != "brief.create" or natural_key != expected_key:
                raise NotAuthorised("brief_create_command_mismatch")
            programme, workstream = _programme_for_workstream(
                session, actor, workstream_id, lock=False
            )
            TransformationProgrammeService._require_programme_authority(
                session,
                actor,
                programme.id,
                workstream.id,
                OPTION_DRAFT_ROLES,
                "brief_create_not_authorised",
            )
            if candidate_id is not None:
                candidate = session.scalar(
                    select(TransformationCandidate.id).where(
                        TransformationCandidate.id == candidate_id,
                        TransformationCandidate.organization_id
                        == actor.organization_id,
                        TransformationCandidate.workstream_id == workstream.id,
                        TransformationCandidate.inclusion_status == "accepted",
                    )
                )
                if candidate is None:
                    raise NotFound("brief_candidate_not_found")
            option_scope = (
                TransformationOption.candidate_id.is_(None)
                if candidate_id is None
                else TransformationOption.candidate_id == candidate_id
            )
            recommendation = session.scalar(
                select(TransformationOption.id).where(
                    TransformationOption.id == recommendation_option_id,
                    TransformationOption.organization_id == actor.organization_id,
                    TransformationOption.workstream_id == workstream.id,
                    option_scope,
                )
            )
            if recommendation is None:
                raise NotFound("brief_recommendation_not_found")
            if not cls._user_has_decision_authority(
                session,
                actor.organization_id,
                programme.id,
                workstream.id,
                decision_authority_id,
                lock=False,
            ):
                raise NotAuthorised("decision_authority_invalid")
            if option_exception is not None and not cls._user_has_decision_authority(
                session,
                actor.organization_id,
                programme.id,
                workstream.id,
                option_exception["authority_id"],
                lock=False,
            ):
                raise NotAuthorised("option_exception_authority_invalid")

        return authorize

    @staticmethod
    def _created_brief_mutation(brief_id, revision):
        response = {"decision_brief_id": brief_id, "revision": revision}
        return DomainMutationResult(
            {"decision_brief_id": brief_id},
            response,
            (
                {
                    "event_type": "transformation.decision_brief_created",
                    "payload": response,
                },
            ),
        )

    @classmethod
    def _create_locked_draft(cls, session, actor, request, claim):
        schema = session.scalar(text("SELECT current_schema()"))
        quoted_schema = session.bind.dialect.identifier_preparer.quote(schema)
        created = session.execute(
            text(
                f"SELECT * FROM {quoted_schema}.archie_create_decision_brief("  # nosec B608 -- current schema is dialect-quoted; function name is literal; values are bound
                "CAST(:capability_document AS text), CAST(:capability AS text), "
                "CAST(:request_document AS text))"
            ),
            {
                "capability_document": claim.capability_document,
                "capability": claim.capability_mac,
                "request_document": canonical_request_document(request),
            },
        ).mappings().one()
        return cls._created_brief_mutation(
            created["decision_brief_id"], created["decision_brief_revision"]
        )

    @classmethod
    def evaluate(cls, *, actor: ActorContext, brief_id: int) -> BriefReadiness:
        brief = cls.load_brief_for_tenant(actor, brief_id)
        option_ids = cls.current_option_version_ids(brief, actor=actor)
        evidence_ids = cls.current_evidence_ids(brief, actor=actor)
        gate = TransformationGateService.evaluate(
            actor=actor,
            workstream_id=brief.workstream_id,
            target_stage="decision_ready",
            decision_candidate_id=brief.candidate_id,
        )
        return BriefReadiness(gate.allowed, gate, option_ids, evidence_ids)

    @classmethod
    def load_brief_for_tenant(cls, actor, brief_id):
        brief_id = _positive_id(brief_id, "brief_id")
        with Session(db.engine) as session:
            brief = session.scalar(
                select(DecisionBrief).where(
                    DecisionBrief.id == brief_id,
                    DecisionBrief.organization_id == actor.organization_id,
                )
            )
            if brief is None:
                raise NotFound("decision_brief_not_found")
            programme, workstream = _programme_for_workstream(
                session, actor, brief.workstream_id, lock=False
            )
            TransformationProgrammeService._require_programme_authority(
                session,
                actor,
                programme.id,
                workstream.id,
                READ_ROLES,
                "brief_read_not_authorised",
            )
            session.expunge(brief)
            return brief

    @classmethod
    def require_latest_frozen_version(cls, brief):
        """Load the latest immutable version belonging to a tenant-bound brief."""
        organization_id = getattr(brief, "organization_id", None)
        if organization_id is None:
            raise NotFound("decision_brief_not_found")
        with Session(db.engine) as session:
            version = session.scalar(
                select(DecisionBriefVersion)
                .where(
                    DecisionBriefVersion.organization_id == organization_id,
                    DecisionBriefVersion.brief_id == brief.id,
                    DecisionBriefVersion.workstream_id == brief.workstream_id,
                )
                .order_by(
                    DecisionBriefVersion.version.desc(),
                    DecisionBriefVersion.id.desc(),
                )
            )
            if version is None:
                raise NotFound("decision_brief_not_found")
            session.expunge(version)
            return version

    @classmethod
    def require_version_for_tenant(cls, actor, version_id):
        """Load one immutable brief version without disclosing foreign IDs."""
        version_id = _positive_id(version_id, "version_id")
        with Session(db.engine) as session:
            version = session.scalar(
                select(DecisionBriefVersion).where(
                    DecisionBriefVersion.id == version_id,
                    DecisionBriefVersion.organization_id == actor.organization_id,
                )
            )
            if version is None:
                raise NotFound("decision_brief_not_found")
            session.expunge(version)
            return version

    @classmethod
    def current_option_version_ids(cls, brief, *, actor):
        with Session(db.engine) as session:
            candidate_scope = (
                TransformationOptionVersion.candidate_id.is_(None)
                if brief.candidate_id is None
                else TransformationOptionVersion.candidate_id == brief.candidate_id
            )
            rows = session.scalars(
                select(TransformationOptionVersion)
                .where(
                    TransformationOptionVersion.organization_id == actor.organization_id,
                    TransformationOptionVersion.workstream_id == brief.workstream_id,
                    candidate_scope,
                )
                .order_by(
                    TransformationOptionVersion.option_id,
                    TransformationOptionVersion.version.desc(),
                )
            ).all()
            latest = {}
            for row in rows:
                latest.setdefault(row.option_id, row)
            current = tuple(latest[key] for key in sorted(latest))
            if any(
                not TransformationOptionService.verify_version_hash(row)
                for row in current
            ):
                raise CommandConflict("option_version_hash_invalid")
            return tuple(row.id for row in current)

    @classmethod
    def current_evidence_ids(cls, brief, *, actor):
        with Session(db.engine) as session:
            candidates = session.scalars(
                select(TransformationCandidate).where(
                    TransformationCandidate.organization_id == actor.organization_id,
                    TransformationCandidate.workstream_id == brief.workstream_id,
                    TransformationCandidate.inclusion_status == "accepted",
                )
            ).all()
            if brief.candidate_id is not None:
                candidates = [row for row in candidates if row.id == brief.candidate_id]
            pairs = {(row.subject_type, row.subject_id) for row in candidates}
            if not pairs:
                return ()
            ids = session.scalars(
                select(EvidenceClaimHead.current_record_id)
                .where(
                    EvidenceClaimHead.organization_id == actor.organization_id,
                    tuple_(
                        EvidenceClaimHead.subject_type, EvidenceClaimHead.subject_id
                    ).in_(sorted(pairs)),
                    EvidenceClaimHead.current_record_id.is_not(None),
                )
                .order_by(
                    EvidenceClaimHead.subject_type,
                    EvidenceClaimHead.subject_id,
                    EvidenceClaimHead.claim_key,
                    EvidenceClaimHead.source_identity,
                    EvidenceClaimHead.id,
                )
            ).all()
            return tuple(sorted(set(ids)))

    @classmethod
    def freeze(
        cls,
        *,
        actor: ActorContext,
        brief_id: int,
        option_version_ids: Sequence[int],
        evidence_ids: Sequence[int],
        assertions: HumanAssertions,
        expected_revision: int,
        command_key: str,
    ) -> CommandResult:
        request = cls.build_freeze_request(
            actor,
            brief_id,
            option_version_ids,
            evidence_ids,
            assertions,
            expected_revision,
        )
        return CommandService.execute(
            actor=actor,
            operation="brief.freeze",
            idempotency_key=command_key,
            payload=request,
            natural_key=f"brief:{brief_id}:version:{expected_revision}",
            authorizer=cls.authorise_brief_freeze(brief_id, expected_revision),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._freeze_locked_snapshot(
                session, actor, request, claim
            ),
        )

    @classmethod
    def build_freeze_request(
        cls,
        actor,
        brief_id,
        option_version_ids,
        evidence_ids,
        assertions,
        expected_revision,
    ):
        brief = cls.load_brief_for_tenant(actor, brief_id)
        option_ids = tuple(
            sorted(_id_sequence(option_version_ids, "option_version_ids"))
        )
        cited_ids = tuple(sorted(_id_sequence(evidence_ids, "evidence_ids")))
        expected_revision = _positive_id(expected_revision, "expected_revision")
        if isinstance(assertions, Mapping):
            forbidden = {
                "client_totals",
                "comparison_totals",
                "cost_total",
                "benefit_total",
            }
            if forbidden.intersection(assertions):
                raise ValueError("client totals are not accepted")
            try:
                assertions = HumanAssertions(**dict(assertions))
            except TypeError as error:
                raise ValueError("invalid human assertions") from error
        if not isinstance(assertions, HumanAssertions):
            raise ValueError("assertions must be HumanAssertions")
        if not isinstance(assertions.reviewed_ai_material, bool):
            raise ValueError("reviewed_ai_material must be boolean")
        unknown_codes = tuple(
            _required_text(value, "acknowledged_unknown_codes")
            for value in assertions.acknowledged_unknown_codes
        )
        if len(set(unknown_codes)) != len(unknown_codes):
            raise ValueError("duplicate acknowledged_unknown_codes")
        superseded_ids = _id_sequence(
            assertions.acknowledged_superseded_evidence_ids,
            "acknowledged_superseded_evidence_ids",
            required=False,
        )
        rationale = _required_text(assertions.rationale, "assertion rationale")
        return _canonical_value(
            {
                "brief_id": brief.id,
                "workstream_id": brief.workstream_id,
                "option_version_ids": option_ids,
                "evidence_ids": cited_ids,
                "assertions": {
                    "reviewed_ai_material": assertions.reviewed_ai_material,
                    "acknowledged_unknown_codes": unknown_codes,
                    "acknowledged_superseded_evidence_ids": superseded_ids,
                    "rationale": rationale,
                },
                "expected_revision": expected_revision,
            }
        )

    @classmethod
    def authorise_brief_freeze(
        cls, brief_id: int, expected_revision: int
    ) -> OperationAuthorizer:
        expected_key = f"brief:{brief_id}:version:{expected_revision}"

        def authorize(session, actor, operation, natural_key):
            if operation != "brief.freeze" or natural_key != expected_key:
                raise NotAuthorised("brief_freeze_command_mismatch")
            brief = session.scalar(
                select(DecisionBrief).where(
                    DecisionBrief.id == brief_id,
                    DecisionBrief.organization_id == actor.organization_id,
                )
            )
            if brief is None:
                raise NotFound("decision_brief_not_found")
            programme, workstream = _programme_for_workstream(
                session, actor, brief.workstream_id, lock=False
            )
            TransformationProgrammeService._require_programme_authority(
                session,
                actor,
                programme.id,
                workstream.id,
                BRIEF_FREEZE_ROLES,
                "brief_freeze_not_authorised",
            )

        return authorize

    @classmethod
    def _freeze_locked_snapshot(cls, session, actor, request, claim):
        scope = session.scalar(
            select(DecisionBrief).where(
                DecisionBrief.id == request["brief_id"],
                DecisionBrief.organization_id == actor.organization_id,
            )
        )
        if scope is None:
            raise NotFound("decision_brief_not_found")
        programme, workstream = _programme_for_workstream(
            session, actor, scope.workstream_id, lock=True
        )
        brief = session.scalar(
            select(DecisionBrief)
            .where(
                DecisionBrief.id == scope.id,
                DecisionBrief.workstream_id == workstream.id,
                DecisionBrief.organization_id == actor.organization_id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if brief is None:
            raise NotFound("decision_brief_not_found")
        if brief.revision != request["expected_revision"]:
            raise CommandConflict("stale_revision")
        if brief.status != "draft":
            raise CommandConflict("brief_not_draft")
        cls._lock_decision_principals(session, actor, brief)
        TransformationProgrammeService._require_programme_authority(
            session,
            actor,
            programme.id,
            workstream.id,
            BRIEF_FREEZE_ROLES,
            "brief_freeze_not_authorised",
            lock=True,
        )
        if not cls._user_has_decision_authority(
            session,
            actor.organization_id,
            programme.id,
            workstream.id,
            brief.decision_authority_id,
            lock=True,
        ):
            raise NotAuthorised("decision_authority_invalid")
        versions = cls._lock_option_versions(
            session, actor, brief, request["option_version_ids"]
        )
        cls._require_options_gate(session, actor, workstream, brief)
        cls._require_viable_options(session, actor, brief, versions, workstream)
        recommendation = cls._recommendation_version(brief, versions)
        candidates, candidate = cls._lock_candidate_scope(session, actor, brief)
        evidence_rows, heads, evidence_citations = cls._lock_evidence_snapshot(
            session,
            actor,
            candidates,
            request["evidence_ids"],
            request["assertions"],
        )
        outcomes, measures = cls._lock_outcomes_and_measures(
            session, actor, programme, workstream
        )
        cls._validate_assertions(brief, request["assertions"], request["evidence_ids"])
        if not outcomes or not measures:
            raise BlockedByEvidence("outcome_measure_snapshot_required")
        next_version = (
            session.scalar(
                select(func.max(DecisionBriefVersion.version)).where(
                    DecisionBriefVersion.organization_id == actor.organization_id,
                    DecisionBriefVersion.brief_id == brief.id,
                )
            )
            or 0
        ) + 1
        created_at = CommandService._database_now(session)
        frozen_payload = cls._build_frozen_payload(
            actor=actor,
            programme=programme,
            workstream=workstream,
            brief=brief,
            candidate=candidate,
            versions=versions,
            recommendation=recommendation,
            evidence_rows=evidence_rows,
            heads=heads,
            evidence_citations=evidence_citations,
            outcomes=outcomes,
            measures=measures,
            assertions=request["assertions"],
            version=next_version,
            source_revision=request["expected_revision"],
            created_at=created_at,
        )
        request_document = canonical_request_document(request)
        canonical_document = _canonical_json(
            cls._hash_envelope(
                organization_id=actor.organization_id,
                brief_id=brief.id,
                workstream_id=workstream.id,
                version=next_version,
                source_revision=request["expected_revision"],
                created_by_id=actor.user_id,
                created_at=created_at,
                frozen_payload=frozen_payload,
                recommendation_option_version_id=recommendation.id,
                option_version_ids=[row.id for row in versions],
                cited_evidence_ids=[row.id for row in evidence_rows],
                outcome_ids=[row.id for row in outcomes],
                measure_ids=[row.id for row in measures],
                policy_version=TransformationGateService.POLICY_VERSION,
                submitted_by_id=actor.user_id,
                submitter_authorized=True,
                decision_authority_id=brief.decision_authority_id,
                human_reviewed_ai=True,
                blockers_cleared=True,
                unknowns_acknowledged=True,
            )
        )
        from_state = brief.status
        with session.begin_nested():
            schema = session.scalar(text("SELECT current_schema()"))
            quoted_schema = session.bind.dialect.identifier_preparer.quote(schema)
            frozen = session.execute(
                text(
                    f"SELECT * FROM {quoted_schema}.archie_freeze_decision_brief_version("  # nosec B608 -- current schema is dialect-quoted; function name is literal; values are bound
                    "CAST(:brief_id AS bigint), CAST(:actor_id AS bigint), "
                    "CAST(:receipt_id AS bigint), CAST(:generation AS integer), "
                    "CAST(:claim_token AS text), CAST(:capability_document AS text), "
                    "CAST(:capability_mac AS text), "
                    "CAST(:expected_revision AS integer), "
                    "CAST(:request_document AS text), "
                    "CAST(:frozen_payload AS jsonb), "
                    "CAST(:canonical_document AS text))"
                ),
                {
                    "brief_id": brief.id,
                    "actor_id": actor.user_id,
                    "receipt_id": claim.receipt_id,
                    "generation": claim.generation,
                    "claim_token": claim.claim_token,
                    "capability_document": claim.capability_document,
                    "capability_mac": claim.capability_mac,
                    "expected_revision": request["expected_revision"],
                    "request_document": request_document,
                    "frozen_payload": json.dumps(
                        frozen_payload,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ),
                    "canonical_document": canonical_document,
                },
            ).mappings().one()
        decision_event = DecisionEvent(
            organization_id=actor.organization_id,
            brief_id=brief.id,
            brief_version_id=frozen["decision_brief_version_id"],
            event_type="brief.version_frozen",
            from_state=from_state,
            to_state="frozen",
            actor_id=actor.user_id,
            rationale=request["assertions"]["rationale"],
            conditions_json=[],
            source_review_id=None,
            command_receipt_id=claim.receipt_id,
            command_generation=claim.generation,
            created_at=frozen["decision_brief_created_at"],
        )
        session.add(decision_event)
        session.flush()
        response = {
            "decision_brief_id": brief.id,
            "decision_brief_version_id": frozen["decision_brief_version_id"],
            "decision_event_id": decision_event.id,
            "version": frozen["decision_brief_version_number"],
            "content_hash": frozen["decision_brief_content_hash"],
            "policy_version": TransformationGateService.POLICY_VERSION,
        }
        return DomainMutationResult(
            {
                "decision_brief_id": brief.id,
                "decision_brief_version_id": frozen["decision_brief_version_id"],
                "decision_event_id": decision_event.id,
            },
            response,
            (
                {
                    "event_type": "transformation.decision_brief_frozen",
                    "payload": {
                        **response,
                        "workstream_id": workstream.id,
                        "option_version_ids": [row.id for row in versions],
                        "evidence_ids": [row.id for row in evidence_rows],
                    },
                },
            ),
        )

    @classmethod
    def _require_options_gate(cls, session, actor, workstream, brief):
        snapshot = TransformationGateService._load_policy_snapshot(
            session=session,
            actor=actor,
            workstream_id=workstream.id,
            lock=True,
        )
        snapshot = TransformationGateService.for_decision_scope(
            snapshot, brief.candidate_id
        )
        transition = TransformationGateService.require_valid_transition(
            snapshot.workstream.lifecycle_stage, "decision_ready"
        )
        blockers, warnings, _evidence_ids = TransformationGateService.evaluate_requirements(
            snapshot, transition
        )
        if blockers:
            raise BlockedByEvidence(
                "gate_requirements_not_met",
                blockers=tuple(blockers),
                warnings=tuple(warnings),
                policy_version=TransformationGateService.POLICY_VERSION,
            )

    @classmethod
    def _lock_option_versions(cls, session, actor, brief, requested_ids):
        option_scope = (
            TransformationOption.candidate_id.is_(None)
            if brief.candidate_id is None
            else TransformationOption.candidate_id == brief.candidate_id
        )
        option_roots = tuple(
            session.scalars(
                select(TransformationOption)
                .where(
                    TransformationOption.organization_id == actor.organization_id,
                    TransformationOption.workstream_id == brief.workstream_id,
                    option_scope,
                )
                .order_by(TransformationOption.id)
                .with_for_update()
            ).all()
        )
        root_ids = tuple(row.id for row in option_roots)
        rows = session.scalars(
            select(TransformationOptionVersion)
            .where(
                TransformationOptionVersion.organization_id == actor.organization_id,
                TransformationOptionVersion.id.in_(requested_ids),
            )
            .order_by(TransformationOptionVersion.id)
            .with_for_update()
        ).all()
        by_id = {row.id: row for row in rows}
        if len(by_id) != len(requested_ids):
            raise NotFound("option_versions_not_found")
        versions = tuple(by_id[row_id] for row_id in requested_ids)
        if any(
            row.workstream_id != brief.workstream_id
            or row.candidate_id != brief.candidate_id
            for row in versions
        ):
            raise CommandConflict("option_version_scope_mismatch")
        if any(not TransformationOptionService.verify_version_hash(row) for row in versions):
            raise CommandConflict("option_version_hash_invalid")
        all_versions = tuple(
            session.scalars(
                select(TransformationOptionVersion)
                .where(
                    TransformationOptionVersion.organization_id
                    == actor.organization_id,
                    TransformationOptionVersion.option_id.in_(root_ids or (-1,)),
                    TransformationOptionVersion.workstream_id == brief.workstream_id,
                    (
                        TransformationOptionVersion.candidate_id.is_(None)
                        if brief.candidate_id is None
                        else TransformationOptionVersion.candidate_id
                        == brief.candidate_id
                    ),
                )
                .order_by(
                    TransformationOptionVersion.option_id,
                    TransformationOptionVersion.version.desc(),
                    TransformationOptionVersion.id.desc(),
                )
                .with_for_update()
            ).all()
        )
        latest_by_option = {}
        for row in all_versions:
            latest_by_option.setdefault(row.option_id, row)
        if set(latest_by_option) != set(root_ids):
            raise CommandConflict("option_version_missing")
        if any(
            latest_by_option.get(row.option_id) is None
            or latest_by_option[row.option_id].id != row.id
            for row in versions
        ):
            raise CommandConflict("option_version_not_latest")
        latest_ids = {
            row.id for option_id, row in latest_by_option.items() if option_id in root_ids
        }
        if set(requested_ids) != latest_ids:
            raise CommandConflict("option_version_set_not_current")
        if any(
            not TransformationOptionService.verify_version_hash(row)
            for row in latest_by_option.values()
        ):
            raise CommandConflict("option_version_hash_invalid")
        return versions

    @classmethod
    def _require_viable_options(cls, session, actor, brief, versions, workstream):
        distinct = {_sha256_canonical(row.content_json) for row in versions}
        if len(distinct) >= 2:
            return
        exception = cls._option_exception(brief)
        if exception is None:
            raise BlockedByEvidence("viable_options_required")
        if not cls._user_has_decision_authority(
            session,
            actor.organization_id,
            workstream.programme_id,
            workstream.id,
            exception["authority_id"],
            lock=True,
        ):
            raise NotAuthorised("option_exception_authority_invalid")

    @staticmethod
    def _option_exception(brief):
        values = (
            brief.option_exception_type,
            brief.option_exception_name,
            brief.option_exception_reason,
            brief.option_exception_authority_id,
        )
        if not any(value is not None for value in values):
            return None
        if (
            brief.option_exception_type not in OPTION_EXCEPTION_TYPES
            or not isinstance(brief.option_exception_name, str)
            or not brief.option_exception_name.strip()
            or not isinstance(brief.option_exception_reason, str)
            or not brief.option_exception_reason.strip()
            or brief.option_exception_authority_id is None
        ):
            raise BlockedByEvidence("option_exception_incomplete")
        return {
            "type": brief.option_exception_type,
            "name": brief.option_exception_name.strip(),
            "reason": brief.option_exception_reason.strip(),
            "authority_id": brief.option_exception_authority_id,
        }

    @staticmethod
    def _lock_decision_principals(session, actor, brief):
        """Acquire every principal lock in one global order before role checks."""
        user_ids = tuple(
            sorted(
                {
                    user_id
                    for user_id in (
                        actor.user_id,
                        brief.decision_authority_id,
                        brief.option_exception_authority_id,
                    )
                    if user_id is not None
                }
            )
        )
        session.scalars(
            select(User)
            .where(
                User.organization_id == actor.organization_id,
                User.id.in_(user_ids),
            )
            .order_by(User.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        ).all()

    @classmethod
    def _user_has_decision_authority(
        cls, session, organization_id, programme_id, workstream_id, user_id, *, lock
    ):
        user_statement = select(User).where(
            User.id == user_id,
            User.organization_id == organization_id,
        )
        assignment_statement = (
            select(ProgrammeRoleAssignment)
            .where(
                ProgrammeRoleAssignment.organization_id == organization_id,
                ProgrammeRoleAssignment.programme_id == programme_id,
                or_(
                    ProgrammeRoleAssignment.workstream_id.is_(None),
                    ProgrammeRoleAssignment.workstream_id == workstream_id,
                ),
                ProgrammeRoleAssignment.user_id == user_id,
            )
            .order_by(ProgrammeRoleAssignment.id)
        )
        if lock:
            user_statement = user_statement.execution_options(
                populate_existing=True
            ).with_for_update()
            assignment_statement = assignment_statement.execution_options(
                populate_existing=True
            ).with_for_update()
        user = session.scalar(user_statement)
        if user is None:
            return False
        roles = TransformationProgrammeService._server_roles(user)
        today = date.today()
        roles.update(
            row.role
            for row in session.scalars(assignment_statement).all()
            if row.effective_from <= today
            and (row.effective_to is None or row.effective_to >= today)
        )
        return bool(roles.intersection(DECISION_AUTHORITY_ROLES))

    @staticmethod
    def _recommendation_version(brief, versions):
        matching = [row for row in versions if row.option_id == brief.recommendation_option_id]
        if len(matching) != 1:
            raise BlockedByEvidence("recommendation_option_version_required")
        return matching[0]

    @staticmethod
    def _lock_candidate_scope(session, actor, brief):
        statement = select(TransformationCandidate).where(
            TransformationCandidate.organization_id == actor.organization_id,
            TransformationCandidate.workstream_id == brief.workstream_id,
            TransformationCandidate.inclusion_status == "accepted",
        )
        if brief.candidate_id is not None:
            statement = statement.where(
                TransformationCandidate.id == brief.candidate_id
            )
        candidates = tuple(
            session.scalars(
                statement
                .order_by(TransformationCandidate.id)
                .with_for_update()
            ).all()
        )
        if not candidates:
            raise BlockedByEvidence("candidate_scope_required")
        selected = None
        if brief.candidate_id is not None:
            selected = candidates[0] if candidates else None
            if selected is None:
                raise CommandConflict("brief_candidate_scope_changed")
        return candidates, selected

    @classmethod
    def _lock_evidence_snapshot(
        cls, session, actor, candidates, requested_ids, assertions
    ):
        membership = {(row.subject_type, row.subject_id) for row in candidates}
        heads = tuple(
            session.scalars(
                select(EvidenceClaimHead)
                .where(
                    EvidenceClaimHead.organization_id == actor.organization_id,
                    tuple_(
                        EvidenceClaimHead.subject_type,
                        EvidenceClaimHead.subject_id,
                    ).in_(sorted(membership)),
                    EvidenceClaimHead.current_record_id.is_not(None),
                )
                .order_by(
                    EvidenceClaimHead.organization_id,
                    EvidenceClaimHead.subject_type,
                    EvidenceClaimHead.subject_id,
                    EvidenceClaimHead.claim_key,
                    EvidenceClaimHead.source_identity,
                    EvidenceClaimHead.id,
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            ).all()
        )
        if not heads:
            raise BlockedByEvidence("required_evidence_incomplete")
        current_ids = {row.current_record_id for row in heads}
        missing_current = current_ids - set(requested_ids)
        if missing_current:
            raise BlockedByEvidence(
                "evidence_snapshot_incomplete",
                missing_evidence_ids=tuple(sorted(missing_current)),
            )
        all_record_ids = set(requested_ids) | current_ids
        records = tuple(
            session.scalars(
                select(EvidenceRecord)
                .where(
                    EvidenceRecord.organization_id == actor.organization_id,
                    EvidenceRecord.id.in_(all_record_ids),
                )
                .order_by(EvidenceRecord.id)
                .with_for_update()
            ).all()
        )
        by_id = {row.id: row for row in records}
        if set(requested_ids) - set(by_id):
            raise NotFound("evidence_records_not_found")
        ordered = tuple(by_id[row_id] for row_id in requested_ids)
        if any((row.subject_type, row.subject_id) not in membership for row in ordered):
            raise CommandConflict("evidence_membership_changed")
        head_by_key = {
            (row.subject_type, row.subject_id, row.claim_key, row.source_identity): row
            for row in heads
        }
        acknowledged_ids = set(assertions["acknowledged_superseded_evidence_ids"])
        if not acknowledged_ids.issubset(set(requested_ids)):
            raise ValueError("acknowledged evidence must be cited")
        candidate_ids = tuple(sorted(row.id for row in candidates))
        requests = tuple(
            session.scalars(
                select(EvidenceRequest)
                .where(
                    EvidenceRequest.organization_id == actor.organization_id,
                    EvidenceRequest.candidate_id.in_(candidate_ids),
                )
                .order_by(EvidenceRequest.candidate_id, EvidenceRequest.claim_key, EvidenceRequest.id)
                .execution_options(populate_existing=True)
                .with_for_update()
            ).all()
        )
        accepted_claims = set()
        incomplete_required = []
        for request in requests:
            accepted = by_id.get(request.accepted_evidence_id)
            key = (
                request.subject_type,
                request.subject_id,
                request.claim_key,
            )
            head = (
                head_by_key.get(
                    (
                        accepted.subject_type,
                        accepted.subject_id,
                        accepted.claim_key,
                        accepted.source_identity,
                    )
                )
                if accepted is not None
                else None
            )
            valid = bool(
                request.status == "accepted"
                and accepted is not None
                and accepted.classification != "conflict"
                and accepted.subject_type == request.subject_type
                and accepted.subject_id == request.subject_id
                and accepted.claim_key == request.claim_key
                and head is not None
                and head.current_record_id == accepted.id
            )
            if valid:
                accepted_claims.add(key)
            elif request.required:
                incomplete_required.append(request.id)
        if incomplete_required or not accepted_claims:
            raise BlockedByEvidence(
                "required_evidence_incomplete",
                evidence_request_ids=tuple(incomplete_required),
            )

        current_records = tuple(by_id[row_id] for row_id in sorted(current_ids))
        if any(
            (row.subject_type, row.subject_id, row.claim_key) not in accepted_claims
            for row in current_records
        ):
            raise BlockedByEvidence("evidence_not_accepted")
        current_conflicts = [
            row for row in current_records if row.classification == "conflict"
        ]
        current_resolutions = [
            row for row in current_records if row.source_type == "governance_resolution"
        ]
        unresolved = [
            conflict
            for conflict in current_conflicts
            if not any(
                resolution.subject_type == conflict.subject_type
                and resolution.subject_id == conflict.subject_id
                and resolution.claim_key == conflict.claim_key
                and conflict.id in set(resolution.cited_evidence_ids or ())
                for resolution in current_resolutions
            )
        ]
        if unresolved:
            raise BlockedByEvidence(
                "evidence_conflict_unresolved",
                evidence_ids=tuple(row.id for row in unresolved),
            )

        now = CommandService._database_now(session)
        citations = []
        for record in ordered:
            key = (
                record.subject_type,
                record.subject_id,
                record.claim_key,
                record.source_identity,
            )
            head = head_by_key.get(key)
            if head is None or head.current_record_id is None:
                raise CommandConflict("evidence_head_changed")
            was_current = head.current_record_id == record.id
            expired = (
                record.freshness_expires_at is not None
                and _utc(record.freshness_expires_at) <= now
            )
            stale = record.freshness_status not in {"fresh", "not_applicable"} or expired
            acknowledged = record.id in acknowledged_ids
            if (not was_current or stale) and not acknowledged:
                raise BlockedByEvidence(
                    "evidence_acknowledgement_required", evidence_id=record.id
                )
            citations.append(
                {
                    "evidence_record_id": record.id,
                    "evidence_head_id": head.id,
                    "head_revision_at_freeze": head.revision,
                    "current_record_id_at_freeze": head.current_record_id,
                    "was_current": was_current,
                    "acknowledged": acknowledged,
                    "freshness_status": (
                        "expired" if expired else record.freshness_status
                    ),
                }
            )
        return ordered, heads, tuple(citations)

    @staticmethod
    def _lock_outcomes_and_measures(session, actor, programme, workstream):
        outcomes = tuple(
            session.scalars(
                select(ProgrammeOutcomeCommitment)
                .where(
                    ProgrammeOutcomeCommitment.organization_id == actor.organization_id,
                    ProgrammeOutcomeCommitment.programme_id == programme.id,
                    or_(
                        ProgrammeOutcomeCommitment.workstream_id == workstream.id,
                        ProgrammeOutcomeCommitment.workstream_id.is_(None),
                    ),
                )
                .order_by(ProgrammeOutcomeCommitment.id)
                .with_for_update()
            ).all()
        )
        outcome_ids = [row.id for row in outcomes]
        measures = tuple(
            session.scalars(
                select(MeasureDefinition)
                .where(
                    MeasureDefinition.organization_id == actor.organization_id,
                    MeasureDefinition.outcome_commitment_id.in_(outcome_ids or [-1]),
                )
                .order_by(MeasureDefinition.id)
                .with_for_update()
            ).all()
        )
        return outcomes, measures

    @staticmethod
    def _validate_assertions(brief, assertions, evidence_ids):
        if assertions["reviewed_ai_material"] is not True:
            raise BlockedByEvidence("human_ai_review_required")
        unknowns = {
            _required_text(value, "brief unknown code") for value in brief.unknown_codes
        }
        acknowledged = set(assertions["acknowledged_unknown_codes"])
        if not unknowns.issubset(acknowledged):
            raise BlockedByEvidence("brief_unknowns_unacknowledged")
        if not acknowledged.issubset(unknowns):
            raise ValueError("acknowledged unknown code is not present on the decision brief")
        if not set(assertions["acknowledged_superseded_evidence_ids"]).issubset(
            set(evidence_ids)
        ):
            raise ValueError("acknowledged evidence must be cited")
        _required_text(assertions["rationale"], "assertion rationale")

    @classmethod
    def _build_frozen_payload(
        cls,
        *,
        actor,
        programme,
        workstream,
        brief,
        candidate,
        versions,
        recommendation,
        evidence_rows,
        heads,
        evidence_citations,
        outcomes,
        measures,
        assertions,
        version,
        source_revision,
        created_at,
    ):
        head_by_id = {row.id: row for row in heads}
        citation_by_record = {row["evidence_record_id"]: row for row in evidence_citations}
        payload = {
            "schema_version": "decision-brief-r1.1",
            "organization_id": actor.organization_id,
            "programme_id": programme.id,
            "workstream_id": workstream.id,
            "brief_id": brief.id,
            "brief_version": version,
            "source_revision": source_revision,
            "title": brief.title,
            "objective": workstream.objective,
            "scope_expression": workstream.scope_expression,
            "candidate": (
                {
                    "id": candidate.id,
                    "subject_type": candidate.subject_type,
                    "subject_id": candidate.subject_id,
                }
                if candidate is not None
                else None
            ),
            "option_versions": [
                {
                    "id": row.id,
                    "option_id": row.option_id,
                    "version": row.version,
                    "content_hash": row.content_hash,
                    "content": row.content_json,
                }
                for row in versions
            ],
            "recommendation_option_version_id": recommendation.id,
            "evidence": [
                {
                    "id": row.id,
                    "subject_type": row.subject_type,
                    "subject_id": row.subject_id,
                    "claim_key": row.claim_key,
                    "source_identity": row.source_identity,
                    "source_version": row.source_version,
                    "source_checksum": row.source_checksum,
                    "value_type": row.value_type,
                    "value": row.value_json,
                    "classification": row.classification,
                    "freshness_status": citation_by_record[row.id][
                        "freshness_status"
                    ],
                    "freshness_expires_at": row.freshness_expires_at,
                    "head": {
                        "id": citation_by_record[row.id]["evidence_head_id"],
                        "revision": citation_by_record[row.id]["head_revision_at_freeze"],
                        "current_record_id": citation_by_record[row.id][
                            "current_record_id_at_freeze"
                        ],
                        "source_identity": head_by_id[
                            citation_by_record[row.id]["evidence_head_id"]
                        ].source_identity,
                    },
                    "was_current": citation_by_record[row.id]["was_current"],
                    "acknowledged": citation_by_record[row.id]["acknowledged"],
                }
                for row in evidence_rows
            ],
            "outcomes": [
                {
                    "id": row.id,
                    "statement": row.statement,
                    "owner_id": row.owner_id,
                    "improvement_direction": row.improvement_direction,
                    "target_date": row.target_date,
                    "lifecycle": row.lifecycle,
                }
                for row in outcomes
            ],
            "measures": [
                {
                    "id": row.id,
                    "outcome_commitment_id": row.outcome_commitment_id,
                    "metric_name": row.metric_name,
                    "unit": row.unit,
                    "currency": row.currency,
                    "aggregation": row.aggregation,
                    "baseline_amount": row.baseline_amount,
                    "target_amount": row.target_amount,
                    "tolerance_amount": row.tolerance_amount,
                    "baseline_value": row.baseline_value,
                    "target_value": row.target_value,
                    "baseline_date": row.baseline_date,
                    "target_date": row.target_date,
                    "cadence": row.cadence,
                    "source_adapter": row.source_adapter,
                    "source_key": row.source_key,
                    "tolerance": row.tolerance,
                    "unavailable_reason": row.unavailable_reason,
                }
                for row in measures
            ],
            "unknowns": brief.unknown_codes,
            "conflicts": brief.conflicts,
            "expected_impacts": brief.expected_impacts,
            "human_assertions": assertions,
            "option_exception": cls._option_exception(brief),
            "decision_authority_id": brief.decision_authority_id,
            "policy_version": TransformationGateService.POLICY_VERSION,
            "created_by_id": actor.user_id,
            "created_at": created_at,
        }
        return _canonical_value(payload)

    @staticmethod
    def _hash_envelope(
        *,
        organization_id,
        brief_id,
        workstream_id,
        version,
        source_revision,
        created_by_id,
        created_at,
        frozen_payload,
        recommendation_option_version_id,
        option_version_ids,
        cited_evidence_ids,
        outcome_ids,
        measure_ids,
        policy_version,
        submitted_by_id,
        submitter_authorized,
        decision_authority_id,
        human_reviewed_ai,
        blockers_cleared,
        unknowns_acknowledged,
    ):
        return {
            "schema_version": "decision-brief-hash-r1.1",
            "organization_id": organization_id,
            "brief_id": brief_id,
            "workstream_id": workstream_id,
            "version": version,
            "source_revision": source_revision,
            "created_by_id": created_by_id,
            "created_at": created_at,
            "frozen_payload": frozen_payload,
            "recommendation_option_version_id": recommendation_option_version_id,
            "option_version_ids": sorted(option_version_ids),
            "cited_evidence_ids": sorted(cited_evidence_ids),
            "outcome_ids": sorted(outcome_ids),
            "measure_ids": sorted(measure_ids),
            "policy_version": policy_version,
            "submitted_by_id": submitted_by_id,
            "submitter_authorized": submitter_authorized,
            "decision_authority_id": decision_authority_id,
            "human_reviewed_ai": human_reviewed_ai,
            "blockers_cleared": blockers_cleared,
            "unknowns_acknowledged": unknowns_acknowledged,
        }

    @classmethod
    def reconstruct_canonical_payload(cls, version: DecisionBriefVersion):
        return cls._hash_envelope(
            organization_id=version.organization_id,
            brief_id=version.brief_id,
            workstream_id=version.workstream_id,
            version=version.version,
            source_revision=version.source_revision,
            created_by_id=version.created_by_id,
            created_at=version.created_at,
            frozen_payload=version.frozen_payload,
            recommendation_option_version_id=version.recommendation_option_version_id,
            option_version_ids=version.option_version_ids,
            cited_evidence_ids=version.cited_evidence_ids,
            outcome_ids=version.outcome_ids,
            measure_ids=version.measure_ids,
            policy_version=version.policy_version,
            submitted_by_id=version.submitted_by_id,
            submitter_authorized=version.submitter_authorized,
            decision_authority_id=version.decision_authority_id,
            human_reviewed_ai=version.human_reviewed_ai,
            blockers_cleared=version.blockers_cleared,
            unknowns_acknowledged=version.unknowns_acknowledged,
        )

    @classmethod
    def verify_hash(cls, version: DecisionBriefVersion) -> bool:
        document = version.canonical_document
        if not isinstance(document, str) or not document:
            return False
        try:
            parsed = json.loads(document)
            expected = _canonical_value(cls.reconstruct_canonical_payload(version))
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        if parsed != expected or _canonical_json(parsed) != document:
            return False
        return hmac.compare_digest(
            version.content_hash,
            hashlib.sha256(document.encode("utf-8")).hexdigest(),
        )


DecisionService = DecisionBriefService


__all__ = [
    "DecisionBriefService",
    "DecisionService",
    "TransformationOptionService",
]

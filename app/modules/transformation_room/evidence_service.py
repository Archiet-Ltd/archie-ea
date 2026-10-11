"""Versioned evidence adapters, requests, conflicts, and guarded claim heads."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import or_, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from app import db
from app.models.application_portfolio import (
    APPLICATION_LIFECYCLE_STAGES,
    APPLICATION_RISK_LEVELS,
    ApplicationComponent,
)
from app.models.application_rationalization import ApplicationDependency
from app.models.business_capabilities import BusinessCapability
from app.models.strategic import StrategicInitiative
from app.models.transformation_evidence import (
    EvidenceClaimHead,
    EvidenceHeadEvent,
    EvidenceRecord,
    EvidenceRequest,
    TransformationCandidate,
)
from app.models.transformation_programme import (
    ISO_4217_CURRENCIES,
    ProgrammeRoleAssignment,
    ProgrammeWorkstream,
)
from app.models.user import User
from app.modules.transformation_room.command_service import CommandService, OperationAuthorizer
from app.modules.transformation_room.domain import (
    ActorContext,
    CommandConflict,
    CommandResult,
    DomainMutationResult,
    FreshnessResult,
    NotAuthorised,
    NotFound,
    SourceResolution,
    SourceVersion,
    TypedEvidenceValue,
)
from app.modules.transformation_room.programme_service import (
    OBJECTIVE_ROLES,
    READ_ROLES,
    TransformationProgrammeService,
)


INVENTORY_FRESHNESS = timedelta(days=90)
EVIDENCE_CLAIM_CONTRACT_VERSION = "application-rationalisation-evidence-r1"
REQUIRED_EVIDENCE_CLAIMS = (
    "application_owner",
    "lifecycle",
    "cost",
    "business_criticality",
    "capability_impact",
    "dependency_impact",
    "risk",
    "source_freshness",
)
_INVENTORY_OBSERVATION_CLAIMS = frozenset(
    {
        "application_owner",
        "lifecycle",
        "business_criticality",
        "risk",
        "source_freshness",
    }
)
_PLACEHOLDER_VALUES = frozenset(
    {
        "-",
        "—",
        "n/a",
        "na",
        "none",
        "not applicable",
        "not available",
        "tbd",
        "to be determined",
        "unknown",
        "unavailable",
    }
)
_LEGACY_LIFECYCLE_VALUES = frozenset(
    {"planning", "development", "testing", "operational", "deprecated", "retired"}
)
_LIFECYCLE_ALIASES = {
    "active": "operational",
    "decommissioned": "retired",
    "inactive": "retired",
    "live": "operational",
    "production": "operational",
    "sunset": "deprecated",
}
_LIFECYCLE_VALUES = frozenset(
    value.casefold()
    for value in (*APPLICATION_LIFECYCLE_STAGES, *_LEGACY_LIFECYCLE_VALUES)
)
_ASSESSED_LEVELS = APPLICATION_RISK_LEVELS
_IMPACT_LEVELS = frozenset({"none", "limited", "material", "critical"})
_RISK_FIELDS = frozenset(
    {"technical_risk", "business_risk", "vendor_risk", "obsolescence_risk"}
)
ATTESTATION_OVERRIDE_ROLES = frozenset(
    {"application_architect", "enterprise_architect", "chief_architect"}
)
DECISION_AUTHORITY_ROLES = frozenset(
    {
        "decision_authority",
        "chief_architect",
        "cto",
        "arb_member",
        "platform_admin",
        "organization_admin",
        "administrator",
    }
)

_ADAPTER_SESSION: ContextVar[Session | None] = ContextVar(
    "transformation_evidence_adapter_session", default=None
)
_ADAPTER_ACTOR: ContextVar[ActorContext | None] = ContextVar(
    "transformation_evidence_adapter_actor", default=None
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("source observed_at must be a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_positive_int(value: Any) -> int:
    """Parse an exact base-10 positive integer; booleans/floats are invalid."""
    if isinstance(value, bool):
        raise ValueError("source key must be a positive integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip(), 10)
    else:
        raise ValueError("source key must be a positive integer")
    if parsed <= 0:
        raise ValueError("source key must be a positive integer")
    return parsed


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("evidence numbers must be finite")
        return str(value)
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("evidence numbers must be finite")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"{type(value).__name__} is not canonical JSON")


def sha256_canonical(value: TypedEvidenceValue | Mapping[str, Any] | Sequence[Any]) -> str:
    """Use Task 3's sorted, UTF-8, whitespace-free canonical JSON rules."""
    payload = asdict(value) if isinstance(value, TypedEvidenceValue) else value
    canonical = json.dumps(
        _json_value(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalised_adapter_key(adapter_key: str) -> str:
    value = unicodedata.normalize("NFC", adapter_key).strip().lower() if isinstance(adapter_key, str) else ""
    if not value:
        raise ValueError("adapter_key is required")
    return value


def canonical_source_identity(adapter_key: str, source_identity: str) -> str:
    """Normalise namespaces and URI scheme/host without folding opaque keys."""
    adapter = _normalised_adapter_key(adapter_key)
    identity = (
        unicodedata.normalize("NFC", source_identity).strip()
        if isinstance(source_identity, str)
        else ""
    )
    if not identity:
        raise ValueError("source_identity is required")
    parsed = urlsplit(identity)
    if parsed.scheme and parsed.netloc:
        userinfo, separator, _host = parsed.netloc.rpartition("@")
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("source URI host is required")
        canonical_host = hostname.lower()
        if ":" in canonical_host and not canonical_host.startswith("["):
            canonical_host = f"[{canonical_host}]"
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("source URI port is invalid") from error
        canonical_netloc = (
            f"{userinfo}@" if separator else ""
        ) + canonical_host + (f":{port}" if port is not None else "")
        return urlunsplit(
            (
                parsed.scheme.lower(),
                canonical_netloc,
                parsed.path,
                parsed.query,
                parsed.fragment,
            )
        )
    prefix, separator, opaque = identity.partition(":")
    if not separator or not opaque:
        return f"{adapter}:{identity}"
    prefix = prefix.strip().lower()
    if prefix == adapter or prefix in {
        "application",
        "attestation",
        "conflict",
        "resolution",
        "unknown",
    }:
        return f"{prefix}:{opaque}"
    return f"{adapter}:{prefix}:{opaque}"


@contextmanager
def _adapter_context(session: Session, actor: ActorContext):
    session_token = _ADAPTER_SESSION.set(session)
    actor_token = _ADAPTER_ACTOR.set(actor)
    try:
        yield
    finally:
        _ADAPTER_ACTOR.reset(actor_token)
        _ADAPTER_SESSION.reset(session_token)


def load_application_for_tenant(
    actor: ActorContext, application_id: int
) -> ApplicationComponent:
    """Tenant-load an active canonical application in the adapter transaction."""
    application_id = parse_positive_int(application_id)
    active_session = _ADAPTER_SESSION.get()
    owns_session = active_session is None
    session = active_session or Session(db.engine)
    try:
        application = session.scalar(
            select(ApplicationComponent).where(
                ApplicationComponent.id == application_id,
                ApplicationComponent.organization_id == actor.organization_id,
                ApplicationComponent.deleted_at.is_(None),
            )
        )
        if application is None:
            raise NotFound("application_not_found")
        if owns_session:
            session.expunge(application)
        return application
    finally:
        if owns_session:
            session.close()


def lock_application(application_id: int) -> ApplicationComponent:
    """Lock the canonical application in the current evidence command session."""
    session = _ADAPTER_SESSION.get()
    actor = _ADAPTER_ACTOR.get()
    if session is None or actor is None:
        raise RuntimeError("lock_application requires an evidence adapter transaction")
    application = session.scalar(
        select(ApplicationComponent)
        .where(
            ApplicationComponent.id == parse_positive_int(application_id),
            ApplicationComponent.organization_id == actor.organization_id,
            ApplicationComponent.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if application is None:
        raise NotFound("application_not_found")
    return application


def canonical_inventory_fields(application: ApplicationComponent) -> dict[str, Any]:
    """Return only canonical inventory facts, retaining explicit nulls."""
    fields = (
        "id",
        "name",
        "application_code",
        "application_type",
        "application_category",
        "deployment_model",
        "criticality",
        "business_criticality",
        "user_count",
        "total_cost_of_ownership",
        "lifecycle_status",
        "health_status",
        "end_of_life_date",
        "application_owner",
        "business_owner",
        "technical_owner",
        "technical_risk",
        "business_risk",
        "vendor_risk",
        "obsolescence_risk",
        "data_classification",
        "version",
        "lock_version",
    )
    return {field: _json_value(getattr(application, field)) for field in fields}


class EvidenceSourceAdapter(Protocol):
    resolve: Callable[[str, ActorContext], SourceResolution]
    read_version: Callable[[SourceResolution], SourceVersion]
    canonical_uri: Callable[[SourceResolution], str]
    freshness: Callable[[SourceVersion], FreshnessResult]
    authorise_correction: Callable[[ActorContext, SourceResolution], bool]


class ApplicationInventoryEvidenceAdapter:
    """Canonical application-inventory evidence adapter."""

    def resolve(self, source_key: str, actor: ActorContext) -> SourceResolution:
        application_id = parse_positive_int(source_key)
        application = load_application_for_tenant(actor, application_id)
        return SourceResolution(
            f"application:{application.id}", "application", application.id
        )

    def read_version(self, resolution: SourceResolution) -> SourceVersion:
        application = lock_application(resolution.canonical_subject_id)
        observed_at = application.updated_at or application.created_at
        if observed_at is None:
            raise ValueError("application source timestamp is unavailable")
        observed_at = _utc(observed_at)
        value = TypedEvidenceValue(
            "json", canonical_inventory_fields(application), None, None
        )
        checksum = sha256_canonical(value)
        return SourceVersion(observed_at.isoformat(), checksum, observed_at, value)

    def canonical_uri(self, resolution: SourceResolution) -> str:
        return f"archie://application/{resolution.canonical_subject_id}"

    def freshness(self, version: SourceVersion) -> FreshnessResult:
        expiry = _utc(version.observed_at) + INVENTORY_FRESHNESS
        return FreshnessResult(
            "fresh" if utcnow() <= expiry else "stale",
            expiry,
            "inventory-r1.1",
        )

    def authorise_correction(
        self, actor: ActorContext, resolution: SourceResolution
    ) -> bool:
        del resolution
        return bool(
            actor.roles
            & frozenset(
                {
                    "application_owner",
                    "application_architect",
                    "enterprise_architect",
                    "chief_architect",
                }
            )
        )


class TransformationEvidenceService:
    """Own version append, request state, conflict, and head-CAS semantics."""

    _adapters: dict[str, EvidenceSourceAdapter] = {
        "application-inventory": ApplicationInventoryEvidenceAdapter()
    }

    @classmethod
    def register_adapter(
        cls, adapter_key: str, adapter: EvidenceSourceAdapter
    ) -> EvidenceSourceAdapter | None:
        key = _normalised_adapter_key(adapter_key)
        previous = cls._adapters.get(key)
        cls._adapters[key] = adapter
        return previous

    @classmethod
    def restore_adapter(
        cls, adapter_key: str, previous: EvidenceSourceAdapter | None
    ) -> None:
        key = _normalised_adapter_key(adapter_key)
        if previous is None:
            cls._adapters.pop(key, None)
        else:
            cls._adapters[key] = previous

    @classmethod
    def _adapter(cls, adapter_key: str) -> tuple[str, EvidenceSourceAdapter]:
        key = _normalised_adapter_key(adapter_key)
        adapter = cls._adapters.get(key)
        if adapter is None:
            raise NotFound("evidence_adapter_not_found")
        return key, adapter

    @staticmethod
    def _require_command_key(command_key: str) -> str:
        value = command_key.strip() if isinstance(command_key, str) else ""
        if not value:
            raise ValueError("command_key is required")
        return value

    @staticmethod
    def _claim_key(claim_key: str) -> str:
        value = unicodedata.normalize("NFC", claim_key).strip() if isinstance(claim_key, str) else ""
        if not value:
            raise ValueError("claim_key is required")
        if len(value) > 100:
            raise ValueError("claim_key is too long")
        return value

    @staticmethod
    def _expected_revision(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("expected_head_revision must be a non-negative integer")
        return value

    @classmethod
    def _typed_value(
        cls,
        value: TypedEvidenceValue,
        *,
        allow_canonical_scalars: bool = False,
    ) -> TypedEvidenceValue:
        if not isinstance(value, TypedEvidenceValue):
            raise TypeError("value must be TypedEvidenceValue")
        value_type = value.value_type.strip().lower() if isinstance(value.value_type, str) else ""
        if value_type not in {"string", "number", "boolean", "date", "datetime", "json", "unknown"}:
            raise ValueError("value_type is not supported")
        raw = value.value
        canonical_number = False
        if allow_canonical_scalars and value_type == "number" and isinstance(raw, str):
            try:
                canonical_number = Decimal(raw).is_finite()
            except ArithmeticError:
                canonical_number = False
        valid = {
            "string": isinstance(raw, str),
            "number": (
                isinstance(raw, (int, float, Decimal)) and not isinstance(raw, bool)
            )
            or canonical_number,
            "boolean": isinstance(raw, bool),
            "date": isinstance(raw, date) and not isinstance(raw, datetime),
            "datetime": isinstance(raw, datetime),
            "json": isinstance(raw, (Mapping, list, tuple)),
            "unknown": raw is None,
        }[value_type]
        if not valid:
            raise ValueError("typed evidence value does not match value_type")
        unit = value.unit.strip() if isinstance(value.unit, str) else None
        currency = value.currency.strip().upper() if isinstance(value.currency, str) else None
        if currency is not None and len(currency) != 3:
            raise ValueError("currency must be a three-letter code")
        return TypedEvidenceValue(value_type, _json_value(raw), unit or None, currency or None)

    @classmethod
    def _validate_claim_value(
        cls,
        claim_key: str,
        value: TypedEvidenceValue,
        *,
        allow_canonical_scalars: bool = False,
    ) -> TypedEvidenceValue:
        value = cls._typed_value(
            value, allow_canonical_scalars=allow_canonical_scalars
        )
        raw = value.value
        if claim_key != "cost" and (value.unit is not None or value.currency is not None):
            raise ValueError("only cost evidence may declare a unit or currency")
        if claim_key == "application_owner":
            if value.value_type == "string":
                owner = raw.strip()
                if not owner or owner.casefold() in _PLACEHOLDER_VALUES:
                    raise ValueError("application owner evidence must name a known owner")
                value = TypedEvidenceValue("string", owner, None, None)
            elif value.value_type == "json":
                names = (
                    raw.get("owner_names")
                    if isinstance(raw, Mapping) and set(raw) == {"owner_names"}
                    else None
                )
                if not isinstance(names, list) or not names or any(
                    not isinstance(name, str)
                    or not name.strip()
                    or name.strip().casefold() in _PLACEHOLDER_VALUES
                    for name in names
                ):
                    raise ValueError("application owner evidence must name an owner")
                canonical_names = sorted({name.strip() for name in names}, key=str.casefold)
                if len(canonical_names) != len(names):
                    raise ValueError("application owner evidence contains duplicate owners")
                value = TypedEvidenceValue(
                    "json", {"owner_names": canonical_names}, None, None
                )
            else:
                raise ValueError("application owner evidence has the wrong type")
        elif claim_key == "lifecycle":
            if value.value_type != "string":
                raise ValueError("lifecycle evidence has the wrong type")
            lifecycle = raw.strip().casefold()
            lifecycle = _LIFECYCLE_ALIASES.get(lifecycle, lifecycle)
            if lifecycle not in _LIFECYCLE_VALUES:
                raise ValueError("lifecycle evidence is not a canonical lifecycle")
            value = TypedEvidenceValue("string", lifecycle, None, None)
        elif claim_key == "cost":
            if value.value_type != "number" or value.unit != "annual_tco":
                raise ValueError("cost evidence requires a number in annual_tco units")
            if value.currency not in ISO_4217_CURRENCIES:
                raise ValueError("cost evidence requires an ISO 4217 currency")
            if isinstance(raw, float):
                raise ValueError("cost evidence must use an exact decimal number")
            try:
                amount = Decimal(str(raw))
                canonical_amount = amount.quantize(Decimal("0.01"))
            except (InvalidOperation, ValueError):
                raise ValueError("cost evidence must use a finite decimal number") from None
            if not amount.is_finite() or amount < 0 or amount != canonical_amount:
                raise ValueError(
                    "cost evidence must be non-negative with at most two decimal places"
                )
            value = TypedEvidenceValue(
                "number", format(canonical_amount, "f"), "annual_tco", value.currency
            )
        elif claim_key == "business_criticality":
            if (
                value.value_type != "json"
                or not isinstance(raw, Mapping)
                or set(raw) != {"value", "source_field"}
                or not isinstance(raw.get("value"), str)
                or raw["value"].strip().casefold() not in _ASSESSED_LEVELS
                or raw.get("source_field")
                not in {"business_criticality", "criticality"}
            ):
                raise ValueError("business criticality evidence is incomplete")
            value = TypedEvidenceValue(
                "json",
                {
                    "value": raw["value"].strip().casefold(),
                    "source_field": raw["source_field"],
                },
                None,
                None,
            )
        elif claim_key in {"capability_impact", "dependency_impact"}:
            id_key = (
                "capability_ids"
                if claim_key == "capability_impact"
                else "dependency_ids"
            )
            if (
                value.value_type != "json"
                or not isinstance(raw, Mapping)
                or set(raw) != {id_key, "impact"}
                or not isinstance(raw.get(id_key), list)
                or any(
                    isinstance(item, bool)
                    or not isinstance(item, int)
                    or item <= 0
                    for item in raw[id_key]
                )
                or not isinstance(raw.get("impact"), str)
                or raw["impact"].strip().casefold() not in _IMPACT_LEVELS
            ):
                raise ValueError(f"{claim_key.replace('_', ' ')} evidence is incomplete")
            ids = raw[id_key]
            impact = raw["impact"].strip().casefold()
            if len(ids) != len(set(ids)):
                raise ValueError(f"{claim_key.replace('_', ' ')} IDs must be unique")
            if (impact == "none") != (not ids):
                raise ValueError(
                    f"{claim_key.replace('_', ' ')} none semantics are incoherent"
                )
            value = TypedEvidenceValue(
                "json", {id_key: sorted(ids), "impact": impact}, None, None
            )
        elif claim_key == "risk":
            if (
                value.value_type != "json"
                or not isinstance(raw, Mapping)
                or set(raw) != _RISK_FIELDS
                or any(
                    not isinstance(raw[field], str)
                    or raw[field].strip().casefold() not in _ASSESSED_LEVELS
                    for field in _RISK_FIELDS
                )
            ):
                raise ValueError("risk evidence must contain every known risk dimension")
            value = TypedEvidenceValue(
                "json",
                {field: raw[field].strip().casefold() for field in sorted(_RISK_FIELDS)},
                None,
                None,
            )
        elif claim_key == "source_freshness":
            if (
                value.value_type != "json"
                or not isinstance(raw, Mapping)
                or set(raw)
                != {"observed_at", "freshness_status", "source_system"}
            ):
                raise ValueError("source freshness evidence is incomplete")
            observed_at = raw["observed_at"]
            try:
                parsed_observed_at = (
                    datetime.fromisoformat(observed_at.strip().replace("Z", "+00:00"))
                    if isinstance(observed_at, str) and observed_at.strip()
                    else None
                )
            except ValueError:
                parsed_observed_at = None
            if parsed_observed_at is None or parsed_observed_at.tzinfo is None:
                raise ValueError("source freshness observed_at is invalid")
            if _utc(parsed_observed_at) > utcnow() + timedelta(seconds=1):
                raise ValueError("source freshness observed_at cannot be in the future")
            if raw["freshness_status"] not in {"fresh", "stale"}:
                raise ValueError("source freshness status is invalid")
            if raw["source_system"] != "application-inventory":
                raise ValueError("source freshness source system is invalid")
            value = TypedEvidenceValue(
                value.value_type,
                {
                    "observed_at": _utc(parsed_observed_at).isoformat(),
                    "freshness_status": raw["freshness_status"],
                    "source_system": "application-inventory",
                },
                None,
                None,
            )
        else:
            raise ValueError("claim is not part of the governed evidence contract")
        return value

    @classmethod
    def _validate_claim_references(
        cls, session, actor, candidate, claim_key: str, value: TypedEvidenceValue
    ) -> None:
        if claim_key == "capability_impact":
            ids = set(value.value["capability_ids"])
            found = set(
                session.scalars(
                    select(BusinessCapability.id).where(
                        BusinessCapability.organization_id == actor.organization_id,
                        BusinessCapability.id.in_(ids or {-1}),
                    )
                ).all()
            )
            if found != ids:
                raise NotFound("capability_impact_reference_not_found")
        elif claim_key == "dependency_impact":
            ids = set(value.value["dependency_ids"])
            dependencies = session.execute(
                select(
                    ApplicationDependency.id,
                    ApplicationDependency.source_app_id,
                    ApplicationDependency.target_app_id,
                ).where(
                    ApplicationDependency.organization_id == actor.organization_id,
                    ApplicationDependency.id.in_(ids or {-1}),
                )
            ).all()
            if {row.id for row in dependencies} != ids:
                raise NotFound("dependency_impact_reference_not_found")
            if candidate.subject_type != "application" or any(
                candidate.subject_id not in {row.source_app_id, row.target_app_id}
                for row in dependencies
            ):
                raise ValueError(
                    "dependency impact references must involve the evidence subject"
                )

    @classmethod
    def _inventory_claim_value(
        cls,
        claim_key: str,
        version: SourceVersion,
        freshness: FreshnessResult,
    ) -> TypedEvidenceValue:
        snapshot = version.value.value
        if version.value.value_type != "json" or not isinstance(snapshot, Mapping):
            raise CommandConflict("claim_adapter_schema_mismatch")
        if claim_key == "application_owner":
            names = []
            for field in ("application_owner", "business_owner", "technical_owner"):
                name = snapshot.get(field)
                if isinstance(name, str) and name.strip() and name.strip() not in names:
                    names.append(name.strip())
            value = TypedEvidenceValue("json", {"owner_names": names}, None, None)
        elif claim_key == "lifecycle":
            value = TypedEvidenceValue(
                "string", snapshot.get("lifecycle_status"), None, None
            )
        elif claim_key == "business_criticality":
            field = (
                "business_criticality"
                if snapshot.get("business_criticality") is not None
                else "criticality"
            )
            value = TypedEvidenceValue(
                "json",
                {"value": snapshot.get(field), "source_field": field},
                None,
                None,
            )
        elif claim_key == "risk":
            value = TypedEvidenceValue(
                "json",
                {
                    field: snapshot.get(field)
                    for field in (
                        "technical_risk",
                        "business_risk",
                        "vendor_risk",
                        "obsolescence_risk",
                    )
                },
                None,
                None,
            )
        elif claim_key == "source_freshness":
            value = TypedEvidenceValue(
                "json",
                {
                    "observed_at": _utc(version.observed_at).isoformat(),
                    "freshness_status": freshness.status,
                    "source_system": "application-inventory",
                },
                None,
                None,
            )
        else:
            raise CommandConflict("claim_adapter_pair_not_supported")
        try:
            return cls._validate_claim_value(claim_key, value)
        except (TypeError, ValueError) as error:
            raise CommandConflict("authoritative_claim_semantics_unavailable") from error

    @classmethod
    def record_satisfies_contract(cls, request, record) -> bool:
        request_claim = getattr(request, "claim_key", None)
        if (
            request_claim not in REQUIRED_EVIDENCE_CLAIMS
            or getattr(request, "claim_contract_version", None)
            != EVIDENCE_CLAIM_CONTRACT_VERSION
            or getattr(record, "claim_contract_version", None)
            != EVIDENCE_CLAIM_CONTRACT_VERSION
        ):
            return False
        source_type = getattr(record, "source_type", None)
        if source_type == "attestation" and request_claim == "source_freshness":
            return False
        if source_type != "attestation" and (
            source_type != "application-inventory"
            or request_claim not in _INVENTORY_OBSERVATION_CLAIMS
        ):
            return False
        try:
            cls._validate_claim_value(
                request_claim,
                TypedEvidenceValue(
                    getattr(record, "value_type", None),
                    getattr(record, "value_json", None),
                    getattr(record, "unit", None),
                    getattr(record, "currency", None),
                ),
                allow_canonical_scalars=True,
            )
        except (TypeError, ValueError):
            return False
        return True

    @staticmethod
    def _evidence_natural_key(payload: Mapping[str, Any]) -> str:
        source_digest = hashlib.sha256(
            payload["source_identity"].encode("utf-8")
        ).hexdigest()
        return (
            f"evidence:{payload['candidate_id']}:{payload['claim_key']}:"
            f"{source_digest}:{payload['expected_head_revision'] + 1}"
        )

    @classmethod
    def _load_scope(
        cls, session, actor, candidate_id: int, *, lock: bool
    ) -> tuple[TransformationCandidate, ProgrammeWorkstream, StrategicInitiative]:
        candidate_id = parse_positive_int(candidate_id)
        scope = session.execute(
            select(
                TransformationCandidate.id,
                TransformationCandidate.workstream_id,
            ).where(
                TransformationCandidate.id == candidate_id,
                TransformationCandidate.organization_id == actor.organization_id,
                TransformationCandidate.inclusion_status == "accepted",
            )
        ).one_or_none()
        if scope is None:
            raise NotFound("candidate_not_found")
        from app.modules.transformation_room.discovery_service import (
            RationalisationDiscoveryService,
        )

        workstream, programme = RationalisationDiscoveryService._load_workstream_graph(
            session, actor, scope.workstream_id, lock=lock
        )
        statement = select(TransformationCandidate).where(
            TransformationCandidate.id == candidate_id,
            TransformationCandidate.organization_id == actor.organization_id,
            TransformationCandidate.workstream_id == workstream.id,
            TransformationCandidate.inclusion_status == "accepted",
        )
        if lock:
            statement = statement.with_for_update()
        candidate = session.scalar(statement)
        if candidate is None:
            raise NotFound("candidate_not_found")
        TransformationProgrammeService._require_active_programme(programme)
        return candidate, workstream, programme

    @classmethod
    def _persisted_actor(
        cls, session, actor, programme_id, workstream_id, *, lock
    ) -> ActorContext:
        user = TransformationProgrammeService._load_runtime_user(
            session, actor, lock=lock
        )
        roles = TransformationProgrammeService._server_roles(user)
        statement = (
            select(ProgrammeRoleAssignment)
            .where(
                ProgrammeRoleAssignment.organization_id == actor.organization_id,
                ProgrammeRoleAssignment.programme_id == programme_id,
                ProgrammeRoleAssignment.user_id == actor.user_id,
                or_(
                    ProgrammeRoleAssignment.workstream_id.is_(None),
                    ProgrammeRoleAssignment.workstream_id == workstream_id,
                ),
            )
            .order_by(ProgrammeRoleAssignment.id)
        )
        if lock:
            statement = statement.with_for_update()
        today = date.today()
        roles.update(
            assignment.role
            for assignment in session.scalars(statement).all()
            if assignment.effective_from <= today
            and (assignment.effective_to is None or assignment.effective_to >= today)
        )
        return replace(actor, roles=frozenset(roles))

    @classmethod
    def _request_actor(
        cls, session, actor, request, programme, workstream, *, lock
    ) -> ActorContext:
        persisted = cls._persisted_actor(
            session, actor, programme.id, workstream.id, lock=lock
        )
        if (
            request.assigned_to_id != actor.user_id
            and not persisted.roles.intersection(ATTESTATION_OVERRIDE_ROLES)
        ):
            raise NotAuthorised("evidence_request_not_authorised")
        return persisted

    @classmethod
    def _decision_actor(
        cls, session, actor, programme, workstream, *, lock
    ) -> ActorContext:
        persisted = cls._persisted_actor(
            session, actor, programme.id, workstream.id, lock=lock
        )
        if not persisted.roles.intersection(DECISION_AUTHORITY_ROLES):
            raise NotAuthorised("evidence_decision_not_authorised")
        return persisted

    @classmethod
    def _load_request_scope(cls, session, actor, request_id: int, *, lock):
        request_id = parse_positive_int(request_id)
        scope = session.execute(
            select(EvidenceRequest.id, EvidenceRequest.candidate_id).where(
                EvidenceRequest.id == request_id,
                EvidenceRequest.organization_id == actor.organization_id,
            )
        ).one_or_none()
        if scope is None:
            raise NotFound("evidence_request_not_found")
        candidate, workstream, programme = cls._load_scope(
            session, actor, scope.candidate_id, lock=lock
        )
        statement = select(EvidenceRequest).where(
            EvidenceRequest.id == request_id,
            EvidenceRequest.organization_id == actor.organization_id,
            EvidenceRequest.candidate_id == candidate.id,
            EvidenceRequest.workstream_id == workstream.id,
            EvidenceRequest.subject_type == candidate.subject_type,
            EvidenceRequest.subject_id == candidate.subject_id,
        )
        if lock:
            statement = statement.with_for_update()
        request = session.scalar(statement)
        if request is None:
            raise NotFound("evidence_request_not_found")
        return request, candidate, workstream, programme

    @classmethod
    def plan_required_requests(
        cls,
        *,
        actor: ActorContext,
        candidate_id: int,
        assignments: Mapping[str, int],
        command_key: str,
        due_at: datetime | None = None,
    ) -> CommandResult:
        candidate_id = parse_positive_int(candidate_id)
        if not isinstance(assignments, Mapping):
            raise TypeError("assignments must be a claim-to-user mapping")
        if set(assignments) != set(REQUIRED_EVIDENCE_CLAIMS):
            raise ValueError("assignments must cover the exact required claim set")
        canonical_assignments = {
            claim: parse_positive_int(assignments[claim])
            for claim in REQUIRED_EVIDENCE_CLAIMS
        }
        canonical_due_at = _utc(due_at).isoformat() if due_at is not None else None
        payload = {
            "candidate_id": candidate_id,
            "claim_contract_version": EVIDENCE_CLAIM_CONTRACT_VERSION,
            "assignments": canonical_assignments,
            "due_at": canonical_due_at,
        }
        natural_key = (
            f"evidence-request-plan:{candidate_id}:"
            f"{EVIDENCE_CLAIM_CONTRACT_VERSION}"
        )

        def authorize(session, runtime_actor, operation, supplied_key):
            if operation != "evidence.request.plan" or supplied_key != natural_key:
                raise NotAuthorised("evidence_request_plan_command_mismatch")
            _candidate, workstream, programme = cls._load_scope(
                session, runtime_actor, candidate_id, lock=False
            )
            TransformationProgrammeService._require_programme_authority(
                session,
                runtime_actor,
                programme.id,
                workstream.id,
                OBJECTIVE_ROLES,
                "evidence_request_plan_not_authorised",
            )

        return CommandService.execute(
            actor=actor,
            operation="evidence.request.plan",
            idempotency_key=cls._require_command_key(command_key),
            payload=payload,
            natural_key=natural_key,
            authorizer=authorize,
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._plan_required_requests_locked(
                session, actor, payload, claim
            ),
        )

    @classmethod
    def _plan_required_requests_locked(cls, session, actor, payload, claim):
        candidate, workstream, programme = cls._load_scope(
            session, actor, payload["candidate_id"], lock=True
        )
        TransformationProgrammeService._require_programme_authority(
            session,
            actor,
            programme.id,
            workstream.id,
            OBJECTIVE_ROLES,
            "evidence_request_plan_not_authorised",
            lock=True,
        )
        assignment_ids = sorted(set(payload["assignments"].values()))
        tenant_assignees = set(
            session.scalars(
                select(User.id).where(
                    User.organization_id == actor.organization_id,
                    User.id.in_(assignment_ids),
                    User.confirmed.is_(True),
                )
            ).all()
        )
        if tenant_assignees != set(assignment_ids):
            raise NotAuthorised("evidence_request_assignee_outside_tenant")
        existing = {
            row.claim_key: row
            for row in session.scalars(
                select(EvidenceRequest)
                .where(
                    EvidenceRequest.organization_id == actor.organization_id,
                    EvidenceRequest.candidate_id == candidate.id,
                )
                .with_for_update()
            ).all()
        }
        unknown_claims = set(existing) - set(REQUIRED_EVIDENCE_CLAIMS)
        if unknown_claims:
            raise CommandConflict("candidate_has_noncontract_evidence_requests")
        planned = []
        due_at = (
            datetime.fromisoformat(payload["due_at"])
            if payload["due_at"] is not None
            else None
        )
        for claim_key in REQUIRED_EVIDENCE_CLAIMS:
            request = existing.get(claim_key)
            if request is None:
                request = EvidenceRequest(
                    organization_id=actor.organization_id,
                    workstream_id=workstream.id,
                    candidate_id=candidate.id,
                    subject_type=candidate.subject_type,
                    subject_id=candidate.subject_id,
                    claim_key=claim_key,
                    claim_contract_version=EVIDENCE_CLAIM_CONTRACT_VERSION,
                    assigned_to_id=payload["assignments"][claim_key],
                    required=True,
                    status="open",
                    due_at=due_at,
                    created_by_id=actor.user_id,
                    revision=1,
                )
                session.add(request)
            else:
                if (
                    request.status != "open"
                    or request.submitted_evidence_id is not None
                    or request.accepted_evidence_id is not None
                    or request.claim_contract_version
                    not in {None, EVIDENCE_CLAIM_CONTRACT_VERSION}
                ):
                    raise CommandConflict("evidence_request_plan_conflict")
                request.claim_contract_version = EVIDENCE_CLAIM_CONTRACT_VERSION
                request.assigned_to_id = payload["assignments"][claim_key]
                request.required = True
                request.due_at = due_at
            planned.append(request)
        session.flush()
        object_ids = {
            f"request_{request.claim_key}_id": request.id for request in planned
        }
        response = {
            "candidate_id": candidate.id,
            "claim_contract_version": EVIDENCE_CLAIM_CONTRACT_VERSION,
            "request_ids": [request.id for request in planned],
        }
        return DomainMutationResult(
            object_ids,
            response,
            (
                {
                    "event_type": "evidence.requests_planned",
                    "payload": {
                        **response,
                        "assignments": dict(payload["assignments"]),
                        "due_at": payload["due_at"],
                        "command_receipt_id": claim.receipt_id,
                        "command_generation": claim.generation,
                    },
                },
            ),
        )

    @classmethod
    def record_observation(
        cls,
        *,
        actor: ActorContext,
        candidate_id: int,
        claim_key: str,
        adapter_key: str,
        source_key: str,
        expected_head_revision: int,
        command_key: str,
    ) -> CommandResult:
        candidate_id = parse_positive_int(candidate_id)
        claim_key = cls._claim_key(claim_key)
        expected_head_revision = cls._expected_revision(expected_head_revision)
        command_key = cls._require_command_key(command_key)
        adapter_name, adapter = cls._adapter(adapter_key)
        with Session(db.engine) as session:
            with _adapter_context(session, actor):
                resolution = adapter.resolve(source_key, actor)
        source_identity = canonical_source_identity(
            adapter_name, resolution.source_identity
        )
        canonical_source_key = (
            str(resolution.canonical_subject_id)
            if isinstance(adapter, ApplicationInventoryEvidenceAdapter)
            else unicodedata.normalize("NFC", str(source_key)).strip()
        )
        payload = {
            "candidate_id": candidate_id,
            "claim_key": claim_key,
            "adapter_key": adapter_name,
            "source_key": canonical_source_key,
            "source_identity": source_identity,
            "expected_head_revision": expected_head_revision,
        }
        natural_key = cls._evidence_natural_key(payload)
        return CommandService.execute(
            actor=actor,
            operation="evidence.observe",
            idempotency_key=command_key,
            payload=payload,
            natural_key=natural_key,
            authorizer=cls.authorise_observation(payload, natural_key),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._record_observation_locked(
                session, actor, payload, claim
            ),
        )

    @classmethod
    def authorise_observation(
        cls, payload: Mapping[str, Any], expected_key: str
    ) -> OperationAuthorizer:
        def authorize(session, actor, operation, natural_key):
            if operation != "evidence.observe" or natural_key != expected_key:
                raise NotAuthorised("evidence_observation_command_mismatch")
            candidate, workstream, programme = cls._load_scope(
                session, actor, payload["candidate_id"], lock=False
            )
            _adapter_name, adapter = cls._adapter(payload["adapter_key"])
            with _adapter_context(session, actor):
                resolution = adapter.resolve(payload["source_key"], actor)
            if (
                resolution.canonical_subject_type != candidate.subject_type
                or resolution.canonical_subject_id != candidate.subject_id
                or canonical_source_identity(
                    payload["adapter_key"], resolution.source_identity
                )
                != payload["source_identity"]
            ):
                raise NotFound("evidence_source_subject_not_found")
            persisted = cls._persisted_actor(
                session, actor, programme.id, workstream.id, lock=False
            )
            if not adapter.authorise_correction(persisted, resolution):
                raise NotAuthorised("evidence_observation_not_authorised")

        return authorize

    @classmethod
    def _record_observation_locked(cls, session, actor, payload, claim):
        candidate, workstream, programme = cls._load_scope(
            session, actor, payload["candidate_id"], lock=True
        )
        adapter_name, adapter = cls._adapter(payload["adapter_key"])
        with _adapter_context(session, actor):
            resolution = adapter.resolve(payload["source_key"], actor)
            source_identity = canonical_source_identity(
                adapter_name, resolution.source_identity
            )
            if (
                resolution.canonical_subject_type != candidate.subject_type
                or resolution.canonical_subject_id != candidate.subject_id
                or source_identity != payload["source_identity"]
            ):
                raise NotFound("evidence_source_subject_not_found")
            version = adapter.read_version(resolution)
            canonical_uri = canonical_source_identity(
                adapter_name, adapter.canonical_uri(resolution)
            )
            freshness = adapter.freshness(version)
        persisted = cls._persisted_actor(
            session, actor, programme.id, workstream.id, lock=True
        )
        if not adapter.authorise_correction(persisted, resolution):
            raise NotAuthorised("evidence_observation_not_authorised")
        raw_value = cls._typed_value(version.value)
        raw_checksum = sha256_canonical(raw_value)
        if version.checksum and version.checksum.lower() != raw_checksum:
            raise CommandConflict("source_checksum_mismatch")
        request = session.scalar(
            select(EvidenceRequest)
            .where(
                EvidenceRequest.organization_id == actor.organization_id,
                EvidenceRequest.candidate_id == candidate.id,
                EvidenceRequest.claim_key == payload["claim_key"],
            )
            .with_for_update()
        )
        governed_request = bool(
            request is not None
            and request.claim_contract_version
            == EVIDENCE_CLAIM_CONTRACT_VERSION
        )
        if governed_request:
            if (
                adapter_name != "application-inventory"
                or not isinstance(adapter, ApplicationInventoryEvidenceAdapter)
                or payload["claim_key"] not in _INVENTORY_OBSERVATION_CLAIMS
            ):
                raise CommandConflict("claim_adapter_pair_not_supported")
            if request.status != "open":
                raise CommandConflict("evidence_request_not_open")
            value = cls._inventory_claim_value(
                payload["claim_key"], version, freshness
            )
        else:
            value = raw_value
        checksum = sha256_canonical(value)
        source_version = version.version.strip() if isinstance(version.version, str) else ""
        if not source_version:
            source_version = f"snapshot:{checksum}"
        record_payload = {
            "value_json": value.value,
            "value_type": value.value_type,
            "unit": value.unit,
            "currency": value.currency,
            "classification": "observed",
            "source_identity": source_identity,
            "source_type": adapter_name,
            "source_record_id": resolution.canonical_subject_id,
            "source_uri": canonical_uri,
            "source_version": source_version,
            "source_checksum": checksum,
            "source_system": adapter_name,
            "collected_at": CommandService._database_now(session),
            "observed_at": _utc(version.observed_at),
            "valid_from": _utc(version.observed_at),
            "valid_until": None,
            "freshness_status": freshness.status,
            "freshness_expires_at": freshness.expires_at,
            "freshness_rule_version": freshness.rule_version,
            "collector_type": "system",
            "collector_id": actor.user_id,
            "cited_evidence_ids": [],
            "confidence": None,
            "confidence_method": None,
            "claim_contract_version": (
                EVIDENCE_CLAIM_CONTRACT_VERSION if governed_request else None
            ),
        }
        mutation = cls._append_and_advance(
            session,
            actor,
            candidate,
            programme,
            workstream,
            payload["claim_key"],
            record_payload,
            payload["expected_head_revision"],
            claim,
            "canonical source observation",
        )
        if not governed_request:
            return mutation
        now = CommandService._database_now(session)
        request.status = "submitted"
        request.submitted_evidence_id = mutation.object_ids["evidence_record_id"]
        request.submitted_at = now
        request.revision += 1
        session.flush()
        response = {
            **dict(mutation.response),
            "request_id": request.id,
            "request_status": request.status,
            "request_revision": request.revision,
        }
        return DomainMutationResult(
            {
                **dict(mutation.object_ids),
                "evidence_request_id": request.id,
            },
            response,
            (
                *mutation.outbox_events,
                {
                    "event_type": "evidence.request_submitted",
                    "payload": {
                        "request_id": request.id,
                        "evidence_id": request.submitted_evidence_id,
                        "revision": request.revision,
                    },
                },
            ),
        )

    @classmethod
    def load_assigned_request(cls, actor, request_id):
        with Session(db.engine) as session:
            request, _candidate, workstream, programme = cls._load_request_scope(
                session, actor, request_id, lock=False
            )
            cls._request_actor(
                session, actor, request, programme, workstream, lock=False
            )
            session.expunge(request)
            return request

    @classmethod
    def load_assigned_open_request(cls, actor, request_id):
        """Compatibility read for callers that explicitly need open state."""
        request = cls.load_assigned_request(actor, request_id)
        if request.status != "open":
            raise CommandConflict("evidence_request_not_open")
        return request

    @classmethod
    def attestation_payload(
        cls, request, value, expected_head_revision, source_identity
    ):
        typed = cls._typed_value(value)
        if request.claim_contract_version == EVIDENCE_CLAIM_CONTRACT_VERSION:
            if request.claim_key == "source_freshness":
                raise CommandConflict("source_freshness_authoritative_source_required")
            typed = cls._validate_claim_value(
                request.claim_key, typed, allow_canonical_scalars=True
            )
        return {
            "request_id": request.id,
            "candidate_id": request.candidate_id,
            "claim_key": request.claim_key,
            "source_identity": source_identity,
            "expected_head_revision": cls._expected_revision(expected_head_revision),
            "value": asdict(typed),
            "claim_contract_version": request.claim_contract_version,
        }

    @classmethod
    def submit_attestation(
        cls,
        *,
        actor: ActorContext,
        request_id: int,
        value: TypedEvidenceValue,
        expected_head_revision: int,
        command_key: str,
    ) -> CommandResult:
        # State is deliberately not checked before the command receipt can be
        # reconciled. The locked handler below owns the new-command precondition.
        request = cls.load_assigned_request(actor, request_id)
        source_identity = f"attestation:user:{actor.user_id}"
        payload = cls.attestation_payload(
            request, value, expected_head_revision, source_identity
        )
        natural_key = cls._evidence_natural_key(payload)
        return CommandService.execute(
            actor=actor,
            operation="evidence.attest",
            idempotency_key=cls._require_command_key(command_key),
            payload=payload,
            natural_key=natural_key,
            authorizer=cls.authorise_attestation(request.id, payload),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._submit_attestation_locked(
                session, actor, payload, claim
            ),
        )

    @classmethod
    def authorise_attestation(
        cls, request_id: int, payload: Mapping[str, Any]
    ) -> OperationAuthorizer:
        expected_key = cls._evidence_natural_key(payload)

        def authorize(session, actor, operation, natural_key):
            if operation != "evidence.attest" or natural_key != expected_key:
                raise NotAuthorised("evidence_attestation_command_mismatch")
            request, _candidate, workstream, programme = cls._load_request_scope(
                session, actor, request_id, lock=False
            )
            if request.candidate_id != payload["candidate_id"] or request.claim_key != payload["claim_key"]:
                raise NotFound("evidence_request_not_found")
            cls._request_actor(
                session, actor, request, programme, workstream, lock=False
            )

        return authorize

    @classmethod
    def _submit_attestation_locked(cls, session, actor, payload, claim):
        request, candidate, workstream, programme = cls._load_request_scope(
            session, actor, payload["request_id"], lock=True
        )
        cls._request_actor(session, actor, request, programme, workstream, lock=True)
        if request.status != "open":
            raise CommandConflict("evidence_request_not_open")
        value = TypedEvidenceValue(**payload["value"])
        if request.claim_contract_version == EVIDENCE_CLAIM_CONTRACT_VERSION:
            if payload.get("claim_contract_version") != EVIDENCE_CLAIM_CONTRACT_VERSION:
                raise CommandConflict("evidence_claim_contract_changed")
            if request.claim_key == "source_freshness":
                raise CommandConflict("source_freshness_authoritative_source_required")
            value = cls._validate_claim_value(
                request.claim_key, value, allow_canonical_scalars=True
            )
            cls._validate_claim_references(
                session, actor, candidate, request.claim_key, value
            )
        else:
            value = cls._typed_value(value)
        now = CommandService._database_now(session)
        checksum = sha256_canonical(value)
        mutation = cls._append_and_advance(
            session,
            actor,
            candidate,
            programme,
            workstream,
            request.claim_key,
            {
                "value_json": value.value,
                "value_type": value.value_type,
                "unit": value.unit,
                "currency": value.currency,
                "classification": "attested",
                "source_identity": payload["source_identity"],
                "source_type": "attestation",
                "source_record_id": actor.user_id,
                "source_uri": None,
                "source_version": f"command:{claim.receipt_id}:{claim.generation}",
                "source_checksum": checksum,
                "source_system": "human_attestation",
                "collected_at": now,
                "observed_at": now,
                "valid_from": now,
                "valid_until": None,
                "freshness_status": "fresh",
                "freshness_expires_at": None,
                "freshness_rule_version": "attestation-r1.1",
                "collector_type": "human",
                "collector_id": actor.user_id,
                "cited_evidence_ids": [],
                "confidence": None,
                "confidence_method": None,
                "claim_contract_version": request.claim_contract_version,
            },
            payload["expected_head_revision"],
            claim,
            "human attestation submitted",
        )
        attestation_id = mutation.object_ids["evidence_record_id"]
        canonical_records = session.scalars(
            select(EvidenceRecord)
            .join(
                EvidenceClaimHead,
                EvidenceClaimHead.current_record_id == EvidenceRecord.id,
            )
            .where(
                EvidenceClaimHead.organization_id == actor.organization_id,
                EvidenceClaimHead.subject_type == candidate.subject_type,
                EvidenceClaimHead.subject_id == candidate.subject_id,
                EvidenceClaimHead.claim_key == request.claim_key,
                EvidenceRecord.id != attestation_id,
                EvidenceRecord.classification != "conflict",
                EvidenceRecord.source_type != "governance_resolution",
            )
            .order_by(EvidenceClaimHead.source_identity, EvidenceRecord.id)
        ).all()
        conflict_mutation = None
        disagreeing = [
            record
            for record in canonical_records
            if not cls._record_agrees(record, value)
        ]
        if disagreeing:
            attestation = session.get(EvidenceRecord, attestation_id)
            cited = [
                record.id
                for record in sorted(
                    (*canonical_records, attestation),
                    key=lambda record: (record.source_identity, record.id),
                )
            ]
            conflict_value = TypedEvidenceValue(
                "json", {"conflicting_evidence_ids": cited}, None, None
            )
            conflict_mutation = cls._append_and_advance(
                session,
                actor,
                candidate,
                programme,
                workstream,
                request.claim_key,
                {
                    "value_json": conflict_value.value,
                    "value_type": "json",
                    "unit": None,
                    "currency": None,
                    "classification": "conflict",
                    "source_identity": f"conflict:request:{request.id}",
                    "source_type": "governance_conflict",
                    "source_record_id": request.id,
                    "source_uri": None,
                    "source_version": f"command:{claim.receipt_id}:{claim.generation}",
                    "source_checksum": sha256_canonical(conflict_value),
                    "source_system": "transformation_room",
                    "collected_at": now,
                    "observed_at": now,
                    "valid_from": now,
                    "valid_until": None,
                    "freshness_status": "not_applicable",
                    "freshness_expires_at": None,
                    "freshness_rule_version": "conflict-r1.1",
                    "collector_type": "system",
                    "collector_id": actor.user_id,
                    "cited_evidence_ids": cited,
                    "confidence": None,
                    "confidence_method": None,
                    "claim_contract_version": request.claim_contract_version,
                },
                0,
                claim,
                "attestation disagrees with canonical observation",
            )
        request.status = "submitted"
        request.submitted_evidence_id = attestation_id
        request.submitted_at = now
        request.revision += 1
        session.flush()
        object_ids = dict(mutation.object_ids)
        outbox = list(mutation.outbox_events)
        if conflict_mutation is not None:
            object_ids["conflict_evidence_id"] = conflict_mutation.object_ids[
                "evidence_record_id"
            ]
            outbox.extend(conflict_mutation.outbox_events)
        response = {
            "request_id": request.id,
            "status": request.status,
            "revision": request.revision,
            **object_ids,
        }
        return DomainMutationResult(
            {"evidence_request_id": request.id, **object_ids}, response, tuple(outbox)
        )

    @staticmethod
    def _record_agrees(record: EvidenceRecord, value: TypedEvidenceValue) -> bool:
        return (
            record.value_type == value.value_type
            and _json_value(record.value_json) == _json_value(value.value)
            and record.unit == value.unit
            and record.currency == value.currency
        )

    @classmethod
    def accept_request(
        cls,
        *,
        actor: ActorContext,
        request_id: int,
        evidence_id: int,
        expected_revision: int,
        command_key: str,
    ) -> CommandResult:
        payload = {
            "request_id": parse_positive_int(request_id),
            "evidence_id": parse_positive_int(evidence_id),
            "expected_revision": parse_positive_int(expected_revision),
        }
        natural_key = (
            f"evidence-request-accept:{payload['request_id']}:"
            f"{payload['expected_revision']}"
        )
        return CommandService.execute(
            actor=actor,
            operation="evidence.request.accept",
            idempotency_key=cls._require_command_key(command_key),
            payload=payload,
            natural_key=natural_key,
            authorizer=cls.authorise_request_acceptance(payload, natural_key),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._accept_request_locked(
                session, actor, payload, claim
            ),
        )

    @classmethod
    def authorise_request_acceptance(cls, payload, expected_key):
        def authorize(session, actor, operation, natural_key):
            if operation != "evidence.request.accept" or natural_key != expected_key:
                raise NotAuthorised("evidence_request_accept_command_mismatch")
            request, _candidate, workstream, programme = cls._load_request_scope(
                session, actor, payload["request_id"], lock=False
            )
            evidence = session.scalar(
                select(EvidenceRecord.id).where(
                    EvidenceRecord.id == payload["evidence_id"],
                    EvidenceRecord.organization_id == actor.organization_id,
                    EvidenceRecord.subject_type == request.subject_type,
                    EvidenceRecord.subject_id == request.subject_id,
                    EvidenceRecord.claim_key == request.claim_key,
                )
            )
            if evidence is None:
                raise NotFound("evidence_record_not_found")
            cls._request_actor(
                session, actor, request, programme, workstream, lock=False
            )

        return authorize

    @classmethod
    def _accept_request_locked(cls, session, actor, payload, claim):
        del claim
        request, candidate, workstream, programme = cls._load_request_scope(
            session, actor, payload["request_id"], lock=True
        )
        cls._request_actor(session, actor, request, programme, workstream, lock=True)
        if request.status != "submitted":
            raise CommandConflict("evidence_request_not_submitted")
        if request.revision != payload["expected_revision"]:
            raise CommandConflict("stale_revision")
        if request.submitted_evidence_id != payload["evidence_id"]:
            raise CommandConflict("evidence_not_submitted_for_request")
        evidence = session.scalar(
            select(EvidenceRecord)
            .where(
                EvidenceRecord.id == payload["evidence_id"],
                EvidenceRecord.organization_id == actor.organization_id,
                EvidenceRecord.subject_type == request.subject_type,
                EvidenceRecord.subject_id == request.subject_id,
                EvidenceRecord.claim_key == request.claim_key,
            )
        )
        if evidence is None:
            raise NotFound("evidence_record_not_found")
        if evidence.classification == "conflict":
            raise CommandConflict("evidence_conflict_unresolved")
        if request.claim_contract_version == EVIDENCE_CLAIM_CONTRACT_VERSION:
            if not cls.record_satisfies_contract(request, evidence):
                raise CommandConflict("evidence_claim_contract_invalid")
            canonical_value = cls._validate_claim_value(
                request.claim_key,
                TypedEvidenceValue(
                    evidence.value_type,
                    evidence.value_json,
                    evidence.unit,
                    evidence.currency,
                ),
                allow_canonical_scalars=True,
            )
            cls._validate_claim_references(
                session, actor, candidate, request.claim_key, canonical_value
            )
        is_current = session.scalar(
            select(EvidenceClaimHead.id).where(
                EvidenceClaimHead.organization_id == actor.organization_id,
                EvidenceClaimHead.subject_type == evidence.subject_type,
                EvidenceClaimHead.subject_id == evidence.subject_id,
                EvidenceClaimHead.claim_key == evidence.claim_key,
                EvidenceClaimHead.source_identity == evidence.source_identity,
                EvidenceClaimHead.current_record_id == evidence.id,
            )
        )
        if is_current is None:
            raise CommandConflict("evidence_record_not_current")
        current_records = session.scalars(
            select(EvidenceRecord)
            .join(
                EvidenceClaimHead,
                EvidenceClaimHead.current_record_id == EvidenceRecord.id,
            )
            .where(
                EvidenceClaimHead.organization_id == actor.organization_id,
                EvidenceClaimHead.subject_type == request.subject_type,
                EvidenceClaimHead.subject_id == request.subject_id,
                EvidenceClaimHead.claim_key == request.claim_key,
                EvidenceRecord.organization_id == actor.organization_id,
            )
            .order_by(EvidenceClaimHead.source_identity, EvidenceRecord.id)
        ).all()
        conflicts = [
            record for record in current_records if record.classification == "conflict"
        ]
        resolutions = [
            record
            for record in current_records
            if record.source_type == "governance_resolution"
        ]
        source_leaves = [
            record
            for record in current_records
            if record.classification != "conflict"
            and record.source_type != "governance_resolution"
        ]
        selected = TypedEvidenceValue(
            evidence.value_type,
            evidence.value_json,
            evidence.unit,
            evidence.currency,
        )
        corrected_to_agreement = bool(source_leaves) and all(
            cls._record_agrees(record, selected) for record in source_leaves
        )
        unresolved = [
            conflict
            for conflict in conflicts
            if not any(
                conflict.id in set(resolution.cited_evidence_ids or ())
                for resolution in resolutions
            )
        ]
        if unresolved and not corrected_to_agreement:
            raise CommandConflict("evidence_conflict_unresolved")
        accepted_now = CommandService._database_now(session)
        request.status = "accepted"
        request.accepted_evidence_id = evidence.id
        request.accepted_at = accepted_now
        request.revision += 1
        session.flush()
        response = {
            "request_id": request.id,
            "evidence_id": evidence.id,
            "status": request.status,
            "revision": request.revision,
        }
        return DomainMutationResult(
            {"evidence_request_id": request.id, "evidence_record_id": evidence.id},
            response,
            ({"event_type": "evidence.request_accepted", "payload": response},),
        )

    @classmethod
    def _request_state_command(
        cls, *, actor, request_id, expected_revision, command_key, action, extra
    ):
        payload = {
            "request_id": parse_positive_int(request_id),
            "expected_revision": parse_positive_int(expected_revision),
            **extra,
        }
        natural_key = (
            f"evidence-request-{action}:{payload['request_id']}:"
            f"{payload['expected_revision']}"
        )
        operation = f"evidence.request.{action}"

        def authorize(session, runtime_actor, supplied_operation, supplied_key):
            if supplied_operation != operation or supplied_key != natural_key:
                raise NotAuthorised("evidence_request_command_mismatch")
            request, _candidate, workstream, programme = cls._load_request_scope(
                session, runtime_actor, payload["request_id"], lock=False
            )
            cls._request_actor(
                session, runtime_actor, request, programme, workstream, lock=False
            )

        return CommandService.execute(
            actor=actor,
            operation=operation,
            idempotency_key=cls._require_command_key(command_key),
            payload=payload,
            natural_key=natural_key,
            authorizer=authorize,
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._change_request_state_locked(
                session, actor, payload, action, claim
            ),
        )

    @classmethod
    def decline_request(
        cls, *, actor, request_id, reason, expected_revision, command_key
    ):
        normalized = reason.strip() if isinstance(reason, str) else ""
        if not normalized:
            raise ValueError("decline reason is required")
        return cls._request_state_command(
            actor=actor,
            request_id=request_id,
            expected_revision=expected_revision,
            command_key=command_key,
            action="decline",
            extra={"reason": normalized},
        )

    @classmethod
    def expire_request(
        cls, *, actor, request_id, expected_revision, command_key
    ):
        return cls._request_state_command(
            actor=actor,
            request_id=request_id,
            expected_revision=expected_revision,
            command_key=command_key,
            action="expire",
            extra={},
        )

    @classmethod
    def _change_request_state_locked(cls, session, actor, payload, action, claim):
        del claim
        request, _candidate, workstream, programme = cls._load_request_scope(
            session, actor, payload["request_id"], lock=True
        )
        cls._request_actor(session, actor, request, programme, workstream, lock=True)
        if request.status not in {"open", "submitted"}:
            raise CommandConflict("evidence_request_not_incomplete")
        if request.revision != payload["expected_revision"]:
            raise CommandConflict("stale_revision")
        now = CommandService._database_now(session)
        if action == "decline":
            request.status = "declined"
            request.decline_reason = payload["reason"]
        elif request.due_at is None or _utc(request.due_at) > now:
            raise CommandConflict("evidence_request_not_expired")
        else:
            request.status = "expired"
            request.expired_at = now
        request.revision += 1
        session.flush()
        response = {
            "request_id": request.id,
            "status": request.status,
            "revision": request.revision,
        }
        return DomainMutationResult(
            {"evidence_request_id": request.id},
            response,
            ({"event_type": f"evidence.request_{action}d", "payload": response},),
        )

    @classmethod
    def waive_unavailable_request(
        cls,
        *,
        actor,
        request_id,
        reason,
        expires_at,
        interim_accountable_id,
        expected_revision,
        command_key,
    ):
        reason = reason.strip() if isinstance(reason, str) else ""
        if not reason:
            raise ValueError("waiver reason is required")
        expires_at = _utc(expires_at)
        payload = {
            "request_id": parse_positive_int(request_id),
            "reason": reason,
            "expires_at": expires_at.isoformat(),
            "interim_accountable_id": parse_positive_int(interim_accountable_id),
            "expected_revision": parse_positive_int(expected_revision),
        }
        natural_key = (
            f"evidence-request-waive:{payload['request_id']}:"
            f"{payload['expected_revision']}"
        )

        def authorize(session, runtime_actor, operation, supplied_key):
            if operation != "evidence.request.waive" or supplied_key != natural_key:
                raise NotAuthorised("evidence_waiver_command_mismatch")
            _request, _candidate, workstream, programme = cls._load_request_scope(
                session, runtime_actor, payload["request_id"], lock=False
            )
            cls._decision_actor(
                session, runtime_actor, programme, workstream, lock=False
            )

        return CommandService.execute(
            actor=actor,
            operation="evidence.request.waive",
            idempotency_key=cls._require_command_key(command_key),
            payload=payload,
            natural_key=natural_key,
            authorizer=authorize,
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._waive_request_locked(
                session, actor, payload, claim
            ),
        )

    @classmethod
    def _waive_request_locked(cls, session, actor, payload, claim):
        del claim
        request, _candidate, workstream, programme = cls._load_request_scope(
            session, actor, payload["request_id"], lock=True
        )
        cls._decision_actor(session, actor, programme, workstream, lock=True)
        if request.status not in {"declined", "expired"}:
            raise CommandConflict("evidence_request_not_unavailable")
        if request.revision != payload["expected_revision"]:
            raise CommandConflict("stale_revision")
        now = CommandService._database_now(session)
        expiry = datetime.fromisoformat(payload["expires_at"])
        if expiry <= now:
            raise CommandConflict("evidence_waiver_expired")
        interim = session.scalar(
            select(User)
            .where(
                User.id == payload["interim_accountable_id"],
                User.organization_id == actor.organization_id,
            )
            .with_for_update()
        )
        if interim is None:
            raise NotFound("interim_accountable_not_found")
        request.waiver_id = request.id
        request.waiver_authority_id = actor.user_id
        request.waiver_reason = payload["reason"]
        request.waiver_expires_at = expiry
        request.interim_accountable_id = interim.id
        request.waived_at = now
        request.revision += 1
        session.flush()
        response = {
            "request_id": request.id,
            "waiver_id": request.waiver_id,
            "revision": request.revision,
        }
        return DomainMutationResult(
            {"evidence_request_id": request.id, "evidence_waiver_id": request.waiver_id},
            response,
            ({"event_type": "evidence.request_waived", "payload": response},),
        )

    @classmethod
    def resolve_conflict(
        cls,
        *,
        actor,
        conflict_evidence_id,
        governing_evidence_id,
        rationale,
        command_key,
    ):
        rationale = rationale.strip() if isinstance(rationale, str) else ""
        if not rationale:
            raise ValueError("resolution rationale is required")
        payload = {
            "conflict_evidence_id": parse_positive_int(conflict_evidence_id),
            "governing_evidence_id": parse_positive_int(governing_evidence_id),
            "rationale": rationale,
        }
        natural_key = (
            f"evidence-conflict-resolution:{payload['conflict_evidence_id']}:"
            f"{payload['governing_evidence_id']}"
        )
        return CommandService.execute(
            actor=actor,
            operation="evidence.conflict.resolve",
            idempotency_key=cls._require_command_key(command_key),
            payload=payload,
            natural_key=natural_key,
            authorizer=cls.authorise_conflict_resolution(payload, natural_key),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._resolve_conflict_locked(
                session, actor, payload, claim
            ),
        )

    @classmethod
    def authorise_conflict_resolution(cls, payload, expected_key):
        def authorize(session, actor, operation, natural_key):
            if operation != "evidence.conflict.resolve" or natural_key != expected_key:
                raise NotAuthorised("evidence_resolution_command_mismatch")
            conflict = session.scalar(
                select(EvidenceRecord).where(
                    EvidenceRecord.id == payload["conflict_evidence_id"],
                    EvidenceRecord.organization_id == actor.organization_id,
                    EvidenceRecord.classification == "conflict",
                )
            )
            if conflict is None:
                raise NotFound("evidence_conflict_not_found")
            _candidate, workstream, programme = cls._load_scope(
                session, actor, conflict.candidate_id, lock=False
            )
            cls._decision_actor(
                session, actor, programme, workstream, lock=False
            )

        return authorize

    @classmethod
    def _resolve_conflict_locked(cls, session, actor, payload, claim):
        conflict = session.scalar(
            select(EvidenceRecord).where(
                EvidenceRecord.id == payload["conflict_evidence_id"],
                EvidenceRecord.organization_id == actor.organization_id,
                EvidenceRecord.classification == "conflict",
            )
        )
        if conflict is None:
            raise NotFound("evidence_conflict_not_found")
        candidate, workstream, programme = cls._load_scope(
            session, actor, conflict.candidate_id, lock=True
        )
        request = session.scalar(
            select(EvidenceRequest)
            .where(
                EvidenceRequest.id == conflict.source_record_id,
                EvidenceRequest.organization_id == actor.organization_id,
                EvidenceRequest.candidate_id == candidate.id,
                EvidenceRequest.subject_type == conflict.subject_type,
                EvidenceRequest.subject_id == conflict.subject_id,
                EvidenceRequest.claim_key == conflict.claim_key,
                EvidenceRequest.status == "submitted",
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if request is None:
            raise NotFound("evidence_conflict_request_not_found")
        cls._decision_actor(session, actor, programme, workstream, lock=True)
        if payload["governing_evidence_id"] not in set(conflict.cited_evidence_ids or []):
            raise CommandConflict("governing_evidence_not_conflict_leaf")
        governing = session.scalar(
            select(EvidenceRecord).where(
                EvidenceRecord.id == payload["governing_evidence_id"],
                EvidenceRecord.organization_id == actor.organization_id,
                EvidenceRecord.subject_type == conflict.subject_type,
                EvidenceRecord.subject_id == conflict.subject_id,
                EvidenceRecord.claim_key == conflict.claim_key,
            )
        )
        if governing is None:
            raise NotFound("governing_evidence_not_found")
        if request.claim_contract_version == EVIDENCE_CLAIM_CONTRACT_VERSION:
            if not cls.record_satisfies_contract(request, governing):
                raise CommandConflict("governing_evidence_claim_contract_invalid")
            governing_value = cls._validate_claim_value(
                request.claim_key,
                TypedEvidenceValue(
                    governing.value_type,
                    governing.value_json,
                    governing.unit,
                    governing.currency,
                ),
                allow_canonical_scalars=True,
            )
            cls._validate_claim_references(
                session, actor, candidate, request.claim_key, governing_value
            )
        resolution_source_identity = canonical_source_identity(
            "governance_resolution", f"resolution:conflict:{conflict.id}"
        )
        governing_head_key = (
            governing.organization_id,
            governing.subject_type,
            governing.subject_id,
            governing.claim_key,
            governing.source_identity,
        )
        resolution_head_key = (
            actor.organization_id,
            conflict.subject_type,
            conflict.subject_id,
            conflict.claim_key,
            resolution_source_identity,
        )
        if governing_head_key == resolution_head_key:
            raise CommandConflict("governing_evidence_source_not_distinct")
        session.execute(
            postgresql_insert(EvidenceClaimHead)
            .values(
                organization_id=actor.organization_id,
                subject_type=conflict.subject_type,
                subject_id=conflict.subject_id,
                claim_key=conflict.claim_key,
                source_identity=resolution_source_identity,
                current_record_id=None,
                revision=0,
            )
            .on_conflict_do_nothing(
                index_elements=(
                    EvidenceClaimHead.organization_id,
                    EvidenceClaimHead.subject_type,
                    EvidenceClaimHead.subject_id,
                    EvidenceClaimHead.claim_key,
                    EvidenceClaimHead.source_identity,
                )
            )
        )
        locked_heads = session.scalars(
            select(EvidenceClaimHead)
            .where(
                EvidenceClaimHead.organization_id == actor.organization_id,
                EvidenceClaimHead.subject_type == conflict.subject_type,
                EvidenceClaimHead.subject_id == conflict.subject_id,
                EvidenceClaimHead.claim_key == conflict.claim_key,
                EvidenceClaimHead.source_identity.in_(
                    (governing.source_identity, resolution_source_identity)
                ),
            )
            .order_by(
                EvidenceClaimHead.organization_id,
                EvidenceClaimHead.subject_type,
                EvidenceClaimHead.subject_id,
                EvidenceClaimHead.claim_key,
                EvidenceClaimHead.source_identity,
                EvidenceClaimHead.id,
            )
            .execution_options(
                populate_existing=True,
                evidence_conflict_resolution_head_lock=True,
            )
            .with_for_update()
        ).all()
        locked_by_source = {head.source_identity: head for head in locked_heads}
        governing_head = locked_by_source.get(governing.source_identity)
        resolution_head = locked_by_source.get(resolution_source_identity)
        if resolution_head is None:
            raise RuntimeError("evidence resolution head upsert failed")
        if governing_head is not None and governing_head.id == resolution_head.id:
            raise CommandConflict("governing_evidence_source_not_distinct")
        if governing_head is None or governing_head.current_record_id != governing.id:
            raise CommandConflict("governing_evidence_not_current")
        now = CommandService._database_now(session)
        value = TypedEvidenceValue(
            "json",
            {
                "conflict_evidence_id": conflict.id,
                "governing_evidence_id": governing.id,
                "rationale": payload["rationale"],
            },
            None,
            None,
        )
        mutation = cls._append_and_advance(
            session,
            actor,
            candidate,
            programme,
            workstream,
            conflict.claim_key,
            {
                "value_json": value.value,
                "value_type": "json",
                "unit": None,
                "currency": None,
                "classification": "derived",
                "source_identity": resolution_source_identity,
                "source_type": "governance_resolution",
                "source_record_id": conflict.id,
                "source_uri": None,
                "source_version": f"command:{claim.receipt_id}:{claim.generation}",
                "source_checksum": sha256_canonical(value),
                "source_system": "transformation_room",
                "collected_at": now,
                "observed_at": now,
                "valid_from": now,
                "valid_until": None,
                "freshness_status": "not_applicable",
                "freshness_expires_at": None,
                "freshness_rule_version": "resolution-r1.1",
                "collector_type": "human",
                "collector_id": actor.user_id,
                "cited_evidence_ids": [conflict.id, governing.id],
                "confidence": None,
                "confidence_method": "decision_authority_selection",
            },
            0,
            claim,
            "decision authority selected governing source",
        )
        resolution_id = mutation.object_ids["evidence_record_id"]
        request.submitted_evidence_id = governing.id
        request.revision += 1
        session.flush()
        object_ids = {
            **mutation.object_ids,
            "evidence_request_id": request.id,
            "governing_evidence_id": governing.id,
            "resolution_evidence_id": resolution_id,
        }
        response = {
            **mutation.response,
            "request_id": request.id,
            "request_revision": request.revision,
            "governing_evidence_id": governing.id,
            "resolution_evidence_id": resolution_id,
        }
        return DomainMutationResult(
            object_ids,
            response,
            (
                *mutation.outbox_events,
                {
                    "event_type": "evidence.request_governed",
                    "payload": {
                        "request_id": request.id,
                        "resolution_evidence_id": resolution_id,
                        "governing_evidence_id": governing.id,
                        "revision": request.revision,
                    },
                },
            ),
        )

    @classmethod
    def _append_and_advance(
        cls,
        session,
        actor,
        candidate,
        programme,
        workstream,
        claim_key,
        record_payload,
        expected_head_revision,
        claim,
        reason,
    ) -> DomainMutationResult:
        source_identity = canonical_source_identity(
            record_payload["source_type"], record_payload["source_identity"]
        )
        insert = (
            postgresql_insert(EvidenceClaimHead)
            .values(
                organization_id=actor.organization_id,
                subject_type=candidate.subject_type,
                subject_id=candidate.subject_id,
                claim_key=claim_key,
                source_identity=source_identity,
                current_record_id=None,
                revision=0,
            )
            .on_conflict_do_nothing(
                index_elements=(
                    EvidenceClaimHead.organization_id,
                    EvidenceClaimHead.subject_type,
                    EvidenceClaimHead.subject_id,
                    EvidenceClaimHead.claim_key,
                    EvidenceClaimHead.source_identity,
                )
            )
        )
        session.execute(insert)
        head = session.scalar(
            select(EvidenceClaimHead)
            .where(
                EvidenceClaimHead.organization_id == actor.organization_id,
                EvidenceClaimHead.subject_type == candidate.subject_type,
                EvidenceClaimHead.subject_id == candidate.subject_id,
                EvidenceClaimHead.claim_key == claim_key,
                EvidenceClaimHead.source_identity == source_identity,
            )
            .with_for_update()
        )
        if head is None:
            raise RuntimeError("evidence head upsert failed")
        if head.revision != expected_head_revision:
            raise CommandConflict(
                "stale_head_revision",
                expected_revision=expected_head_revision,
                current_revision=head.revision,
            )
        new_revision = head.revision + 1
        old_record_id = head.current_record_id
        record = EvidenceRecord(
            organization_id=actor.organization_id,
            programme_id=programme.id,
            workstream_id=workstream.id,
            candidate_id=candidate.id,
            subject_type=candidate.subject_type,
            subject_id=candidate.subject_id,
            claim_key=claim_key,
            created_by_id=actor.user_id,
            supersedes_id=old_record_id,
            source_identity=source_identity,
            **{key: value for key, value in record_payload.items() if key != "source_identity"},
        )
        session.add(record)
        session.flush()
        advanced_revision = session.scalar(
            text(
                "SELECT public.archie_advance_evidence_head("
                ":head_id, :new_record_id, :expected_revision, :actor_id, "
                ":receipt_id, :generation, :claim_token)"
            ),
            {
                "head_id": head.id,
                "new_record_id": record.id,
                "expected_revision": expected_head_revision,
                "actor_id": actor.user_id,
                "receipt_id": claim.receipt_id,
                "generation": claim.generation,
                "claim_token": claim.claim_token,
            },
        )
        if advanced_revision != new_revision:
            raise CommandConflict("evidence_head_advance_failed")
        event = session.scalar(
            select(EvidenceHeadEvent).where(
                EvidenceHeadEvent.organization_id == actor.organization_id,
                EvidenceHeadEvent.head_id == head.id,
                EvidenceHeadEvent.new_record_id == record.id,
                EvidenceHeadEvent.command_receipt_id == claim.receipt_id,
                EvidenceHeadEvent.command_generation == claim.generation,
                EvidenceHeadEvent.revision == new_revision,
            )
        )
        if event is None or event.reason != reason:
            raise CommandConflict("evidence_head_event_missing")
        object_ids = {
            "evidence_record_id": record.id,
            "evidence_head_id": head.id,
            "evidence_head_event_id": event.id,
        }
        response = {
            **object_ids,
            "head_revision": new_revision,
            "source_identity": source_identity,
        }
        return DomainMutationResult(
            object_ids,
            response,
            (
                {
                    "event_type": "evidence.head_advanced",
                    "payload": {
                        **response,
                        "old_record_id": old_record_id,
                        "actor_id": actor.user_id,
                        "command_receipt_id": claim.receipt_id,
                        "command_generation": claim.generation,
                    },
                },
            ),
        )

    @classmethod
    def active_evidence(
        cls, *, actor: ActorContext, subject_type: str, subject_id: int
    ) -> Sequence[EvidenceRecord]:
        subject_type = subject_type.strip() if isinstance(subject_type, str) else ""
        subject_id = parse_positive_int(subject_id)
        if not subject_type:
            raise ValueError("subject_type is required")
        with Session(db.engine) as session:
            candidate_ids = session.scalars(
                select(TransformationCandidate.id)
                .join(
                    ProgrammeWorkstream,
                    (ProgrammeWorkstream.id == TransformationCandidate.workstream_id)
                    & (
                        ProgrammeWorkstream.organization_id
                        == TransformationCandidate.organization_id
                    ),
                )
                .where(
                    TransformationCandidate.organization_id == actor.organization_id,
                    TransformationCandidate.subject_type == subject_type,
                    TransformationCandidate.subject_id == subject_id,
                    TransformationCandidate.inclusion_status == "accepted",
                    ProgrammeWorkstream.organization_id == actor.organization_id,
                )
                .order_by(
                    ProgrammeWorkstream.programme_id,
                    ProgrammeWorkstream.id,
                    TransformationCandidate.id,
                )
            ).all()
            if not candidate_ids:
                raise NotFound("evidence_subject_not_found")
            authorized = False
            for candidate_id in candidate_ids:
                _candidate, workstream, programme = cls._load_scope(
                    session, actor, candidate_id, lock=False
                )
                try:
                    TransformationProgrammeService._require_programme_authority(
                        session,
                        actor,
                        programme.id,
                        workstream.id,
                        READ_ROLES,
                        "evidence_read_not_authorised",
                    )
                except NotAuthorised:
                    continue
                authorized = True
                break
            if not authorized:
                raise NotAuthorised("evidence_read_not_authorised")
            records = tuple(
                session.scalars(
                    select(EvidenceRecord)
                    .join(
                        EvidenceClaimHead,
                        EvidenceClaimHead.current_record_id == EvidenceRecord.id,
                    )
                    .where(
                        EvidenceClaimHead.organization_id == actor.organization_id,
                        EvidenceClaimHead.subject_type == subject_type,
                        EvidenceClaimHead.subject_id == subject_id,
                        EvidenceRecord.organization_id == actor.organization_id,
                        EvidenceRecord.subject_type == subject_type,
                        EvidenceRecord.subject_id == subject_id,
                    )
                    .order_by(
                        EvidenceRecord.claim_key,
                        EvidenceRecord.source_identity,
                        EvidenceRecord.id,
                    )
                ).all()
            )
            session.expunge_all()
            return records


__all__ = [
    "ApplicationInventoryEvidenceAdapter",
    "EvidenceSourceAdapter",
    "INVENTORY_FRESHNESS",
    "TransformationEvidenceService",
    "canonical_inventory_fields",
    "canonical_source_identity",
    "load_application_for_tenant",
    "lock_application",
    "parse_positive_int",
    "sha256_canonical",
]

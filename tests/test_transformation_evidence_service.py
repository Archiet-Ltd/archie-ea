"""Versioned Transformation Room evidence and request service contracts."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.application_rationalization import ApplicationDependency
from app.models.business_capabilities import BusinessCapability
from app.models.organization import Organization
from app.models.transformation_evidence import (
    EvidenceClaimHead,
    EvidenceHeadEvent,
    EvidenceRecord,
    EvidenceRequest,
)
from app.models.transformation_programme import ProgrammeRoleAssignment, ProgrammeWorkstream
from app.models.user import User
from app.modules.transformation_room.discovery_service import RationalisationDiscoveryService
from app.modules.transformation_room.domain import (
    ActorContext,
    CommandConflict,
    NotAuthorised,
    NotFound,
    SourceResolution,
    SourceVersion,
    TypedEvidenceValue,
)
from app.modules.transformation_room.evidence_service import (
    ApplicationInventoryEvidenceAdapter,
    REQUIRED_EVIDENCE_CLAIMS,
    TransformationEvidenceService,
    canonical_source_identity,
    parse_positive_int,
    sha256_canonical,
)
from app.modules.transformation_room.gate_service import TransformationGateService

from tests.test_rationalisation_discovery_service import (
    _discover,
    _justified_distinct,
    _seed_scope,
)


@dataclass(frozen=True)
class EvidenceScope:
    organization_id: int
    foreign_organization_id: int
    actor_id: int
    foreign_actor_id: int
    workstream_id: int
    candidate_id: int
    application_id: int
    capability_id: int
    dependency_id: int
    request_id: int
    actor: ActorContext
    foreign_actor: ActorContext


@pytest.fixture(scope="module", autouse=True)
def evidence_schema(app, _schema):
    from app.commands.reconcile_schema import _reconcile

    with app.app_context():
        _added, failed, _missing, _blocking = _reconcile(dry_run=False)
        assert failed == []


@pytest.fixture
def evidence_scope(app, _schema):
    """Persist command prerequisites visible to independent fenced sessions."""
    suffix = uuid.uuid4().hex[:10]
    with app.app_context():
        db.session.remove()
        prior_steward = app.config.pop("TRANSFORMATION_PORTFOLIO_STEWARD_ID", None)
        seeded = _seed_scope(db.session, suffix=f"evidence-{suffix}", commit=True)
        discovered = next(
            item for item in _discover(seeded) if item.application_id == seeded.application_id
        )
        accepted = RationalisationDiscoveryService.accept_candidate(
            actor=seeded.actor,
            workstream_id=seeded.workstream_id,
            application_id=seeded.application_id,
            signal_digests=discovered.signal_digests,
            overlap_disposition=_justified_distinct(seeded),
            inclusion_reason="Govern this canonical inventory subject",
            command_key=f"evidence-candidate-{suffix}",
        )
        foreign_org = Organization(
            name=f"Evidence foreign {suffix}", slug=f"evidence-foreign-{suffix}"
        )
        db.session.add(foreign_org)
        db.session.flush()
        foreign_user = User(
            email=f"evidence-foreign-{suffix}@example.test",
            organization_id=foreign_org.id,
            confirmed=True,
            enterprise_role="chief_architect",
        )
        db.session.add(foreign_user)
        db.session.flush()
        foreign_org_id = foreign_org.id
        foreign_user_id = foreign_user.id
        db.session.commit()
        db.session.remove()
        scope = EvidenceScope(
            organization_id=seeded.organization_id,
            foreign_organization_id=foreign_org_id,
            actor_id=seeded.actor_id,
            foreign_actor_id=foreign_user_id,
            workstream_id=seeded.workstream_id,
            candidate_id=accepted.object_ids["candidate_id"],
            application_id=seeded.application_id,
            capability_id=seeded.capability_id,
            dependency_id=seeded.dependency_id,
            request_id=accepted.object_ids["evidence_request_id"],
            actor=seeded.actor,
            foreign_actor=ActorContext(
                foreign_user_id,
                foreign_org_id,
                frozenset({"chief_architect"}),
                f"foreign-request-{suffix}",
            ),
        )
        try:
            yield scope
        finally:
            if prior_steward is not None:
                app.config["TRANSFORMATION_PORTFOLIO_STEWARD_ID"] = prior_steward
            db.session.remove()
            with db.engine.begin() as connection:
                connection.exec_driver_sql("SET LOCAL session_replication_role = replica")
                for table_name in (
                    "transformation_outbox_events",
                    "operation_results",
                    "command_materialisations",
                    "command_idempotency_records",
                    "evidence_head_events",
                    "evidence_claim_heads",
                    "evidence_records",
                    "candidate_signals",
                    "candidate_overlap_dispositions",
                    "evidence_requests",
                    "transformation_candidates",
                    "application_dependencies",
                    "application_capability_mapping",
                    "programme_role_assignments",
                    "programme_workstreams",
                    "strategic_initiatives",
                    "application_owners",
                    "application_components",
                    "business_capability",
                    "users",
                ):
                    connection.execute(
                        text(
                            f'DELETE FROM "{table_name}" '
                            "WHERE organization_id IN (:organization_id, :foreign_id)"
                        ),
                        {
                            "organization_id": scope.organization_id,
                            "foreign_id": scope.foreign_organization_id,
                        },
                    )
                connection.execute(
                    text(
                        "DELETE FROM subscriptions "
                        "WHERE organization_id IN (:organization_id, :foreign_id)"
                    ),
                    {
                        "organization_id": scope.organization_id,
                        "foreign_id": scope.foreign_organization_id,
                    },
                )
                connection.execute(
                    text("DELETE FROM organizations WHERE id IN (:organization_id, :foreign_id)"),
                    {
                        "organization_id": scope.organization_id,
                        "foreign_id": scope.foreign_organization_id,
                    },
                )


def _record_inventory(scope: EvidenceScope, *, expected_revision=0, key="inventory-root"):
    return TransformationEvidenceService.record_observation(
        actor=scope.actor,
        candidate_id=scope.candidate_id,
        claim_key="application_owner",
        adapter_key="Application-Inventory",
        source_key=str(scope.application_id),
        expected_head_revision=expected_revision,
        command_key=key,
    )


def _plan_all_requests(scope: EvidenceScope, *, key="plan-all-evidence"):
    return TransformationEvidenceService.plan_required_requests(
        actor=scope.actor,
        candidate_id=scope.candidate_id,
        assignments={claim: scope.actor_id for claim in REQUIRED_EVIDENCE_CLAIMS},
        command_key=key,
    )


def _justified_distinct_candidate(candidate):
    overlap = next(
        signal
        for signal in candidate.signals
        if signal.rule_code == "capability_overlap"
    )
    overlap_ids = overlap.observed_values["overlapping_application_ids"]
    assert overlap_ids
    return {
        "decision": "justified_distinct",
        "overlapping_application_ids": overlap_ids,
        "rationale": "The applications serve materially different operating contexts.",
    }


def test_required_request_plan_materialises_exact_contract_and_replays(evidence_scope):
    scope = evidence_scope
    created = _plan_all_requests(scope)
    replayed = _plan_all_requests(scope)

    with Session(db.engine) as session:
        requests = session.scalars(
            select(EvidenceRequest)
            .where(
                EvidenceRequest.organization_id == scope.organization_id,
                EvidenceRequest.candidate_id == scope.candidate_id,
            )
            .order_by(EvidenceRequest.claim_key)
        ).all()
    assert {request.claim_key for request in requests} == set(
        REQUIRED_EVIDENCE_CLAIMS
    )
    assert len(requests) == len(REQUIRED_EVIDENCE_CLAIMS) == 8
    assert all(request.required and request.status == "open" for request in requests)
    assert all(request.claim_contract_version for request in requests)
    assert created.created is True
    assert replayed.created is False and replayed.idempotent is True
    assert replayed.object_ids == created.object_ids

    with pytest.raises(NotFound):
        TransformationEvidenceService.plan_required_requests(
            actor=scope.foreign_actor,
            candidate_id=scope.candidate_id,
            assignments={
                claim: scope.foreign_actor_id for claim in REQUIRED_EVIDENCE_CLAIMS
            },
            command_key="foreign-plan-probe",
        )


def test_required_claim_contract_rejects_relabeling_and_empty_semantics(
    evidence_scope,
):
    scope = evidence_scope
    _plan_all_requests(scope)

    with pytest.raises(CommandConflict, match="claim_adapter_pair_not_supported"):
        TransformationEvidenceService.record_observation(
            actor=scope.actor,
            candidate_id=scope.candidate_id,
            claim_key="cost",
            adapter_key="application-inventory",
            source_key=str(scope.application_id),
            expected_head_revision=0,
            command_key="reject-inventory-as-cost",
        )
    lifecycle_request_id = next(
        value
        for key, value in _plan_all_requests(scope).object_ids.items()
        if key == "request_lifecycle_id"
    )
    with pytest.raises(ValueError, match="lifecycle evidence"):
        TransformationEvidenceService.submit_attestation(
            actor=scope.actor,
            request_id=lifecycle_request_id,
            value=TypedEvidenceValue("string", "   ", None, None),
            expected_head_revision=0,
            command_key="reject-empty-lifecycle",
        )
    capability_request_id = next(
        value
        for key, value in _plan_all_requests(scope).object_ids.items()
        if key == "request_capability_impact_id"
    )
    with pytest.raises(ValueError, match="capability impact evidence is incomplete"):
        TransformationEvidenceService.submit_attestation(
            actor=scope.actor,
            request_id=capability_request_id,
            value=TypedEvidenceValue(
                "json",
                {"capability_ids": [], "impact": "unknown"},
                None,
                None,
            ),
            expected_head_revision=0,
            command_key="reject-unknown-capability-impact",
        )
    freshness_request_id = next(
        value
        for key, value in _plan_all_requests(scope).object_ids.items()
        if key == "request_source_freshness_id"
    )
    with pytest.raises(CommandConflict, match="authoritative_source_required"):
        TransformationEvidenceService.submit_attestation(
            actor=scope.actor,
            request_id=freshness_request_id,
            value=TypedEvidenceValue(
                "json",
                {
                    "observed_at": "not-a-timestamp",
                    "freshness_status": "fresh",
                    "source_system": "application-inventory",
                },
                None,
                None,
            ),
            expected_head_revision=0,
            command_key="reject-invalid-freshness-timestamp",
        )


@pytest.mark.parametrize(
    ("claim_key", "value"),
    (
        ("application_owner", TypedEvidenceValue("string", "N/A", None, None)),
        (
            "application_owner",
            TypedEvidenceValue(
                "json",
                {"owner_names": ["Retail Operations"], "untrusted": True},
                None,
                None,
            ),
        ),
        ("lifecycle", TypedEvidenceValue("string", "banana", None, None)),
        (
            "cost",
            TypedEvidenceValue("number", Decimal("-0.01"), "annual_tco", "GBP"),
        ),
        (
            "cost",
            TypedEvidenceValue("number", 0.1, "annual_tco", "GBP"),
        ),
        (
            "cost",
            TypedEvidenceValue(
                "number", Decimal("12.345"), "annual_tco", "GBP"
            ),
        ),
        (
            "business_criticality",
            TypedEvidenceValue(
                "json",
                {"value": "severe", "source_field": "business_criticality"},
                None,
                None,
            ),
        ),
        (
            "capability_impact",
            TypedEvidenceValue(
                "json", {"capability_ids": [], "impact": "material"}, None, None
            ),
        ),
        (
            "capability_impact",
            TypedEvidenceValue(
                "json", {"capability_ids": [3, 3], "impact": "material"}, None, None
            ),
        ),
        (
            "dependency_impact",
            TypedEvidenceValue(
                "json", {"dependency_ids": [4], "impact": "none"}, None, None
            ),
        ),
        (
            "risk",
            TypedEvidenceValue(
                "json",
                {
                    "technical_risk": "severe",
                    "business_risk": "medium",
                    "vendor_risk": "low",
                    "obsolescence_risk": "critical",
                },
                None,
                None,
            ),
        ),
        (
            "source_freshness",
            TypedEvidenceValue(
                "json",
                {
                    "observed_at": "not-a-timestamp",
                    "freshness_status": "fresh",
                    "source_system": "application-inventory",
                },
                None,
                None,
            ),
        ),
        (
            "source_freshness",
            TypedEvidenceValue(
                "json",
                {
                    "observed_at": (
                        datetime.now(timezone.utc) + timedelta(days=1)
                    ).isoformat(),
                    "freshness_status": "fresh",
                    "source_system": "application-inventory",
                },
                None,
                None,
            ),
        ),
        (
            "source_freshness",
            TypedEvidenceValue(
                "json",
                {
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "freshness_status": "fresh",
                    "source_system": "application-inventory",
                    "claimed_by_client": True,
                },
                None,
                None,
            ),
        ),
    ),
)
def test_claim_contract_rejects_noncanonical_or_incoherent_semantics(
    claim_key, value
):
    """Every governed claim has an exact schema and canonical vocabulary."""
    with pytest.raises(ValueError):
        TransformationEvidenceService._validate_claim_value(claim_key, value)


def test_source_freshness_cannot_be_self_declared_by_attestation(evidence_scope):
    planned = _plan_all_requests(evidence_scope)

    with pytest.raises(CommandConflict, match="authoritative_source_required"):
        TransformationEvidenceService.submit_attestation(
            actor=evidence_scope.actor,
            request_id=planned.object_ids["request_source_freshness_id"],
            value=TypedEvidenceValue(
                "json",
                {
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "freshness_status": "fresh",
                    "source_system": "application-inventory",
                },
                None,
                None,
            ),
            expected_head_revision=0,
            command_key="reject-self-declared-freshness",
        )


@pytest.mark.parametrize("claim_key", ("capability_impact", "dependency_impact"))
def test_impact_claim_rejects_cross_tenant_references(evidence_scope, claim_key):
    scope = evidence_scope
    with Session(db.engine) as session, session.begin():
        foreign_source = ApplicationComponent(
            organization_id=scope.foreign_organization_id,
            name=f"Foreign source {uuid.uuid4().hex[:8]}",
        )
        foreign_target = ApplicationComponent(
            organization_id=scope.foreign_organization_id,
            name=f"Foreign target {uuid.uuid4().hex[:8]}",
        )
        session.add_all((foreign_source, foreign_target))
        session.flush()
        foreign_capability = BusinessCapability(
            organization_id=scope.foreign_organization_id,
            name=f"Foreign capability {uuid.uuid4().hex[:8]}",
            code=f"EVID-{uuid.uuid4().hex[:10]}",
            level=2,
        )
        foreign_dependency = ApplicationDependency(
            organization_id=scope.foreign_organization_id,
            source_app_id=foreign_source.id,
            target_app_id=foreign_target.id,
            dependency_type="api_call",
        )
        session.add_all((foreign_capability, foreign_dependency))
        session.flush()
        foreign_id = (
            foreign_capability.id
            if claim_key == "capability_impact"
            else foreign_dependency.id
        )
    planned = _plan_all_requests(scope)
    id_key = (
        "capability_ids" if claim_key == "capability_impact" else "dependency_ids"
    )

    with pytest.raises(NotFound, match=f"{claim_key}_reference_not_found"):
        TransformationEvidenceService.submit_attestation(
            actor=scope.actor,
            request_id=planned.object_ids[f"request_{claim_key}_id"],
            value=TypedEvidenceValue(
                "json", {id_key: [foreign_id], "impact": "material"}, None, None
            ),
            expected_head_revision=0,
            command_key=f"reject-foreign-{claim_key}",
        )


def test_cost_claim_is_persisted_as_canonical_decimal(evidence_scope):
    planned = _plan_all_requests(evidence_scope)
    submitted = TransformationEvidenceService.submit_attestation(
        actor=evidence_scope.actor,
        request_id=planned.object_ids["request_cost_id"],
        value=TypedEvidenceValue(
            "number", Decimal("125000"), "annual_tco", "GBP"
        ),
        expected_head_revision=0,
        command_key="canonical-decimal-cost",
    )

    with Session(db.engine) as session:
        record = session.get(
            EvidenceRecord, submitted.object_ids["evidence_record_id"]
        )
    assert record.value_json == "125000.00"


def test_planned_observation_submits_and_accepts_the_bound_request(evidence_scope):
    scope = evidence_scope
    with Session(db.engine) as session, session.begin():
        application = session.get(ApplicationComponent, scope.application_id)
        application.application_owner = "Retail Operations"
        application.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    planned = _plan_all_requests(scope)
    request_id = planned.object_ids["request_application_owner_id"]

    observed = TransformationEvidenceService.record_observation(
        actor=scope.actor,
        candidate_id=scope.candidate_id,
        claim_key="application_owner",
        adapter_key="application-inventory",
        source_key=f"00{scope.application_id}",
        expected_head_revision=0,
        command_key="planned-owner-observation",
    )
    accepted = TransformationEvidenceService.accept_request(
        actor=scope.actor,
        request_id=request_id,
        evidence_id=observed.object_ids["evidence_record_id"],
        expected_revision=observed.response["request_revision"],
        command_key="accept-planned-owner-observation",
    )

    with Session(db.engine) as session:
        request = session.get(EvidenceRequest, request_id)
        record = session.get(
            EvidenceRecord, observed.object_ids["evidence_record_id"]
        )
    assert observed.response["request_id"] == request_id
    assert request.submitted_evidence_id == record.id
    assert request.accepted_evidence_id == record.id
    assert request.status == "accepted"
    assert accepted.response["revision"] == observed.response["request_revision"] + 1
    assert record.value_type == "json"
    assert record.value_json == {"owner_names": ["Retail Operations"]}
    assert record.claim_contract_version == request.claim_contract_version


def _grant_decision_authority(scope: EvidenceScope):
    with Session(db.engine) as session, session.begin():
        programme_id = session.scalar(
            select(ProgrammeWorkstream.programme_id).where(
                ProgrammeWorkstream.organization_id == scope.organization_id,
                ProgrammeWorkstream.id == scope.workstream_id,
            )
        )
        session.add(
            ProgrammeRoleAssignment(
                organization_id=scope.organization_id,
                programme_id=programme_id,
                workstream_id=scope.workstream_id,
                user_id=scope.actor_id,
                role="decision_authority",
                effective_from=date.today() - timedelta(days=1),
                assigned_by_id=scope.actor_id,
            )
        )


def _accept_same_subject_in_second_workstream(scope: EvidenceScope, *, key: str):
    """Create a second real workstream/candidate for the fixture application."""
    with Session(db.engine) as session, session.begin():
        first = session.get(ProgrammeWorkstream, scope.workstream_id)
        second = ProgrammeWorkstream(
            organization_id=scope.organization_id,
            programme_id=first.programme_id,
            workstream_type="application_rationalisation",
            objective="Govern the same application through a second candidate",
            scope_expression={"application_ids": [scope.application_id]},
            lifecycle_stage="discover",
            lead_id=scope.actor_id,
            revision=1,
        )
        session.add(second)
        session.flush()
        workstream_id = second.id
    second_scope = EvidenceScope(
        organization_id=scope.organization_id,
        foreign_organization_id=scope.foreign_organization_id,
        actor_id=scope.actor_id,
        foreign_actor_id=scope.foreign_actor_id,
        workstream_id=workstream_id,
        candidate_id=0,
        application_id=scope.application_id,
        capability_id=scope.capability_id,
        dependency_id=scope.dependency_id,
        request_id=0,
        actor=scope.actor,
        foreign_actor=scope.foreign_actor,
    )
    discovered = next(
        item
        for item in _discover(second_scope)
        if item.application_id == scope.application_id
    )
    return RationalisationDiscoveryService.accept_candidate(
        actor=scope.actor,
        workstream_id=workstream_id,
        application_id=scope.application_id,
        signal_digests=discovered.signal_digests,
        overlap_disposition=_justified_distinct_candidate(discovered),
        inclusion_reason="Exercise global governed evidence heads",
        command_key=f"{key}-candidate",
    )


def _record_named_source(
    scope: EvidenceScope,
    *,
    candidate_id: int,
    adapter_key: str,
    source_identity: str,
    value: str,
    expected_revision: int = 0,
):
    previous = TransformationEvidenceService.register_adapter(
        adapter_key,
        _NamedInventoryAdapter(source_identity, value),
    )
    try:
        return TransformationEvidenceService.record_observation(
            actor=scope.actor,
            candidate_id=candidate_id,
            claim_key="application_owner",
            adapter_key=adapter_key,
            source_key=str(scope.application_id),
            expected_head_revision=expected_revision,
            command_key=f"{adapter_key}-{expected_revision}",
        )
    finally:
        TransformationEvidenceService.restore_adapter(adapter_key, previous)


def test_strict_source_helpers_preserve_opaque_keys_and_reject_bad_ids():
    """Catches lossy source normalization or permissive integer parsing."""
    assert parse_positive_int("17") == 17
    for invalid in (None, True, "", " 0 ", "-2", "1.5", "abc"):
        with pytest.raises(ValueError):
            parse_positive_int(invalid)
    assert canonical_source_identity(
        "  InVenTory  ", "HTTPS://Inventory.EXAMPLE/Apps/CaseSensitive?Key=ABC"
    ) == "https://inventory.example/Apps/CaseSensitive?Key=ABC"
    assert canonical_source_identity(
        "inventory", "HTTPS://CaseSensitive:Secret@Inventory.EXAMPLE/Apps"
    ) == "https://CaseSensitive:Secret@inventory.example/Apps"
    assert canonical_source_identity(" Attestation ", "User:CaseSensitive") == (
        "attestation:user:CaseSensitive"
    )


def test_inventory_observation_versions_metadata_and_active_head(evidence_scope):
    """Catches newest-row guessing, source metadata loss, or in-place correction."""
    scope = evidence_scope
    root = _record_inventory(scope)
    root_id = root.object_ids["evidence_record_id"]
    with Session(db.engine) as session, session.begin():
        application = session.scalar(
            select(ApplicationComponent).where(
                ApplicationComponent.organization_id == scope.organization_id,
                ApplicationComponent.id == scope.application_id,
            )
        )
        application.application_owner = "Retail Operations"
        application.updated_at = datetime.now(timezone.utc)

    correction = _record_inventory(scope, expected_revision=1, key="inventory-correction")
    correction_id = correction.object_ids["evidence_record_id"]
    active = TransformationEvidenceService.active_evidence(
        actor=scope.actor,
        subject_type="application",
        subject_id=scope.application_id,
    )

    with Session(db.engine) as session:
        old = session.get(EvidenceRecord, root_id)
        current = session.get(EvidenceRecord, correction_id)
        head = session.get(EvidenceClaimHead, correction.object_ids["evidence_head_id"])
        events = session.scalars(
            select(EvidenceHeadEvent)
            .where(EvidenceHeadEvent.organization_id == scope.organization_id)
            .order_by(EvidenceHeadEvent.revision)
        ).all()
    assert old.supersedes_id is None
    assert current.supersedes_id == old.id
    assert current.value_type == "json"
    assert current.classification == "observed"
    assert current.source_identity == f"application:{scope.application_id}"
    assert current.source_uri == f"archie://application/{scope.application_id}"
    assert len(current.source_checksum) == 64
    assert current.freshness_status == "fresh"
    assert current.freshness_rule_version == "inventory-r1.1"
    assert current.created_by_id == scope.actor_id
    assert current.collected_at is not None and current.observed_at is not None
    assert head.current_record_id == current.id and head.revision == 2
    assert [event.revision for event in events] == [1, 2]
    assert {row.id for row in active} == {current.id}

    replayed = _record_inventory(
        scope, expected_revision=1, key="natural-key-correction-replay"
    )
    assert replayed.created is False and replayed.idempotent is True
    assert replayed.object_ids == correction.object_ids
    with pytest.raises(CommandConflict, match="stale_head_revision"):
        _record_inventory(scope, expected_revision=3, key="stale-correction")
    with Session(db.engine) as session:
        assert session.scalar(
            select(func.count())
            .select_from(EvidenceRecord)
            .where(EvidenceRecord.organization_id == scope.organization_id)
        ) == 2


class _UnversionedInventoryAdapter(ApplicationInventoryEvidenceAdapter):
    def resolve(self, source_key, actor):
        resolved = super().resolve(source_key, actor)
        return SourceResolution(
            "HTTPS://Inventory.EXAMPLE/Apps/CaseSensitive",
            resolved.canonical_subject_type,
            resolved.canonical_subject_id,
        )

    def read_version(self, resolution):
        version = super().read_version(resolution)
        return SourceVersion("", version.checksum, version.observed_at, version.value)


class _NamedInventoryAdapter(ApplicationInventoryEvidenceAdapter):
    """A real adapter variant for an independently governed current source."""

    def __init__(self, source_identity: str, value: str):
        self.source_identity = source_identity
        self.value = value

    def resolve(self, source_key, actor):
        resolved = super().resolve(source_key, actor)
        return SourceResolution(
            self.source_identity,
            resolved.canonical_subject_type,
            resolved.canonical_subject_id,
        )

    def read_version(self, resolution):
        inventory = super().read_version(resolution)
        value = TypedEvidenceValue("string", self.value, None, None)
        return SourceVersion(
            f"{inventory.version}:{self.source_identity}",
            sha256_canonical(value),
            inventory.observed_at,
            value,
        )


def test_unversioned_adapter_gets_content_addressed_snapshot_version(evidence_scope):
    """Catches an unversioned source being stored without a reproducible snapshot identity."""
    scope = evidence_scope
    previous = TransformationEvidenceService.register_adapter(
        " Snapshot-Inventory ", _UnversionedInventoryAdapter()
    )
    try:
        result = TransformationEvidenceService.record_observation(
            actor=scope.actor,
            candidate_id=scope.candidate_id,
            claim_key="inventory_snapshot",
            adapter_key=" SNAPSHOT-INVENTORY ",
            source_key=str(scope.application_id),
            expected_head_revision=0,
            command_key="unversioned-snapshot",
        )
    finally:
        TransformationEvidenceService.restore_adapter("snapshot-inventory", previous)

    with Session(db.engine) as session:
        record = session.get(EvidenceRecord, result.object_ids["evidence_record_id"])
    assert record.source_identity == "https://inventory.example/Apps/CaseSensitive"
    assert record.source_version == f"snapshot:{record.source_checksum}"


def test_attestation_agreement_submits_then_accepts_request(evidence_scope):
    """Catches agreement overwriting the canonical source or skipping request acceptance."""
    scope = evidence_scope
    observed = _record_inventory(scope)
    with Session(db.engine) as session:
        value = session.get(
            EvidenceRecord, observed.object_ids["evidence_record_id"]
        ).value_json
    submitted = TransformationEvidenceService.submit_attestation(
        actor=scope.actor,
        request_id=scope.request_id,
        value=TypedEvidenceValue("json", value, None, None),
        expected_head_revision=0,
        command_key="agree-attestation",
    )
    evidence_id = submitted.object_ids["evidence_record_id"]
    assert "conflict_evidence_id" not in submitted.object_ids
    accepted = TransformationEvidenceService.accept_request(
        actor=scope.actor,
        request_id=scope.request_id,
        evidence_id=evidence_id,
        expected_revision=2,
        command_key="accept-agreement",
    )
    with Session(db.engine) as session:
        request = session.get(EvidenceRequest, scope.request_id)
        evidence = session.get(EvidenceRecord, evidence_id)
        heads = session.scalars(
            select(EvidenceClaimHead).where(
                EvidenceClaimHead.organization_id == scope.organization_id,
                EvidenceClaimHead.claim_key == "application_owner",
            )
        ).all()
    assert request.status == "accepted"
    assert request.accepted_evidence_id == evidence.id
    assert accepted.response["revision"] == 3
    assert evidence.classification == "attested"
    assert evidence.source_identity == f"attestation:user:{scope.actor_id}"
    assert len(heads) == 2


def test_request_command_natural_key_uses_normalized_identifiers(evidence_scope):
    """Equivalent textual IDs must replay the exact accepted request result."""
    scope = evidence_scope
    observed = _record_inventory(scope, key="normalized-request-observation")
    with Session(db.engine) as session:
        value = session.get(
            EvidenceRecord, observed.object_ids["evidence_record_id"]
        ).value_json
    submitted = TransformationEvidenceService.submit_attestation(
        actor=scope.actor,
        request_id=scope.request_id,
        value=TypedEvidenceValue("json", value, None, None),
        expected_head_revision=0,
        command_key="normalized-request-attestation",
    )
    evidence_id = submitted.object_ids["evidence_record_id"]

    accepted = TransformationEvidenceService.accept_request(
        actor=scope.actor,
        request_id=f"00{scope.request_id}",
        evidence_id=f"00{evidence_id}",
        expected_revision="002",
        command_key="normalized-request-accept",
    )
    replayed = TransformationEvidenceService.accept_request(
        actor=scope.actor,
        request_id=scope.request_id,
        evidence_id=evidence_id,
        expected_revision=2,
        command_key="normalized-request-accept",
    )

    assert accepted.created is True
    assert replayed.created is False and replayed.idempotent is True
    assert replayed.object_ids == accepted.object_ids


def test_accept_request_rejects_current_evidence_not_submitted_for_request(
    evidence_scope,
):
    """Catches accepting a different current source than the request submission."""
    scope = evidence_scope
    observed = _record_inventory(scope)
    with Session(db.engine) as session:
        value = session.get(
            EvidenceRecord, observed.object_ids["evidence_record_id"]
        ).value_json
    TransformationEvidenceService.submit_attestation(
        actor=scope.actor,
        request_id=scope.request_id,
        value=TypedEvidenceValue("json", value, None, None),
        expected_head_revision=0,
        command_key="submitted-source-binding",
    )

    with pytest.raises(CommandConflict, match="evidence_not_submitted_for_request"):
        TransformationEvidenceService.accept_request(
            actor=scope.actor,
            request_id=scope.request_id,
            evidence_id=observed.object_ids["evidence_record_id"],
            expected_revision=2,
            command_key="reject-different-current-source",
        )


def test_accept_request_rejects_submitted_attestation_while_conflict_unresolved(
    evidence_scope,
):
    """Catches accepting an attestation while a current source still disagrees."""
    scope = evidence_scope
    _record_inventory(scope)
    submitted = TransformationEvidenceService.submit_attestation(
        actor=scope.actor,
        request_id=scope.request_id,
        value=TypedEvidenceValue("string", "Disputed owner", None, None),
        expected_head_revision=0,
        command_key="unresolved-submission",
    )

    with pytest.raises(CommandConflict, match="evidence_conflict_unresolved"):
        TransformationEvidenceService.accept_request(
            actor=scope.actor,
            request_id=scope.request_id,
            evidence_id=submitted.object_ids["evidence_record_id"],
            expected_revision=2,
            command_key="reject-unresolved-submission",
        )


def test_attestation_compares_every_current_source_and_cites_stable_leaf_order(
    evidence_scope,
):
    """Catches one agreeing source suppressing disagreements from other heads."""
    scope = evidence_scope
    observed = _record_inventory(scope)
    adapters = (
        ("source-z", _NamedInventoryAdapter("external:Z-source", "Different")),
        ("source-a", _NamedInventoryAdapter("external:A-source", "Shared")),
    )
    previous = {
        key: TransformationEvidenceService.register_adapter(key, adapter)
        for key, adapter in adapters
    }
    try:
        source_z = TransformationEvidenceService.record_observation(
            actor=scope.actor,
            candidate_id=scope.candidate_id,
            claim_key="application_owner",
            adapter_key="source-z",
            source_key=str(scope.application_id),
            expected_head_revision=0,
            command_key="three-source-z",
        )
        source_a = TransformationEvidenceService.record_observation(
            actor=scope.actor,
            candidate_id=scope.candidate_id,
            claim_key="application_owner",
            adapter_key="source-a",
            source_key=str(scope.application_id),
            expected_head_revision=0,
            command_key="three-source-a",
        )
    finally:
        for key, _adapter in adapters:
            TransformationEvidenceService.restore_adapter(key, previous[key])

    submitted = TransformationEvidenceService.submit_attestation(
        actor=scope.actor,
        request_id=scope.request_id,
        value=TypedEvidenceValue("string", "Shared", None, None),
        expected_head_revision=0,
        command_key="three-source-attestation",
    )
    conflict_id = submitted.object_ids["conflict_evidence_id"]
    current_ids = {
        observed.object_ids["evidence_record_id"],
        source_z.object_ids["evidence_record_id"],
        source_a.object_ids["evidence_record_id"],
        submitted.object_ids["evidence_record_id"],
    }
    with Session(db.engine) as session:
        conflict = session.get(EvidenceRecord, conflict_id)
        ordered = session.execute(
            select(EvidenceRecord.id, EvidenceRecord.source_identity)
            .where(EvidenceRecord.id.in_(current_ids))
            .order_by(EvidenceRecord.source_identity, EvidenceRecord.id)
        ).all()
    assert conflict.cited_evidence_ids == [row.id for row in ordered]


def test_shared_subject_heads_are_membership_authorized_not_candidate_owned(
    evidence_scope,
):
    """Catches arbitrary-first candidate auth and evidence provenance ownership."""
    scope = evidence_scope
    observed = _record_inventory(scope)
    with db.session.begin():
        first = db.session.get(ProgrammeWorkstream, scope.workstream_id)
        peer = User(
            email=f"shared-evidence-{uuid.uuid4().hex[:10]}@example.test",
            organization_id=scope.organization_id,
            confirmed=True,
            enterprise_role="application_manager",
        )
        db.session.add(peer)
        db.session.flush()
        second = ProgrammeWorkstream(
            organization_id=scope.organization_id,
            programme_id=first.programme_id,
            workstream_type="application_rationalisation",
            objective="Govern the same application in a second scope",
            scope_expression={"application_ids": [scope.application_id]},
            lifecycle_stage="discover",
            lead_id=peer.id,
            revision=1,
        )
        db.session.add(second)
        db.session.flush()
        db.session.add(
            ProgrammeRoleAssignment(
                organization_id=scope.organization_id,
                programme_id=first.programme_id,
                workstream_id=second.id,
                user_id=peer.id,
                role="evidence_owner",
                effective_from=date.today() - timedelta(days=1),
                assigned_by_id=scope.actor_id,
            )
        )
        peer_id = peer.id
        second_workstream_id = second.id

    second_scope = EvidenceScope(
        organization_id=scope.organization_id,
        foreign_organization_id=scope.foreign_organization_id,
        actor_id=scope.actor_id,
        foreign_actor_id=scope.foreign_actor_id,
        workstream_id=second_workstream_id,
        candidate_id=0,
        application_id=scope.application_id,
        capability_id=scope.capability_id,
        dependency_id=scope.dependency_id,
        request_id=0,
        actor=scope.actor,
        foreign_actor=scope.foreign_actor,
    )
    discovered = next(
        item
        for item in _discover(second_scope)
        if item.application_id == scope.application_id
    )
    second_acceptance = RationalisationDiscoveryService.accept_candidate(
        actor=scope.actor,
        workstream_id=second_workstream_id,
        application_id=scope.application_id,
        signal_digests=discovered.signal_digests,
        overlap_disposition=_justified_distinct_candidate(discovered),
        inclusion_reason="Share the canonical evidence head",
        command_key="second-workstream-candidate",
    )
    second_request_id = second_acceptance.object_ids["evidence_request_id"]
    evidence_id = observed.object_ids["evidence_record_id"]
    with Session(db.engine) as session, session.begin():
        for request_id in (scope.request_id, second_request_id):
            request = session.get(EvidenceRequest, request_id)
            request.status = "submitted"
            request.submitted_evidence_id = evidence_id
            request.submitted_at = datetime.now(timezone.utc)

    peer_actor = ActorContext(
        peer_id,
        scope.organization_id,
        frozenset({"forged_client_role"}),
        "shared-subject-peer",
    )
    active = TransformationEvidenceService.active_evidence(
        actor=peer_actor,
        subject_type="application",
        subject_id=scope.application_id,
    )
    first_accepted = TransformationEvidenceService.accept_request(
        actor=scope.actor,
        request_id=scope.request_id,
        evidence_id=evidence_id,
        expected_revision=2,
        command_key="accept-first-shared-head",
    )
    second_accepted = TransformationEvidenceService.accept_request(
        actor=peer_actor,
        request_id=second_request_id,
        evidence_id=evidence_id,
        expected_revision=2,
        command_key="accept-second-shared-head",
    )

    assert [record.id for record in active] == [evidence_id]
    assert first_accepted.response["status"] == "accepted"
    assert second_accepted.response["status"] == "accepted"


def test_disagreement_creates_conflict_and_decision_authority_resolution(evidence_scope):
    """Catches silent source selection when an attestation disagrees with observation."""
    scope = evidence_scope
    observed = _record_inventory(scope)
    submitted = TransformationEvidenceService.submit_attestation(
        actor=scope.actor,
        request_id=scope.request_id,
        value=TypedEvidenceValue("string", "Different owner", None, None),
        expected_head_revision=0,
        command_key="disagree-attestation",
    )
    conflict_id = submitted.object_ids["conflict_evidence_id"]
    _grant_decision_authority(scope)
    resolved = TransformationEvidenceService.resolve_conflict(
        actor=scope.actor,
        conflict_evidence_id=conflict_id,
        governing_evidence_id=observed.object_ids["evidence_record_id"],
        rationale="The governed inventory owner is accountable for this decision.",
        command_key="resolve-owner-conflict",
    )
    resolution_id = resolved.object_ids["evidence_record_id"]
    accepted = TransformationEvidenceService.accept_request(
        actor=scope.actor,
        request_id=scope.request_id,
        evidence_id=observed.object_ids["evidence_record_id"],
        expected_revision=3,
        command_key="accept-governed-resolution",
    )
    with Session(db.engine) as session:
        conflict = session.get(EvidenceRecord, conflict_id)
        resolution = session.get(EvidenceRecord, resolution_id)
        request = session.get(EvidenceRequest, scope.request_id)
    assert conflict.classification == "conflict"
    assert set(conflict.cited_evidence_ids) == {
        observed.object_ids["evidence_record_id"],
        submitted.object_ids["evidence_record_id"],
    }
    assert resolution.classification == "derived"
    assert resolution.value_json["governing_evidence_id"] == (
        observed.object_ids["evidence_record_id"]
    )
    assert resolution.value_json["rationale"].startswith("The governed")
    assert set(resolution.cited_evidence_ids) == {
        conflict.id,
        observed.object_ids["evidence_record_id"],
    }
    assert request.submitted_evidence_id == observed.object_ids["evidence_record_id"]
    assert request.accepted_evidence_id == observed.object_ids["evidence_record_id"]
    assert accepted.response["revision"] == 4


def test_exact_eight_contract_accepts_governing_leaf_after_conflict_resolution(
    evidence_scope,
):
    """Resolution provenance must not replace the selected typed claim value."""
    scope = evidence_scope
    with Session(db.engine) as session, session.begin():
        application = session.get(ApplicationComponent, scope.application_id)
        application.application_owner = "Retail Operations"
        application.lifecycle_status = "operational"
        application.business_criticality = "high"
        application.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    second = _accept_same_subject_in_second_workstream(
        scope, key="exact-eight-owner-provenance"
    )
    TransformationEvidenceService.plan_required_requests(
        actor=scope.actor,
        candidate_id=second.object_ids["candidate_id"],
        assignments={claim: scope.actor_id for claim in REQUIRED_EVIDENCE_CLAIMS},
        command_key="exact-eight-owner-provenance-plan",
    )
    owner_observation = TransformationEvidenceService.record_observation(
        actor=scope.actor,
        candidate_id=second.object_ids["candidate_id"],
        claim_key="application_owner",
        adapter_key="application-inventory",
        source_key=str(scope.application_id),
        expected_head_revision=0,
        command_key="exact-eight-owner-observation",
    )
    planned = _plan_all_requests(scope, key="exact-eight-conflict-plan")
    owner_attestation = TransformationEvidenceService.submit_attestation(
        actor=scope.actor,
        request_id=planned.object_ids["request_application_owner_id"],
        value=TypedEvidenceValue("string", "Different owner", None, None),
        expected_head_revision=0,
        command_key="exact-eight-owner-disagreement",
    )
    _grant_decision_authority(scope)
    resolved = TransformationEvidenceService.resolve_conflict(
        actor=scope.actor,
        conflict_evidence_id=owner_attestation.object_ids["conflict_evidence_id"],
        governing_evidence_id=owner_observation.object_ids["evidence_record_id"],
        rationale="The current application inventory owns this fact.",
        command_key="exact-eight-owner-resolution",
    )
    accepted_owner = TransformationEvidenceService.accept_request(
        actor=scope.actor,
        request_id=planned.object_ids["request_application_owner_id"],
        evidence_id=owner_observation.object_ids["evidence_record_id"],
        expected_revision=resolved.response["request_revision"],
        command_key="exact-eight-owner-acceptance",
    )

    inventory_claims = (
        "lifecycle",
        "business_criticality",
        "risk",
        "source_freshness",
    )
    for claim_key in inventory_claims:
        submitted = TransformationEvidenceService.record_observation(
            actor=scope.actor,
            candidate_id=scope.candidate_id,
            claim_key=claim_key,
            adapter_key="application-inventory",
            source_key=str(scope.application_id),
            expected_head_revision=0,
            command_key=f"exact-eight-observe-{claim_key}",
        )
        TransformationEvidenceService.accept_request(
            actor=scope.actor,
            request_id=planned.object_ids[f"request_{claim_key}_id"],
            evidence_id=submitted.object_ids["evidence_record_id"],
            expected_revision=submitted.response["request_revision"],
            command_key=f"exact-eight-accept-{claim_key}",
        )

    attested_values = {
        "cost": TypedEvidenceValue(
            "number", Decimal("125000.00"), "annual_tco", "GBP"
        ),
        "capability_impact": TypedEvidenceValue(
            "json",
            {"capability_ids": [scope.capability_id], "impact": "material"},
            None,
            None,
        ),
        "dependency_impact": TypedEvidenceValue(
            "json",
            {"dependency_ids": [scope.dependency_id], "impact": "material"},
            None,
            None,
        ),
    }
    for claim_key, value in attested_values.items():
        submitted = TransformationEvidenceService.submit_attestation(
            actor=scope.actor,
            request_id=planned.object_ids[f"request_{claim_key}_id"],
            value=value,
            expected_head_revision=0,
            command_key=f"exact-eight-attest-{claim_key}",
        )
        TransformationEvidenceService.accept_request(
            actor=scope.actor,
            request_id=planned.object_ids[f"request_{claim_key}_id"],
            evidence_id=submitted.object_ids["evidence_record_id"],
            expected_revision=submitted.response["revision"],
            command_key=f"exact-eight-accept-{claim_key}",
        )

    with Session(db.engine) as session, session.begin():
        request = session.get(
            EvidenceRequest, planned.object_ids["request_application_owner_id"]
        )
        resolution = session.get(
            EvidenceRecord, resolved.object_ids["evidence_record_id"]
        )
        workstream = session.get(ProgrammeWorkstream, scope.workstream_id)
        workstream.lifecycle_stage = "evidence"
        assert request.submitted_evidence_id == owner_observation.object_ids[
            "evidence_record_id"
        ]
        assert request.accepted_evidence_id == owner_observation.object_ids[
            "evidence_record_id"
        ]
        assert resolution.value_json["governing_evidence_id"] == (
            owner_observation.object_ids["evidence_record_id"]
        )
        assert resolution.cited_evidence_ids == [
            owner_attestation.object_ids["conflict_evidence_id"],
            owner_observation.object_ids["evidence_record_id"],
        ]

    gate = TransformationGateService.evaluate(
        actor=scope.actor,
        workstream_id=scope.workstream_id,
        target_stage="options",
    )
    assert accepted_owner.response["status"] == "accepted"
    assert gate.allowed is True
    assert gate.blockers == ()


def test_resolution_accepts_current_cited_leaf_from_another_candidate_provenance(
    evidence_scope,
):
    """Catches treating a global evidence head as owned by one candidate."""
    scope = evidence_scope
    second = _accept_same_subject_in_second_workstream(
        scope, key="foreign-provenance-resolution"
    )
    foreign_leaf = _record_named_source(
        scope,
        candidate_id=second.object_ids["candidate_id"],
        adapter_key="foreign-provenance-source",
        source_identity="external:cross-workstream-owner",
        value="Governed owner",
    )
    submitted = TransformationEvidenceService.submit_attestation(
        actor=scope.actor,
        request_id=scope.request_id,
        value=TypedEvidenceValue("string", "Disputed owner", None, None),
        expected_head_revision=0,
        command_key="foreign-provenance-attestation",
    )
    _grant_decision_authority(scope)

    resolved = TransformationEvidenceService.resolve_conflict(
        actor=scope.actor,
        conflict_evidence_id=submitted.object_ids["conflict_evidence_id"],
        governing_evidence_id=foreign_leaf.object_ids["evidence_record_id"],
        rationale="The other workstream owns the current governed source.",
        command_key="select-foreign-provenance-leaf",
    )

    with Session(db.engine) as session:
        governing = session.get(
            EvidenceRecord, foreign_leaf.object_ids["evidence_record_id"]
        )
        resolution = session.get(
            EvidenceRecord, resolved.object_ids["evidence_record_id"]
        )
    assert governing.candidate_id == second.object_ids["candidate_id"]
    assert resolution.candidate_id == scope.candidate_id
    assert resolution.value_json["governing_evidence_id"] == governing.id


def test_resolution_rejects_governing_leaf_from_its_own_resolution_source(
    evidence_scope,
):
    """Catches a resolution superseding the head that grants its authority."""
    scope = evidence_scope
    _record_inventory(scope)
    submitted = TransformationEvidenceService.submit_attestation(
        actor=scope.actor,
        request_id=scope.request_id,
        value=TypedEvidenceValue("string", "Disputed owner", None, None),
        expected_head_revision=0,
        command_key="self-governing-source-attestation",
    )
    with Session(db.engine) as session, session.begin():
        synthetic_conflict_id = session.scalar(
            text(
                "SELECT nextval("
                "pg_get_serial_sequence('evidence_records', 'id'))"
            )
        )
    resolution_source_identity = f"resolution:conflict:{synthetic_conflict_id}"
    governing = _record_named_source(
        scope,
        candidate_id=scope.candidate_id,
        adapter_key=f"self-resolution-source-{synthetic_conflict_id}",
        source_identity=resolution_source_identity,
        value="Self-governing owner",
    )
    governing_id = governing.object_ids["evidence_record_id"]
    with Session(db.engine) as session, session.begin():
        original = session.get(
            EvidenceRecord, submitted.object_ids["conflict_evidence_id"]
        )
        values = {
            column.name: getattr(original, column.name)
            for column in EvidenceRecord.__table__.columns
            if column.name not in {"id", "created_at"}
        }
        conflict_value = TypedEvidenceValue(
            "json", {"conflicting_evidence_ids": [governing_id]}, None, None
        )
        values.update(
            source_identity=(
                f"conflict:self-resolution-source:{scope.request_id}:"
                f"{synthetic_conflict_id}"
            ),
            source_version=f"self-resolution-source:{synthetic_conflict_id}",
            value_json=conflict_value.value,
            source_checksum=sha256_canonical(conflict_value),
            cited_evidence_ids=[governing_id],
            supersedes_id=None,
        )
        session.add(EvidenceRecord(id=synthetic_conflict_id, **values))
    _grant_decision_authority(scope)

    def persisted_state():
        with Session(db.engine) as session:
            head = session.scalar(
                select(EvidenceClaimHead).where(
                    EvidenceClaimHead.organization_id == scope.organization_id,
                    EvidenceClaimHead.subject_type == "application",
                    EvidenceClaimHead.subject_id == scope.application_id,
                    EvidenceClaimHead.claim_key == "application_owner",
                    EvidenceClaimHead.source_identity == resolution_source_identity,
                )
            )
            return (
                session.scalar(
                    select(func.count())
                    .select_from(EvidenceRecord)
                    .where(EvidenceRecord.organization_id == scope.organization_id)
                ),
                session.scalar(
                    select(func.count())
                    .select_from(EvidenceHeadEvent)
                    .where(EvidenceHeadEvent.organization_id == scope.organization_id)
                ),
                (head.id, head.current_record_id, head.revision),
            )

    before = persisted_state()
    with pytest.raises(
        CommandConflict, match="governing_evidence_source_not_distinct"
    ):
        TransformationEvidenceService.resolve_conflict(
            actor=scope.actor,
            conflict_evidence_id=synthetic_conflict_id,
            governing_evidence_id=governing_id,
            rationale="A resolution cannot replace its own governing source.",
            command_key="reject-self-governing-resolution-source",
        )

    assert persisted_state() == before


def test_resolution_rejects_cited_leaf_that_is_no_longer_current(evidence_scope):
    """Catches a historically cited record being selected after source correction."""
    scope = evidence_scope
    second = _accept_same_subject_in_second_workstream(
        scope, key="noncurrent-foreign-provenance"
    )
    candidate_id = second.object_ids["candidate_id"]
    old_leaf = _record_named_source(
        scope,
        candidate_id=candidate_id,
        adapter_key="corrected-cross-workstream-source",
        source_identity="external:corrected-owner",
        value="Old owner",
    )
    submitted = TransformationEvidenceService.submit_attestation(
        actor=scope.actor,
        request_id=scope.request_id,
        value=TypedEvidenceValue("string", "Disputed owner", None, None),
        expected_head_revision=0,
        command_key="noncurrent-foreign-attestation",
    )
    _record_named_source(
        scope,
        candidate_id=candidate_id,
        adapter_key="corrected-cross-workstream-source",
        source_identity="external:corrected-owner",
        value="New owner",
        expected_revision=1,
    )
    _grant_decision_authority(scope)

    with pytest.raises(CommandConflict, match="governing_evidence_not_current"):
        TransformationEvidenceService.resolve_conflict(
            actor=scope.actor,
            conflict_evidence_id=submitted.object_ids["conflict_evidence_id"],
            governing_evidence_id=old_leaf.object_ids["evidence_record_id"],
            rationale="A historical leaf must not govern.",
            command_key="reject-noncurrent-foreign-leaf",
        )


def test_resolution_rejects_current_leaf_not_cited_by_conflict(evidence_scope):
    """Catches selecting a source that appeared only after the conflict snapshot."""
    scope = evidence_scope
    second = _accept_same_subject_in_second_workstream(
        scope, key="uncited-foreign-provenance"
    )
    _record_inventory(scope)
    submitted = TransformationEvidenceService.submit_attestation(
        actor=scope.actor,
        request_id=scope.request_id,
        value=TypedEvidenceValue("string", "Disputed owner", None, None),
        expected_head_revision=0,
        command_key="uncited-foreign-attestation",
    )
    uncited = _record_named_source(
        scope,
        candidate_id=second.object_ids["candidate_id"],
        adapter_key="late-cross-workstream-source",
        source_identity="external:late-owner",
        value="Late owner",
    )
    _grant_decision_authority(scope)

    with pytest.raises(CommandConflict, match="governing_evidence_not_conflict_leaf"):
        TransformationEvidenceService.resolve_conflict(
            actor=scope.actor,
            conflict_evidence_id=submitted.object_ids["conflict_evidence_id"],
            governing_evidence_id=uncited.object_ids["evidence_record_id"],
            rationale="An uncited leaf must not govern.",
            command_key="reject-uncited-current-leaf",
        )


def test_resolution_rejects_cited_leaf_from_foreign_tenant(evidence_scope):
    """Catches a malicious citation turning a foreign record into governing evidence."""
    scope = evidence_scope
    observed = _record_inventory(scope)
    submitted = TransformationEvidenceService.submit_attestation(
        actor=scope.actor,
        request_id=scope.request_id,
        value=TypedEvidenceValue("string", "Disputed owner", None, None),
        expected_head_revision=0,
        command_key="foreign-tenant-citation-attestation",
    )
    with Session(db.engine) as session, session.begin():
        source = session.get(EvidenceRecord, observed.object_ids["evidence_record_id"])
        values = {
            column.name: getattr(source, column.name)
            for column in EvidenceRecord.__table__.columns
            if column.name not in {"id", "created_at"}
        }
        values.update(
            organization_id=scope.foreign_organization_id,
            created_by_id=scope.foreign_actor_id,
            collector_id=scope.foreign_actor_id,
            source_identity="foreign-tenant:governing-owner",
            source_version="foreign-tenant-probe",
            supersedes_id=None,
        )
        foreign_leaf = EvidenceRecord(**values)
        session.add(foreign_leaf)
        session.flush()

        legitimate = session.get(
            EvidenceRecord, submitted.object_ids["conflict_evidence_id"]
        )
        conflict_values = {
            column.name: getattr(legitimate, column.name)
            for column in EvidenceRecord.__table__.columns
            if column.name not in {"id", "created_at"}
        }
        conflict_value = TypedEvidenceValue(
            "json", {"conflicting_evidence_ids": [foreign_leaf.id]}, None, None
        )
        conflict_values.update(
            source_identity=f"conflict:foreign-tenant-probe:{scope.request_id}",
            source_version="foreign-tenant-conflict-probe",
            value_json=conflict_value.value,
            source_checksum=sha256_canonical(conflict_value),
            cited_evidence_ids=[foreign_leaf.id],
            supersedes_id=None,
        )
        synthetic_conflict = EvidenceRecord(**conflict_values)
        session.add(synthetic_conflict)
        session.flush()
        conflict_id = synthetic_conflict.id
        foreign_leaf_id = foreign_leaf.id
    _grant_decision_authority(scope)

    with pytest.raises(NotFound, match="governing_evidence_not_found"):
        TransformationEvidenceService.resolve_conflict(
            actor=scope.actor,
            conflict_evidence_id=conflict_id,
            governing_evidence_id=foreign_leaf_id,
            rationale="Foreign tenant evidence must never govern.",
            command_key="reject-foreign-tenant-governing-leaf",
        )


def test_decline_and_expiry_do_not_complete_required_request(evidence_scope):
    """Catches declined/expired evidence debt being treated as accepted evidence."""
    scope = evidence_scope
    declined = TransformationEvidenceService.decline_request(
        actor=scope.actor,
        request_id=scope.request_id,
        reason="The assignee cannot attest this fact.",
        expected_revision=1,
        command_key="decline-request",
    )
    with Session(db.engine) as session:
        request = session.get(EvidenceRequest, scope.request_id)
    assert declined.response["status"] == "declined"
    assert request.required is True
    assert request.accepted_evidence_id is None
    assert request.waiver_id is None


def test_expired_request_can_receive_authorised_expiring_unavailable_waiver(
    evidence_scope,
):
    """Catches indefinite, anonymous, or unaccountable evidence waivers."""
    scope = evidence_scope
    with Session(db.engine) as session, session.begin():
        request = session.get(EvidenceRequest, scope.request_id)
        request.due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    expired = TransformationEvidenceService.expire_request(
        actor=scope.actor,
        request_id=scope.request_id,
        expected_revision=2,
        command_key="expire-request",
    )
    assert expired.response["status"] == "expired"
    _grant_decision_authority(scope)
    waived = TransformationEvidenceService.waive_unavailable_request(
        actor=scope.actor,
        request_id=scope.request_id,
        reason="Source owner is unavailable during the decision window.",
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        interim_accountable_id=scope.actor_id,
        expected_revision=3,
        command_key="waive-expired-request",
    )
    with Session(db.engine) as session:
        request = session.get(EvidenceRequest, scope.request_id)
    assert request.status == "expired"
    assert request.accepted_evidence_id is None
    assert request.waiver_id == request.id
    assert request.waiver_authority_id == scope.actor_id
    assert request.interim_accountable_id == scope.actor_id
    assert request.waiver_expires_at > datetime.now(timezone.utc)
    assert waived.response["revision"] == 4


def test_cross_tenant_other_assignee_and_forged_role_are_denied(evidence_scope):
    """Catches request IDs or ActorContext claims bypassing tenant/assignee checks."""
    scope = evidence_scope
    with pytest.raises(NotFound):
        TransformationEvidenceService.submit_attestation(
            actor=scope.foreign_actor,
            request_id=scope.request_id,
            value=TypedEvidenceValue("string", "Foreign claim", None, None),
            expected_head_revision=0,
            command_key="foreign-attestation",
        )
    with db.session.begin():
        peer = User(
            email=f"evidence-peer-{uuid.uuid4().hex[:10]}@example.test",
            organization_id=scope.organization_id,
            confirmed=True,
            enterprise_role="portfolio_manager",
        )
        db.session.add(peer)
        db.session.flush()
        peer_id = peer.id
    forged = ActorContext(
        peer_id,
        scope.organization_id,
        frozenset({"chief_architect", "decision_authority"}),
        "forged-evidence-role",
    )
    with pytest.raises(NotAuthorised):
        TransformationEvidenceService.submit_attestation(
            actor=forged,
            request_id=scope.request_id,
            value=TypedEvidenceValue("string", "Unassigned claim", None, None),
            expected_head_revision=0,
            command_key="other-assignee",
        )

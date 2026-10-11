"""Deterministic, evidence-citing application rationalisation discovery."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date, timedelta

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import db
from app.models.application_capability import ApplicationCapabilityMapping
from app.models.application_portfolio import ApplicationComponent
from app.models.application_rationalization import ApplicationDependency
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.models.archimate_relationship_sync import _ensure_relationship
from app.models.business_capabilities import BusinessCapability
from app.models.organization import Organization
from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus
from app.models.transformation_db_guards import ensure_transformation_db_guards
from app.models.strategic import StrategicInitiative
from app.models.transformation_evidence import (
    CandidateOverlapDisposition,
    CandidateSignal,
    EvidenceRequest,
    TransformationCandidate,
)
from app.models.transformation_programme import (
    ProgrammeRoleAssignment,
    ProgrammeWorkstream,
)
from app.models.user import User
from app.modules.transformation_room.domain import (
    ActorContext,
    CommandConflict,
    DiscoveryFilters,
    NotAuthorised,
)
from app.modules.transformation_room.command_service import CommandService
from app.modules.transformation_room.discovery_service import (
    RationalisationDiscoveryService,
)
from app.modules.transformation_room.gate_service import TransformationGateService


@dataclass(frozen=True)
class DiscoveryScope:
    organization_id: int
    actor_id: int
    portfolio_peer_id: int
    steward_id: int
    workstream_id: int
    application_id: int
    sibling_application_id: int
    unknown_application_id: int
    dependency_id: int
    capability_id: int
    mapping_ids: tuple[int, int]
    actor: ActorContext


@pytest.fixture(scope="module", autouse=True)
def discovery_guard_schema(app, _schema):
    with app.app_context(), db.engine.begin() as connection:
        ensure_transformation_db_guards(connection)


def _seed_scope(session, *, suffix: str, commit: bool = False) -> DiscoveryScope:
    organization = Organization(
        name=f"Discovery Org {suffix}", slug=f"discovery-{suffix}"
    )
    session.add(organization)
    session.flush()
    # Callers add more people than Community admits; record a plan that has room.
    session.add(Subscription(
        organization_id=organization.id, plan=SubscriptionPlan.enterprise,
        status=SubscriptionStatus.active, seats_purchased=0,
    ))
    architect = User(
        email=f"architect-{suffix}@example.test",
        organization_id=organization.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    portfolio_peer = User(
        email=f"portfolio-peer-{suffix}@example.test",
        organization_id=organization.id,
        confirmed=True,
        enterprise_role="portfolio_manager",
    )
    steward = User(
        email=f"steward-{suffix}@example.test",
        organization_id=organization.id,
        confirmed=True,
        enterprise_role="portfolio_manager",
    )
    session.add_all((architect, portfolio_peer, steward))
    session.flush()
    target = ApplicationComponent(
        organization_id=organization.id,
        name=f"Target {suffix}",
        total_cost_of_ownership=125000.0,
        end_of_life_date=date(2027, 3, 31),
        technical_risk="high",
        business_risk="medium",
        vendor_risk="low",
        obsolescence_risk="critical",
        health_status="at_risk",
    )
    sibling = ApplicationComponent(
        organization_id=organization.id,
        name=f"Sibling {suffix}",
        total_cost_of_ownership=95000.0,
    )
    unknown = ApplicationComponent(
        organization_id=organization.id,
        name=f"Unknown {suffix}",
    )
    session.add_all((target, sibling, unknown))
    session.flush()
    capability = BusinessCapability(
        organization_id=organization.id,
        name=f"Customer servicing {suffix}",
        code=f"DISC-{suffix}",
        level=2,
    )
    session.add(capability)
    session.flush()
    target_mapping = ApplicationCapabilityMapping(
        organization_id=organization.id,
        application_component_id=target.id,
        business_capability_id=capability.id,
        coverage_percentage=80,
        is_active=True,
    )
    sibling_mapping = ApplicationCapabilityMapping(
        organization_id=organization.id,
        application_component_id=sibling.id,
        business_capability_id=capability.id,
        coverage_percentage=75,
        is_active=True,
    )
    dependency = ApplicationDependency(
        organization_id=organization.id,
        source_app_id=target.id,
        target_app_id=sibling.id,
        dependency_type="api_call",
        dependency_strength="critical",
        status="active",
    )
    programme = StrategicInitiative(
        organization_id=organization.id,
        name=f"Application rationalisation {suffix}",
        description="Reduce duplicated cost without disrupting service",
        record_kind="transformation_programme",
        status="draft",
        owner_id=architect.id,
        revision=1,
    )
    session.add_all((target_mapping, sibling_mapping, dependency, programme))
    session.flush()
    workstream = ProgrammeWorkstream(
        organization_id=organization.id,
        programme_id=programme.id,
        workstream_type="application_rationalisation",
        objective="Remove avoidable application duplication",
        scope_expression={"application_ids": [target.id, unknown.id]},
        lifecycle_stage="discover",
        lead_id=architect.id,
        revision=1,
    )
    session.add(workstream)
    session.flush()
    scope = DiscoveryScope(
        organization_id=organization.id,
        actor_id=architect.id,
        portfolio_peer_id=portfolio_peer.id,
        steward_id=steward.id,
        workstream_id=workstream.id,
        application_id=target.id,
        sibling_application_id=sibling.id,
        unknown_application_id=unknown.id,
        dependency_id=dependency.id,
        capability_id=capability.id,
        mapping_ids=(target_mapping.id, sibling_mapping.id),
        actor=ActorContext(
            architect.id,
            organization.id,
            frozenset({"forged_client_role"}),
            f"request-{suffix}",
        ),
    )
    if commit:
        session.commit()
    else:
        session.flush()
    return scope


def _discover(scope: DiscoveryScope):
    return RationalisationDiscoveryService.discover(
        actor=scope.actor,
        workstream_id=scope.workstream_id,
        filters=DiscoveryFilters(business_unit_ids=(), capability_ids=()),
    )


def _justified_distinct(scope: DiscoveryScope):
    return {
        "decision": "justified_distinct",
        "overlapping_application_ids": [scope.sibling_application_id],
        "rationale": "The applications serve materially different operating contexts.",
    }


def test_capability_archimate_element_inherits_tenant(db_session):
    scope = _seed_scope(db_session, suffix=uuid.uuid4().hex[:10])

    capability = db_session.get(BusinessCapability, scope.capability_id)
    element = db_session.get(ArchiMateElement, capability.archimate_element_id)

    assert element is not None
    assert element.organization_id == scope.organization_id
    relationships = db_session.scalars(
        select(ArchiMateRelationship).where(
            ArchiMateRelationship.target_id == element.id,
        )
    ).all()
    assert relationships
    assert {relationship.organization_id for relationship in relationships} == {
        scope.organization_id
    }


def test_discovery_exposes_seven_real_signals_and_writes_nothing(db_session):
    """Catches opaque scores, fabricated fallbacks, unstable order, or read-side writes."""
    scope = _seed_scope(db_session, suffix=uuid.uuid4().hex[:10])
    before = (
        db_session.scalar(select(func.count()).select_from(TransformationCandidate)),
        db_session.scalar(select(func.count()).select_from(CandidateSignal)),
    )

    discovered = _discover(scope)

    assert [item.application_id for item in discovered] == [
        scope.application_id,
        scope.unknown_application_id,
    ]
    target = discovered[0]
    assert [signal.rule_code for signal in target.signals] == [
        "capability_overlap",
        "cost",
        "end_of_life",
        "risk",
        "technical_health",
        "dependency_concentration",
        "owner_data_gaps",
    ]
    assert target.signal_digests == tuple(
        signal.content_hash for signal in target.signals
    )
    for signal in target.signals:
        assert signal.rule_version.startswith(
            RationalisationDiscoveryService.RULESET_VERSION
        )
        assert signal.evaluated_at is not None
        assert len(signal.content_hash) == 64
        assert isinstance(signal.source_record_ids, dict)
        assert isinstance(signal.observed_values, dict)

    by_rule = {signal.rule_code: signal for signal in target.signals}
    assert by_rule["capability_overlap"].observed_values == {
        "capability_ids": [scope.capability_id],
        "overlapping_application_ids": [scope.sibling_application_id],
        "overlap_count": 1,
    }
    assert set(by_rule["capability_overlap"].source_record_ids["application_capability_mapping"]) == set(
        scope.mapping_ids
    )
    assert by_rule["cost"].observed_values["total_cost_of_ownership"] == 125000.0
    assert by_rule["end_of_life"].observed_values["end_of_life_date"] == "2027-03-31"
    assert by_rule["risk"].observed_values["obsolescence_risk"] == "critical"
    assert by_rule["technical_health"].observed_values["health_status"] == "at_risk"
    assert by_rule["dependency_concentration"].source_record_ids[
        "application_dependencies"
    ] == [scope.dependency_id]
    assert by_rule["owner_data_gaps"].observed_values["owner_record_ids"] == []
    assert by_rule["owner_data_gaps"].observed_values["missing_owner"] is True
    assert db_session.scalar(select(func.count()).select_from(TransformationCandidate)) == before[0]
    assert db_session.scalar(select(func.count()).select_from(CandidateSignal)) == before[1]


def test_missing_inputs_are_named_unknowns_never_numeric_zero(db_session):
    """Catches absent inventory facts being presented as measured zeroes."""
    scope = _seed_scope(db_session, suffix=uuid.uuid4().hex[:10])

    unknown = next(
        item for item in _discover(scope) if item.application_id == scope.unknown_application_id
    )
    by_rule = {signal.rule_code: signal for signal in unknown.signals}

    assert unknown.confidence is None
    assert set(unknown.unknown_codes) >= {
        "capability_overlap_unavailable",
        "cost_unavailable",
        "end_of_life_unavailable",
        "risk_unavailable",
        "technical_health_unavailable",
        "dependency_data_unavailable",
    }
    assert by_rule["cost"].observed_values == {"total_cost_of_ownership": None}
    assert by_rule["cost"].confidence is None
    assert by_rule["dependency_concentration"].observed_values == {
        "dependency_record_ids": None,
        "dependency_count": None,
    }


@pytest.mark.parametrize("invalid_value", ["", "   ", "unknown", "unsupported"])
def test_invalid_risk_categories_are_named_unknowns(db_session, invalid_value):
    scope = _seed_scope(db_session, suffix=uuid.uuid4().hex[:10])
    application = db_session.get(ApplicationComponent, scope.application_id)
    application.technical_risk = invalid_value
    db_session.flush()

    target = next(
        item for item in _discover(scope) if item.application_id == scope.application_id
    )
    signal = next(item for item in target.signals if item.rule_code == "risk")

    assert signal.unknown_code == "risk_unavailable"
    assert signal.confidence is None
    assert signal.observed_values["technical_risk"] is None


@pytest.mark.parametrize("invalid_value", ["", "   ", "unknown", "unsupported"])
def test_invalid_health_categories_are_named_unknowns(db_session, invalid_value):
    scope = _seed_scope(db_session, suffix=uuid.uuid4().hex[:10])
    application = db_session.get(ApplicationComponent, scope.application_id)
    application.health_status = invalid_value
    db_session.flush()

    target = next(
        item for item in _discover(scope) if item.application_id == scope.application_id
    )
    signal = next(
        item for item in target.signals if item.rule_code == "technical_health"
    )

    assert signal.unknown_code == "technical_health_unavailable"
    assert signal.confidence is None
    assert signal.observed_values["health_status"] is None


def test_supported_categories_are_normalized_before_digesting(db_session):
    scope = _seed_scope(db_session, suffix=uuid.uuid4().hex[:10])
    application = db_session.get(ApplicationComponent, scope.application_id)
    application.technical_risk = " HIGH "
    application.health_status = " AT_RISK "
    db_session.flush()

    target = next(
        item for item in _discover(scope) if item.application_id == scope.application_id
    )
    signals = {item.rule_code: item for item in target.signals}

    assert signals["risk"].observed_values["technical_risk"] == "high"
    assert signals["risk"].confidence == 1
    assert signals["technical_health"].observed_values["health_status"] == "at_risk"
    assert signals["technical_health"].confidence == 1


def test_capability_signal_rejects_cross_tenant_mapping_and_excludes_it(
    db_session, make_org
):
    """A cross-tenant mapping cannot commit or leak a foreign citation."""
    scope = _seed_scope(db_session, suffix=uuid.uuid4().hex[:10])
    target_mapping = db_session.scalar(
        select(ApplicationCapabilityMapping).where(
            ApplicationCapabilityMapping.organization_id == scope.organization_id,
            ApplicationCapabilityMapping.id == scope.mapping_ids[0],
        )
    )
    db_session.delete(target_mapping)
    foreign_org = make_org("foreign-capability")
    foreign_capability = BusinessCapability(
        organization_id=foreign_org.id,
        name="Foreign capability",
        code=f"FOREIGN-{uuid.uuid4().hex[:10]}",
        level=2,
    )
    db_session.add(foreign_capability)
    db_session.flush()
    corrupt_mapping = ApplicationCapabilityMapping(
        organization_id=scope.organization_id,
        application_component_id=scope.application_id,
        business_capability_id=foreign_capability.id,
        coverage_percentage=100,
        is_active=True,
    )
    savepoint = db_session.begin_nested()
    try:
        db_session.add(corrupt_mapping)
        with pytest.raises(ValueError, match="must share a tenant"):
            db_session.flush()
    finally:
        savepoint.rollback()

    target = next(
        item for item in _discover(scope) if item.application_id == scope.application_id
    )
    signal = next(
        item for item in target.signals if item.rule_code == "capability_overlap"
    )

    assert signal.unknown_code == "capability_overlap_unavailable"
    assert signal.confidence is None
    assert signal.observed_values == {
        "capability_ids": None,
        "overlapping_application_ids": None,
        "overlap_count": None,
    }
    assert foreign_capability.id not in signal.source_record_ids.get(
        "business_capability", ()
    )
    assert corrupt_mapping.id not in signal.source_record_ids.get(
        "application_capability_mapping", ()
    )


def test_relationship_sync_rejects_foreign_same_tenant_endpoints(
    db_session, make_org, tenant_ctx
):
    scope = _seed_scope(db_session, suffix=uuid.uuid4().hex[:10])
    application = db_session.get(ApplicationComponent, scope.application_id)
    capability = db_session.get(BusinessCapability, scope.capability_id)
    active_org = make_org("relationship-active")

    with tenant_ctx(active_org.id):
        with pytest.raises(ValueError, match="outside the active tenant"):
            _ensure_relationship(
                db_session,
                "serving",
                application.archimate_element_id,
                capability.archimate_element_id,
            )
    with pytest.raises(ValueError, match="outside the active tenant"):
        _ensure_relationship(
            db_session,
            "serving",
            application.archimate_element_id,
            capability.archimate_element_id,
            expected_organization_id=active_org.id,
        )


@pytest.fixture
def committed_scope(app, _schema):
    """Persist prerequisites because fenced commands intentionally use new sessions."""
    suffix = uuid.uuid4().hex[:10]
    with app.app_context():
        db.session.remove()
        scope = _seed_scope(db.session, suffix=suffix, commit=True)
        previous_steward = app.config.get("TRANSFORMATION_PORTFOLIO_STEWARD_ID")
        app.config["TRANSFORMATION_PORTFOLIO_STEWARD_ID"] = scope.steward_id
        try:
            yield scope
        finally:
            if previous_steward is None:
                app.config.pop("TRANSFORMATION_PORTFOLIO_STEWARD_ID", None)
            else:
                app.config["TRANSFORMATION_PORTFOLIO_STEWARD_ID"] = previous_steward
            db.session.remove()
            with db.engine.begin() as connection:
                connection.exec_driver_sql("SET LOCAL session_replication_role = replica")
                for table_name in (
                    "transformation_outbox_events",
                    "operation_results",
                    "command_materialisations",
                    "command_idempotency_records",
                    "candidate_overlap_dispositions",
                    "candidate_signals",
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
                            "WHERE organization_id = :organization_id"
                        ),
                        {"organization_id": scope.organization_id},
                    )
                connection.execute(
                    text("DELETE FROM subscriptions WHERE organization_id = :organization_id"),
                    {"organization_id": scope.organization_id},
                )
                connection.execute(
                    text("DELETE FROM organizations WHERE id = :organization_id"),
                    {"organization_id": scope.organization_id},
                )


def test_acceptance_recomputes_citations_replays_and_does_not_copy_application(
    committed_scope,
):
    """Catches duplicate inventory rows, uncited acceptance, replay duplication, or gate advance."""
    scope = committed_scope
    discovered = next(
        item for item in _discover(scope) if item.application_id == scope.application_id
    )
    with Session(db.engine) as session:
        app_count_before = session.scalar(
            select(func.count())
            .select_from(ApplicationComponent)
            .where(ApplicationComponent.organization_id == scope.organization_id)
        )
    created = RationalisationDiscoveryService.accept_candidate(
        actor=scope.actor,
        workstream_id=scope.workstream_id,
        application_id=scope.application_id,
        signal_digests=discovered.signal_digests,
        overlap_disposition=_justified_distinct(scope),
        inclusion_reason="  Duplicate capability and material risk  ",
        command_key="accept-target",
    )
    replayed = RationalisationDiscoveryService.accept_candidate(
        actor=scope.actor,
        workstream_id=scope.workstream_id,
        application_id=scope.application_id,
        signal_digests=discovered.signal_digests,
        overlap_disposition=_justified_distinct(scope),
        inclusion_reason="Duplicate capability and material risk",
        command_key="accept-target",
    )

    with Session(db.engine) as session:
        candidate = session.scalar(
            select(TransformationCandidate).where(
                TransformationCandidate.organization_id == scope.organization_id,
                TransformationCandidate.workstream_id == scope.workstream_id,
                TransformationCandidate.subject_type == "application",
                TransformationCandidate.subject_id == scope.application_id,
            )
        )
        signals = session.scalars(
            select(CandidateSignal)
            .where(
                CandidateSignal.organization_id == scope.organization_id,
                CandidateSignal.candidate_id == candidate.id,
            )
            .order_by(CandidateSignal.rule_code)
        ).all()
        request = session.scalar(
            select(EvidenceRequest).where(
                EvidenceRequest.organization_id == scope.organization_id,
                EvidenceRequest.candidate_id == candidate.id,
                EvidenceRequest.claim_key == "application_owner",
            )
        )
        workstream = session.scalar(
            select(ProgrammeWorkstream).where(
                ProgrammeWorkstream.organization_id == scope.organization_id,
                ProgrammeWorkstream.id == scope.workstream_id,
            )
        )
        app_count_after = session.scalar(
            select(func.count())
            .select_from(ApplicationComponent)
            .where(ApplicationComponent.organization_id == scope.organization_id)
        )

    assert created.created is True and created.idempotent is False
    assert replayed.created is False and replayed.idempotent is True
    assert replayed.operation_result_id == created.operation_result_id
    assert candidate.inclusion_reason == "Duplicate capability and material risk"
    assert {row.content_hash for row in signals} == set(discovered.signal_digests)
    assert len(signals) == 7
    assert request.required is True and request.status == "open"
    assert request.assigned_to_id == scope.steward_id
    assert workstream.lifecycle_stage == "discover" and workstream.revision == 1
    assert app_count_after == app_count_before

    with Session(db.engine) as session:
        disposition = session.scalar(
            select(CandidateOverlapDisposition).where(
                CandidateOverlapDisposition.organization_id
                == scope.organization_id,
                CandidateOverlapDisposition.candidate_id == candidate.id,
            )
        )
    assert disposition.decision == "justified_distinct"
    assert disposition.overlapping_application_ids == [scope.sibling_application_id]
    assert disposition.decided_by_id == scope.actor_id

    with pytest.raises(CommandConflict, match="natural_key_payload_conflict"):
        RationalisationDiscoveryService.accept_candidate(
            actor=scope.actor,
            workstream_id=scope.workstream_id,
            application_id=scope.application_id,
            signal_digests=discovered.signal_digests,
            overlap_disposition=_justified_distinct(scope),
            inclusion_reason="Same subject, second command",
            command_key="accept-target-again",
        )


def test_acceptance_requires_the_exact_current_rule_set(committed_scope):
    scope = committed_scope
    discovered = next(
        item for item in _discover(scope) if item.application_id == scope.application_id
    )

    with pytest.raises(CommandConflict, match="candidate_signal_set_incomplete"):
        RationalisationDiscoveryService.accept_candidate(
            actor=scope.actor,
            workstream_id=scope.workstream_id,
            application_id=scope.application_id,
            signal_digests=discovered.signal_digests[:-1],
            overlap_disposition=_justified_distinct(scope),
            inclusion_reason="A subset must never define the governed basis.",
            command_key="reject-signal-subset",
        )


def test_positive_overlap_requires_authorised_immutable_disposition(committed_scope):
    scope = committed_scope
    discovered = next(
        item for item in _discover(scope) if item.application_id == scope.application_id
    )

    with pytest.raises(
        CommandConflict, match="capability_overlap_disposition_required"
    ):
        RationalisationDiscoveryService.accept_candidate(
            actor=scope.actor,
            workstream_id=scope.workstream_id,
            application_id=scope.application_id,
            signal_digests=discovered.signal_digests,
            inclusion_reason="Positive overlap cannot resolve itself.",
            command_key="missing-overlap-disposition",
        )

    with pytest.raises(NotAuthorised, match="overlap_disposition_subject_outside_tenant"):
        RationalisationDiscoveryService.accept_candidate(
            actor=scope.actor,
            workstream_id=scope.workstream_id,
            application_id=scope.application_id,
            signal_digests=discovered.signal_digests,
            overlap_disposition={
                "decision": "justified_distinct",
                "overlapping_application_ids": [scope.sibling_application_id + 10_000_000],
                "rationale": "A foreign or nonexistent subject cannot be dispositioned.",
            },
            inclusion_reason="Reject a non-tenant overlap reference.",
            command_key="foreign-overlap-disposition",
        )


@pytest.mark.parametrize("forgery", ("cross_tenant", "stale_generation"))
def test_overlap_disposition_insert_is_bound_to_signed_live_candidate_command(
    committed_scope, forgery
):
    """Direct SQL cannot forge a disposition's tenant or receipt generation."""
    scope = committed_scope
    discovered = next(
        item for item in _discover(scope) if item.application_id == scope.application_id
    )
    overlap = next(
        signal for signal in discovered.signals if signal.rule_code == "capability_overlap"
    )
    natural_key = f"candidate:{scope.workstream_id}:application:{scope.application_id}"
    claim = CommandService.claim_or_reconcile(
        actor=scope.actor,
        operation="candidate.accept",
        idempotency_key=f"overlap-guard-{forgery}",
        request_digest="a" * 64,
        natural_key=natural_key,
        authorizer=RationalisationDiscoveryService.authorise_candidate_acceptance(
            scope.workstream_id, scope.application_id
        ),
    )
    with Session(db.engine) as session:
        transaction = session.begin()
        try:
            candidate = TransformationCandidate(
                organization_id=scope.organization_id,
                workstream_id=scope.workstream_id,
                subject_type="application",
                subject_id=scope.application_id,
                inclusion_status="accepted",
                inclusion_reason="Database guard fixture",
                accepted_by_id=scope.actor_id,
                accepted_at=CommandService._database_now(session),
                ruleset_version=RationalisationDiscoveryService.RULESET_VERSION,
                ruleset_digest="b" * 64,
                revision=1,
            )
            session.add(candidate)
            session.flush()
            session.add(
                CandidateSignal(
                    organization_id=scope.organization_id,
                    candidate_id=candidate.id,
                    rule_code="capability_overlap",
                    rule_version=overlap.rule_version,
                    payload_json={
                        "observed_values": overlap.observed_values,
                        "confidence": str(overlap.confidence),
                        "unknown_code": overlap.unknown_code,
                    },
                    source_record_ids=overlap.source_record_ids,
                    evaluated_at=overlap.evaluated_at,
                    content_hash=overlap.content_hash,
                )
            )
            session.flush()
            organization_id = scope.organization_id
            generation = claim.generation
            if forgery == "cross_tenant":
                foreign_org = Organization(
                    name=f"Forged overlap {uuid.uuid4().hex[:8]}",
                    slug=f"forged-overlap-{uuid.uuid4().hex[:8]}",
                )
                session.add(foreign_org)
                session.flush()
                organization_id = foreign_org.id
            else:
                generation += 1
            session.add(
                CandidateOverlapDisposition(
                    organization_id=organization_id,
                    candidate_id=candidate.id,
                    signal_digest=overlap.content_hash,
                    decision="justified_distinct",
                    overlapping_application_ids=[scope.sibling_application_id],
                    rationale="A forged row must fail before persistence.",
                    target_application_id=None,
                    decided_by_id=scope.actor_id,
                    command_receipt_id=claim.receipt_id,
                    command_generation=generation,
                    decided_at=CommandService._database_now(session),
                )
            )
            with pytest.raises(DBAPIError):
                session.flush()
        finally:
            transaction.rollback()

def test_live_discovery_gate_loads_accepted_candidate_signals_and_owner_request(
    committed_scope,
):
    """Catches the persisted gate snapshot replacing installed Task 5 rows with empties."""
    scope = committed_scope
    discovered = next(
        item for item in _discover(scope) if item.application_id == scope.application_id
    )
    RationalisationDiscoveryService.accept_candidate(
        actor=scope.actor,
        workstream_id=scope.workstream_id,
        application_id=scope.application_id,
        signal_digests=discovered.signal_digests,
        overlap_disposition=_justified_distinct(scope),
        inclusion_reason="Accepted scope still needs owner evidence",
        command_key="live-gate-candidate",
    )

    snapshot = TransformationGateService.load_policy_snapshot(
        actor=scope.actor,
        workstream_id=scope.workstream_id,
    )
    gate = TransformationGateService.evaluate(
        actor=scope.actor,
        workstream_id=scope.workstream_id,
        target_stage="evidence",
    )

    assert len(snapshot.accepted_candidates) == 1
    assert snapshot.accepted_candidates[0].subject_exists is True
    assert snapshot.accepted_candidates[0].duplicates_resolved is True
    assert len(snapshot.evidence_requests) == 1
    assert snapshot.evidence_requests[0].claim_key == "application_owner"
    assert "candidates" not in snapshot.unavailable_resources
    assert "evidence" not in snapshot.unavailable_resources
    assert gate.allowed is False
    assert {blocker.code for blocker in gate.blockers} == {
        "application_owner_evidence_required"
    }


def test_acceptance_rejects_stale_signal_digest(committed_scope):
    """Catches acceptance persisting facts that changed after discovery."""
    scope = committed_scope
    discovered = next(
        item for item in _discover(scope) if item.application_id == scope.application_id
    )
    with Session(db.engine) as session, session.begin():
        application = session.scalar(
            select(ApplicationComponent).where(
                ApplicationComponent.organization_id == scope.organization_id,
                ApplicationComponent.id == scope.application_id,
            )
        )
        application.total_cost_of_ownership = 130000.0

    with pytest.raises(CommandConflict, match="candidate_signals_stale"):
        RationalisationDiscoveryService.accept_candidate(
            actor=scope.actor,
            workstream_id=scope.workstream_id,
            application_id=scope.application_id,
            signal_digests=discovered.signal_digests,
            inclusion_reason="Signals must still match",
            command_key="stale-signals",
        )

    with Session(db.engine) as session:
        assert session.scalar(
            select(func.count())
            .select_from(TransformationCandidate)
            .where(TransformationCandidate.organization_id == scope.organization_id)
        ) == 0


def test_owner_request_falls_back_to_workstream_architect_without_configuration(
    app, committed_scope
):
    """Catches arbitrary portfolio users being guessed when no steward is configured."""
    scope = committed_scope
    app.config.pop("TRANSFORMATION_PORTFOLIO_STEWARD_ID", None)
    discovered = next(
        item
        for item in _discover(scope)
        if item.application_id == scope.unknown_application_id
    )

    result = RationalisationDiscoveryService.accept_candidate(
        actor=scope.actor,
        workstream_id=scope.workstream_id,
        application_id=scope.unknown_application_id,
        signal_digests=discovered.signal_digests,
        inclusion_reason="Missing source facts require governed follow-up",
        command_key="architect-owner-request",
    )

    with Session(db.engine) as session:
        request = session.scalar(
            select(EvidenceRequest).where(
                EvidenceRequest.organization_id == scope.organization_id,
                EvidenceRequest.id == result.object_ids["evidence_request_id"],
            )
        )
    assert request.assigned_to_id == scope.actor_id


def test_owner_request_rejects_configured_steward_from_another_tenant(
    app, db_session, make_org
):
    scope = _seed_scope(db_session, suffix=uuid.uuid4().hex[:10])
    foreign_org = make_org("foreign-steward")
    foreign_steward = User(
        email=f"foreign-steward-{uuid.uuid4().hex[:10]}@example.test",
        organization_id=foreign_org.id,
        confirmed=True,
        enterprise_role="portfolio_manager",
    )
    db_session.add(foreign_steward)
    db_session.flush()
    workstream = db_session.scalar(
        select(ProgrammeWorkstream).where(
            ProgrammeWorkstream.organization_id == scope.organization_id,
            ProgrammeWorkstream.id == scope.workstream_id,
        )
    )
    previous_steward = app.config.get("TRANSFORMATION_PORTFOLIO_STEWARD_ID")
    app.config["TRANSFORMATION_PORTFOLIO_STEWARD_ID"] = foreign_steward.id
    try:
        assignee_id = RationalisationDiscoveryService._owner_request_assignee(
            db_session, scope.actor, workstream
        )
    finally:
        if previous_steward is None:
            app.config.pop("TRANSFORMATION_PORTFOLIO_STEWARD_ID", None)
        else:
            app.config["TRANSFORMATION_PORTFOLIO_STEWARD_ID"] = previous_steward

    assert assignee_id == scope.actor_id


def test_owner_request_falls_back_when_configured_steward_is_not_active(
    app, committed_scope
):
    scope = committed_scope
    with Session(db.engine) as session, session.begin():
        steward = session.scalar(
            select(User).where(
                User.organization_id == scope.organization_id,
                User.id == scope.steward_id,
            )
        )
        steward.confirmed = False
    app.config["TRANSFORMATION_PORTFOLIO_STEWARD_ID"] = scope.steward_id
    discovered = next(
        item
        for item in _discover(scope)
        if item.application_id == scope.unknown_application_id
    )

    result = RationalisationDiscoveryService.accept_candidate(
        actor=scope.actor,
        workstream_id=scope.workstream_id,
        application_id=scope.unknown_application_id,
        signal_digests=discovered.signal_digests,
        inclusion_reason="Inactive steward must not own evidence",
        command_key="inactive-steward-fallback",
    )

    with Session(db.engine) as session:
        request = session.scalar(
            select(EvidenceRequest).where(
                EvidenceRequest.organization_id == scope.organization_id,
                EvidenceRequest.id == result.object_ids["evidence_request_id"],
            )
        )
    assert request.assigned_to_id == scope.actor_id


@pytest.mark.parametrize(
    ("configured_value", "expected"),
    [("42", "42"), ("", "None"), ("not-an-id", "None"), ("0", "None")],
)
def test_portfolio_steward_id_is_loaded_and_validated_from_environment(
    configured_value, expected
):
    """Exercises the deployment config path rather than mutating Flask config."""
    environment = os.environ.copy()
    environment.update(
        {
            "TRANSFORMATION_PORTFOLIO_STEWARD_ID": configured_value,
            "SECRET_KEY": "test-secret",
            "ADMIN_PASSWORD": "test-password",
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from config import Config; "
                "print(repr(Config.TRANSFORMATION_PORTFOLIO_STEWARD_ID))"
            ),
        ],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().splitlines()[-1] == expected


def test_acceptance_replay_reauthorises_from_persisted_role(committed_scope):
    """Catches immutable command replay bypassing current candidate authority."""
    scope = committed_scope
    discovered = next(
        item for item in _discover(scope) if item.application_id == scope.application_id
    )
    RationalisationDiscoveryService.accept_candidate(
        actor=scope.actor,
        workstream_id=scope.workstream_id,
        application_id=scope.application_id,
        signal_digests=discovered.signal_digests,
        overlap_disposition=_justified_distinct(scope),
        inclusion_reason="Authorised first call",
        command_key="reauthorise-replay",
    )
    with Session(db.engine) as session, session.begin():
        architect = session.scalar(
            select(User).where(
                User.organization_id == scope.organization_id,
                User.id == scope.actor_id,
            )
        )
        architect.enterprise_role = "application_manager"

    with pytest.raises(NotAuthorised, match="candidate_acceptance_not_authorised"):
        RationalisationDiscoveryService.accept_candidate(
            actor=scope.actor,
            workstream_id=scope.workstream_id,
            application_id=scope.application_id,
            signal_digests=discovered.signal_digests,
            overlap_disposition=_justified_distinct(scope),
            inclusion_reason="Authorised first call",
            command_key="reauthorise-replay",
        )


def test_locked_acceptance_rechecks_role_revoked_after_command_claim(committed_scope):
    """Catches authority revoked between receipt claim and the locked domain handler."""
    scope = committed_scope
    discovered = next(
        item for item in _discover(scope) if item.application_id == scope.application_id
    )
    request = {
        "workstream_id": scope.workstream_id,
        "application_id": scope.application_id,
        "signal_digests": tuple(sorted(discovered.signal_digests)),
        "inclusion_reason": "Authority must survive until persistence",
        "overlap_disposition": (
            RationalisationDiscoveryService._normalise_overlap_disposition(
                _justified_distinct(scope)
            )
        ),
    }
    natural_key = (
        f"candidate:{scope.workstream_id}:application:{scope.application_id}"
    )
    claim = CommandService.claim_or_reconcile(
        actor=scope.actor,
        operation="candidate.accept",
        idempotency_key="revoked-after-claim",
        request_digest=CommandService.request_digest(request),
        natural_key=natural_key,
        authorizer=RationalisationDiscoveryService.authorise_candidate_acceptance(
            scope.workstream_id, scope.application_id
        ),
    )
    with Session(db.engine) as session, session.begin():
        architect = session.scalar(
            select(User).where(
                User.organization_id == scope.organization_id,
                User.id == scope.actor_id,
            )
        )
        architect.enterprise_role = "application_manager"

    with pytest.raises(NotAuthorised, match="candidate_acceptance_not_authorised"):
        CommandService._execute_claim(
            actor=scope.actor,
            operation="candidate.accept",
            claim=claim,
            authorizer=None,
            handler=lambda session, fenced_claim: (
                RationalisationDiscoveryService._accept_recomputed_candidate(
                    session, scope.actor, request, fenced_claim
                )
            ),
        )

    with Session(db.engine) as session:
        assert session.scalar(
            select(func.count())
            .select_from(TransformationCandidate)
            .where(TransformationCandidate.organization_id == scope.organization_id)
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(CandidateSignal)
            .where(CandidateSignal.organization_id == scope.organization_id)
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(EvidenceRequest)
            .where(EvidenceRequest.organization_id == scope.organization_id)
        ) == 0


@pytest.mark.parametrize("authority_source", ["enterprise_role", "programme_role"])
def test_candidate_commit_serializes_with_concurrent_authority_revocation(
    app, committed_scope, authority_source
):
    """Catches actor/assignment revocation committing after auth but before candidate."""
    scope = committed_scope
    assignment_id = None
    with Session(db.engine) as session, session.begin():
        actor = session.scalar(
            select(User).where(
                User.organization_id == scope.organization_id,
                User.id == scope.actor_id,
            )
        )
        if authority_source == "programme_role":
            actor.enterprise_role = "application_manager"
            programme_id = session.scalar(
                select(ProgrammeWorkstream.programme_id).where(
                    ProgrammeWorkstream.organization_id == scope.organization_id,
                    ProgrammeWorkstream.id == scope.workstream_id,
                )
            )
            assignment = ProgrammeRoleAssignment(
                organization_id=scope.organization_id,
                programme_id=programme_id,
                workstream_id=None,
                user_id=scope.actor_id,
                role="programme_owner",
                effective_from=date.today() - timedelta(days=1),
                assigned_by_id=scope.actor_id,
            )
            session.add(assignment)
            session.flush()
            assignment_id = assignment.id

    discovered = next(
        item for item in _discover(scope) if item.application_id == scope.application_id
    )
    request = {
        "workstream_id": scope.workstream_id,
        "application_id": scope.application_id,
        "signal_digests": tuple(sorted(discovered.signal_digests)),
        "inclusion_reason": "Authority and mutation must serialize",
        "overlap_disposition": (
            RationalisationDiscoveryService._normalise_overlap_disposition(
                _justified_distinct(scope)
            )
        ),
    }
    claim = CommandService.claim_or_reconcile(
        actor=scope.actor,
        operation="candidate.accept",
        idempotency_key=f"serialized-revocation-{authority_source}",
        request_digest=CommandService.request_digest(request),
        natural_key=(
            f"candidate:{scope.workstream_id}:application:{scope.application_id}"
        ),
        authorizer=RationalisationDiscoveryService.authorise_candidate_acceptance(
            scope.workstream_id, scope.application_id
        ),
    )

    engine = db.engine
    authority_read = threading.Event()
    release_candidate = threading.Event()
    revocation_started = threading.Event()
    revocation_pid_ready = threading.Event()
    candidate_results = []
    candidate_errors = []
    revocation_errors = []
    revocation_pid = []
    candidate_thread_name = f"candidate-{authority_source}"

    def pause_after_authority_rows(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        if (
            threading.current_thread().name == candidate_thread_name
            and statement.lstrip().upper().startswith("SELECT")
            and "programme_role_assignments" in statement.lower()
        ):
            authority_read.set()
            if not release_candidate.wait(timeout=10):
                raise TimeoutError("candidate authorization pause was not released")

    def accept_candidate():
        with app.app_context():
            try:
                candidate_results.append(
                    CommandService._execute_claim(
                        actor=scope.actor,
                        operation="candidate.accept",
                        claim=claim,
                        authorizer=None,
                        handler=lambda session, fenced_claim: (
                            RationalisationDiscoveryService._accept_recomputed_candidate(
                                session, scope.actor, request, fenced_claim
                            )
                        ),
                    )
                )
            except Exception as error:  # asserted after both workers finish
                candidate_errors.append(error)
            finally:
                db.session.remove()

    def revoke_authority():
        with app.app_context():
            try:
                with Session(engine) as session, session.begin():
                    revocation_pid.append(
                        session.scalar(text("SELECT pg_backend_pid()"))
                    )
                    revocation_pid_ready.set()
                    revocation_started.set()
                    if authority_source == "programme_role":
                        assignment = session.scalar(
                            select(ProgrammeRoleAssignment)
                            .where(
                                ProgrammeRoleAssignment.organization_id
                                == scope.organization_id,
                                ProgrammeRoleAssignment.id == assignment_id,
                            )
                            .with_for_update()
                        )
                        assignment.effective_to = date.today() - timedelta(days=1)
                    else:
                        actor = session.scalar(
                            select(User)
                            .where(
                                User.organization_id == scope.organization_id,
                                User.id == scope.actor_id,
                            )
                            .with_for_update()
                        )
                        actor.enterprise_role = "application_manager"
            except Exception as error:  # asserted after both workers finish
                revocation_errors.append(error)
            finally:
                db.session.remove()

    event.listen(engine, "after_cursor_execute", pause_after_authority_rows)
    candidate_thread = threading.Thread(
        target=accept_candidate, name=candidate_thread_name, daemon=True
    )
    revocation_thread = threading.Thread(
        target=revoke_authority,
        name=f"revocation-{authority_source}",
        daemon=True,
    )
    revocation_waited_on_lock = False
    try:
        candidate_thread.start()
        assert authority_read.wait(timeout=10)
        revocation_thread.start()
        assert revocation_started.wait(timeout=5)
        assert revocation_pid_ready.wait(timeout=5)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and revocation_thread.is_alive():
            with engine.connect() as connection:
                revocation_waited_on_lock = connection.scalar(
                    text(
                        "SELECT wait_event_type = 'Lock' FROM pg_stat_activity "
                        "WHERE pid = :pid"
                    ),
                    {"pid": revocation_pid[0]},
                ) is True
            if revocation_waited_on_lock:
                break
            time.sleep(0.01)
    finally:
        release_candidate.set()
        candidate_thread.join(timeout=10)
        revocation_thread.join(timeout=10)
        event.remove(engine, "after_cursor_execute", pause_after_authority_rows)

    assert revocation_waited_on_lock is True
    assert candidate_thread.is_alive() is False
    assert revocation_thread.is_alive() is False
    assert candidate_errors == []
    assert revocation_errors == []
    assert len(candidate_results) == 1
    with Session(engine) as session:
        assert session.scalar(
            select(func.count())
            .select_from(TransformationCandidate)
            .where(
                TransformationCandidate.organization_id == scope.organization_id,
                TransformationCandidate.workstream_id == scope.workstream_id,
                TransformationCandidate.subject_id == scope.application_id,
            )
        ) == 1
        if authority_source == "programme_role":
            assert session.get(ProgrammeRoleAssignment, assignment_id).effective_to < date.today()
        else:
            assert session.get(User, scope.actor_id).enterprise_role == "application_manager"


def test_candidate_signal_is_database_immutable(committed_scope):
    """Catches accepted signal citations being edited after their command commits."""
    scope = committed_scope
    discovered = next(
        item for item in _discover(scope) if item.application_id == scope.application_id
    )
    result = RationalisationDiscoveryService.accept_candidate(
        actor=scope.actor,
        workstream_id=scope.workstream_id,
        application_id=scope.application_id,
        signal_digests=discovered.signal_digests,
        overlap_disposition=_justified_distinct(scope),
        inclusion_reason="Freeze the discovery basis",
        command_key="immutable-signal",
    )
    signal_id = next(
        value for key, value in result.object_ids.items() if key.startswith("signal_")
    )

    with Session(db.engine) as session:
        signal = session.scalar(
            select(CandidateSignal).where(
                CandidateSignal.organization_id == scope.organization_id,
                CandidateSignal.id == signal_id,
            )
        )
        signal.payload_json = {"observed_values": {"forged": True}}
        with pytest.raises(Exception, match="append-only"):
            session.commit()

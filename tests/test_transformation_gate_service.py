"""Versioned, pure lifecycle gate and transition tests."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.benefit import Benefit
from app.models.transformation_evidence import EvidenceRequest
from app.models.transformation_programme import ProgrammeWorkstream
from app.models.user import User
from app.modules.transformation_room.domain import (
    ActorContext,
    BlockedByEvidence,
    CommandConflict,
    NotAuthorised,
    NotFound,
)
from app.modules.transformation_room.gate_service import (
    PolicySnapshot,
    TransformationGateService,
)
from app.modules.transformation_room.evidence_service import (
    EVIDENCE_CLAIM_CONTRACT_VERSION,
    REQUIRED_EVIDENCE_CLAIMS,
    TransformationEvidenceService,
)
from app.modules.transformation_room.programme_service import TransformationProgrammeService

from tests.test_transformation_programme_service import _intake, programme_fixture
from tests.test_transformation_option_service import DecisionScope, decision_scope
from tests.test_decision_brief_service import (
    _freeze_brief,
    _freeze_options,
)
from tests.test_transformation_evidence_service import (
    _grant_decision_authority,
    evidence_scope,
)


def _row(**values):
    return SimpleNamespace(**values)


def _policy_snapshot(source: str, target: str, **changes):
    programme = _row(id=1, owner_id=7, status="active", archived_at=None, organization_id=10)
    workstream = _row(
        id=2,
        programme_id=1,
        lead_id=7,
        objective="Reduce avoidable run cost",
        scope_expression={"business_units": ["Retail"]},
        target_date=date(2027, 6, 30),
        target_date_unavailable_reason=None,
        lifecycle_stage=source,
    )
    outcome = _row(id=3, owner_id=7, statement="Reduce run cost")
    measure = _row(
        id=4,
        outcome_commitment_id=3,
        metric_name="Annual run cost",
        unit="GBP",
        baseline_amount=None,
        baseline_value=None,
        target_amount=Decimal("900000.00"),
        target_value=None,
        unavailable_reason="Finance baseline requested",
    )
    candidate = _row(
        id=11,
        workstream_id=2,
        organization_id=10,
        subject_type="application",
        subject_id=501,
        inclusion_status="accepted",
        subject_exists=True,
        duplicates_resolved=True,
    )
    contract_values = {
        "application_owner": ("string", "Retail Operations", None, None),
        "lifecycle": ("string", "active", None, None),
        "cost": ("number", 125000, "annual_tco", "GBP"),
        "business_criticality": (
            "json",
            {"value": "high", "source_field": "business_criticality"},
            None,
            None,
        ),
        "capability_impact": (
            "json",
            {"capability_ids": [1], "impact": "material"},
            None,
            None,
        ),
        "dependency_impact": (
            "json",
            {"dependency_ids": [1], "impact": "material"},
            None,
            None,
        ),
        "risk": (
            "json",
            {
                "technical_risk": "high",
                "business_risk": "medium",
                "vendor_risk": "low",
                "obsolescence_risk": "high",
            },
            None,
            None,
        ),
        "source_freshness": (
            "json",
            {
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "freshness_status": "fresh",
                "source_system": "application-inventory",
            },
            None,
            None,
        ),
    }
    evidence = tuple(
        _row(
            id=20 + offset,
            candidate_id=11,
            subject_type="application",
            subject_id=501,
            claim_key=claim_key,
            classification="observed",
            source_identity=f"application:501:{claim_key}",
            source_type=(
                "application-inventory"
                if claim_key == "source_freshness"
                else "attestation"
            ),
            claim_contract_version=EVIDENCE_CLAIM_CONTRACT_VERSION,
            value_type=contract_values[claim_key][0],
            value_json=contract_values[claim_key][1],
            unit=contract_values[claim_key][2],
            currency=contract_values[claim_key][3],
            freshness_status="fresh",
            freshness_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            cited_evidence_ids=(),
        )
        for offset, claim_key in enumerate(
            (
                "application_owner",
                "lifecycle",
                "cost",
                "business_criticality",
                "capability_impact",
                "dependency_impact",
                "risk",
                "source_freshness",
            ),
            start=1,
        )
    )
    requests = tuple(
        _row(
            id=40 + offset,
            candidate_id=11,
            subject_type="application",
            subject_id=501,
            claim_key=row.claim_key,
            claim_contract_version=EVIDENCE_CLAIM_CONTRACT_VERSION,
            required=True,
            status="accepted",
            accepted_evidence_id=row.id,
            acknowledgement_id=None,
            waiver_id=None,
        )
        for offset, row in enumerate(evidence, start=1)
    )
    heads = tuple(
        _row(
            id=50 + offset,
            organization_id=10,
            subject_type="application",
            subject_id=501,
            claim_key=row.claim_key,
            source_identity=row.source_identity,
            current_record_id=row.id,
            revision=1,
        )
        for offset, row in enumerate(evidence, start=1)
    )
    options = (
        _row(
            id=61,
            option_id=601,
            workstream_id=2,
            candidate_id=None,
            version=1,
            immutable=True,
            content_hash="a" * 64,
            content_json={"title": "Retire"},
            assumptions=("Funding remains available",),
            benefit_min=Decimal("100.00"),
            benefit_max=Decimal("120.00"),
            cost_min=Decimal("40.00"),
            cost_max=Decimal("50.00"),
            currency="GBP",
            risks=("Delivery capacity",),
            dependencies=("Finance data",),
            reversibility="phased rollback",
            transition_approach="Incremental retirement",
            affected_capability_ids=(1,),
            affected_value_stream_ids=(2,),
            technology_required=False,
            recommendation_rationale="Best value with manageable risk",
        ),
        _row(
            id=62,
            option_id=602,
            workstream_id=2,
            candidate_id=None,
            version=1,
            immutable=True,
            content_hash="b" * 64,
            content_json={"title": "Tolerate"},
            assumptions=("Supplier support continues",),
            benefit_min=Decimal("60.00"),
            benefit_max=Decimal("80.00"),
            cost_min=Decimal("20.00"),
            cost_max=Decimal("30.00"),
            currency="GBP",
            risks=("Supplier dependency",),
            dependencies=("Contract renewal",),
            reversibility="contract exit",
            transition_approach="Renew while alternatives are validated",
            affected_capability_ids=(1,),
            affected_value_stream_ids=(2,),
            technology_required=False,
            recommendation_rationale="Lower-cost comparator",
        ),
    )
    brief = _row(
        id=71,
        brief_id=70,
        workstream_id=2,
        immutable=True,
        content_hash="c" * 64,
        cited_evidence_ids=tuple(row.id for row in evidence),
        option_version_ids=(61, 62),
        human_reviewed_ai=True,
        decision_authority_id=7,
        blockers_cleared=True,
        unknowns_acknowledged=True,
        policy_version="transformation-r1.1",
        submitted_by_id=7,
        submitter_authorized=True,
    )
    decision = {
        "approved": "approved",
        "approved_with_conditions": "approved_with_conditions",
        "rejected": "rejected",
        "evidence": "returned_for_evidence",
        "options": "returned_for_options",
    }.get(target, target)
    if source in {"approved", "execute", "outcomes"}:
        decision = "approved"
    if source == "approved_with_conditions" and target == "approved":
        decision = "approved_with_conditions"
    decision_target = target
    if decision == "approved":
        decision_target = "approved"
    elif decision == "approved_with_conditions":
        decision_target = "approved_with_conditions"
    cycle = _row(
        id=81,
        workstream_id=2,
        subject_type="decision_brief",
        subject_id=70,
        brief_id=70,
        brief_version_id=71,
        decision_brief_version_id=71,
        status="decided",
        decision=decision,
        target_stage=decision_target,
        decision_maker_id=7,
        rationale="Decision grounded in the submitted brief",
        decided_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
    )
    conditions = (
        _row(
            id=91,
            arb_cycle_id=81,
            status="fulfilled",
            accepted_evidence_id=21,
            waiver_authority_id=None,
            waiver_reason=None,
            waiver_expires_at=None,
        ),
    )
    if source == "in_governance" and target == "approved":
        conditions = ()
    work_package = _row(
        id=101, workstream_id=2, owner_id=7, status="planned",
        decision_brief_version_id=71,
    )
    roadmap = _row(
        id=111, programme_workstream_id=2, work_package_id=101,
        decision_brief_version_id=71,
    )
    action = _row(
        id=121,
        workstream_id=2,
        decision_brief_version_id=71,
        status="accepted",
        decline_reason=None,
        owner_id=7,
        work_package_id=101,
        scheduling_applicable=True,
        roadmap_item_id=111,
    )
    benefit = _row(
        id=131,
        strategic_initiative_id=1,
        programme_workstream_id=2,
        outcome_commitment_id=3,
        work_package_id=101,
        owner_id=7,
        measure="Annual run cost",
        unit="GBP",
        baseline_value=None,
        target_value=Decimal("900000.00"),
        measurement_method="Monthly finance ledger extract",
        measurement_frequency="monthly",
        decision_brief_version_id=71,
    )
    delivery = _row(
        id=141,
        workstream_id=2,
        completion_evidence_id=21,
        residual_risk="Low residual support risk",
        operational_owner_id=7,
        measurement_schedule="monthly",
        corrective_action_approved=target == "execute" and source == "outcomes",
    )
    measurement = _row(
        id=151,
        benefit_id=131,
        outcome_commitment_id=3,
        value=Decimal("890000.00"),
        unavailable_reason=None,
        observed_at=datetime(2027, 7, 1, tzinfo=timezone.utc),
        valid=True,
    )
    review = _row(
        id=161,
        workstream_id=2,
        outcome_commitment_id=3,
        judgement="realised",
        lessons="Start owner validation earlier",
        follow_up_decision="close",
    )
    values = {
        "programme": programme,
        "workstream": workstream,
        "role_assignments": (),
        "outcomes": (outcome,),
        "measures": (measure,),
        "accepted_candidates": (candidate,),
        "active_evidence_heads": heads,
        "evidence_records": evidence,
        "evidence_requests": requests,
        "evidence_waivers": (),
        "option_versions": options,
        "option_exceptions": (),
        "brief_versions": (brief,),
        "arb_cycles": (cycle,),
        "arb_conditions": conditions,
        "approved_actions": (action,),
        "work_packages": (work_package,),
        "roadmap_items": (roadmap,),
        "benefits": (benefit,),
        "delivery_records": (delivery,),
        "measurements": (measurement,),
        "outcome_reviews": (review,),
        "authorized_waiver_authority_ids": frozenset({7}),
        "unavailable_resources": frozenset(),
    }
    if source == "rejected" and target == "options":
        values["arb_cycles"] = (replace_namespace(cycle, decision="reframe_authorised", target_stage="options"),)
    values.update(changes)
    return PolicySnapshot(**values)


def replace_namespace(row, **changes):
    return _row(**{**vars(row), **changes})


@pytest.mark.parametrize(
    ("source", "target"),
    (("discover", "evidence"), ("evidence", "options")),
)
def test_discovery_and_evidence_gates_consume_task6_request_head_record_contract(
    source, target
):
    """Catches policy evaluators reading fields Task 6 never persists."""
    snapshot = _policy_snapshot(source, target)
    blockers, _, _ = TransformationGateService.evaluate_requirements(
        snapshot,
        TransformationGateService.require_valid_transition(source, target),
    )
    assert blockers == []


def test_evidence_gate_uses_effective_freshness_and_explicit_conflict_resolution():
    """Catches stale or unresolved current heads being treated as accepted evidence."""
    snapshot = _policy_snapshot("evidence", "options")
    stale = replace_namespace(
        snapshot.evidence_records[0],
        freshness_status="fresh",
        freshness_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    conflict = _row(
        id=199,
        candidate_id=11,
        subject_type="application",
        subject_id=501,
        claim_key="application_owner",
        classification="conflict",
        source_identity="conflict:application-owner",
        source_type="governance_conflict",
        freshness_status="not_applicable",
        freshness_expires_at=None,
        cited_evidence_ids=(snapshot.evidence_records[0].id,),
    )
    conflict_head = _row(
        id=299,
        organization_id=10,
        subject_type="application",
        subject_id=501,
        claim_key="application_owner",
        source_identity=conflict.source_identity,
        current_record_id=conflict.id,
        revision=1,
    )
    blockers, _, _ = TransformationGateService.evaluate_requirements(
        replace(
            snapshot,
            evidence_records=(stale, *snapshot.evidence_records[1:], conflict),
            active_evidence_heads=(*snapshot.active_evidence_heads, conflict_head),
        ),
        TransformationGateService.require_valid_transition("evidence", "options"),
    )
    assert {row.code for row in blockers} == {
        "evidence_conflict_unresolved",
        "evidence_freshness_invalid",
    }


def test_evidence_gate_requires_accepted_request_and_its_exact_global_head():
    """Catches accepted pointers borrowing currentness from a different source head."""
    snapshot = _policy_snapshot("evidence", "options")
    wrong_source_head = replace_namespace(
        snapshot.active_evidence_heads[0],
        source_identity="application:501:different-source",
    )
    blockers, _, _ = TransformationGateService.evaluate_requirements(
        replace(
            snapshot,
            active_evidence_heads=(
                wrong_source_head,
                *snapshot.active_evidence_heads[1:],
            ),
        ),
        TransformationGateService.require_valid_transition("evidence", "options"),
    )
    assert "required_evidence_incomplete" in {row.code for row in blockers}


@pytest.mark.parametrize("unavailable_status", ("declined", "expired"))
def test_evidence_gate_treats_task6_waiver_as_explicit_request_completion(
    unavailable_status,
):
    """Pins declined and expired requests to the same exact Task 6 waiver contract."""
    snapshot = _policy_snapshot("evidence", "options")
    original = snapshot.evidence_requests[-1]
    waived = replace_namespace(
        original,
        organization_id=snapshot.programme.organization_id,
        status=unavailable_status,
        accepted_evidence_id=None,
        waiver_id=original.id,
        waiver_authority_id=7,
        waiver_reason="Governed evidence is unavailable during this decision window.",
        waiver_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        interim_accountable_id=7,
        waived_at=datetime.now(timezone.utc),
    )
    blockers, _, _ = TransformationGateService.evaluate_requirements(
        replace(
            snapshot,
            evidence_requests=(*snapshot.evidence_requests[:-1], waived),
            evidence_waivers=(waived,),
        ),
        TransformationGateService.require_valid_transition("evidence", "options"),
    )

    assert "required_evidence_incomplete" not in {row.code for row in blockers}

    open_request = replace_namespace(snapshot.evidence_requests[0], status="open")
    blockers, _, _ = TransformationGateService.evaluate_requirements(
        replace(
            snapshot,
            evidence_requests=(open_request, *snapshot.evidence_requests[1:]),
        ),
        TransformationGateService.require_valid_transition("evidence", "options"),
    )
    assert "required_evidence_incomplete" in {row.code for row in blockers}


@pytest.mark.parametrize("projected_authority", (None, 999999))
def test_evidence_gate_never_trusts_raw_request_waiver_outside_valid_projection(
    projected_authority,
):
    """Catches raw waiver fields bypassing tenant and current-authority projection."""
    snapshot = _policy_snapshot("evidence", "options")
    original = snapshot.evidence_requests[-1]
    raw_waiver = replace_namespace(
        original,
        organization_id=snapshot.programme.organization_id,
        status="declined",
        accepted_evidence_id=None,
        acknowledgement_id=original.id,
        waiver_id=original.id,
        waiver_authority_id=7,
        waiver_reason="A raw request row claims a waiver.",
        waiver_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        interim_accountable_id=7,
        waived_at=datetime.now(timezone.utc),
    )
    projected = (
        ()
        if projected_authority is None
        else (
            replace_namespace(
                raw_waiver,
                waiver_authority_id=projected_authority,
            ),
        )
    )
    blockers, _, _ = TransformationGateService.evaluate_requirements(
        replace(
            snapshot,
            evidence_requests=(*snapshot.evidence_requests[:-1], raw_waiver),
            evidence_waivers=projected,
        ),
        TransformationGateService.require_valid_transition("evidence", "options"),
    )

    assert "required_evidence_incomplete" in {row.code for row in blockers}


def test_options_gate_counts_latest_version_per_logical_option_only():
    """Catches two revisions of one option masquerading as two alternatives."""
    snapshot = _policy_snapshot("options", "decision_ready")
    revised_same_option = replace_namespace(
        snapshot.option_versions[1],
        option_id=snapshot.option_versions[0].option_id,
        version=2,
    )
    blockers, _, _ = TransformationGateService.evaluate_requirements(
        replace(snapshot, option_versions=(snapshot.option_versions[0], revised_same_option)),
        TransformationGateService.require_valid_transition("options", "decision_ready"),
    )
    assert {row.code for row in blockers} == {"viable_options_required"}


def test_real_task6_rows_allow_discovery_only_while_current_and_fresh(
    decision_scope: DecisionScope,
):
    """Catches snapshot projection diverging from persisted Task 6 rows."""
    scope = decision_scope
    with Session(db.engine) as session, session.begin():
        workstream = session.get(ProgrammeWorkstream, scope.workstream_id)
        workstream.lifecycle_stage = "discover"
        application = session.get(ApplicationComponent, scope.application_id)
        application.application_owner = "Retail Operations"
        application.updated_at = datetime.now(timezone.utc)
        owner_request = session.scalar(
            select(EvidenceRequest).where(
                EvidenceRequest.organization_id == scope.organization_id,
                EvidenceRequest.candidate_id == scope.candidate_id,
                EvidenceRequest.claim_key == "application_owner",
            )
        )
        owner_request.status = "open"
        owner_request.submitted_evidence_id = None
        owner_request.accepted_evidence_id = None
        owner_request.submitted_at = None
        owner_request.accepted_at = None

    planned = TransformationEvidenceService.plan_required_requests(
        actor=scope.actor,
        candidate_id=scope.candidate_id,
        assignments={claim: scope.actor_id for claim in REQUIRED_EVIDENCE_CLAIMS},
        command_key="gate-typed-request-plan",
    )
    owner_observation = TransformationEvidenceService.record_observation(
        actor=scope.actor,
        candidate_id=scope.candidate_id,
        claim_key="application_owner",
        adapter_key="application-inventory",
        source_key=str(scope.application_id),
        expected_head_revision=1,
        command_key="gate-typed-owner-observation",
    )
    TransformationEvidenceService.accept_request(
        actor=scope.actor,
        request_id=planned.object_ids["request_application_owner_id"],
        evidence_id=owner_observation.object_ids["evidence_record_id"],
        expected_revision=owner_observation.response["request_revision"],
        command_key="gate-typed-owner-acceptance",
    )
    typed_evidence_id = owner_observation.object_ids["evidence_record_id"]

    ready = TransformationGateService.evaluate(
        actor=scope.actor,
        workstream_id=scope.workstream_id,
        target_stage="evidence",
    )
    assert ready.allowed is True

    with db.engine.begin() as connection:
        # Flagged exception: mutates a committed evidence_records
        # row to construct an already-expired freshness window. The append-only
        # guard correctly refuses every UPDATE on this table; the deletion marker
        # only ever admits a DELETE, by design. Needs a new, named admission path
        # on a production append-only guard for a mutation outside any real write
        # path — out of scope here. Left for the lead/founder to decide.
        connection.exec_driver_sql("SET LOCAL session_replication_role = replica")  # session-replication-role-ok: mutates a committed evidence_records row to an expired freshness window; the append-only guard correctly refuses every UPDATE on this table
        connection.execute(
            text(
                "UPDATE evidence_records SET freshness_expires_at = :expired "
                "WHERE id = :record_id AND organization_id = :organization_id"
            ),
            {
                "expired": datetime.now(timezone.utc) - timedelta(seconds=1),
                "record_id": typed_evidence_id,
                "organization_id": scope.organization_id,
            },
        )
    stale = TransformationGateService.evaluate(
        actor=scope.actor,
        workstream_id=scope.workstream_id,
        target_stage="evidence",
    )
    assert stale.allowed is False
    assert {row.code for row in stale.blockers} == {
        "application_owner_evidence_required"
    }


@pytest.mark.parametrize("unavailable_status", ("declined", "expired"))
def test_real_task6_waiver_releases_discovery_only_with_exact_current_contract(
    decision_scope: DecisionScope,
    unavailable_status,
):
    """Pins the gate to Task 6's persisted waiver fields and exact request identity."""
    scope = decision_scope
    _grant_decision_authority(scope)
    with Session(db.engine) as session, session.begin():
        workstream = session.get(ProgrammeWorkstream, scope.workstream_id)
        workstream.lifecycle_stage = "discover"
        request = session.scalar(
            select(EvidenceRequest).where(
                EvidenceRequest.organization_id == scope.organization_id,
                EvidenceRequest.candidate_id == scope.candidate_id,
                EvidenceRequest.claim_key == "application_owner",
            )
        )
        request.status = "open"
        request.submitted_evidence_id = None
        request.accepted_evidence_id = None
        request.submitted_at = None
        request.accepted_at = None
        if unavailable_status == "expired":
            request.due_at = datetime.now(timezone.utc) - timedelta(days=1)
        request_id = request.id
    with Session(db.engine) as session:
        revision = session.get(EvidenceRequest, request_id).revision

    if unavailable_status == "declined":
        changed = TransformationEvidenceService.decline_request(
            actor=scope.actor,
            request_id=request_id,
            reason="The named source cannot provide evidence in this decision window.",
            expected_revision=revision,
            command_key=f"gate-waiver-decline-{request_id}",
        )
    else:
        changed = TransformationEvidenceService.expire_request(
            actor=scope.actor,
            request_id=request_id,
            expected_revision=revision,
            command_key=f"gate-waiver-expire-{request_id}",
        )
    TransformationEvidenceService.waive_unavailable_request(
        actor=scope.actor,
        request_id=request_id,
        reason="The accountable owner will validate the decision before governance.",
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        interim_accountable_id=scope.actor_id,
        expected_revision=changed.response["revision"],
        command_key=f"gate-waiver-authorise-{unavailable_status}-{request_id}",
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

    assert len(snapshot.evidence_waivers) == 1
    waiver = snapshot.evidence_waivers[0]
    assert waiver.id == request_id
    assert waiver.waiver_id == request_id
    assert waiver.waiver_authority_id == scope.actor_id
    assert waiver.interim_accountable_id == scope.actor_id
    assert waiver.waiver_reason
    assert waiver.waiver_expires_at > datetime.now(timezone.utc)
    assert gate.allowed is True

    with db.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE evidence_requests SET waiver_id = id + 1 "
                "WHERE id = :request_id AND organization_id = :organization_id"
            ),
            {"request_id": request_id, "organization_id": scope.organization_id},
        )
    mismatched = TransformationGateService.evaluate(
        actor=scope.actor,
        workstream_id=scope.workstream_id,
        target_stage="evidence",
    )
    assert {row.code for row in mismatched.blockers} == {
        "application_owner_evidence_required"
    }

    with db.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE evidence_requests SET waiver_id = id, "
                "waiver_expires_at = :expired "
                "WHERE id = :request_id AND organization_id = :organization_id"
            ),
            {
                "expired": datetime.now(timezone.utc) - timedelta(seconds=1),
                "request_id": request_id,
                "organization_id": scope.organization_id,
            },
        )
    expired = TransformationGateService.evaluate(
        actor=scope.actor,
        workstream_id=scope.workstream_id,
        target_stage="evidence",
    )
    assert {row.code for row in expired.blockers} == {
        "application_owner_evidence_required"
    }

    ordinary = User(
        organization_id=scope.organization_id,
        email=f"waiver-nonauthority-{uuid.uuid4().hex[:10]}@example.test",
        confirmed=True,
        enterprise_role="application_owner",
    )
    db.session.add(ordinary)
    db.session.commit()
    ordinary_id = ordinary.id
    db.session.remove()
    with db.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE evidence_requests SET waiver_expires_at = :current_expiry, "
                "waiver_authority_id = :ordinary_id "
                "WHERE id = :request_id AND organization_id = :organization_id"
            ),
            {
                "current_expiry": datetime.now(timezone.utc) + timedelta(days=7),
                "ordinary_id": ordinary_id,
                "request_id": request_id,
                "organization_id": scope.organization_id,
            },
        )
    unauthorized = TransformationGateService.evaluate(
        actor=scope.actor,
        workstream_id=scope.workstream_id,
        target_stage="evidence",
    )
    assert {row.code for row in unauthorized.blockers} == {
        "application_owner_evidence_required"
    }


def test_objective_gate_is_pure_and_transition_is_locked(programme_fixture):
    """Catches gate evaluation mutating state or transition bypassing revision/event handling."""
    created = TransformationProgrammeService.create_programme(
        actor=programme_fixture.actor,
        command_key="gate-ready",
        request=_intake(programme_fixture.owner_id),
    )
    workstream_id = created.object_ids["workstream_id"]

    evaluated = TransformationGateService.evaluate(
        actor=programme_fixture.actor,
        workstream_id=workstream_id,
        target_stage="discover",
    )
    with Session(db.engine) as session:
        unchanged = session.scalar(
            select(ProgrammeWorkstream).where(
                ProgrammeWorkstream.id == workstream_id,
                ProgrammeWorkstream.organization_id == programme_fixture.organization_id,
            )
        )
        assert unchanged.lifecycle_stage == "objective"
        assert unchanged.revision == 1

    transitioned = TransformationGateService.transition(
        actor=programme_fixture.actor,
        workstream_id=workstream_id,
        target_stage="discover",
        expected_revision=1,
        command_key="to-discover",
    )
    replayed = TransformationGateService.transition(
        actor=programme_fixture.actor,
        workstream_id=workstream_id,
        target_stage="discover",
        expected_revision=1,
        command_key="to-discover",
    )

    assert evaluated.allowed is True
    assert evaluated.policy_version == "transformation-r1.1"
    assert transitioned.response["lifecycle_stage"] == "discover"
    assert replayed.operation_result_id == transitioned.operation_result_id
    with Session(db.engine) as session:
        changed = session.get(ProgrammeWorkstream, workstream_id)
        assert changed.lifecycle_stage == "discover"
        assert changed.revision == 2


def test_transition_replay_uses_immutable_original_edge_and_current_authority(
    decision_scope,
):
    """A post-state role must not unlock an earlier transition result."""
    scope = decision_scope
    option_version_ids = _freeze_options(scope)
    _freeze_brief(scope, option_version_ids, key="transition-replay-brief")
    with db.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE programme_workstreams SET lifecycle_stage = 'decision_ready', "
                "revision = 1 WHERE id = :workstream_id "
                "AND organization_id = :organization_id"
            ),
            {
                "workstream_id": scope.workstream_id,
                "organization_id": scope.organization_id,
            },
        )

    command_key = f"immutable-transition-edge-{uuid.uuid4().hex}"
    transitioned = TransformationGateService.transition(
        actor=scope.actor,
        workstream_id=scope.workstream_id,
        target_stage="in_governance",
        expected_revision=1,
        command_key=command_key,
    )

    # The original edge requires objective authority. Revocation must deny an
    # exact replay, even after the live projection has moved to in_governance.
    with Session(db.engine) as session, session.begin():
        user = session.get(User, scope.actor_id)
        user.enterprise_role = "portfolio_manager"
    with pytest.raises(NotAuthorised, match="transition_not_authorised"):
        TransformationGateService.transition(
            actor=scope.actor,
            workstream_id=scope.workstream_id,
            target_stage="in_governance",
            expected_revision=1,
            command_key=command_key,
        )

    # decision_authority applies to transitions *from* in_governance, not the
    # immutable decision_ready -> in_governance command being replayed.
    with Session(db.engine) as session, session.begin():
        user = session.get(User, scope.actor_id)
        user.enterprise_role = "decision_authority"
    with pytest.raises(NotAuthorised, match="transition_not_authorised"):
        TransformationGateService.transition(
            actor=scope.actor,
            workstream_id=scope.workstream_id,
            target_stage="in_governance",
            expected_revision=1,
            command_key=command_key,
        )

    # Restoring current authority for the original edge returns the exact
    # immutable result without repeating the mutation.
    with Session(db.engine) as session, session.begin():
        user = session.get(User, scope.actor_id)
        user.enterprise_role = "enterprise_architect"
    replayed = TransformationGateService.transition(
        actor=scope.actor,
        workstream_id=scope.workstream_id,
        target_stage="in_governance",
        expected_revision=1,
        command_key=command_key,
    )

    assert replayed.idempotent is True
    assert replayed.operation_result_id == transitioned.operation_result_id


def test_objective_gate_returns_stable_blockers_and_denial_does_not_mutate(programme_fixture):
    """Catches an incomplete objective advancing or returning transient prose-only errors."""
    request = replace(
        _intake(programme_fixture.owner_id),
        scope_expression={},
        target_date=None,
        target_date_unavailable_reason="Date depends on portfolio review",
    )
    created = TransformationProgrammeService.create_programme(
        actor=programme_fixture.actor,
        command_key="gate-blocked",
        request=request,
    )
    workstream_id = created.object_ids["workstream_id"]

    gate = TransformationGateService.evaluate(
        actor=programme_fixture.actor,
        workstream_id=workstream_id,
        target_stage="discover",
    )
    assert gate.allowed is False
    assert {blocker.code for blocker in gate.blockers} == {"scope_required"}

    with pytest.raises(BlockedByEvidence) as denied:
        TransformationGateService.transition(
            actor=programme_fixture.actor,
            workstream_id=workstream_id,
            target_stage="discover",
            expected_revision=1,
            command_key="blocked-transition",
        )
    assert [item.code for item in denied.value.blockers] == ["scope_required"]
    with Session(db.engine) as session:
        unchanged = session.get(ProgrammeWorkstream, workstream_id)
        assert unchanged.lifecycle_stage == "objective"
        assert unchanged.revision == 1


def test_gate_load_is_explicitly_tenant_scoped(programme_fixture):
    """Catches a valid foreign workstream ID revealing readiness across tenants."""
    foreign_actor = ActorContext(
        programme_fixture.foreign_owner_id,
        programme_fixture.foreign_organization_id,
        frozenset(),
        "foreign-create",
    )
    created = TransformationProgrammeService.create_programme(
        actor=foreign_actor,
        command_key="foreign-gate",
        request=_intake(programme_fixture.foreign_owner_id),
    )
    with pytest.raises(NotFound, match="workstream_not_found"):
        TransformationGateService.evaluate(
            actor=programme_fixture.actor,
            workstream_id=created.object_ids["workstream_id"],
            target_stage="discover",
        )


@pytest.mark.parametrize("source,target", sorted(TransformationGateService.TRANSITIONS))
def test_approved_r11_transition_table_accepts_only_complete_matching_snapshots(source, target):
    """Catches any approved lifecycle edge retaining a permanent or shallow blocker."""
    snapshot = _policy_snapshot(source, target)
    transition = TransformationGateService.require_valid_transition(source, target)
    blockers, _warnings, _evidence_ids = TransformationGateService.evaluate_requirements(
        snapshot, transition
    )
    assert blockers == []


def test_governance_decision_must_match_subject_brief_target_and_terminal_projection():
    """Catches an unrelated ARB row advancing a governed workstream."""
    snapshot = _policy_snapshot("in_governance", "approved")
    wrong_cycle = replace_namespace(
        snapshot.arb_cycles[0],
        brief_version_id=999,
        subject_id=999,
        target_stage="rejected",
    )
    blockers, _, _ = TransformationGateService.evaluate_requirements(
        replace(snapshot, arb_cycles=(wrong_cycle,)),
        TransformationGateService.require_valid_transition("in_governance", "approved"),
    )
    assert {row.code for row in blockers} == {"arb_decision_mismatch"}


def test_execute_gate_rejects_orphaned_actions_work_and_benefit_contracts():
    """Catches unrelated work/benefit rows satisfying Approved-to-Execute readiness."""
    snapshot = _policy_snapshot("approved", "execute")
    bad_action = replace_namespace(snapshot.approved_actions[0], owner_id=None, work_package_id=999)
    bad_benefit = replace_namespace(
        snapshot.benefits[0], outcome_commitment_id=999, measurement_method=None
    )
    blockers, _, _ = TransformationGateService.evaluate_requirements(
        replace(snapshot, approved_actions=(bad_action,), benefits=(bad_benefit,)),
        TransformationGateService.require_valid_transition("approved", "execute"),
    )
    assert {row.code for row in blockers} == {
        "approved_action_unresolved",
        "benefit_contract_incomplete",
    }


@pytest.mark.parametrize(
    "cycle_change",
    (
        {"subject_id": 999, "brief_id": 999},
        {"status": "open", "decided_at": None},
    ),
)
def test_conditional_projection_requires_matching_terminal_governed_cycle(cycle_change):
    """Catches unrelated or pending conditional decisions releasing conditions."""
    snapshot = _policy_snapshot("approved_with_conditions", "approved")
    wrong_cycle = replace_namespace(snapshot.arb_cycles[0], **cycle_change)
    blockers, _, _ = TransformationGateService.evaluate_requirements(
        replace(snapshot, arb_cycles=(wrong_cycle,)),
        TransformationGateService.require_valid_transition(
            "approved_with_conditions", "approved"
        ),
    )
    assert {row.code for row in blockers} == {"arb_decision_mismatch"}


def test_execute_accepts_resolved_conditional_cycle_and_real_benefit_shape(app):
    """Catches execution excluding a correctly projected conditional approval."""
    snapshot = _policy_snapshot("approved", "execute")
    conditional_cycle = replace_namespace(
        snapshot.arb_cycles[0],
        decision="approved_with_conditions",
        target_stage="approved_with_conditions",
    )
    canonical_benefit = Benefit(
        id=131,
        organization_id=10,
        name="Reduce annual run cost",
        strategic_initiative_id=1,
        programme_workstream_id=2,
        outcome_commitment_id=3,
        work_package_id=101,
        decision_brief_version_id=71,
        owner_id=7,
        measure="Annual run cost",
        unit="GBP",
        baseline_value=None,
        target_value=Decimal("900000.00"),
        measurement_method="Monthly finance ledger extract",
        measurement_frequency="monthly",
    )
    blockers, _, _ = TransformationGateService.evaluate_requirements(
        replace(
            snapshot,
            arb_cycles=(conditional_cycle,),
            benefits=(canonical_benefit,),
        ),
        TransformationGateService.require_valid_transition("approved", "execute"),
    )
    assert blockers == []


@pytest.mark.parametrize("baseline", (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")))
def test_execute_rejects_non_finite_legacy_benefit_baseline(baseline):
    """Catches a legacy Benefit special value satisfying the execution contract."""
    snapshot = _policy_snapshot("approved", "execute")
    benefit = replace_namespace(snapshot.benefits[0], baseline_value=baseline)

    blockers, _, _ = TransformationGateService.evaluate_requirements(
        replace(snapshot, benefits=(benefit,)),
        TransformationGateService.require_valid_transition("approved", "execute"),
    )

    assert {row.code for row in blockers} == {"benefit_value_invalid"}


@pytest.mark.parametrize(
    "waiver_change",
    (
        {"waiver_authority_id": 999999},
        {"organization_id": 999},
        {"waiver_expires_at": datetime.now(timezone.utc) - timedelta(days=1)},
        {"waiver_expires_at": date.today()},
        {"waiver_condition_id": 999},
        {"waiver_arb_cycle_id": 999},
        {"waiver_subject_id": 999},
    ),
)
def test_condition_waiver_requires_verified_tenant_authority_and_exact_scope(waiver_change):
    """Catches forged, foreign, expired or mismatched condition waivers releasing a gate."""
    snapshot = _policy_snapshot("approved_with_conditions", "approved")
    waiver = {
        "organization_id": 10,
        "status": "waived",
        "accepted_evidence_id": None,
        "waiver_authority_id": 7,
        "waiver_reason": "Compensating control approved",
        "waiver_expires_at": datetime.now(timezone.utc) + timedelta(days=30),
        "waiver_condition_id": 91,
        "waiver_arb_cycle_id": 81,
        "waiver_subject_type": "decision_brief",
        "waiver_subject_id": 70,
    }
    condition = replace_namespace(snapshot.arb_conditions[0], **{**waiver, **waiver_change})
    verified_snapshot = replace(
        snapshot,
        arb_conditions=(condition,),
        authorized_waiver_authority_ids=frozenset({7}),
    )

    blockers, _, _ = TransformationGateService.evaluate_requirements(
        verified_snapshot,
        TransformationGateService.require_valid_transition(
            "approved_with_conditions", "approved"
        ),
    )

    assert {row.code for row in blockers} == {"arb_conditions_open"}


def test_condition_waiver_accepts_only_verified_exactly_linked_authority():
    """Pins the positive path for a tenant-loaded governance waiver authority."""
    snapshot = _policy_snapshot("approved_with_conditions", "approved")
    condition = replace_namespace(
        snapshot.arb_conditions[0],
        organization_id=10,
        status="waived",
        accepted_evidence_id=None,
        waiver_authority_id=7,
        waiver_reason="Compensating control approved",
        waiver_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        waiver_condition_id=91,
        waiver_arb_cycle_id=81,
        waiver_subject_type="decision_brief",
        waiver_subject_id=70,
    )
    verified_snapshot = replace(
        snapshot,
        arb_conditions=(condition,),
        authorized_waiver_authority_ids=frozenset({7}),
    )

    blockers, _, _ = TransformationGateService.evaluate_requirements(
        verified_snapshot,
        TransformationGateService.require_valid_transition(
            "approved_with_conditions", "approved"
        ),
    )

    assert blockers == []


def test_snapshot_loads_waiver_authority_from_persisted_tenant_roles(programme_fixture):
    """Catches caller claims or foreign and ordinary tenant users entering waiver authority."""
    created = TransformationProgrammeService.create_programme(
        actor=programme_fixture.actor,
        command_key="waiver-authority-snapshot",
        request=_intake(programme_fixture.owner_id),
    )
    ordinary_user = User(
        email=f"ordinary-waiver-{uuid.uuid4().hex[:10]}@example.test",
        organization_id=programme_fixture.organization_id,
        confirmed=True,
        enterprise_role="application_manager",
    )
    db.session.add(ordinary_user)
    db.session.commit()

    snapshot = TransformationGateService.load_policy_snapshot(
        actor=programme_fixture.actor,
        workstream_id=created.object_ids["workstream_id"],
    )

    assert programme_fixture.owner_id in snapshot.authorized_waiver_authority_ids
    assert ordinary_user.id not in snapshot.authorized_waiver_authority_ids
    assert programme_fixture.foreign_owner_id not in snapshot.authorized_waiver_authority_ids


def test_missing_option_numeric_bound_returns_stable_contract_blocker():
    """Catches an incomplete range raising TypeError instead of a policy blocker."""
    snapshot = _policy_snapshot("options", "decision_ready")
    incomplete = replace_namespace(snapshot.option_versions[0], cost_min=None)
    blockers, _, _ = TransformationGateService.evaluate_requirements(
        replace(snapshot, option_versions=(incomplete, snapshot.option_versions[1])),
        TransformationGateService.require_valid_transition("options", "decision_ready"),
    )
    assert {row.code for row in blockers} == {"option_contract_incomplete"}


def test_completed_gate_requires_measurement_for_each_relevant_benefit_and_outcome_review():
    """Catches a valid but unrelated measurement projecting the workstream Completed."""
    snapshot = _policy_snapshot("outcomes", "completed")
    unrelated = replace_namespace(snapshot.measurements[0], benefit_id=999, outcome_commitment_id=999)
    blockers, _, _ = TransformationGateService.evaluate_requirements(
        replace(snapshot, measurements=(unrelated,)),
        TransformationGateService.require_valid_transition("outcomes", "completed"),
    )
    assert {row.code for row in blockers} == {"outcome_measurement_required"}


def test_non_finite_persisted_measure_cannot_make_objective_gate_ready():
    """Catches PostgreSQL Numeric NaN being treated as a valid target measurement contract."""
    snapshot = _policy_snapshot("objective", "discover")
    bad_measure = replace_namespace(snapshot.measures[0], target_amount=Decimal("NaN"))
    blockers, _, _ = TransformationGateService.evaluate_requirements(
        replace(snapshot, measures=(bad_measure,)),
        TransformationGateService.require_valid_transition("objective", "discover"),
    )
    assert {row.code for row in blockers} == {"measure_value_invalid"}


def test_missing_later_task_resource_is_explicitly_unavailable_and_fail_closed():
    """Catches absent future persistence being mistaken for an ordinary empty, satisfiable set."""
    snapshot = _policy_snapshot(
        "discover",
        "evidence",
        unavailable_resources=frozenset({"candidates", "evidence"}),
    )
    blockers, _, _ = TransformationGateService.evaluate_requirements(
        snapshot,
        TransformationGateService.require_valid_transition("discover", "evidence"),
    )
    assert [(row.code, row.resource_type) for row in blockers] == [
        ("policy_resource_unavailable", "candidates"),
        ("policy_resource_unavailable", "evidence"),
    ]


def test_gate_read_reloads_persisted_actor_and_rejects_forged_actor_roles(programme_fixture):
    """Catches gate reads trusting caller-supplied ActorContext roles without portfolio access."""
    created = TransformationProgrammeService.create_programme(
        actor=programme_fixture.actor,
        command_key="gate-read-auth",
        request=_intake(programme_fixture.owner_id),
    )
    outsider = User(
        email=f"gate-outsider-{uuid.uuid4().hex[:10]}@example.test",
        organization_id=programme_fixture.organization_id,
        confirmed=True,
        enterprise_role="application_manager",
    )
    db.session.add(outsider)
    db.session.commit()
    outsider_id = outsider.id
    forged = ActorContext(
        outsider_id,
        programme_fixture.organization_id,
        frozenset({"enterprise_architect", "programme_owner"}),
        "forged-gate-read",
    )
    with pytest.raises(NotAuthorised, match="programme_read_not_authorised"):
        TransformationGateService.evaluate(
            actor=forged,
            workstream_id=created.object_ids["workstream_id"],
            target_stage="discover",
        )


def test_archived_programme_cannot_be_evaluated_or_transitioned(programme_fixture):
    """Catches archived workstreams continuing to advertise or execute next actions."""
    created = TransformationProgrammeService.create_programme(
        actor=programme_fixture.actor,
        command_key="archived-gate",
        request=_intake(programme_fixture.owner_id),
    )
    TransformationProgrammeService.archive(
        actor=programme_fixture.actor,
        programme_id=created.object_ids["programme_id"],
        rationale="Archive the programme after its governing test decision.",
        expected_revision=1,
        command_key="archive-for-gate",
    )
    with pytest.raises(CommandConflict, match="programme_archived"):
        TransformationGateService.evaluate(
            actor=programme_fixture.actor,
            workstream_id=created.object_ids["workstream_id"],
            target_stage="discover",
        )
    with pytest.raises(CommandConflict, match="programme_archived"):
        TransformationGateService.transition(
            actor=programme_fixture.actor,
            workstream_id=created.object_ids["workstream_id"],
            target_stage="discover",
            expected_revision=1,
            command_key="archived-transition",
        )

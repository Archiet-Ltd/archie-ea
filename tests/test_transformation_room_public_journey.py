"""One real, public-operation journey through the Wave 1 transformation room."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import db
from app.models.application_capability import ApplicationCapabilityMapping
from app.models.application_owner import ApplicationOwner
from app.models.application_portfolio import ApplicationComponent
from app.models.application_rationalization import ApplicationDependency
from app.models.business_capabilities import BusinessCapability
from app.models.organization import Organization
from app.models.transformation_decision import (
    DecisionBrief,
    DecisionBriefVersion,
    TransformationOption,
)
from app.models.transformation_evidence import EvidenceRecord, EvidenceRequest
from app.models.user import User
from app.models.unified_capability import ValueStream
from app.modules.solutions_strategic.v2.services.programme_setup_service import (
    ProgrammeSetupService,
)
from app.modules.transformation_room.decision_service import (
    DecisionBriefService,
    TransformationOptionService,
)
from app.modules.transformation_room.discovery_service import (
    RationalisationDiscoveryService,
)
from app.modules.transformation_room.domain import (
    ActorContext,
    DiscoveryFilters,
    HumanAssertions,
    NotFound,
    ProgrammeIntake,
    TypedEvidenceValue,
)
from app.modules.transformation_room.evidence_service import (
    REQUIRED_EVIDENCE_CLAIMS,
    TransformationEvidenceService,
)
from app.modules.transformation_room.gate_service import TransformationGateService


@dataclass(frozen=True)
class PublicJourneyScope:
    organization_id: int
    foreign_organization_id: int
    actor_id: int
    foreign_actor_id: int
    application_id: int
    dependency_id: int
    capability_id: int
    value_stream_id: int
    actor: ActorContext
    foreign_actor: ActorContext


@pytest.fixture
def public_journey_scope(app, _schema):
    """Only inventory/identity prerequisites are seeded; domain rows use services."""
    suffix = uuid.uuid4().hex[:10]
    with app.app_context():
        db.session.remove()
        with db.session.begin():
            session = db.session
            organization = Organization(
                name=f"Public journey {suffix}", slug=f"public-journey-{suffix}"
            )
            foreign_organization = Organization(
                name=f"Foreign journey {suffix}", slug=f"foreign-journey-{suffix}"
            )
            session.add_all((organization, foreign_organization))
            session.flush()
            actor_user = User(
                email=f"journey-architect-{suffix}@example.test",
                organization_id=organization.id,
                confirmed=True,
                enterprise_role="enterprise_architect",
            )
            foreign_user = User(
                email=f"journey-foreign-{suffix}@example.test",
                organization_id=foreign_organization.id,
                confirmed=True,
                enterprise_role="enterprise_architect",
            )
            session.add_all((actor_user, foreign_user))
            session.flush()
            application = ApplicationComponent(
                organization_id=organization.id,
                name=f"Claims platform {suffix}",
                application_owner="Claims Operations",
                lifecycle_status="operational",
                business_criticality="Critical",
                total_cost_of_ownership=Decimal("125000.00"),
                end_of_life_date=date(2027, 3, 31),
                technical_risk="high",
                business_risk="medium",
                vendor_risk="low",
                obsolescence_risk="high",
                health_status="at_risk",
                updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            dependency_target = ApplicationComponent(
                organization_id=organization.id,
                name=f"Policy platform {suffix}",
            )
            capability = BusinessCapability(
                organization_id=organization.id,
                name=f"Claims servicing {suffix}",
                code=f"JNY-{suffix}",
                level=2,
            )
            value_stream = ValueStream(
                organization_id=organization.id,
                name=f"Resolve a claim {suffix}",
                code=f"VS-{suffix}",
                value_stream_type="customer_facing",
            )
            session.add_all(
                (application, dependency_target, capability, value_stream)
            )
            session.flush()
            mapping = ApplicationCapabilityMapping(
                organization_id=organization.id,
                application_component_id=application.id,
                business_capability_id=capability.id,
                coverage_percentage=100,
                is_active=True,
            )
            dependency = ApplicationDependency(
                organization_id=organization.id,
                source_app_id=application.id,
                target_app_id=dependency_target.id,
                dependency_type="api_call",
                dependency_strength="critical",
                status="active",
            )
            owner = ApplicationOwner(
                organization_id=organization.id,
                application_id=application.id,
                user_id=actor_user.id,
                assigned_by=actor_user.id,
                ownership_type="primary",
            )
            session.add_all((mapping, dependency, owner))
            session.flush()
            scope = PublicJourneyScope(
                organization_id=organization.id,
                foreign_organization_id=foreign_organization.id,
                actor_id=actor_user.id,
                foreign_actor_id=foreign_user.id,
                application_id=application.id,
                dependency_id=dependency.id,
                capability_id=capability.id,
                value_stream_id=value_stream.id,
                actor=ActorContext(
                    actor_user.id,
                    organization.id,
                    frozenset({"forged_client_role"}),
                    f"journey-{suffix}",
                ),
                foreign_actor=ActorContext(
                    foreign_user.id,
                    foreign_organization.id,
                    frozenset({"chief_architect"}),
                    f"foreign-journey-{suffix}",
                ),
            )
        previous_steward = app.config.get("TRANSFORMATION_PORTFOLIO_STEWARD_ID")
        app.config["TRANSFORMATION_PORTFOLIO_STEWARD_ID"] = scope.actor_id
        try:
            yield scope
        finally:
            if previous_steward is None:
                app.config.pop("TRANSFORMATION_PORTFOLIO_STEWARD_ID", None)
            else:
                app.config["TRANSFORMATION_PORTFOLIO_STEWARD_ID"] = previous_steward
            db.session.remove()
            from app.services.org_deletion_service import OrganizationDeletionService

            for org_id in (scope.organization_id, scope.foreign_organization_id):
                if db.session.get(Organization, org_id) is not None:
                    OrganizationDeletionService.delete_organization(org_id)
            db.session.remove()


def _option_draft(scope, *, title, action_type, ordinal):
    return {
        "title": title,
        "action_type": action_type,
        "description": f"Governed {action_type} alternative",
        "assumptions": [f"Assumption {ordinal} is explicitly human-owned"],
        "dependencies": [f"Dependency {ordinal} is resolved before execution"],
        "impacts": [
            {
                "impact_type": "capability",
                "subject_id": scope.capability_id,
                "description": f"Capability impact {ordinal}",
            },
            {
                "impact_type": "value_stream",
                "subject_id": scope.value_stream_id,
                "description": f"Value-stream impact {ordinal}",
            },
        ],
        "risks": [f"Risk {ordinal} has an owned mitigation"],
        "reversibility": "Reversible until the governed cutover",
        "transition_approach": f"Transition wave {ordinal}",
        "affected_capability_ids": [scope.capability_id],
        "affected_value_stream_ids": [scope.value_stream_id],
        "recommendation_rationale": f"Alternative {ordinal} rationale",
        "cost_min": Decimal(10000 * ordinal),
        "cost_max": Decimal(15000 * ordinal),
        "benefit_min": Decimal(20000 * ordinal),
        "benefit_max": Decimal(30000 * ordinal),
        "risk_min": Decimal("0.10") * ordinal,
        "risk_max": Decimal("0.20") * ordinal,
        "currency": "GBP",
        "technology_required": ordinal % 2 == 0,
    }


def _expire_and_remove_result(scope, *, operation, command_key, result_id):
    """Model a lost/damaged result envelope while retaining the domain effect.

    This organisation is not being deleted, so the org-deletion marker does not apply here
    and would not help regardless: the guard's unconditional first check on this table
    refuses to null an already-set ``operation_result_id`` under any admission, by design
    (the marker is never allowed to admit an UPDATE, only a DELETE).
    No real caller writes this exact "lost envelope, result discarded" shape in one
    transaction, so it is modelled directly here with the trigger switched off for this
    transaction only.
    """
    with db.engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL session_replication_role = replica")  # session-replication-role-ok: see docstring above — constructs a test-only "lost result envelope" state no real caller produces, not an organisation deletion
        connection.execute(
            text(
                "DELETE FROM transformation_outbox_events "
                "WHERE operation_result_id = :result_id"
            ),
            {"result_id": result_id},
        )
        connection.execute(
            text(
                "UPDATE command_idempotency_records "
                "SET status = 'retryable_failure', operation_result_id = NULL, "
                "last_error_class = 'envelope_lost', "
                "lease_expires_at = clock_timestamp() - interval '1 second' "
                "WHERE organization_id = :organization_id "
                "AND actor_id = :actor_id AND operation = :operation "
                "AND idempotency_key = :command_key"
            ),
            {
                "organization_id": scope.organization_id,
                "actor_id": scope.actor_id,
                "operation": operation,
                "command_key": command_key,
            },
        )
        connection.execute(
            text("DELETE FROM operation_results WHERE id = :result_id"),
            {"result_id": result_id},
        )


def test_business_first_to_frozen_brief_uses_only_public_operations(
    public_journey_scope,
):
    """Proves the real governed journey, tenancy, replay and materialisation."""
    scope = public_journey_scope
    intake = ProgrammeIntake(
        name="Claims portfolio rationalisation",
        objective="Reduce avoidable application cost without service disruption",
        owner_id=scope.actor_id,
        target_date=date(2027, 12, 31),
        target_date_unavailable_reason=None,
        workstream_type="application_rationalisation",
        scope_expression={"application_ids": [scope.application_id]},
        outcome={
            "statement": "Reduce annual claims-platform run cost",
            "owner_id": scope.actor_id,
            "direction": "decrease",
            "measure": {
                "metric_name": "Annual run cost",
                "unit": "GBP",
                "currency": "GBP",
                "aggregation": "sum",
                "baseline_value": Decimal("125000.00"),
                "target_value": Decimal("95000.00"),
                "unavailable_reason": None,
            },
        },
    )
    programme = ProgrammeSetupService.create_business_first_programme(
        actor=scope.actor, command_key="journey-programme", request=intake
    )
    _expire_and_remove_result(
        scope,
        operation="programme.create",
        command_key="journey-programme",
        result_id=programme.operation_result_id,
    )
    programme_replay = ProgrammeSetupService.create_business_first_programme(
        actor=scope.actor, command_key="journey-programme", request=intake
    )
    assert programme_replay.idempotent is True
    assert programme_replay.object_ids == programme.object_ids
    workstream_id = programme.object_ids["workstream_id"]

    discover_stage = TransformationGateService.transition(
        actor=scope.actor,
        workstream_id=workstream_id,
        target_stage="discover",
        expected_revision=1,
        command_key="journey-to-discover",
    )
    discovered = RationalisationDiscoveryService.discover(
        actor=scope.actor,
        workstream_id=workstream_id,
        filters=DiscoveryFilters(business_unit_ids=(), capability_ids=()),
    )
    candidate_view = next(
        item for item in discovered if item.application_id == scope.application_id
    )
    overlap = next(
        signal
        for signal in candidate_view.signals
        if signal.rule_code == "capability_overlap"
    )
    assert overlap.observed_values["overlap_count"] == 0
    accepted = RationalisationDiscoveryService.accept_candidate(
        actor=scope.actor,
        workstream_id=workstream_id,
        application_id=scope.application_id,
        signal_digests=candidate_view.signal_digests,
        inclusion_reason="The complete current ruleset supports governed assessment",
        command_key="journey-candidate",
    )
    _expire_and_remove_result(
        scope,
        operation="candidate.accept",
        command_key="journey-candidate",
        result_id=accepted.operation_result_id,
    )
    accepted_replay = RationalisationDiscoveryService.accept_candidate(
        actor=scope.actor,
        workstream_id=workstream_id,
        application_id=scope.application_id,
        signal_digests=candidate_view.signal_digests,
        inclusion_reason="The complete current ruleset supports governed assessment",
        command_key="journey-candidate",
    )
    assert accepted_replay.idempotent is True
    candidate_id = accepted.object_ids["candidate_id"]

    assignments = {claim: scope.actor_id for claim in REQUIRED_EVIDENCE_CLAIMS}
    planned = TransformationEvidenceService.plan_required_requests(
        actor=scope.actor,
        candidate_id=candidate_id,
        assignments=assignments,
        command_key="journey-evidence-plan",
    )
    _expire_and_remove_result(
        scope,
        operation="evidence.request.plan",
        command_key="journey-evidence-plan",
        result_id=planned.operation_result_id,
    )
    planned_replay = TransformationEvidenceService.plan_required_requests(
        actor=scope.actor,
        candidate_id=candidate_id,
        assignments=assignments,
        command_key="journey-evidence-plan",
    )
    assert planned_replay.idempotent is True
    assert planned_replay.object_ids == planned.object_ids
    with pytest.raises(NotFound):
        TransformationEvidenceService.plan_required_requests(
            actor=scope.foreign_actor,
            candidate_id=candidate_id,
            assignments={
                claim: scope.foreign_actor_id for claim in REQUIRED_EVIDENCE_CLAIMS
            },
            command_key="journey-cross-tenant-plan",
        )

    accepted_evidence_ids = []
    authoritative_claims = {
        "application_owner",
        "lifecycle",
        "business_criticality",
        "risk",
        "source_freshness",
    }
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
    for claim_key in REQUIRED_EVIDENCE_CLAIMS:
        request_id = planned.object_ids[f"request_{claim_key}_id"]
        if claim_key in authoritative_claims:
            submitted = TransformationEvidenceService.record_observation(
                actor=scope.actor,
                candidate_id=candidate_id,
                claim_key=claim_key,
                adapter_key="application-inventory",
                source_key=str(scope.application_id),
                expected_head_revision=0,
                command_key=f"journey-observe-{claim_key}",
            )
            request_revision = submitted.response["request_revision"]
        else:
            submitted = TransformationEvidenceService.submit_attestation(
                actor=scope.actor,
                request_id=request_id,
                value=attested_values[claim_key],
                expected_head_revision=0,
                command_key=f"journey-attest-{claim_key}",
            )
            request_revision = submitted.response["revision"]
        evidence_id = submitted.object_ids["evidence_record_id"]
        accepted_request = TransformationEvidenceService.accept_request(
            actor=scope.actor,
            request_id=request_id,
            evidence_id=evidence_id,
            expected_revision=request_revision,
            command_key=f"journey-accept-{claim_key}",
        )
        assert accepted_request.response["status"] == "accepted"
        accepted_evidence_ids.append(evidence_id)

    evidence_stage = TransformationGateService.transition(
        actor=scope.actor,
        workstream_id=workstream_id,
        target_stage="evidence",
        expected_revision=discover_stage.response["revision"],
        command_key="journey-to-evidence",
    )
    options_stage = TransformationGateService.transition(
        actor=scope.actor,
        workstream_id=workstream_id,
        target_stage="options",
        expected_revision=evidence_stage.response["revision"],
        command_key="journey-to-options",
    )
    assert options_stage.response["lifecycle_stage"] == "options"

    option_ids = []
    for ordinal, (title, action_type) in enumerate(
        (("Tolerate", "tolerate"), ("Retire", "retire")), start=1
    ):
        option = TransformationOptionService.create_draft(
            actor=scope.actor,
            workstream_id=workstream_id,
            candidate_id=candidate_id,
            draft=_option_draft(
                scope, title=title, action_type=action_type, ordinal=ordinal
            ),
            command_key=f"journey-option-{ordinal}",
        )
        option_ids.append(option.object_ids["option_id"])
    version_ids = tuple(
        TransformationOptionService.freeze_version(
            actor=scope.actor,
            option_id=option_id,
            expected_revision=1,
            command_key=f"journey-option-freeze-{ordinal}",
        ).object_ids["option_version_id"]
        for ordinal, option_id in enumerate(option_ids, start=1)
    )
    comparison = TransformationOptionService.compare(
        actor=scope.actor, option_version_ids=version_ids
    )
    assert comparison.comparable_currency == "GBP"
    assert comparison.conflicts == ()

    brief = DecisionBriefService.create_brief(
        actor=scope.actor,
        workstream_id=workstream_id,
        candidate_id=candidate_id,
        title="Claims platform rationalisation decision",
        recommendation_option_id=option_ids[1],
        decision_authority_id=scope.actor_id,
        unknown_codes=tuple(candidate_view.unknown_codes),
        conflicts=(),
        expected_impacts=("Lower run cost after a governed retirement",),
        command_key="journey-brief",
    )
    brief_replay = DecisionBriefService.create_brief(
        actor=scope.actor,
        workstream_id=workstream_id,
        candidate_id=candidate_id,
        title="Claims platform rationalisation decision",
        recommendation_option_id=option_ids[1],
        decision_authority_id=scope.actor_id,
        unknown_codes=tuple(candidate_view.unknown_codes),
        conflicts=(),
        expected_impacts=("Lower run cost after a governed retirement",),
        command_key="journey-brief",
    )
    assert brief_replay.idempotent is True
    brief_id = brief.object_ids["decision_brief_id"]
    frozen = DecisionBriefService.freeze(
        actor=scope.actor,
        brief_id=brief_id,
        option_version_ids=tuple(reversed(version_ids)),
        evidence_ids=tuple(reversed(accepted_evidence_ids)),
        assertions=HumanAssertions(
            reviewed_ai_material=True,
            acknowledged_unknown_codes=tuple(candidate_view.unknown_codes),
            acknowledged_superseded_evidence_ids=(),
            rationale="A human reviewed every cited fact, option and recommendation.",
        ),
        expected_revision=1,
        command_key="journey-brief-freeze",
    )
    _expire_and_remove_result(
        scope,
        operation="brief.freeze",
        command_key="journey-brief-freeze",
        result_id=frozen.operation_result_id,
    )
    frozen_replay = DecisionBriefService.freeze(
        actor=scope.actor,
        brief_id=brief_id,
        option_version_ids=version_ids,
        evidence_ids=tuple(sorted(accepted_evidence_ids)),
        assertions=HumanAssertions(
            reviewed_ai_material=True,
            acknowledged_unknown_codes=tuple(candidate_view.unknown_codes),
            acknowledged_superseded_evidence_ids=(),
            rationale="A human reviewed every cited fact, option and recommendation.",
        ),
        expected_revision=1,
        command_key="journey-brief-freeze",
    )
    assert frozen_replay.idempotent is True
    assert frozen_replay.object_ids == frozen.object_ids

    with Session(db.engine) as session:
        requests = session.scalars(
            select(EvidenceRequest).where(
                EvidenceRequest.organization_id == scope.organization_id,
                EvidenceRequest.candidate_id == candidate_id,
            )
        ).all()
        frozen_brief = session.get(
            DecisionBriefVersion, frozen.object_ids["decision_brief_version_id"]
        )
        brief_root = session.get(DecisionBrief, brief_id)
        assert session.scalar(
            select(func.count())
            .select_from(TransformationOption)
            .where(TransformationOption.organization_id == scope.organization_id)
        ) == 2
        assert session.scalar(
            select(func.count())
            .select_from(DecisionBrief)
            .where(DecisionBrief.organization_id == scope.organization_id)
        ) == 1
        assert session.scalar(
            select(func.count())
            .select_from(EvidenceRecord)
            .where(EvidenceRecord.organization_id == scope.organization_id)
        ) == len(REQUIRED_EVIDENCE_CLAIMS)
    assert len(requests) == len(REQUIRED_EVIDENCE_CLAIMS)
    assert all(request.status == "accepted" for request in requests)
    assert brief_root.status == "frozen"
    assert frozen_brief.cited_evidence_ids == sorted(accepted_evidence_ids)
    assert frozen_brief.option_version_ids == sorted(version_ids)


def test_advance_route_actually_transitions_the_workstream(app, public_journey_scope):
    """F-07, Capgemini dry-run: TransformationGateService.transition (used
    directly above) already worked — nothing in this module ever called it
    from a server-rendered POST, so every stage past Objective was
    permanently read-only through the real UI. This drives the actual HTTP
    route a browser would hit, not the service function directly."""
    scope = public_journey_scope
    intake = ProgrammeIntake(
        name="Advance route programme",
        objective="Prove the advance route actually transitions the workstream",
        owner_id=scope.actor_id,
        target_date=date(2027, 12, 31),
        target_date_unavailable_reason=None,
        workstream_type="application_rationalisation",
        scope_expression={"application_ids": [scope.application_id]},
        outcome={
            "statement": "Reduce annual claims-platform run cost",
            "owner_id": scope.actor_id,
            "direction": "decrease",
            "measure": {
                "metric_name": "Annual run cost",
                "unit": "GBP",
                "currency": "GBP",
                "aggregation": "sum",
                "baseline_value": Decimal("125000.00"),
                "target_value": Decimal("95000.00"),
                "unavailable_reason": None,
            },
        },
    )
    programme = ProgrammeSetupService.create_business_first_programme(
        actor=scope.actor, command_key="advance-route-programme", request=intake
    )
    programme_id = programme.object_ids["programme_id"]
    workstream_id = programme.object_ids["workstream_id"]

    from tests.test_ba_tenant_and_authz import _login
    client = app.test_client()
    with app.app_context():
        _login(client, scope.actor_id)

        page = client.get(
            f"/solutions/programmes/{programme_id}/workstreams/{workstream_id}/objective"
        )
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert "Advance now" in html
        assert f"/workstreams/{workstream_id}/objective/advance" in html

        resp = client.post(
            f"/solutions/programmes/{programme_id}/workstreams/{workstream_id}/objective/advance",
            data={"expected_revision": "1", "command_key": "advance-route-to-discover"},
        )
        assert resp.status_code == 303, resp.get_data(as_text=True)
        assert resp.headers["Location"].endswith(
            f"/workstreams/{workstream_id}/discover"
        )

        # Read back independently — the transition actually persisted, not
        # just a redirect that looked successful.
        snapshot = TransformationGateService.load_policy_snapshot(
            actor=scope.actor, workstream_id=workstream_id
        )
        assert snapshot.workstream.lifecycle_stage == "discover"

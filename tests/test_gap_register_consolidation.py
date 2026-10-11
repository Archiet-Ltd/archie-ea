"""merge-gap-stores, and gap_register_service.create_gap.

roadmap_gaps, implementation_gaps and compliance_gaps have no organisation
column at all. This merges them into the one gap register (`gaps`,
app.models.implementation_migration.Gap, which already carries TenantMixin),
attributing an organisation per source (application/capability/creator for
roadmap_gaps; architecture for implementation_gaps; assignee for
compliance_gaps), recording provenance, and marking each merged source row
retired_into_id -- never dropping it. capability_gap_analysis and
capability_gap_details are a different concept and are not touched here
(a deliberate decision, not an omission).
"""
import uuid

import pytest


def _user(db_session, org, label="u"):
    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"{label}-{suffix}@example.com",
        first_name="Test",
        last_name=label,
        organization_id=org.id,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _architecture_model(db_session, org, label="arch"):
    from app.models.archimate_core import ArchitectureModel

    suffix = uuid.uuid4().hex[:8]
    am = ArchitectureModel(name=f"{label}-{suffix}", organization_id=org.id)
    db_session.add(am)
    db_session.flush()
    return am


def _app_component(db_session, org, label="app"):
    from app.models.application_portfolio import ApplicationComponent

    suffix = uuid.uuid4().hex[:8]
    comp = ApplicationComponent(
        name=f"{label}-{suffix}", description="test application", organization_id=org.id,
    )
    db_session.add(comp)
    db_session.flush()
    return comp


def _run(command, dry_run=False):
    """Invoke the gap consolidation CLI command and return its output."""
    from app import create_app

    app = create_app("testing")
    runner = app.test_cli_runner()
    args = [command]
    if dry_run:
        args.append("--dry-run")
    return runner.invoke(args=args)


@pytest.fixture
def two_orgs(db_session, make_org):
    org_a = make_org("gap-register-a")
    org_b = make_org("gap-register-b")
    return org_a, org_b


# ── merge-gap-stores: roadmap_gaps ──────────────────────────────────────────


def test_merge_roadmap_gap_attributed_by_application(db_session, two_orgs):
    from app.models.roadmap_models import RoadmapGap
    from app.models.implementation_migration import Gap

    org_a, _org_b = two_orgs
    app_comp = _app_component(db_session, org_a)

    source = RoadmapGap(
        name="Missing API Gateway", gap_type="technology", priority="high",
        risk_level="high", source_application_id=app_comp.id,
    )
    db_session.add(source)
    db_session.commit()
    source_id = source.id
    assert source.retired_into_id is None

    result = _run("merge-gap-stores")
    assert result.exit_code == 0, f"merge failed: {result.output}"

    db_session.refresh(source)
    assert source.retired_into_id is not None, "source row must be marked retired_into_id"

    merged = Gap.query.filter_by(id=source.retired_into_id).one()
    assert merged.name == "Missing API Gateway"
    assert merged.source_table == "roadmap_gaps"
    assert merged.source_id == source_id
    assert merged.organization_id == org_a.id
    assert merged.severity == "high"
    assert merged.priority == "high"
    assert merged.gap_kind == "capability_shortfall"


def test_merge_roadmap_gap_attributed_by_creator_when_no_links(db_session, two_orgs):
    from app.models.roadmap_models import RoadmapGap
    from app.models.implementation_migration import Gap

    org_a, _org_b = two_orgs
    user_a = _user(db_session, org_a, "roadmap-creator")

    source = RoadmapGap(name="Process Gap", gap_type="process", created_by=user_a.id)
    db_session.add(source)
    db_session.commit()

    result = _run("merge-gap-stores")
    assert result.exit_code == 0, f"merge failed: {result.output}"

    db_session.refresh(source)
    merged = Gap.query.filter_by(id=source.retired_into_id).one()
    assert merged.organization_id == org_a.id
    assert merged.source_table == "roadmap_gaps"


def test_merge_roadmap_gap_folds_impact_assessment_into_description(db_session, two_orgs):
    """RoadmapGap.impact_assessment has no equivalent column on Gap, so it is
    folded into the description rather than silently dropped."""
    from app.models.roadmap_models import RoadmapGap
    from app.models.implementation_migration import Gap

    org_a, _org_b = two_orgs
    user_a = _user(db_session, org_a, "impact-creator")

    source = RoadmapGap(
        name="Data Gap", gap_type="data", description="Missing lineage",
        impact_assessment="Reports cannot be trusted", created_by=user_a.id,
    )
    db_session.add(source)
    db_session.commit()

    result = _run("merge-gap-stores")
    assert result.exit_code == 0, f"merge failed: {result.output}"

    db_session.refresh(source)
    merged = Gap.query.filter_by(id=source.retired_into_id).one()
    assert "Missing lineage" in merged.description
    assert "Reports cannot be trusted" in merged.description


# ── merge-gap-stores: implementation_gaps ───────────────────────────────────


def test_merge_implementation_gap_attributed_by_architecture(db_session, two_orgs):
    from app.models.implementation_planning import ImplementationGap
    from app.models.implementation_migration import Gap

    org_a, _org_b = two_orgs
    arch = _architecture_model(db_session, org_a)

    source = ImplementationGap(
        name="Cloud Adoption Gap", gap_type="technology", architecture_id=arch.id,
        baseline_state="On-premises", target_state="Cloud-native", impact_level="high",
    )
    db_session.add(source)
    db_session.commit()

    result = _run("merge-gap-stores")
    assert result.exit_code == 0, f"merge failed: {result.output}"

    db_session.refresh(source)
    assert source.retired_into_id is not None

    merged = Gap.query.filter_by(id=source.retired_into_id).one()
    assert merged.source_table == "implementation_gaps"
    assert merged.organization_id == org_a.id
    assert merged.current_state_ref == "On-premises"
    assert merged.target_state_ref == "Cloud-native"
    assert merged.severity == "high"


def test_merge_implementation_gap_unattributable_quarantined(db_session, two_orgs, tenant_ctx):
    """No architecture link: stays quarantined (organization_id NULL),
    invisible to both organisations -- never a guess."""
    from app.models.implementation_planning import ImplementationGap
    from app.models.implementation_migration import Gap

    org_a, org_b = two_orgs
    source = ImplementationGap(name="Orphan Gap", gap_type="technology")
    db_session.add(source)
    db_session.commit()

    result = _run("merge-gap-stores")
    assert result.exit_code == 0, f"merge failed: {result.output}"

    db_session.refresh(source)
    assert source.retired_into_id is not None
    merged_id = source.retired_into_id

    merged = Gap.query.filter_by(id=merged_id).one()
    assert merged.organization_id is None

    with tenant_ctx(org_a.id):
        visible_to_a = Gap.query.filter_by(id=merged_id).first()
    with tenant_ctx(org_b.id):
        visible_to_b = Gap.query.filter_by(id=merged_id).first()
    assert visible_to_a is None
    assert visible_to_b is None


# ── merge-gap-stores: compliance_gaps ───────────────────────────────────────


def test_merge_compliance_gap_attributed_by_assignee(db_session, two_orgs):
    from app.models.compliance_models import ComplianceGap
    from app.models.implementation_migration import Gap

    org_a, _org_b = two_orgs
    user_a = _user(db_session, org_a, "assignee")

    source = ComplianceGap(
        gap_type="missing_requirement", title="No encryption at rest",
        description="Storage layer has no encryption control",
        risk_level="critical", assigned_to_id=user_a.id,
    )
    db_session.add(source)
    db_session.commit()

    result = _run("merge-gap-stores")
    assert result.exit_code == 0, f"merge failed: {result.output}"

    db_session.refresh(source)
    assert source.retired_into_id is not None

    merged = Gap.query.filter_by(id=source.retired_into_id).one()
    assert merged.name == "No encryption at rest"
    assert merged.source_table == "compliance_gaps"
    assert merged.organization_id == org_a.id
    assert merged.severity == "critical"


def test_merge_compliance_gap_falls_back_to_identified_by(db_session, two_orgs):
    from app.models.compliance_models import ComplianceGap
    from app.models.implementation_migration import Gap

    org_a, _org_b = two_orgs
    user_a = _user(db_session, org_a, "identifier")

    source = ComplianceGap(
        gap_type="inadequate_control", title="No audit log retention policy",
        description="Retention undefined", risk_level="medium",
        identified_by_id=user_a.id,
    )
    db_session.add(source)
    db_session.commit()

    result = _run("merge-gap-stores")
    assert result.exit_code == 0, f"merge failed: {result.output}"

    db_session.refresh(source)
    merged = Gap.query.filter_by(id=source.retired_into_id).one()
    assert merged.organization_id == org_a.id


# ── merge-gap-stores: idempotency, dry-run, cross-tenant safety ────────────


def test_merge_is_idempotent(db_session, two_orgs):
    from app.models.roadmap_models import RoadmapGap
    from app.models.implementation_migration import Gap

    org_a, _org_b = two_orgs
    user_a = _user(db_session, org_a, "idempotent-creator")
    source = RoadmapGap(name="Idempotent Gap", gap_type="technology", created_by=user_a.id)
    db_session.add(source)
    db_session.commit()

    _run("merge-gap-stores")
    db_session.refresh(source)
    first_retired_into = source.retired_into_id
    assert first_retired_into is not None

    result2 = _run("merge-gap-stores")
    assert result2.exit_code == 0

    db_session.refresh(source)
    assert source.retired_into_id == first_retired_into, "idempotent re-run must not re-merge"

    count = Gap.query.filter_by(source_table="roadmap_gaps", source_id=source.id).count()
    assert count == 1, "re-running the merge must not duplicate the row"


def test_merge_dry_run_changes_nothing(db_session, two_orgs):
    from app.models.roadmap_models import RoadmapGap

    org_a, _org_b = two_orgs
    user_a = _user(db_session, org_a, "dry-run-creator")
    source = RoadmapGap(name="Dry Run Gap", gap_type="technology", created_by=user_a.id)
    db_session.add(source)
    db_session.commit()

    result = _run("merge-gap-stores", dry_run=True)
    assert result.exit_code == 0
    assert "dry-run" in result.output.lower()

    db_session.refresh(source)
    assert source.retired_into_id is None, "dry-run must not write"


def test_merge_never_crosses_organisations(db_session, two_orgs):
    """Org A's roadmap_gaps row merges to A's Gap; org B's own gap discovery
    of the same name is a separate row, never a shared or cross-org match."""
    from app.models.roadmap_models import RoadmapGap
    from app.models.implementation_migration import Gap

    org_a, org_b = two_orgs
    user_a = _user(db_session, org_a, "cross-a")
    user_b = _user(db_session, org_b, "cross-b")

    source_a = RoadmapGap(name="Shared Name Gap", gap_type="technology", created_by=user_a.id)
    source_b = RoadmapGap(name="Shared Name Gap", gap_type="technology", created_by=user_b.id)
    db_session.add_all([source_a, source_b])
    db_session.commit()

    result = _run("merge-gap-stores")
    assert result.exit_code == 0, f"merge failed: {result.output}"

    db_session.refresh(source_a)
    db_session.refresh(source_b)
    merged_a = Gap.query.filter_by(id=source_a.retired_into_id).one()
    merged_b = Gap.query.filter_by(id=source_b.retired_into_id).one()
    assert merged_a.id != merged_b.id
    assert merged_a.organization_id == org_a.id
    assert merged_b.organization_id == org_b.id


# ── gap_register_service.create_gap: the canonical writer ──────────────────


def test_create_gap_writes_to_the_one_register(db_session, two_orgs):
    from app.services import gap_register_service
    from app.models.implementation_migration import Gap

    org_a, _org_b = two_orgs
    gap, created = gap_register_service.create_gap(org_a.id, "New Finding", gap_type="coverage")
    db_session.commit()

    assert created is True
    assert isinstance(gap, Gap)
    assert gap.organization_id == org_a.id
    assert gap.name == "New Finding"


def test_create_gap_refuses_duplicate_within_organisation(db_session, two_orgs):
    from app.services import gap_register_service

    org_a, _org_b = two_orgs
    first, created1 = gap_register_service.create_gap(org_a.id, "Repeat Finding")
    db_session.commit()
    second, created2 = gap_register_service.create_gap(org_a.id, "Repeat Finding")
    db_session.commit()

    assert created1 is True
    assert created2 is False
    assert second.id == first.id


def test_create_gap_same_name_different_organisation_is_not_a_duplicate(db_session, two_orgs):
    from app.services import gap_register_service

    org_a, org_b = two_orgs
    gap_a, created_a = gap_register_service.create_gap(org_a.id, "Both Orgs Have This")
    db_session.commit()
    gap_b, created_b = gap_register_service.create_gap(org_b.id, "Both Orgs Have This")
    db_session.commit()

    assert created_a is True
    assert created_b is True
    assert gap_a.id != gap_b.id
    assert gap_a.organization_id == org_a.id
    assert gap_b.organization_id == org_b.id


def test_create_gap_duplicate_check_scoped_by_architecture_when_given(db_session, two_orgs):
    from app.services import gap_register_service

    org_a, _org_b = two_orgs
    arch = _architecture_model(db_session, org_a)

    gap_1, created_1 = gap_register_service.create_gap(
        org_a.id, "Scoped Finding", architecture_id=arch.id
    )
    db_session.commit()
    # Same name, no architecture given: a different scope, not a duplicate.
    gap_2, created_2 = gap_register_service.create_gap(org_a.id, "Scoped Finding")
    db_session.commit()

    assert created_1 is True
    assert created_2 is True
    assert gap_1.id != gap_2.id


def test_create_gap_requires_organization_id(db_session):
    from app.services import gap_register_service

    with pytest.raises(ValueError):
        gap_register_service.create_gap(None, "No Org Gap")


def test_create_gap_requires_a_name(db_session, two_orgs):
    from app.services import gap_register_service

    org_a, _org_b = two_orgs
    with pytest.raises(ValueError):
        gap_register_service.create_gap(org_a.id, "   ")


# ── GapDiscoveryService.save_discovered_gaps ─────────────────────────────────
#
# save_discovered_gaps used to construct app.models.implementation_migration.
# Gap (imported here under the confusing local alias ImplementationGap) with
# keyword arguments that belong to a different, unrelated model also named
# ImplementationGap (app.models.implementation_planning.ImplementationGap).
# Every call raised a TypeError, silently swallowed, so nothing was ever
# saved -- these tests fail against that behaviour and pass against the fix.


def _fake_gaps_data(*names):
    return {
        "gaps": [
            {
                "name": name,
                "gap_type": "technology",
                "gap_description": f"{name} description",
                "baseline_state": "Current",
                "target_state": "Target",
                "impact_level": "high",
                "priority": "high",
                "proposed_solution": "Do the work",
                "success_criteria": "Done",
            }
            for name in names
        ]
    }


def test_save_discovered_gaps_writes_to_the_one_register(db_session, two_orgs):
    from app.services.gap_discovery_service import GapDiscoveryService
    from app.models.implementation_migration import Gap

    org_a, _org_b = two_orgs
    service = GapDiscoveryService()

    result = service.save_discovered_gaps(_fake_gaps_data("Disaster Recovery Gap"), None, org_a.id)

    assert result == {"saved": 1, "duplicates": 0, "failed": 0}
    gap = Gap.query.filter_by(organization_id=org_a.id, name="Disaster Recovery Gap").one()
    assert gap.severity == "high"
    assert gap.priority == "high"
    assert gap.current_state_ref == "Current"
    assert gap.target_state_ref == "Target"
    assert gap.auto_generated is True
    assert gap.generation_source == "gap_discovery"
    assert "Do the work" in gap.description


def test_save_discovered_gaps_refuses_duplicates_on_a_second_run(db_session, two_orgs):
    """Re-running discovery over the same architecture must not grow the
    register with what is already there."""
    from app.services.gap_discovery_service import GapDiscoveryService

    org_a, _org_b = two_orgs
    service = GapDiscoveryService()
    gaps_data = _fake_gaps_data("Cloud Adoption Gap", "API Management Gap")

    first = service.save_discovered_gaps(gaps_data, None, org_a.id)
    assert first == {"saved": 2, "duplicates": 0, "failed": 0}

    second = service.save_discovered_gaps(gaps_data, None, org_a.id)
    assert second == {"saved": 0, "duplicates": 2, "failed": 0}


def test_save_discovered_gaps_is_per_organisation(db_session, two_orgs):
    from app.services.gap_discovery_service import GapDiscoveryService
    from app.models.implementation_migration import Gap

    org_a, org_b = two_orgs
    service = GapDiscoveryService()
    gaps_data = _fake_gaps_data("Security Architecture Gap")

    service.save_discovered_gaps(gaps_data, None, org_a.id)
    result_b = service.save_discovered_gaps(gaps_data, None, org_b.id)

    assert result_b == {"saved": 1, "duplicates": 0, "failed": 0}, (
        "the same finding in a different organisation is not a duplicate"
    )
    gaps_a = Gap.query.filter_by(organization_id=org_a.id, name="Security Architecture Gap").all()
    gaps_b = Gap.query.filter_by(organization_id=org_b.id, name="Security Architecture Gap").all()
    assert len(gaps_a) == 1
    assert len(gaps_b) == 1
    assert gaps_a[0].id != gaps_b[0].id


def test_save_discovered_gaps_requires_organization_id():
    from app.services.gap_discovery_service import GapDiscoveryService

    service = GapDiscoveryService()
    with pytest.raises(ValueError):
        service.save_discovered_gaps(_fake_gaps_data("No Org Gap"), None, None)

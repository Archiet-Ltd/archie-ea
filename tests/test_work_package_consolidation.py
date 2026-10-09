"""backfill-work-package-org and merge-work-package-stores.

unified_work_packages predates TenantMixin, so it has no organisation column
today. This backfills it with the attribution rule -- linked programme
or element, else creator, else quarantine -- then merges
technology_roadmap_initiatives, roadmap_work_packages and
implementation_work_packages into it (work_packages already carries
TenantMixin and is merged unchanged), recording provenance and marking each
merged source row retired_into_id rather than dropping it.
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


def _archimate_element(db_session, org, label="ae"):
    from app.models.archimate_core import ArchiMateElement

    suffix = uuid.uuid4().hex[:8]
    ae = ArchiMateElement(
        name=f"{label}-{suffix}", type="WorkPackage", layer="implementation",
        organization_id=org.id,
    )
    db_session.add(ae)
    db_session.flush()
    return ae


def _app_component(db_session, org, label="app"):
    from app.models.application_portfolio import ApplicationComponent

    suffix = uuid.uuid4().hex[:8]
    comp = ApplicationComponent(
        name=f"{label}-{suffix}", description="test application", organization_id=org.id,
    )
    db_session.add(comp)
    db_session.flush()
    return comp


def _architecture_model(db_session, org, label="arch"):
    from app.models.archimate_core import ArchitectureModel

    suffix = uuid.uuid4().hex[:8]
    am = ArchitectureModel(name=f"{label}-{suffix}", organization_id=org.id)
    db_session.add(am)
    db_session.flush()
    return am


def _enterprise_initiative(db_session, org, label="ei"):
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    suffix = uuid.uuid4().hex[:8]
    ei = EnterpriseInitiative(name=f"{label}-{suffix}", organization_id=org.id)
    db_session.add(ei)
    db_session.flush()
    return ei


def _unified_wp(db_session, **kwargs):
    """Create a UnifiedWorkPackage.  Callers that omit organization_id are
    testing the backfill that attributes it -- the NULL is deliberate, not a
    wiring defect."""
    from app.models.unified_work_package import UnifiedWorkPackage

    kwargs.setdefault("name", f"uwp-{uuid.uuid4().hex[:8]}")
    # wiring-ok: backfill test helper -- callers that omit org_id are testing
    # the attribution backfill that sets it later
    row = UnifiedWorkPackage(**kwargs)
    db_session.add(row)
    db_session.flush()
    return row


def _run(command, dry_run=False):
    """Invoke a work-package consolidation CLI command and return its output."""
    from app import create_app

    app = create_app("testing")
    runner = app.test_cli_runner()
    args = [command]
    if dry_run:
        args.append("--dry-run")
    return runner.invoke(args=args)


@pytest.fixture(autouse=True)
def _bridge_off():
    """These tests seed rows in the retired stores to exercise the deploy
    merge; the session bridge would copy them on insert."""
    from app.services import work_package_bridge

    with work_package_bridge.suspended():
        yield


@pytest.fixture
def two_orgs(db_session, make_org):
    org_a = make_org("wp-backfill-a")
    org_b = make_org("wp-backfill-b")
    return org_a, org_b


# ── backfill-work-package-org: attribution rule ─────────────────────────────


def test_attributed_by_linked_programme(db_session, two_orgs, tenant_ctx):
    """A row linked to an enterprise initiative gets that initiative's org,
    and after the backfill the tenant filter correctly scopes reads."""
    org_a, org_b = two_orgs
    ei_a = _enterprise_initiative(db_session, org_a)
    ei_b = _enterprise_initiative(db_session, org_b)

    wp_a = _unified_wp(db_session, enterprise_initiative_id=ei_a.id)
    wp_b = _unified_wp(db_session, enterprise_initiative_id=ei_b.id)
    db_session.commit()

    assert wp_a.organization_id is None
    assert wp_b.organization_id is None

    result = _run("backfill-work-package-org")
    assert result.exit_code == 0, f"backfill failed: {result.output}"

    db_session.refresh(wp_a)
    db_session.refresh(wp_b)
    assert wp_a.organization_id == org_a.id
    assert wp_b.organization_id == org_b.id

    # Positive isolation: each org sees its own work package and not the other's.
    from app.models.unified_work_package import UnifiedWorkPackage

    with tenant_ctx(org_a.id):
        visible_to_a = UnifiedWorkPackage.query.filter_by(id=wp_a.id).first()
        assert visible_to_a is not None, "org A must see its own work package"
    with tenant_ctx(org_b.id):
        visible_to_a_from_b = UnifiedWorkPackage.query.filter_by(id=wp_a.id).first()
        assert visible_to_a_from_b is None, "org B must not see org A's work package"

    with tenant_ctx(org_b.id):
        visible_to_b = UnifiedWorkPackage.query.filter_by(id=wp_b.id).first()
        assert visible_to_b is not None, "org B must see its own work package"
    with tenant_ctx(org_a.id):
        visible_to_b_from_a = UnifiedWorkPackage.query.filter_by(id=wp_b.id).first()
        assert visible_to_b_from_a is None, "org A must not see org B's work package"


def test_attributed_by_linked_element_when_no_programme(db_session, two_orgs):
    """No programme link: fall back to the linked ArchiMate element's org."""
    org_a, org_b = two_orgs
    ae_a = _archimate_element(db_session, org_a)
    ae_b = _archimate_element(db_session, org_b)

    wp_a = _unified_wp(db_session, archimate_element_id=ae_a.id)
    wp_b = _unified_wp(db_session, archimate_element_id=ae_b.id)
    db_session.commit()

    result = _run("backfill-work-package-org")
    assert result.exit_code == 0, f"backfill failed: {result.output}"

    db_session.refresh(wp_a)
    db_session.refresh(wp_b)
    assert wp_a.organization_id == org_a.id
    assert wp_b.organization_id == org_b.id


def test_programme_link_wins_over_element_link(db_session, two_orgs):
    """When both a programme and an element are linked (to different orgs),
    the programme wins, per the attribution rule's stated order."""
    org_a, org_b = two_orgs
    ei_a = _enterprise_initiative(db_session, org_a)
    ae_b = _archimate_element(db_session, org_b)

    wp = _unified_wp(
        db_session, enterprise_initiative_id=ei_a.id, archimate_element_id=ae_b.id
    )
    db_session.commit()

    result = _run("backfill-work-package-org")
    assert result.exit_code == 0, f"backfill failed: {result.output}"

    db_session.refresh(wp)
    assert wp.organization_id == org_a.id, "linked programme must win over linked element"


def test_attributed_by_application_component_element(db_session, two_orgs):
    """No programme and no ArchiMate element link: fall back to the linked
    application component."""
    org_a, _org_b = two_orgs
    comp = _app_component(db_session, org_a)

    wp = _unified_wp(db_session, application_component_id=comp.id)
    db_session.commit()

    result = _run("backfill-work-package-org")
    assert result.exit_code == 0, f"backfill failed: {result.output}"

    db_session.refresh(wp)
    assert wp.organization_id == org_a.id


def test_attributed_by_creator_when_no_programme_or_element(db_session, two_orgs):
    """No programme and no element link: fall back to the creator's org."""
    org_a, _org_b = two_orgs
    user_a = _user(db_session, org_a, "creator")

    wp = _unified_wp(db_session, created_by=user_a.id)
    db_session.commit()

    result = _run("backfill-work-package-org")
    assert result.exit_code == 0, f"backfill failed: {result.output}"

    db_session.refresh(wp)
    assert wp.organization_id == org_a.id


def test_unattributable_row_quarantined_and_invisible_to_both_orgs(
    db_session, two_orgs, tenant_ctx
):
    """A row with no programme, element or creator link stays NULL after the
    backfill, and is invisible to both organisations under the tenant filter
    -- that NULL is the quarantine, not a bug."""
    org_a, org_b = two_orgs

    wp = _unified_wp(db_session, name="orphan-work-package")
    db_session.commit()
    orphan_id = wp.id

    result = _run("backfill-work-package-org")
    assert result.exit_code == 0, f"backfill failed: {result.output}"

    db_session.refresh(wp)
    assert wp.organization_id is None, "unattributable row must stay NULL (quarantine)"

    from app.models.unified_work_package import UnifiedWorkPackage

    with tenant_ctx(org_a.id):
        visible_to_a = UnifiedWorkPackage.query.filter_by(id=orphan_id).first()
    with tenant_ctx(org_b.id):
        visible_to_b = UnifiedWorkPackage.query.filter_by(id=orphan_id).first()

    assert visible_to_a is None, "quarantined row must not be visible to organisation A"
    assert visible_to_b is None, "quarantined row must not be visible to organisation B"


def test_backfill_dry_run_changes_nothing(db_session, two_orgs):
    org_a, _org_b = two_orgs
    ei_a = _enterprise_initiative(db_session, org_a)
    wp = _unified_wp(db_session, enterprise_initiative_id=ei_a.id)
    db_session.commit()

    result = _run("backfill-work-package-org", dry_run=True)
    assert result.exit_code == 0
    assert "dry-run" in result.output.lower()

    db_session.refresh(wp)
    assert wp.organization_id is None, "dry-run must not write"


def test_backfill_is_idempotent(db_session, two_orgs):
    org_a, _org_b = two_orgs
    ei_a = _enterprise_initiative(db_session, org_a)
    wp = _unified_wp(db_session, enterprise_initiative_id=ei_a.id)
    db_session.commit()

    _run("backfill-work-package-org")
    db_session.refresh(wp)
    first_org = wp.organization_id
    assert first_org == org_a.id

    result2 = _run("backfill-work-package-org")
    assert result2.exit_code == 0

    db_session.refresh(wp)
    assert wp.organization_id == first_org


# ── merge-work-package-stores ───────────────────────────────────────────────


def test_merge_technology_roadmap_initiative(db_session, two_orgs):
    """A technology_roadmap_initiatives row with a linked solution merges into
    unified_work_packages with the solution's org, provenance recorded, and
    the source row marked retired_into_id."""
    from app.models.implementation_migration import TechnologyRoadmapInitiative
    from app.models.solution_models import Solution
    from app.models.unified_work_package import UnifiedWorkPackage

    org_a, _org_b = two_orgs
    solution = Solution(name=f"sol-{uuid.uuid4().hex[:8]}", organization_id=org_a.id)
    db_session.add(solution)
    db_session.flush()

    source = TechnologyRoadmapInitiative(
        name="Cloud Migration",
        fiscal_year_start=2026,
        fiscal_year_end=2027,
        solution_id=solution.id,
    )
    db_session.add(source)
    db_session.commit()
    source_id = source.id
    assert source.retired_into_id is None

    result = _run("merge-work-package-stores")
    assert result.exit_code == 0, f"merge failed: {result.output}"

    db_session.refresh(source)
    assert source.retired_into_id is not None, "source row must be marked retired_into_id"

    merged = UnifiedWorkPackage.query.filter_by(id=source.retired_into_id).one()
    assert merged.name == "Cloud Migration"
    assert merged.source_table == "technology_roadmap_initiatives"
    assert merged.source_id == source_id
    assert merged.organization_id == org_a.id


def test_merge_roadmap_work_package_attributed_by_creator(db_session, two_orgs):
    """roadmap_work_packages has no programme/element FK, so its rows are
    attributed via their creator alone."""
    from app.models.roadmap_models import RoadmapWorkPackage
    from app.models.unified_work_package import UnifiedWorkPackage

    org_a, _org_b = two_orgs
    user_a = _user(db_session, org_a, "roadmap-creator")

    source = RoadmapWorkPackage(
        name="Roadmap Item", business_capability="Customer Management",
        created_by=user_a.id,
    )
    db_session.add(source)
    db_session.commit()

    result = _run("merge-work-package-stores")
    assert result.exit_code == 0, f"merge failed: {result.output}"

    db_session.refresh(source)
    assert source.retired_into_id is not None

    merged = UnifiedWorkPackage.query.filter_by(id=source.retired_into_id).one()
    assert merged.source_table == "roadmap_work_packages"
    assert merged.organization_id == org_a.id
    assert merged.business_capability == "Customer Management"


def test_merge_implementation_work_package_by_application_component(db_session, two_orgs):
    from app.models.implementation_planning import ImplementationWorkPackage
    from app.models.unified_work_package import UnifiedWorkPackage

    org_a, _org_b = two_orgs
    comp = _app_component(db_session, org_a)

    source = ImplementationWorkPackage(
        name="Implementation Task", application_component_id=comp.id,
    )
    db_session.add(source)
    db_session.commit()

    result = _run("merge-work-package-stores")
    assert result.exit_code == 0, f"merge failed: {result.output}"

    db_session.refresh(source)
    merged = UnifiedWorkPackage.query.filter_by(id=source.retired_into_id).one()
    assert merged.source_table == "implementation_work_packages"
    assert merged.organization_id == org_a.id


def test_merge_work_packages_copies_own_organization(db_session, two_orgs):
    """work_packages already carries TenantMixin, so its own organization_id
    is copied straight across -- no attribution guesswork needed."""
    from app.models.implementation_migration import WorkPackage
    from app.models.unified_work_package import UnifiedWorkPackage

    org_a, _org_b = two_orgs

    source = WorkPackage(name="Governed Work Package", organization_id=org_a.id)
    db_session.add(source)
    db_session.commit()

    result = _run("merge-work-package-stores")
    assert result.exit_code == 0, f"merge failed: {result.output}"

    db_session.refresh(source)
    assert source.retired_into_id is not None

    merged = UnifiedWorkPackage.query.filter_by(id=source.retired_into_id).one()
    assert merged.source_table == "work_packages"
    assert merged.organization_id == org_a.id


def test_merge_unattributable_row_quarantined(db_session, two_orgs, tenant_ctx):
    """A merged row with no resolvable organisation lands in unified_work_packages
    with organization_id NULL, and is invisible to both organisations."""
    from app.models.implementation_migration import TechnologyRoadmapInitiative
    from app.models.unified_work_package import UnifiedWorkPackage

    org_a, org_b = two_orgs

    source = TechnologyRoadmapInitiative(
        name="Orphan Initiative", fiscal_year_start=2026, fiscal_year_end=2027,
    )
    db_session.add(source)
    db_session.commit()

    result = _run("merge-work-package-stores")
    assert result.exit_code == 0, f"merge failed: {result.output}"

    db_session.refresh(source)
    assert source.retired_into_id is not None
    merged_id = source.retired_into_id

    merged = UnifiedWorkPackage.query.filter_by(id=merged_id).one()
    assert merged.organization_id is None

    with tenant_ctx(org_a.id):
        visible_to_a = UnifiedWorkPackage.query.filter_by(id=merged_id).first()
    with tenant_ctx(org_b.id):
        visible_to_b = UnifiedWorkPackage.query.filter_by(id=merged_id).first()
    assert visible_to_a is None
    assert visible_to_b is None


def test_merge_is_idempotent(db_session, two_orgs):
    """Re-running the merge must not create a second unified_work_packages row
    for an already-retired source row."""
    from app.models.implementation_migration import TechnologyRoadmapInitiative
    from app.models.unified_work_package import UnifiedWorkPackage

    org_a, _org_b = two_orgs
    source = TechnologyRoadmapInitiative(
        name="Idempotent Initiative", fiscal_year_start=2026, fiscal_year_end=2027,
    )
    db_session.add(source)
    db_session.commit()

    _run("merge-work-package-stores")
    db_session.refresh(source)
    first_retired_into = source.retired_into_id
    assert first_retired_into is not None

    result2 = _run("merge-work-package-stores")
    assert result2.exit_code == 0

    db_session.refresh(source)
    assert source.retired_into_id == first_retired_into, "idempotent re-run must not re-merge"

    count = (
        UnifiedWorkPackage.query.filter_by(
            source_table="technology_roadmap_initiatives", source_id=source.id
        ).count()
    )
    assert count == 1, "re-running the merge must not duplicate the row"


def test_merge_dry_run_changes_nothing(db_session, two_orgs):
    from app.models.implementation_migration import TechnologyRoadmapInitiative

    source = TechnologyRoadmapInitiative(
        name="Dry Run Initiative", fiscal_year_start=2026, fiscal_year_end=2027,
    )
    db_session.add(source)
    db_session.commit()

    result = _run("merge-work-package-stores", dry_run=True)
    assert result.exit_code == 0
    assert "dry-run" in result.output.lower()

    db_session.refresh(source)
    assert source.retired_into_id is None, "dry-run must not write"

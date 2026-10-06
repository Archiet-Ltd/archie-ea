"""backfill-layer-tenancy's new _DERIVABLE_ORG entries for the motivation,
requirements, strategic and technology-layer tables gaining TenantMixin, the
_PROVENANCE_ONLY quarantine guard for tables with no reliable attribution,
and HybridTenantMixin's shared-catalogue tables.

Every table here was created via db.create_all() with organization_id already
NOT NULL (the model's own declaration), unlike a real production database
where reconcile-schema only ever adds it nullable. Each test's own ALTER TABLE
... DROP NOT NULL loosens that one column back to the pre-backfill shape
inside the test's own rolled-back transaction -- the same technique already
used in tests/test_impact_analysis_history_tenancy.py -- so organization_id
can be set to NULL and then derived, proving the backfill's own logic rather
than assuming a schema state this project's tests cannot otherwise reach.

Uses the shared fixtures (tests/conftest.py) per CLAUDE.md's convention.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.commands.backfill_layer_tenancy import repair_layer_tenancy

pytestmark = pytest.mark.usefixtures("db_session")


def _drop_not_null(db_session, *tables):
    for t in tables:
        db_session.execute(text(f'ALTER TABLE "{t}" ALTER COLUMN organization_id DROP NOT NULL'))


def _element(db_session, org, type_="Driver", layer="motivation"):
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(
        name=f"El {uuid.uuid4().hex[:8]}", type=type_, layer=layer, organization_id=org.id
    )
    db_session.add(el)
    db_session.flush()
    return el


def _solution(db_session, org):
    from app.models.solution_models import Solution

    sol = Solution(name=f"Sol {uuid.uuid4().hex[:8]}", organization_id=org.id)
    db_session.add(sol)
    db_session.flush()
    return sol


def _initiative(db_session, org):
    from app.models.strategic import StrategicInitiative

    init = StrategicInitiative(name=f"Init {uuid.uuid4().hex[:8]}", organization_id=org.id)
    db_session.add(init)
    db_session.flush()
    return init


def _capability(db_session, org):
    from app.models.business_capabilities import BusinessCapability

    cap = BusinessCapability(
        name=f"Cap {uuid.uuid4().hex[:8]}", organization_id=org.id,
        current_maturity_level=1, target_maturity_level=3,
    )
    db_session.add(cap)
    db_session.flush()
    return cap


def _user(db_session, org):
    from app.models.user import User

    user = User(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test", last_name="User", organization_id=org.id, confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_single_fk_tables_resolve_to_each_rows_own_organisation(db_session, make_org):
    """drivers/meanings/values/assessments/archimate_resources each derive
    solely from archimate_element_id -- one shared UPDATE...FROM shape;
    proving it for each anchor covers every table using that exact pattern."""
    from app.models.motivation import Driver, Meaning, Value, Assessment
    from app.models.archimate_technology import Resource

    org_a = make_org("sfk-a")
    org_b = make_org("sfk-b")
    _drop_not_null(db_session, "drivers", "meanings", "values", "assessments", "archimate_resources")

    el_a = _element(db_session, org_a)
    el_b = _element(db_session, org_b)

    driver_a = Driver(name="D-a", archimate_element_id=el_a.id, organization_id=None)
    driver_b = Driver(name="D-b", archimate_element_id=el_b.id, organization_id=None)
    meaning_a = Meaning(name="M-a", archimate_element_id=el_a.id, organization_id=None)
    value_b = Value(name="V-b", archimate_element_id=el_b.id, organization_id=None)
    assessment_a = Assessment(name="A-a", archimate_element_id=el_a.id, organization_id=None)
    resource_b = Resource(name="R-b", archimate_element_id=el_b.id, organization_id=None)
    db_session.add_all([driver_a, driver_b, meaning_a, value_b, assessment_a, resource_b])
    db_session.flush()

    repair_layer_tenancy()

    for row, org in (
        (driver_a, org_a), (driver_b, org_b), (meaning_a, org_a),
        (value_b, org_b), (assessment_a, org_a), (resource_b, org_b),
    ):
        db_session.refresh(row)
        assert row.organization_id == org.id


def test_not_null_fk_tables_always_resolve(db_session, make_org):
    """motivation_bridge_links/strategic_milestones/capability_health_overrides/
    programme_snapshots/solution_adr_links each hang off a NOT NULL FK to an
    already-fenced table, so a real orphan is structurally impossible."""
    from app.models.motivation import MotivationBridgeLink
    from app.models.strategic import StrategicMilestone, CapabilityHealthOverride, ProgrammeSnapshot
    from app.models.solution_architect_models import SolutionADRLink, SolutionAnalysisSession, SolutionSessionStatus

    org_a = make_org("nnfk-a")
    org_b = make_org("nnfk-b")
    _drop_not_null(
        db_session, "motivation_bridge_links", "strategic_milestones",
        "capability_health_overrides", "programme_snapshots", "solution_adr_links",
    )

    # SolutionADRLink.adr_id references architecture_decisions (the
    # canonical register, app/models/architecture_decision.py) since PR
    # 305 -- not the legacy architecture_decision_records table.
    from app.models.architecture_decision import ArchitectureDecision

    sol_a = _solution(db_session, org_a)
    init_b = _initiative(db_session, org_b)
    cap_a = _capability(db_session, org_a)
    user_a = _user(db_session, org_a)
    session_b = SolutionAnalysisSession(
        name=f"S {uuid.uuid4().hex[:8]}", status=SolutionSessionStatus.IN_PROGRESS,
        created_by_id=user_a.id, organization_id=org_b.id,
    )
    # SolutionADRLink.adr_id is NOT NULL -- a minimal valid record to link to.
    adr = ArchitectureDecision(
        title="T", context="C", decision="D", rationale="R",
        consequences="Q", organization_id=org_b.id,
    )
    db_session.add_all([session_b, adr])
    db_session.flush()

    bridge = MotivationBridgeLink(
        solution_id=sol_a.id, solution_element_type="SolutionDriver", solution_element_id=1,
        enterprise_element_type="Driver", enterprise_element_id=1, organization_id=None,
    )
    milestone = StrategicMilestone(initiative_id=init_b.id, name="M1", organization_id=None)
    override = CapabilityHealthOverride(
        capability_id=cap_a.id, original_score=50.0, override_score=80.0,
        justification="test", override_reason="strategic", created_by_id=user_a.id,
        organization_id=None,
    )
    snapshot = ProgrammeSnapshot(initiative_id=init_b.id, organization_id=None)
    adr_link = SolutionADRLink(session_id=session_b.id, adr_id=adr.id, organization_id=None)
    db_session.add_all([bridge, milestone, override, snapshot, adr_link])
    db_session.flush()

    repair_layer_tenancy()

    for row, org in (
        (bridge, org_a), (milestone, org_b), (override, org_a),
        (snapshot, org_b), (adr_link, org_b),
    ):
        db_session.refresh(row)
        assert row.organization_id == org.id


def test_goals_falls_back_from_element_to_driver_to_creator(db_session, make_org):
    from app.models.motivation import Driver, Goal

    org_a = make_org("goal-a")
    org_b = make_org("goal-b")
    org_c = make_org("goal-c")
    _drop_not_null(db_session, "drivers", "goals")

    el_a = _element(db_session, org_a)
    driver_b = Driver(name="D-b", organization_id=org_b.id)
    user_c = _user(db_session, org_c)
    db_session.add(driver_b)
    db_session.flush()

    goal_via_element = Goal(name="G-element", archimate_element_id=el_a.id, organization_id=None)
    goal_via_driver = Goal(name="G-driver", driver_id=driver_b.id, organization_id=None)
    goal_via_creator = Goal(name="G-creator", created_by_id=user_c.id, organization_id=None)
    db_session.add_all([goal_via_element, goal_via_driver, goal_via_creator])
    db_session.flush()

    repair_layer_tenancy()

    db_session.refresh(goal_via_element)
    db_session.refresh(goal_via_driver)
    db_session.refresh(goal_via_creator)
    assert goal_via_element.organization_id == org_a.id
    assert goal_via_driver.organization_id == org_b.id
    assert goal_via_creator.organization_id == org_c.id


def test_stakeholders_falls_back_from_element_to_creator(db_session, make_org):
    from sqlalchemy import insert

    from app.models.motivation import Stakeholder

    org_a = make_org("stk-a")
    org_b = make_org("stk-b")
    _drop_not_null(db_session, "stakeholders")

    el_a = _element(db_session, org_a, type_="Stakeholder")
    user_b = _user(db_session, org_b)

    via_element = Stakeholder(name="S-element", archimate_element_id=el_a.id, organization_id=None)
    db_session.add(via_element)

    # A legacy row written before create_stakeholder_archimate existed: a
    # raw Core insert bypasses that before_insert listener, so
    # archimate_element_id stays genuinely NULL -- unlike any row the ORM
    # constructor can produce today, which always gets one. This is the one
    # shape the creator-fallback branch below actually exists for.
    via_creator_id = db_session.execute(
        insert(Stakeholder.__table__).values(
            name="S-creator", created_by_id=user_b.id, organization_id=None,
        )
    ).inserted_primary_key[0]
    db_session.flush()

    repair_layer_tenancy()

    db_session.refresh(via_element)
    via_creator = db_session.get(Stakeholder, via_creator_id)
    assert via_element.organization_id == org_a.id
    assert via_creator.organization_id == org_b.id


def test_requirements_tries_its_four_element_columns_then_application_component(db_session, make_org):
    from app.models.models import Requirement
    from app.models.application_portfolio import ApplicationComponent

    org_a = make_org("req-a")
    org_b = make_org("req-b")
    _drop_not_null(db_session, "requirements")

    el_a = _element(db_session, org_a, type_="Requirement", layer="motivation")
    app_b = ApplicationComponent(name=f"App {uuid.uuid4().hex[:8]}", organization_id=org_b.id)
    db_session.add(app_b)
    db_session.flush()

    via_element = Requirement(title="R-element", archimate_element_id=el_a.id, organization_id=None)
    via_app = Requirement(title="R-app", application_component_id=app_b.id, organization_id=None)
    db_session.add_all([via_element, via_app])
    db_session.flush()

    repair_layer_tenancy()

    db_session.refresh(via_element)
    db_session.refresh(via_app)
    assert via_element.organization_id == org_a.id
    assert via_app.organization_id == org_b.id


def test_strategic_recommendations_falls_back_from_capability_to_raters(db_session, make_org):
    from app.models.strategic import StrategicRecommendation

    org_a = make_org("rec-a")
    org_b = make_org("rec-b")
    org_c = make_org("rec-c")
    _drop_not_null(db_session, "strategic_recommendations")

    cap_a = _capability(db_session, org_a)
    user_b = _user(db_session, org_b)
    user_c = _user(db_session, org_c)

    def _rec(**kw):
        return StrategicRecommendation(
            dashboard="capability_health", title="T", description="D", rationale="R",
            priority="MEDIUM", confidence_score=0.5, model_used="m", provider_used="p",
            organization_id=None, **kw,
        )

    via_cap = _rec(capability_id=cap_a.id)
    via_creator = _rec(created_by_id=user_b.id)
    via_rater = _rec(rated_by_id=user_c.id)
    db_session.add_all([via_cap, via_creator, via_rater])
    db_session.flush()

    repair_layer_tenancy()

    db_session.refresh(via_cap)
    db_session.refresh(via_creator)
    db_session.refresh(via_rater)
    assert via_cap.organization_id == org_a.id
    assert via_creator.organization_id == org_b.id
    assert via_rater.organization_id == org_c.id


def test_solution_migration_roadmap_falls_back_from_solution_to_generator(db_session, make_org):
    from app.models.strategic import SolutionMigrationRoadmap

    org_a = make_org("smr-a")
    org_b = make_org("smr-b")
    _drop_not_null(db_session, "solution_migration_roadmaps", "solutions")

    sol_a = _solution(db_session, org_a)
    user_b = _user(db_session, org_b)

    via_solution = SolutionMigrationRoadmap(solution_id=sol_a.id, organization_id=None)
    via_generator = SolutionMigrationRoadmap(
        solution_id=sol_a.id, generated_by_id=user_b.id, organization_id=None
    )
    # via_generator's own solution belongs to org_a, so it resolves to org_a via the
    # solution first -- to actually exercise the generated_by_id fallback, its
    # solution_id must point at something with no organisation to derive from.
    orphan_solution = _solution(db_session, org_a)
    db_session.execute(text(
        "UPDATE solutions SET organization_id = NULL WHERE id = :i"
    ), {"i": orphan_solution.id})
    via_generator.solution_id = orphan_solution.id
    db_session.add_all([via_solution, via_generator])
    db_session.flush()

    # Nulling orphan_solution's own organization_id (above) also makes it an
    # orphan in the solutions table itself -- a side effect of this test's
    # own setup, not something solution_migration_roadmaps resolution needs.
    # solutions has no _DERIVABLE_ORG rule of its own, so on this two-
    # organisation database its one unresolved row is left NULL and reported
    # (_resolve_org_id refuses to guess among more than one active,
    # non-default organisation, with or without --org-id -- passing one here
    # would only raise, since two now exist) -- that deferral is unrelated to
    # this test: the two roadmap rows below are resolved by
    # solution_migration_roadmaps' own _DERIVABLE_ORG rule before the generic
    # resolver is ever consulted for them, as the assertions confirm
    # (via_generator resolves to org_b, not org_a).
    repair_layer_tenancy()

    db_session.refresh(via_solution)
    db_session.refresh(via_generator)
    assert via_solution.organization_id == org_a.id
    assert via_generator.organization_id == org_b.id


def test_enterprise_briefing_resolves_via_generated_by_id_with_no_declared_fk(db_session, make_org):
    from app.models.strategic import EnterpriseBriefing

    org_a = make_org("brief-a")
    _drop_not_null(db_session, "enterprise_briefings")

    user_a = _user(db_session, org_a)
    briefing = EnterpriseBriefing(generated_by_id=user_a.id, organization_id=None)
    db_session.add(briefing)
    db_session.flush()

    repair_layer_tenancy()

    db_session.refresh(briefing)
    assert briefing.organization_id == org_a.id


def test_unattributable_rows_are_quarantined_not_guessed_in_a_multi_org_database(db_session, make_org):
    """A driver with neither archimate_element_id nor created_by_id has no
    provenance at all. With more than one organisation in the database, it
    must stay NULL and be reported -- never handed to one operator-chosen org."""
    from app.models.motivation import Driver

    make_org("orphan-a")
    make_org("orphan-b")
    _drop_not_null(db_session, "drivers")

    orphan = Driver(name="No provenance", organization_id=None)
    db_session.add(orphan)
    db_session.flush()

    stats = repair_layer_tenancy()

    db_session.refresh(orphan)
    assert orphan.organization_id is None
    assert stats["unresolved"].get("drivers", 0) >= 1


def test_hybrid_tenant_mixin_tables_are_nullable_and_not_in_the_tenant_worklist(db_session, make_org):
    """Shared-catalogue tables (framework.py etc.) use HybridTenantMixin, not
    TenantMixin: backfill-layer-tenancy's worklist must not include them, and
    a row with no organisation is the correct, permanent state, not an
    unbackfilled orphan."""
    from app.commands.backfill_layer_tenancy import _tenant_tables
    from app.models.reference_models import ReferenceModel

    assert "reference_model" not in _tenant_tables()

    shared = ReferenceModel(name=f"RM {uuid.uuid4().hex[:8]}", code=f"C{uuid.uuid4().hex[:8]}")
    db_session.add(shared)
    db_session.flush()

    assert shared.organization_id is None

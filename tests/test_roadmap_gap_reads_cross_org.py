"""The roadmap generators read a gap only from the caller's own organisation.

Two real organisations, each with a capability and a gap raised against it. Organisation A's
signed-in user asks the generator route for organisation B's gap, and the synchroniser runs
for organisation A: B's gap is never read, no work package is made from it, and A's own gap
still produces its package.
"""

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

GENERATE = "/api/roadmap/automation/generate-work-packages"


def _user(db_session, org):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="rg-%s@example.com" % uuid.uuid4().hex[:10], first_name="R", last_name="G",
        organization_id=org.id, role=Role.query.filter_by(name="User").first(),
        confirmed=True, enterprise_role="enterprise_architect",
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "architect")
    db_session.flush()
    return user


def _gap(db_session, org, label):
    from app.models.roadmap_models import ImplementationGap
    from app.models.unified_capability import UnifiedCapability

    capability = UnifiedCapability(
        name="Capability %s" % label, code="RG%s" % uuid.uuid4().hex[:8], level=1,
        organization_id=org.id, source_org_id=org.id, status="defined",
    )
    db_session.add(capability)
    db_session.flush()
    gap = ImplementationGap(
        name="Gap %s %s" % (label, uuid.uuid4().hex[:6]), gap_type="technology",
        status="open", source_capability_id=capability.id,
    )
    db_session.add(gap)
    db_session.flush()
    return gap


def _packages_for(gap):
    from app.models.roadmap_models import RoadmapWorkPackage

    return RoadmapWorkPackage.query.filter_by(source_type="gap", source_id=gap.id).count()


def test_generator_route_refuses_another_organisations_gap(db_session, make_org, client, login_as):
    org_a, org_b = make_org("rg-a"), make_org("rg-b")
    user_a = _user(db_session, org_a)
    own, other = _gap(db_session, org_a, "own"), _gap(db_session, org_b, "other")
    login_as(client, user_a)

    response = client.post(GENERATE, json={"source_type": "gap", "source_id": other.id})
    assert response.status_code == 404, response.status_code
    assert other.name not in response.get_data(as_text=True)

    response = client.post(GENERATE, json={"source_type": "gap", "source_id": own.id})
    assert response.status_code == 200, response.status_code
    names = [p["name"] for p in response.get_json()["generated_packages"]]
    assert any(own.name in n for n in names), names


def test_gap_sync_reads_only_the_callers_gaps(db_session, make_org, tenant_ctx):
    from app.modules.solutions_strategic.v2.services.roadmap_sync import RoadmapDataSync

    org_a, org_b = make_org("rg-sa"), make_org("rg-sb")
    own, other = _gap(db_session, org_a, "own"), _gap(db_session, org_b, "other")
    user_a = _user(db_session, org_a)

    with tenant_ctx(org_a.id):
        result = RoadmapDataSync().sync_gaps_to_initiatives(org_a.id, user_a.id)

    assert result["errors"] == [], result["errors"]
    assert _packages_for(other) == 0
    assert _packages_for(own) == 1


def test_resolved_gap_sync_does_not_complete_another_organisations_package(
    db_session, make_org, tenant_ctx
):
    from app.models.roadmap_models import RoadmapWorkPackage
    from app.modules.solutions_strategic.v2.services.roadmap_sync import RoadmapDataSync

    org_a, org_b = make_org("rg-ra"), make_org("rg-rb")
    _gap(db_session, org_a, "own")
    user_a = _user(db_session, org_a)
    other = _gap(db_session, org_b, "other")
    other.status = "resolved"
    package = RoadmapWorkPackage(
        name="Package for other %s" % uuid.uuid4().hex[:6], business_capability="x", status="planned",
        source_type="gap", source_id=other.id, created_by=user_a.id,
    )
    db_session.add(package)
    db_session.flush()

    with tenant_ctx(org_a.id):
        resolved = RoadmapDataSync()._sync_resolved_gaps(org_a.id)

    db_session.refresh(package)
    assert resolved == 0
    assert package.status == "planned"


@pytest.mark.parametrize("module", [
    "app.services.roadmap_automation",
    "app.modules.solutions_strategic.v2.services.roadmap_automation",
])
def test_both_generators_read_a_gap_by_id_only_in_the_callers_organisation(
    db_session, make_org, tenant_ctx, module
):
    import importlib

    from werkzeug.exceptions import NotFound

    engine = importlib.import_module(module).RoadmapAutomationEngine()
    org_a, org_b = make_org("rg-ga"), make_org("rg-gb")
    own, other = _gap(db_session, org_a, "own"), _gap(db_session, org_b, "other")

    with tenant_ctx(org_a.id):
        with pytest.raises(NotFound):
            engine.generate_work_packages("gap", other.id, {})
        packages = engine.generate_work_packages("gap", own.id, {})
    assert any(own.name in p["name"] for p in packages)


def _gap_on(db_session, label, capability=None, application=None, creator=None):
    from app.models.roadmap_models import ImplementationGap

    gap = ImplementationGap(
        name="Gap %s %s" % (label, uuid.uuid4().hex[:6]), gap_type="technology", status="open",
        source_capability_id=capability.id if capability else None,
        source_application_id=application.id if application else None,
        created_by=creator.id if creator else None,
    )
    db_session.add(gap)
    db_session.flush()
    return gap


def _capability(db_session, org, label):
    from app.models.unified_capability import UnifiedCapability

    capability = UnifiedCapability(
        name="Capability %s" % label, code="RG%s" % uuid.uuid4().hex[:8], level=1,
        organization_id=org.id if org else None, source_org_id=org.id if org else None,
        status="defined",
    )
    db_session.add(capability)
    db_session.flush()
    return capability


def _application(db_session, org, label):
    from app.models.application_portfolio import ApplicationComponent

    application = ApplicationComponent(
        name="App %s %s" % (label, uuid.uuid4().hex[:6]), organization_id=org.id)
    db_session.add(application)
    db_session.flush()
    return application


def _readers(gap, org_a, org_b, tenant_ctx):
    from app.models.roadmap_models import ImplementationGap

    seen = []
    for org in (org_a, org_b):
        with tenant_ctx(org.id):
            if ImplementationGap.in_organization(org.id).filter_by(id=gap.id).count():
                seen.append(org.id)
    return seen


def test_a_gap_on_one_organisations_capability_and_anothers_application_is_read_by_the_application_owner(
    db_session, make_org, tenant_ctx
):
    org_a, org_b = make_org("rg-ma"), make_org("rg-mb")
    gap = _gap_on(db_session, "mixed", capability=_capability(db_session, org_a, "a"),
                  application=_application(db_session, org_b, "b"))
    assert _readers(gap, org_a, org_b, tenant_ctx) == [org_b.id]


def test_an_unlinked_gap_belongs_to_its_creators_organisation(db_session, make_org, tenant_ctx):
    org_a, org_b = make_org("rg-ua"), make_org("rg-ub")
    gap = _gap_on(db_session, "unlinked", creator=_user(db_session, org_a))
    assert _readers(gap, org_a, org_b, tenant_ctx) == [org_a.id]


def test_a_gap_on_one_application_is_read_only_by_its_organisation(db_session, make_org, tenant_ctx):
    org_a, org_b = make_org("rg-pa"), make_org("rg-pb")
    gap = _gap_on(db_session, "app", application=_application(db_session, org_a, "a"))
    assert _readers(gap, org_a, org_b, tenant_ctx) == [org_a.id]


def test_the_gap_register_merge_gives_a_gap_the_organisation_that_reads_it(
    db_session, make_org, tenant_ctx
):
    from app.commands.consolidate_gaps import _MERGE_SOURCES
    from app.models.roadmap_models import ROADMAP_GAP_OWNER_SQL

    sql = _MERGE_SOURCES[0]["insert_sql"]
    assert ROADMAP_GAP_OWNER_SQL in sql


@pytest.mark.parametrize("module", [
    "app.services.roadmap_automation",
    "app.modules.solutions_strategic.v2.services.roadmap_automation",
])
def test_a_shared_capability_run_does_not_read_another_organisations_gap(
    db_session, make_org, tenant_ctx, module, monkeypatch
):
    import importlib

    engine = importlib.import_module(module).RoadmapAutomationEngine()
    # The capability path first reads a table named "applications" whose columns no model
    # creates; stand in an empty answer for that one query so the run reaches the gap read.
    module_object = importlib.import_module(module)
    real_text = module_object.text
    monkeypatch.setattr(module_object, "text", lambda sql: real_text(
        "SELECT 1 WHERE false" if "FROM applications a" in str(sql) else sql))
    org_a, org_b = make_org("rg-ca"), make_org("rg-cb")
    shared = _capability(db_session, None, "shared")
    _gap_on(db_session, "theirs", capability=shared, creator=_user(db_session, org_b))
    _gap_on(db_session, "ours", capability=shared, creator=_user(db_session, org_a))

    with tenant_ctx(org_a.id):
        packages = engine.generate_work_packages("capability", shared.id, {})
    assert "with 0 applications and 1 gaps" in packages[0]["description"], packages[0]["description"]


def test_gap_sync_does_not_credit_user_one_for_the_work_package(db_session, make_org, tenant_ctx):
    from app.models.roadmap_models import RoadmapWorkPackage
    from app.modules.solutions_strategic.v2.services.roadmap_sync import RoadmapDataSync

    org_a = make_org("rg-ka")
    gap = _gap(db_session, org_a, "own")

    with tenant_ctx(org_a.id):
        RoadmapDataSync().sync_gaps_to_initiatives(org_a.id, None)

    package = RoadmapWorkPackage.query.filter_by(source_type="gap", source_id=gap.id).one()
    assert package.created_by is None, package.created_by

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
        organization_id=org.id, status="defined",
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

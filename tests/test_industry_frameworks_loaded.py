"""Industry process frameworks are there for every organisation, and adopting a
process writes to that organisation's capability model only."""

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _person(db_session, org, enterprise_role="business_architect", platform_role="Architect"):
    from app.models.user import Role, User

    suffix = uuid.uuid4().hex[:8]
    role = Role.query.filter_by(name=platform_role).first()
    if role is None:
        role = Role(name=platform_role)
        db_session.add(role)
        db_session.flush()
    user = User(
        email="ba-%s@example.com" % suffix, first_name="Ba", last_name="Test",
        organization_id=org.id, confirmed=True, enterprise_role=enterprise_role,
    )
    user.role = role
    db_session.add(user)
    db_session.flush()
    return user


def test_a_new_organisation_sees_every_framework_with_processes(db_session, make_org):
    from app.modules.industry_apqc.framework_data import FRAMEWORKS
    from app.services.industry_apqc_service import IndustryAPQCService

    make_org("fresh")
    summaries = IndustryAPQCService().framework_summaries()
    codes = {s["framework"].industry_code for s in summaries}
    assert codes >= {f["industry_code"] for f in FRAMEWORKS}
    for item in summaries:
        assert item["total"] > 13, item["framework"].industry_code
        assert item["categories"] == 13


def test_the_hierarchy_runs_from_category_to_level_three(db_session, make_org):
    from app.services.industry_apqc_service import IndustryAPQCService

    make_org("tree")
    tree = IndustryAPQCService().build_hierarchy("MFG")
    assert [n["code"] for n in tree[:3]] == ["1.0", "2.0", "3.0"]
    assert [n["code"] for n in tree if not n["specific"]][-1] == "13.0"
    category = tree[0]
    group = next(c for c in category["children"] if c["code"] == "1.1")
    assert [c["code"] for c in group["children"]][:2] == ["1.1.1", "1.1.2"]
    assert {c["level"] for c in group["children"]} == {3}
    production = next(n for n in tree if n["code"] == "4.0")
    assert any(c["specific"] and c["name"] == "Shop floor management"
               for c in production["children"])


def test_loading_twice_adds_nothing(db_session, make_org):
    from app import db
    from app.models.industry_apqc import IndustryAPQCProcess
    from app.modules.industry_apqc.framework_data import load_default_frameworks

    make_org("twice")
    before = IndustryAPQCProcess.query.count()
    assert load_default_frameworks(db.session.connection()) == 0
    assert IndustryAPQCProcess.query.count() == before


def test_recommendations_address_opens_the_tab(db_session, make_org, client, login_as):
    org = make_org("redirect")
    user = _person(db_session, org)
    login_as(client, user)
    resp = client.get("/industry-apqc/recommendations?industry=BFS")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/industry-apqc/framework/BFS?tab=recommendations")
    page = client.get("/industry-apqc/framework/BFS?tab=recommendations")
    assert page.status_code == 200
    assert b"Credit risk modeling" in page.data
    assert b"Seed" not in page.data


def test_adopting_a_process_lands_in_that_organisations_capability_model_only(
    db_session, make_org, client, login_as
):
    from app.models.unified_capability import UnifiedCapability
    from app.models.industry_apqc import IndustryAPQCProcess

    org_a, org_b = make_org("adopt-a"), make_org("adopt-b")
    ba = _person(db_session, org_a)
    login_as(client, ba)
    process = IndustryAPQCProcess.query.filter_by(industry_process_name="Credit risk modeling").first()

    resp = client.post("/industry-apqc/api/frameworks/BFS/processes/%d/adopt" % process.id)
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body["success"] and body["created"]

    def held(org):
        return UnifiedCapability.query.filter(
            UnifiedCapability.organization_id == org.id,
            UnifiedCapability.name == "Credit risk modeling",
        ).count()

    assert held(org_a) == 1
    assert held(org_b) == 0
    again = client.post("/industry-apqc/api/frameworks/BFS/processes/%d/adopt" % process.id)
    assert again.get_json()["created"] is False
    assert held(org_a) == 1

    # The other organisation's recommendations still offer it.
    from app.services.industry_apqc_service import IndustryAPQCService

    service = IndustryAPQCService()
    mine = {r["process"].industry_process_name: r["adopted"]
            for r in service.recommended_processes("BFS", org_a.id)}
    theirs = {r["process"].industry_process_name: r["adopted"]
              for r in service.recommended_processes("BFS", org_b.id)}
    assert mine["Credit risk modeling"] is True
    assert theirs["Credit risk modeling"] is False


def test_a_viewer_cannot_adopt(db_session, make_org, client, login_as):
    from app.models.industry_apqc import IndustryAPQCProcess

    org = make_org("viewer")
    viewer = _person(db_session, org, enterprise_role="procurement", platform_role="Viewer")
    login_as(client, viewer)
    process = IndustryAPQCProcess.query.filter_by(industry_process_name="Credit risk modeling").first()
    resp = client.post("/industry-apqc/api/frameworks/BFS/processes/%d/adopt" % process.id)
    assert resp.status_code in (401, 403)

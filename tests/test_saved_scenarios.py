"""Saving a scenario is for people who design, and keeps only figures the server worked out.

``POST /strategic/api/scenarios`` writes a row every colleague in the organisation lists and compares. A
viewer is refused, the severity and cost kept are the server's own whatever the body claims, and a body that
is not the shape the screen draws is refused. Another organisation never lists the row.
"""

from __future__ import annotations

import uuid

import pytest

from app import db

pytestmark = pytest.mark.usefixtures("db_session")

URL = "/strategic/api/scenarios"


def _user(db_session, org, enterprise_role):
    from werkzeug.security import generate_password_hash

    from app.models.user import User

    user = User(
        email=f"scn-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Scenario",
        last_name="Tester",
        confirmed=True,
        organization_id=org.id,
        enterprise_role=enterprise_role,
        password_hash=generate_password_hash("x"),
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def world(db_session, make_org):
    from app.models import ArchiMateElement

    org_a, org_b = make_org("scn-a"), make_org("scn-b")
    element = ArchiMateElement(
        name="Ledger %s" % uuid.uuid4().hex[:6], type="ApplicationComponent", layer="application",
        organization_id=org_a.id)
    db_session.add(element)
    db_session.flush()
    return {
        "org": org_a,
        "element": element,
        "architect": _user(db_session, org_a, "solution_architect"),
        "viewer": _user(db_session, org_a, "business_user"),
        "other": _user(db_session, org_b, "solution_architect"),
    }


def _body(element, **figures):
    result = {"severity": "LOW", "estimated_risk_cost": 1, "impacted_capabilities": [], "impacted_processes": [],
              "dependencies": [], "indirect_dependencies": [], "summary": "s", "financial_risk_reason": ""}
    result.update(figures)
    return {"name": "Retire it", "change_type": "RETIRE", "element": {"id": element.id}, "analysis_result": result}


def _count(db_session, org):
    """Rows kept for the test's own organisation, so rows other journeys left in the shared database do not count."""
    return db_session.execute(
        db.text("select count(*) from saved_scenarios where organization_id = :o"), {"o": org.id}).scalar_one()


def test_a_viewer_cannot_save_a_scenario(client, login_as, db_session, world):
    login_as(client, world["viewer"])
    before = _count(db_session, world["org"])
    response = client.post(URL, json=_body(world["element"]))
    assert response.status_code == 403, response.get_data(as_text=True)[:300]
    assert _count(db_session, world["org"]) == before


def test_an_architect_saves_and_the_server_keeps_its_own_severity_and_cost(client, login_as, db_session, world):
    from app.modules.solutions_strategic.v2.services.impact_analysis_service import ImpactAnalysisService

    login_as(client, world["architect"])
    claimed = _body(world["element"], severity="LOW", estimated_risk_cost=1)
    response = client.post(URL, json=claimed)
    assert response.status_code == 201, response.get_data(as_text=True)[:300]
    kept = response.get_json()["analysis_result"]
    server = ImpactAnalysisService.analyze_change_impact(world["element"].id, "RETIRE", record=False)
    assert kept["severity"] == server["risk_level"]
    assert kept["estimated_risk_cost"] == server["estimated_financial_risk"]
    assert kept["estimated_risk_cost"] != 1


def test_a_body_that_is_not_an_analysis_is_refused(client, login_as, db_session, world):
    login_as(client, world["architect"])
    before = _count(db_session, world["org"])
    body = _body(world["element"])
    body["analysis_result"] = "not an analysis"
    response = client.post(URL, json=body)
    assert response.status_code == 400, response.get_data(as_text=True)[:300]
    assert _count(db_session, world["org"]) == before


@pytest.mark.parametrize("field,value", [
    ("dependencies", "not a list"),
    ("impacted_capabilities", ["a string row"]),
    ("summary", {"not": "text"}),
])
def test_what_the_browser_sends_for_the_lists_is_ignored(client, login_as, db_session, world, field, value):
    login_as(client, world["architect"])
    response = client.post(URL, json=_body(world["element"], **{field: value}))
    assert response.status_code == 201, response.get_data(as_text=True)[:300]
    kept = response.get_json()["analysis_result"]
    assert kept["dependencies"] == [] and kept["impacted_capabilities"] == []
    assert "summary" not in kept


def test_the_counts_kept_are_the_servers_not_the_ones_sent(client, login_as, db_session, world):
    """An element with nothing reaching it, saved with seven capabilities and nine dependencies claimed."""
    login_as(client, world["architect"])
    claim = [{"id": i, "name": "claimed %d" % i, "type": "BusinessCapability"} for i in range(9)]
    response = client.post(URL, json=_body(
        world["element"], impacted_capabilities=claim[:7], dependencies=claim, indirect_dependencies=claim))
    assert response.status_code == 201, response.get_data(as_text=True)[:300]
    kept = response.get_json()["analysis_result"]
    assert (kept["impacted_capabilities"], kept["impacted_processes"], kept["dependencies"],
            kept["indirect_dependencies"]) == ([], [], [], [])


def test_the_capabilities_and_processes_kept_are_the_ones_the_server_reached(client, login_as, db_session, world):
    from app.models import ArchiMateElement
    from app.models.archimate_core import ArchiMateRelationship

    org_id = world["architect"].organization_id
    cap = ArchiMateElement(name="Cap", type="BusinessCapability", layer="business", organization_id=org_id)
    proc = ArchiMateElement(name="Proc", type="BusinessProcess", layer="business", organization_id=org_id)
    db_session.add_all([cap, proc])
    db_session.flush()
    for target in (cap, proc):
        db_session.add(ArchiMateRelationship(
            source_id=world["element"].id, target_id=target.id, type="serving", organization_id=org_id))
    db_session.flush()
    login_as(client, world["architect"])
    response = client.post(URL, json=_body(world["element"]))
    assert response.status_code == 201, response.get_data(as_text=True)[:300]
    kept = response.get_json()["analysis_result"]
    assert [r["name"] for r in kept["impacted_capabilities"]] == ["Cap"]
    assert [r["name"] for r in kept["impacted_processes"]] == ["Proc"]
    assert len(kept["dependencies"]) == 2


def test_the_page_and_the_route_read_one_permission(app, db_session, world, make_org):
    """The Save control is shown to exactly the people the route admits."""
    from app.utils.rbac import user_has_role
    from app.modules.solutions_strategic.v2.routes.strategic_routes import SCENARIO_SAVE_ROLES

    allowed = {role: user_has_role(_user(db_session, make_org("role"), role), *SCENARIO_SAVE_ROLES) for role in
               ("solution_architect", "enterprise_architect", "arb_member", "portfolio_manager",
                "business_architect", "security_architect", "data_architect", "cto", "application_manager")}
    assert {r for r, ok in allowed.items() if ok} == {
        "solution_architect", "enterprise_architect", "arb_member", "portfolio_manager"}


def test_the_save_control_is_shown_only_to_a_person_the_route_admits(client, login_as, db_session, world, make_org):
    data_architect = _user(db_session, make_org("da"), "data_architect")
    for user, shown in ((world["architect"], True), (data_architect, False), (world["viewer"], False)):
        login_as(client, user)
        for path in ("/strategic/impact-analysis", "/simulations"):
            page = client.get(path).get_data(as_text=True)
            assert ('data-testid="save-scenario"' in page) is shown, (user.enterprise_role, path)
            assert ('data-testid="save-scenario-not-allowed"' in page) is (not shown)


def test_another_organisation_does_not_list_the_scenario(client, login_as, db_session, world):
    login_as(client, world["architect"])
    assert client.post(URL, json=_body(world["element"])).status_code == 201
    login_as(client, world["other"])
    listed = client.get(URL)
    assert listed.status_code == 200
    assert listed.get_json() == []


def test_a_colleague_lists_what_an_architect_saved(client, login_as, db_session, world, make_org):
    login_as(client, world["architect"])
    assert client.post(URL, json=_body(world["element"])).status_code == 201
    colleague = _user(db_session, world["org"], "data_architect")
    login_as(client, colleague)
    listed = client.get(URL)
    assert listed.status_code == 200, listed.get_data(as_text=True)[:300]
    assert [r["name"] for r in listed.get_json()] == ["Retire it"]

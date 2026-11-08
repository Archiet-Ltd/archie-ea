"""Saving a scenario is for people who design, and keeps only figures the server worked out.

``POST /strategic/api/scenarios`` writes a row every colleague in the organisation lists and compares. A
viewer is refused, the severity and cost kept are the server's own whatever the body claims, and a body that
is not the shape the screen draws is refused. Another organisation never lists the row.
"""

from __future__ import annotations

import uuid

import pytest

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


def _count(db_session):
    from app.models.saved_scenario import SavedScenario

    return db_session.query(SavedScenario).count()


def test_a_viewer_cannot_save_a_scenario(client, login_as, db_session, world):
    login_as(client, world["viewer"])
    before = _count(db_session)
    response = client.post(URL, json=_body(world["element"]))
    assert response.status_code == 403, response.get_data(as_text=True)[:300]
    assert _count(db_session) == before


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


@pytest.mark.parametrize("field,value", [
    ("dependencies", "not a list"),
    ("impacted_capabilities", ["a string row"]),
    ("summary", {"not": "text"}),
])
def test_an_analysis_that_is_not_screen_shaped_is_refused(client, login_as, db_session, world, field, value):
    login_as(client, world["architect"])
    before = _count(db_session)
    response = client.post(URL, json=_body(world["element"], **{field: value}))
    assert response.status_code == 400, response.get_data(as_text=True)[:300]
    assert _count(db_session) == before


def test_another_organisation_does_not_list_the_scenario(client, login_as, db_session, world):
    login_as(client, world["architect"])
    assert client.post(URL, json=_body(world["element"])).status_code == 201
    login_as(client, world["other"])
    listed = client.get(URL)
    assert listed.status_code == 200
    assert listed.get_json() == []

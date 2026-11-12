"""Impact Analysis and the portfolio impact reply follow the one cost rule the simulation follows.

A person the rule withholds cost from reads no running cost in any reply and is told in a plain sentence why;
a person the rule admits reads the figure the analysis worked out. Another organisation's costs reach nobody.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

ONE = "/strategic/api/impact-analysis"
PORTFOLIO = "/strategic/api/portfolio-impact"
CONTRACT = "/api/v1/impact/analyze"
COST = 1234567.0
OTHER_COST = 7654321.0


def _user(db_session, org, enterprise_role):
    from werkzeug.security import generate_password_hash

    from app.models.user import User

    user = User(email="gate-%s@example.com" % uuid.uuid4().hex[:10], first_name="Gate", last_name="Tester",
                confirmed=True, organization_id=org.id, enterprise_role=enterprise_role,
                password_hash=generate_password_hash("x"))
    db_session.add(user)
    db_session.flush()
    return user


def _application(db_session, org, name, cost):
    from app.models import ArchiMateElement
    from app.models.application_portfolio import ApplicationComponent

    element = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org.id)
    db_session.add(element)
    db_session.flush()
    db_session.add(ApplicationComponent(name=name, organization_id=org.id, archimate_element_id=element.id,
                                        total_cost_of_ownership=cost, lifecycle_status="3. Production"))
    db_session.flush()
    return element


@pytest.fixture
def world(db_session, make_org):
    org_a, org_b = make_org("gate-a"), make_org("gate-b")
    return {
        "app": _application(db_session, org_a, "Ledger %s" % uuid.uuid4().hex[:6], COST),
        "other_app": _application(db_session, org_b, "Ledger %s" % uuid.uuid4().hex[:6], OTHER_COST),
        "architect": _user(db_session, org_a, "solution_architect"),
        "cto": _user(db_session, org_a, "cto"),
        "other_viewer": _user(db_session, org_b, "business_user"),
        "other_cto": _user(db_session, org_b, "cto"),
    }


def _holds_no_cost(text):
    return not any(n in text for n in (str(int(COST)), str(int(OTHER_COST)), "1,234,567", "7,654,321"))


def _one(client, element):
    reply = client.post(ONE, json={"element_id": element.id, "change_type": "RETIRE"})
    assert reply.status_code == 200, reply.get_data(as_text=True)[:300]
    return reply


def _portfolio(client, element):
    reply = client.post(PORTFOLIO, json={"change_scenarios": [{"element_id": element.id, "change_type": "RETIRE"}]})
    assert reply.status_code == 200, reply.get_data(as_text=True)[:300]
    return reply


def test_impact_analysis_leaves_the_cost_out_for_the_architect_the_rule_withholds_from(client, login_as, world):
    login_as(client, world["architect"])
    reply = _one(client, world["app"])
    assert _holds_no_cost(reply.get_data(as_text=True)), reply.get_data(as_text=True)[:500]
    body = reply.get_json()
    assert body["estimated_financial_risk"] is None
    assert "withheld for your role" in body["financial_risk_reason"]
    assert body["costs_withheld"] is True
    assert body["changed_application"]["annual_cost"] is None
    assert all(a["annual_cost"] is None for a in body["reached_applications"])


def test_impact_analysis_gives_the_cost_to_the_cto(client, login_as, world):
    login_as(client, world["cto"])
    body = _one(client, world["app"]).get_json()
    assert body["estimated_financial_risk"] == COST
    assert body["changed_application"]["annual_cost"] == COST
    assert not body.get("costs_withheld")


def test_portfolio_impact_leaves_the_cost_out_for_the_architect_and_gives_it_to_the_cto(client, login_as, world):
    login_as(client, world["architect"])
    reply = _portfolio(client, world["app"])
    assert _holds_no_cost(reply.get_data(as_text=True)), reply.get_data(as_text=True)[:500]
    impact = reply.get_json()["individual_impacts"][0]
    assert impact["estimated_financial_risk"] is None
    assert "withheld for your role" in impact["financial_risk_reason"]
    assert impact["changed_application"]["annual_cost"] is None
    login_as(client, world["cto"])
    admitted = _portfolio(client, world["app"]).get_json()["individual_impacts"][0]
    assert admitted["estimated_financial_risk"] == COST
    assert admitted["changed_application"]["annual_cost"] == COST


def test_the_contract_reply_leaves_the_cost_out_for_the_architect_and_gives_it_to_the_cto(client, login_as, world):
    body = {"element_id": world["app"].id, "scenario": "retirement"}
    login_as(client, world["architect"])
    reply = client.post(CONTRACT, json=body)
    assert reply.status_code == 200, reply.get_data(as_text=True)[:300]
    assert _holds_no_cost(reply.get_data(as_text=True)), reply.get_data(as_text=True)[:500]
    assert reply.get_json()["data"]["breakdown"]["estimated_financial_risk"] is None
    login_as(client, world["cto"])
    admitted = client.post(CONTRACT, json=body).get_json()["data"]["breakdown"]
    assert admitted["estimated_financial_risk"] == COST


def test_another_organisation_reads_none_of_the_costs(client, login_as, world):
    login_as(client, world["other_viewer"])
    withheld = _one(client, world["other_app"]).get_json()
    assert withheld["estimated_financial_risk"] is None
    assert _holds_no_cost(client.post(ONE, json={"element_id": world["app"].id, "change_type": "RETIRE"}).get_data(as_text=True))
    login_as(client, world["other_cto"])
    own = _one(client, world["other_app"]).get_json()
    assert own["estimated_financial_risk"] == OTHER_COST
    assert str(int(COST)) not in client.post(
        ONE, json={"element_id": world["app"].id, "change_type": "RETIRE"}).get_data(as_text=True)
    assert str(int(COST)) not in _portfolio(client, world["other_app"]).get_data(as_text=True)

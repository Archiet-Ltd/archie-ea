"""The capability heatmap works out investment only for a person the one cost rule allows.

An architect the rule withholds from reads the maturity view, no investment figure in the reply or on the
page, and one plain sentence saying why; the chief technology officer reads the investment view.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

API = "/dashboard/api/capability-heatmap?group_by=domain"
PAGE = "/dashboard/capability-heatmap"
SENTENCE = "Cost figures are withheld for your role."


def _user(db_session, org, enterprise_role):
    from werkzeug.security import generate_password_hash

    from app.models.user import User

    user = User(email="heat-%s@example.com" % uuid.uuid4().hex[:10], first_name="Heat", last_name="Tester",
                confirmed=True, organization_id=org.id, enterprise_role=enterprise_role,
                password_hash=generate_password_hash("x"))
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def world(db_session, make_org):
    org_a, org_b = make_org("heat-a"), make_org("heat-b")
    return {"architect": _user(db_session, org_a, "solution_architect"), "cto": _user(db_session, org_a, "cto"),
            "other_architect": _user(db_session, org_b, "solution_architect")}


def test_the_reply_holds_no_investment_for_the_architect_and_says_why(client, login_as, world):
    login_as(client, world["architect"])
    reply = client.get(API)
    assert reply.status_code == 200, reply.get_data(as_text=True)[:300]
    data = reply.get_json()["data"]
    assert data["costs_withheld"] == SENTENCE
    assert "investment_summary" not in data
    assert not any("total_investment" in d for d in data.get("domains", []))


def test_the_reply_holds_the_investment_summary_for_the_chief_technology_officer(client, login_as, world):
    login_as(client, world["cto"])
    reply = client.get(API)
    assert reply.status_code == 200, reply.get_data(as_text=True)[:300]
    data = reply.get_json()["data"]
    assert "investment_summary" in data
    assert "costs_withheld" not in data


def test_the_page_offers_the_architect_no_investment_view_and_says_why_once(client, login_as, world):
    login_as(client, world["architect"])
    text = client.get(PAGE).get_data(as_text=True)
    assert text.count(SENTENCE) == 1
    assert "With Investment" not in text


def test_the_page_offers_the_chief_technology_officer_the_investment_view(client, login_as, world):
    login_as(client, world["cto"])
    text = client.get(PAGE).get_data(as_text=True)
    assert "With Investment" in text
    assert SENTENCE not in text


def test_an_architect_of_another_organisation_is_withheld_from_too(client, login_as, world):
    login_as(client, world["other_architect"])
    data = client.get(API).get_json()["data"]
    assert data["costs_withheld"] == SENTENCE and "investment_summary" not in data

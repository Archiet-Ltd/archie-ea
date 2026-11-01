"""A risk put in money with the Open FAIR method: the figure is stable, is one cost fact
that the risk page, the application page and the cited answer all read, and never crosses
organisations."""

import re
import uuid
from decimal import Decimal

import pytest

FACTORS = {
    "frequency": ["1", "2", "4"],
    "vulnerability": ["0.2", "0.4", "0.6"],
    "direct_loss": ["10000", "50000", "200000"],
    "knock_on_loss": ["5000", "20000", "100000"],
}


def _user(db_session, org, role="enterprise_architect"):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(email="of-%s@example.com" % uuid.uuid4().hex[:10], first_name="Ada", last_name="Lovelace",
                organization_id=org.id, confirmed=True, enterprise_role=role)
    user.role = Role.query.filter_by(name="Architect").one()
    db_session.add(user)
    db_session.flush()
    return user


def _world(db_session, make_org, title="Billing vendor outage"):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.risk import Risk
    from app.models.risk_entity_link import RiskEntityLink

    org = make_org("fair")
    user = _user(db_session, org)
    billing = ApplicationComponent(name="Billing %s" % uuid.uuid4().hex[:6], organization_id=org.id)
    risk = Risk(title=title, likelihood=3, impact=3, organization_id=org.id)
    db_session.add_all([billing, risk])
    db_session.flush()
    db_session.add(RiskEntityLink(organization_id=org.id, risk_id=risk.id, entity_type="application",
                                  entity_id=billing.id))
    db_session.flush()
    return org, user, billing, risk


def test_same_inputs_give_the_same_figure():
    from app.services import open_fair

    factors = open_fair.parse_factors(FACTORS)
    first = open_fair.simulate(factors)
    assert first == open_fair.simulate(factors)
    assert 0 < first["low"] < first["likely"] < first["high"]


def test_a_range_out_of_order_is_refused_in_plain_words():
    from app.services import open_fair

    bad = dict(FACTORS, vulnerability=["0.5", "0.4", "0.6"])
    with pytest.raises(open_fair.FactorError, match="most likely value must sit between"):
        open_fair.parse_factors(bad)
    with pytest.raises(open_fair.FactorError, match="share from 0 to 1"):
        open_fair.parse_factors(dict(FACTORS, vulnerability=["0.5", "0.6", "1.5"]))


def test_risk_page_application_page_and_answer_show_the_identical_figure(
        db_session, make_org, tenant_ctx):
    from app.modules.intelligence.services.query_catalogue import run_entry
    from app.services import open_fair
    from app.services.application_fact_sheet import build_fact_sheet

    org, user, billing, risk = _world(db_session, make_org)
    with tenant_ctx(org.id):
        open_fair.save_assessment(org.id, risk.id, FACTORS, user.id)
        loss = open_fair.losses_for_risks(org.id, [risk.id])[risk.id]
        words = open_fair.describe(loss)

        sheet = build_fact_sheet(billing)
        assert sheet["risk_losses"][risk.id] == words

        answer = run_entry("risk_cost", org.id, risk="billing vendor")
        assert answer["total"] == 1
        row = answer["rows"][0]
        assert (row["yearly_loss_likely"], row["yearly_loss_range"]) == (words["likely"], words["range"])
        assert words["likely"] in row["source_line"] and words["date"] in row["source_line"]
        assert "Ada Lovelace" in row["source_line"] and "Open FAIR" in row["source_line"]
        assert answer["citations"][0]["id"] == risk.id


def test_saving_again_replaces_the_figure_and_keeps_one_fact_each(db_session, make_org, tenant_ctx):
    from app.models.cost_fact import CostFact
    from app.services import open_fair

    org, user, _billing, risk = _world(db_session, make_org)
    with tenant_ctx(org.id):
        open_fair.save_assessment(org.id, risk.id, FACTORS, user.id)
        before = open_fair.losses_for_risks(org.id, [risk.id])[risk.id]["likely"]
        bigger = dict(FACTORS, direct_loss=["100000", "500000", "900000"])
        open_fair.save_assessment(org.id, risk.id, bigger, user.id)
        after = open_fair.losses_for_risks(org.id, [risk.id])[risk.id]["likely"]
        assert after > before
        assert CostFact.query.filter_by(organization_id=org.id, element_type="risk",
                                        element_id=risk.id).count() == 3


def test_another_organisation_never_sees_the_figure(db_session, make_org, tenant_ctx):
    from app.modules.intelligence.services.query_catalogue import run_entry
    from app.services import open_fair

    org_a, user_a, billing_a, risk_a = _world(db_session, make_org)
    org_b, _user_b, billing_b, risk_b = _world(db_session, make_org)
    with tenant_ctx(org_a.id):
        open_fair.save_assessment(org_a.id, risk_a.id, FACTORS, user_a.id)
    with tenant_ctx(org_b.id):
        assert open_fair.losses_for_risks(org_b.id, [risk_a.id, risk_b.id]) == {}
        assert open_fair.losses_for_application(org_b.id, billing_b.id) == []
        assert open_fair.losses_for_application(org_b.id, billing_a.id) == []
        with pytest.raises(LookupError):
            open_fair.save_assessment(org_b.id, risk_a.id, FACTORS, None)
        answer = run_entry("risk_cost", org_b.id, risk="billing vendor")
        assert answer["rows"] == [] and "no yearly loss" in answer["reason"]
        with pytest.raises(PermissionError):
            open_fair.losses_for_risks(org_a.id, [risk_a.id])


def test_the_chat_question_reaches_the_entry_and_names_the_risk(db_session, make_org, tenant_ctx):
    from app.modules.intelligence.services.nl_query_interpreter import interpret

    found = interpret("What does the billing vendor risk cost us?")
    assert found["entry_id"] == "risk_cost"
    assert found["params"] == {"risk": "billing vendor"}


def test_a_risk_with_no_figure_says_so_and_offers_the_next_step(db_session, make_org, tenant_ctx):
    from app.modules.intelligence.services.query_catalogue import run_entry

    org, _user_, _billing, _risk = _world(db_session, make_org)
    with tenant_ctx(org.id):
        answer = run_entry("risk_cost", org.id, risk="billing vendor")
    assert answer["rows"] == []
    assert answer["reason"] == "no yearly loss has been worked out for 'Billing vendor outage' yet"
    assert "enter the four factors" in answer["next_step"]
    assert not re.search(r"\d", answer["reason"])

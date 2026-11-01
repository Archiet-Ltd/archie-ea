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


@pytest.mark.parametrize("question", [
    "What do our risks cost us each year?",
    "What do my risks cost us?",
    "How much do all risks cost?",
    "What do the risks cost each year?",
])
def test_a_question_about_our_risks_names_no_single_risk(question):
    from app.modules.intelligence.services.nl_query_interpreter import interpret

    found = interpret(question)
    assert found["entry_id"] == "risk_cost"
    assert not found["params"].get("risk")


def test_what_do_our_risks_cost_lists_every_costed_risk_with_its_source(db_session, make_org, tenant_ctx):
    from app.models.risk import Risk
    from app.modules.intelligence.services.nl_query_interpreter import interpret
    from app.modules.intelligence.services.query_catalogue import run_entry
    from app.services import open_fair

    org, user, _billing, risk = _world(db_session, make_org)
    second = Risk(title="Supplier outage", likelihood=2, impact=4, organization_id=org.id)
    unpriced = Risk(title="Data centre flood", likelihood=1, impact=5, organization_id=org.id)
    db_session.add_all([second, unpriced])
    db_session.flush()
    with tenant_ctx(org.id):
        open_fair.save_assessment(org.id, risk.id, FACTORS, user.id)
        open_fair.save_assessment(org.id, second.id, FACTORS, user.id)
        found = interpret("What do our risks cost us each year?")
        answer = run_entry(found["entry_id"], org.id, **found["params"])
    assert {row["id"] for row in answer["rows"]} == {risk.id, second.id}
    assert answer["total"] == 2
    for row in answer["rows"]:
        assert row["yearly_loss_likely"] and row["yearly_loss_range"] and "Open FAIR" in row["source_line"]
    assert answer["answer"] == "what our risks cost each year"
    assert {c["id"] for c in answer["citations"]} == {risk.id, second.id}


def test_what_does_the_supplier_outage_risk_cost_names_that_risk(db_session, make_org, tenant_ctx):
    from app.models.risk import Risk
    from app.modules.intelligence.services.nl_query_interpreter import interpret
    from app.modules.intelligence.services.query_catalogue import run_entry
    from app.services import open_fair

    org, user, _billing, risk = _world(db_session, make_org)
    second = Risk(title="Supplier outage", likelihood=2, impact=4, organization_id=org.id)
    db_session.add(second)
    db_session.flush()
    with tenant_ctx(org.id):
        open_fair.save_assessment(org.id, risk.id, FACTORS, user.id)
        open_fair.save_assessment(org.id, second.id, FACTORS, user.id)
        found = interpret("what does the supplier outage risk cost")
        assert found["params"] == {"risk": "supplier outage"}
        answer = run_entry(found["entry_id"], org.id, **found["params"])
    assert [row["id"] for row in answer["rows"]] == [second.id]


def test_a_name_that_matches_nothing_gets_a_plain_answer_saying_so(db_session, make_org, tenant_ctx):
    from app.modules.intelligence.services.query_catalogue import run_entry

    org, _user_, _billing, _risk = _world(db_session, make_org)
    with tenant_ctx(org.id):
        answer = run_entry("risk_cost", org.id, risk="zebra crossing")
    assert answer["rows"] == []
    assert answer["reason"] == "nothing recorded matches 'zebra crossing'"
    assert answer["next_step"]


def test_a_name_matching_several_risks_lists_each_one(db_session, make_org, tenant_ctx):
    from app.models.risk import Risk
    from app.modules.intelligence.services.query_catalogue import run_entry
    from app.services import open_fair

    org, user, _billing, risk = _world(db_session, make_org, title="Billing vendor outage")
    other = Risk(title="Billing vendor insolvency", likelihood=2, impact=4, organization_id=org.id)
    third = Risk(title="Billing vendor breach", likelihood=2, impact=4, organization_id=org.id)
    db_session.add_all([other, third])
    db_session.flush()
    with tenant_ctx(org.id):
        open_fair.save_assessment(org.id, risk.id, FACTORS, user.id)
        open_fair.save_assessment(org.id, other.id, FACTORS, user.id)
        answer = run_entry("risk_cost", org.id, risk="billing vendor")
        none_priced = run_entry("risk_cost", org.id, risk="insolvency billing")
    assert {row["id"] for row in answer["rows"]} == {risk.id, other.id}
    assert "Billing vendor breach" in answer["reason"]
    assert none_priced["total"] == 1

    org2, _u, _b, _r = _world(db_session, make_org, title="Card vendor outage")
    db_session.add(Risk(title="Card vendor breach", likelihood=2, impact=4, organization_id=org2.id))
    db_session.flush()
    with tenant_ctx(org2.id):
        unpriced = run_entry("risk_cost", org2.id, risk="card vendor")
    assert unpriced["rows"] == []
    assert "Card vendor outage" in unpriced["reason"] and "Card vendor breach" in unpriced["reason"]
    assert "next_url" not in unpriced


def test_the_evidence_footer_says_knock_on_loss_is_counted_on_every_event():
    from app.services import open_fair

    loss = {"likely": 1, "low": 1, "high": 2, "currency": "GBP", "entered_at": None}
    assert "knock-on loss counted on every loss event" in open_fair.describe(loss)["method"]

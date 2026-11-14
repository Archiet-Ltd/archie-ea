"""The one masker and the one list of money names in app/services/cost_visibility.py.

Written from one goal: a person the cost rule withholds from never receives an amount, however the amount is
named in a reply or written into a sentence, and never loses a figure that is not money.
"""
import pytest

from app.services.cost_visibility import (
    COSTS_WITHHELD,
    is_money_key,
    without_amounts,
    withhold_costs,
    withhold_evidence_trail,
)


@pytest.mark.parametrize("text", [
    "est. £612,000 a year",
    "about $12,500 in licences",
    "€40000 for the first year",
    "GBP 40,000 for the contract",
    "USD 12,500 a seat",
    "EUR40000 up front",
    "40 000 EUR sign on",
    "1.5 million pounds over three years",
    "2 million dollars of spend",
    "40,000 pounds",
])
def test_every_written_amount_is_replaced(text):
    out = without_amounts(text)
    assert "a cost figure" in out
    assert not any(char.isdigit() for char in out), out


@pytest.mark.parametrize("text", ["12 users on 3 sites", "ready by 2026", "version 2.5 of the plan", "40 000 words"])
def test_a_number_that_is_not_money_is_kept(text):
    assert without_amounts(text) == text


@pytest.mark.parametrize("name", [
    "estimated_cost", "investment_required", "amount", "budget_spent", "budget_allocated", "budget_remaining",
    "spent_to_date", "annual_savings", "total_tco", "revenue", "capex", "payback_months", "roi_percentage",
    "totalCost", "license_cost_annual", "forecast_cost", "estimated_savings",
])
def test_a_name_that_says_money_is_money(name):
    assert is_money_key(name)


@pytest.mark.parametrize("name", [
    "total_estimated_hours", "component_scores", "score_weights", "application_count", "cost_centre",
    "costs_withheld", "costs_withheld_sentence", "name", "status", "work_package_id", "estimated_days",
])
def test_a_name_that_is_not_money_is_kept(name):
    assert not is_money_key(name)


def test_the_masker_leaves_out_every_amount_at_any_depth_and_keeps_the_rest():
    reply = {"success": True, "name": "Move", "total_estimated_hours": 120, "score_weights": {"cost": 1},
             "rows": [{"id": 4, "investment_required": 557731, "amount": 336611, "note": "costs £5,000 a year"}],
             "summary": {"total_tco": 90, "count": 3}}
    out = withhold_costs(reply)
    assert out["rows"][0]["investment_required"] is None and out["rows"][0]["amount"] is None
    assert out["summary"]["total_tco"] is None and out["summary"]["count"] == 3
    assert out["total_estimated_hours"] == 120 and out["name"] == "Move"
    assert "5,000" not in out["rows"][0]["note"]
    assert out["costs_withheld"] is True and out["costs_withheld_sentence"] == COSTS_WITHHELD
    assert reply["rows"][0]["investment_required"] == 557731, "the stored reply is not changed"


def test_the_evidence_trail_loses_its_cost_factors_amount_and_the_amounts_in_its_text():
    trail = {"factors": [{"factor": "Annual Cost (TCO)", "raw_value": 482917, "rationale": "pays £482,917 a year"},
                         {"factor": "Users", "raw_value": 40, "rationale": "40 active users"}]}
    out = withhold_evidence_trail(trail)
    cost, users = out["factors"]
    assert cost["raw_value"] is None and "482" not in cost["rationale"]
    assert users["raw_value"] == 40 and users["rationale"] == "40 active users"
    assert out["costs_withheld_sentence"] == COSTS_WITHHELD


# ---- the helper a route calls on the reply it is about to send, as a signed-in person ----

def _signed_in(db_session, make_org, role):
    import uuid

    from app.models.user import User

    org = make_org("mask-" + uuid.uuid4().hex[:6])
    user = User(email="mask-%s@example.com" % uuid.uuid4().hex[:8], first_name="M", last_name="P",
                organization_id=org.id, confirmed=True, enterprise_role=role)
    user.password = "Sup3rSecret!23"
    db_session.add(user)
    db_session.flush()
    return user


def _reply_of(app, user):
    from flask_login import login_user

    from app.services.cost_visibility import withhold_reply

    reply = {"success": True, "total_estimated_hours": 120, "component_scores": {"fit": 70},
             "score_weights": {"cost": 0.25}, "cost_efficiency": 0.4,
             "work_packages": [{"name": "Cutover", "estimated_cost": 374821, "estimated_hours": 40}]}
    with app.test_request_context("/"):
        login_user(user)
        return withhold_reply(reply), reply


def test_a_withheld_person_keeps_hours_scores_and_weights_and_loses_the_amounts(app, db_session, make_org):
    architect = _signed_in(db_session, make_org, "solution_architect")
    out, sent = _reply_of(app, architect)
    assert out["total_estimated_hours"] == 120 and out["component_scores"] == {"fit": 70}
    assert out["score_weights"] == {"cost": 0.25}
    assert out["work_packages"][0]["estimated_hours"] == 40 and out["work_packages"][0]["name"] == "Cutover"
    assert out["work_packages"][0]["estimated_cost"] is None and out["cost_efficiency"] is None
    assert out["costs_withheld"] is True and out["costs_withheld_sentence"] == COSTS_WITHHELD
    assert sent["work_packages"][0]["estimated_cost"] == 374821


def test_an_allowed_person_receives_the_reply_unchanged(app, db_session, make_org):
    cto = _signed_in(db_session, make_org, "cto")
    out, sent = _reply_of(app, cto)
    assert out == sent


def test_a_list_of_records_named_for_money_stays_and_loses_its_amounts():
    reply = {"critical_investments": [{"name": "Payments", "estimated_cost": 90000}], "cost_history": [1, 2, 3]}
    out = withhold_costs(reply)
    assert out["critical_investments"][0]["name"] == "Payments"
    assert out["critical_investments"][0]["estimated_cost"] is None
    assert out["cost_history"] is None


def test_a_list_of_scored_records_still_loses_the_amounts_in_each_record():
    reply = {"capability_scores": [{"name": "Payments", "score": 7, "estimated_cost": 777777.0}]}
    out = withhold_costs(reply)
    assert out["capability_scores"][0]["estimated_cost"] is None
    assert out["capability_scores"][0]["score"] == 7 and out["capability_scores"][0]["name"] == "Payments"


def test_a_part_named_for_money_loses_every_number_in_it_and_says_so():
    out = withhold_costs({"cost_breakdown": {"licence": 100, "support": 50, "notes": "pays £5,000"}, "name": "x"})
    assert out["cost_breakdown"]["licence"] is None and out["cost_breakdown"]["support"] is None
    assert "5,000" not in out["cost_breakdown"]["notes"] and out["cost_breakdown"]["costs_withheld"] is True
    assert out["name"] == "x"


def test_a_sentence_under_a_money_named_key_stays_and_an_amount_written_as_text_goes():
    out = withhold_costs({"cost_not_saved": "The cost was not saved", "annual_cost": "5000.00", "total_cost": "£5,000",
                          "note_on_cost": "ask about 12 sites"})
    assert out["cost_not_saved"] == "The cost was not saved"
    assert out["annual_cost"] is None and out["total_cost"] is None
    assert out["note_on_cost"] == "ask about 12 sites"

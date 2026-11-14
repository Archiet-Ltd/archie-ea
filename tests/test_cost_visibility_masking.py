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

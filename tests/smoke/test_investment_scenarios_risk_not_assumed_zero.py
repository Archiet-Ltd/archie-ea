"""An architect chooses between scenarios and a scenario with no linked risks is never ranked as zero risk.

Goal: "I compare two capabilities with near-identical returns, one with a recorded risk and one
with none, and the screen does not reward the one whose risk nobody recorded." The allocation
chart beside the matrix plots only the investments someone estimated.
"""
import re

import pytest
from playwright.sync_api import expect

from tests.smoke.test_investment_matrix_scores_journey import _row, two_orgs  # noqa: F401
from tests.smoke.test_investment_scenarios_no_invented_benefit import _compare, _open


def _record(page, name, benefit):
    _row(page, name).get_by_role("button", name="Calculate ROI").click()
    roi = page.get_by_role("dialog", name="ROI Calculator")
    roi.get_by_label("Enter investment amount").fill("100000")
    roi.get_by_label("Enter timeline").fill("24")
    roi.get_by_label("Expected annual benefit").fill(str(benefit))
    roi.get_by_role("button", name="Save", exact=True).click()
    roi.get_by_role("button", name="Close").last.click()


def _recommended(dialog):
    """The footer line naming the recommended scenario, or None."""
    line = dialog.get_by_text(re.compile(r"^Scenario \d recommended$"))
    return line.first.inner_text() if line.first.is_visible() else None


@pytest.mark.smoke
@pytest.mark.journey
def test_equal_returns_do_not_favour_the_scenario_with_no_linked_risks(browser, live_server, two_orgs):
    names = two_orgs["names"]
    page = _open(browser, live_server, two_orgs)
    _record(page, names["child"], 60000)
    _record(page, names["plain"], 60000)
    dialog = _compare(page, names["child"], names["plain"])
    page.wait_for_timeout(500)
    expect(dialog.get_by_test_id("scenario-risk-note")).to_have_text(
        "Risk not compared: one scenario has no linked risks.")
    expect(dialog.get_by_test_id("scenario-tie-note")).to_be_visible()
    assert _recommended(dialog) is None, "equal returns must not recommend the unrisked scenario"


@pytest.mark.smoke
@pytest.mark.journey
def test_a_slightly_better_return_wins_over_an_unrecorded_risk(browser, live_server, two_orgs):
    names = two_orgs["names"]
    page = _open(browser, live_server, two_orgs)
    _record(page, names["child"], 60000)
    _record(page, names["plain"], 59000)
    dialog = _compare(page, names["child"], names["plain"])
    page.wait_for_timeout(500)
    expect(dialog.get_by_test_id("scenario-risk-note")).to_be_visible()
    assert _recommended(dialog) == "Scenario 1 recommended"


@pytest.mark.smoke
@pytest.mark.journey
def test_risk_is_compared_when_every_scenario_has_it(browser, live_server, two_orgs):
    names = two_orgs["names"]
    page = _open(browser, live_server, two_orgs)
    _record(page, names["top"], 60000)
    _record(page, names["child"], 60000)
    dialog = _compare(page, names["top"], names["child"])
    page.wait_for_timeout(500)
    expect(dialog.get_by_test_id("scenario-risk-note")).not_to_be_visible()
    assert _recommended(dialog) == "Scenario 2 recommended"


@pytest.mark.smoke
@pytest.mark.journey
def test_comparing_on_a_phone_raises_no_script_error(browser, live_server, two_orgs):
    names = two_orgs["names"]
    page = _open(browser, live_server, two_orgs, width=390, height=844)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    _record(page, names["child"], 60000)
    dialog = _compare(page, names["child"], names["plain"])
    page.wait_for_timeout(500)
    expect(dialog.get_by_test_id("scenario-ranking-note")).to_be_visible()
    assert errors == [], errors


@pytest.mark.smoke
@pytest.mark.journey
def test_allocation_chart_plots_only_estimated_investments(browser, live_server, two_orgs):
    page = _open(browser, live_server, two_orgs)
    page.wait_for_function("window.Chart && Chart.getChart('investmentChart')")
    data = page.evaluate("Chart.getChart('investmentChart').data.datasets[0].data")
    assert sorted(v for v in data if v is not None) == [120.0], data
    assert data.count(None) == 3, data
    note = page.get_by_test_id("allocation-note")
    expect(note).to_contain_text("Plots 1 of 4 capabilities")
    expect(note).to_contain_text("3 not estimated")

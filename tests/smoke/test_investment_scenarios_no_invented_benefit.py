"""An architect compares investment scenarios and is never shown a benefit nobody recorded.

Goal: "I put two capabilities side by side to choose between them. Where no benefit is
recorded the comparison says so, and where I have recorded one the return is worked out
from it." The ROI dialog already behaves this way; the comparison dialog has to agree.
"""
import pytest
from playwright.sync_api import expect

from tests.smoke.fresh_org import sign_in
from tests.smoke.test_investment_matrix_scores_journey import (  # noqa: F401
    PAGE_TIMEOUT, _row, two_orgs,
)

MATRIX = "/strategic/investment-matrix"


def _open(browser, live_server, orgs, width=1440, height=900):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": height})
    page = context.new_page()
    sign_in(page, live_server, orgs["mine"]["emails"]["enterprise_architect"])
    page.goto(live_server + MATRIX, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    return page


def _compare(page, *names):
    page.get_by_role("button", name="Compare Scenarios").click()
    dialog = page.get_by_role("dialog", name="Investment Scenario Comparison")
    expect(dialog).to_be_visible()
    for index, name in enumerate(names):
        dialog.locator("select").nth(index).select_option(label=name)
    return dialog


def _cells(dialog, metric):
    row = dialog.locator("tbody tr").filter(has=dialog.page.get_by_role("cell", name=metric, exact=True))
    return [text.strip() for text in row.locator("td").all_inner_texts()[1:]]


@pytest.mark.smoke
@pytest.mark.journey
def test_comparison_without_a_recorded_benefit_invents_none(browser, live_server, two_orgs):
    names = two_orgs["names"]
    page = _open(browser, live_server, two_orgs)
    dialog = _compare(page, names["top"], names["plain"])
    text = dialog.inner_text()
    assert "-70.00%" not in text and "-£341,818" not in text, text
    assert "£36,000" not in text, text
    assert _cells(dialog, "Initial Investment")[0] == "£120,000"
    for metric in ("Annual Benefit", "ROI %", "Net Present Value", "Payback Period"):
        for cell in _cells(dialog, metric):
            assert "£0" not in cell and "0.00%" not in cell, (metric, cell)
    assert "Not recorded" in _cells(dialog, "Annual Benefit")[0]
    # A capability whose cost nobody estimated is not shown as costing nothing.
    assert "Not estimated" in _cells(dialog, "Initial Investment")[1]
    # The priority is the one the matrix gives, not a MEDIUM that nothing computed.
    priority = _cells(dialog, "Priority Level")[0]
    assert priority in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}, priority
    for badge in dialog.get_by_text("Recommended").all():
        assert not badge.is_visible(), "nothing is recommended when no return is known"


@pytest.mark.smoke
@pytest.mark.journey
def test_comparison_works_the_return_out_from_a_recorded_benefit(browser, live_server, two_orgs):
    names = two_orgs["names"]
    page = _open(browser, live_server, two_orgs)
    _row(page, names["top"]).get_by_role("button", name="Calculate ROI").click()
    roi = page.get_by_role("dialog", name="ROI Calculator")
    roi.get_by_label("Expected annual benefit").fill("60000")
    roi.get_by_role("button", name="Save", exact=True).click()
    roi.get_by_role("button", name="Close").last.click()

    dialog = _compare(page, names["top"])
    assert _cells(dialog, "Annual Benefit") == ["£60,000"]
    assert _cells(dialog, "ROI %") == ["-50.00%"]
    assert _cells(dialog, "Net Present Value")[0].startswith("-£")


@pytest.mark.smoke
@pytest.mark.journey
def test_roi_figures_fit_the_panel_on_a_phone(browser, live_server, two_orgs):
    names = two_orgs["names"]
    page = _open(browser, live_server, two_orgs, width=390, height=844)
    _row(page, names["top"]).get_by_role("button", name="Calculate ROI").click()
    roi = page.get_by_role("dialog", name="ROI Calculator")
    roi.get_by_label("Expected annual benefit").fill("60000")
    metrics = roi.get_by_test_id("roi-metrics")
    expect(metrics).to_be_visible()
    box = metrics.bounding_box()
    for value in metrics.locator("[data-testid='roi-metric-value']").all():
        inner = value.bounding_box()
        assert inner["x"] >= box["x"] - 0.5, (value.inner_text(), inner, box)
        assert inner["x"] + inner["width"] <= box["x"] + box["width"] + 0.5, (value.inner_text(), inner, box)
        fits = value.evaluate("e => e.scrollWidth <= e.clientWidth + 1")
        assert fits, value.inner_text()


@pytest.mark.smoke
@pytest.mark.journey
def test_discount_rate_and_timeline_are_labelled_as_assumptions(browser, live_server, two_orgs):
    names = two_orgs["names"]
    page = _open(browser, live_server, two_orgs)
    _row(page, names["top"]).get_by_role("button", name="Calculate ROI").click()
    roi = page.get_by_role("dialog", name="ROI Calculator")
    note = roi.get_by_test_id("roi-assumptions")
    expect(note).to_be_visible()
    expect(note).to_contain_text("assumed")
    expect(note).to_contain_text("Change them")
    roi.get_by_label("Discount rate").fill("8")
    assert roi.get_by_label("Discount rate").input_value() == "8"

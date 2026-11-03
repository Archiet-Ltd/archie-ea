"""An architect compares scenarios and no score, bar or point is drawn from a figure nobody recorded.

Goal: "I compare capabilities and every figure is either real or says why it is missing."
The matrix says "Not scored" and "No risks linked"; the comparison has to say the same,
its charts plot nothing for a scenario with no recorded benefit, and on a phone the
picker and the table stay inside the dialog.
"""
import pytest
from playwright.sync_api import expect

from tests.smoke.test_investment_matrix_scores_journey import _row, two_orgs  # noqa: F401
from tests.smoke.test_investment_scenarios_no_invented_benefit import _cells, _compare, _open

CHART_DATA = """id => {
    const chart = Chart.getChart(document.getElementById(id));
    return chart ? chart.data.datasets[0].data : null;
}"""


def _chart(dialog, canvas_id):
    return dialog.page.evaluate(CHART_DATA, canvas_id)


@pytest.mark.smoke
@pytest.mark.journey
def test_scores_nobody_recorded_read_as_the_matrix_reads_them(browser, live_server, two_orgs):
    names = two_orgs["names"]
    page = _open(browser, live_server, two_orgs)
    dialog = _compare(page, names["top"], names["unrated"])
    strategic = _cells(dialog, "Strategic Score")
    risk = _cells(dialog, "Risk Score")
    assert any(cell == "Not scored" for cell in strategic), strategic
    assert any(cell == "No risks linked" for cell in risk), risk
    for cell in strategic + risk:
        assert cell in {"Not scored", "No risks linked"} or cell.endswith(" / 25"), cell
        assert not cell.startswith("0 /"), cell


@pytest.mark.smoke
@pytest.mark.journey
def test_charts_plot_nothing_for_a_scenario_without_a_benefit(browser, live_server, two_orgs):
    names = two_orgs["names"]
    page = _open(browser, live_server, two_orgs)
    dialog = _compare(page, names["top"], names["plain"])
    for canvas in ("comparisonRoiChart", "comparisonPaybackChart"):
        data = _chart(dialog, canvas)
        assert data[:2] == [None, None], (canvas, data)
    note = dialog.get_by_test_id("scenario-chart-note")
    expect(note).to_be_visible()
    expect(note).to_contain_text("no recorded benefit")


@pytest.mark.smoke
@pytest.mark.journey
def test_charts_plot_the_recorded_scenario_and_only_that_one(browser, live_server, two_orgs):
    names = two_orgs["names"]
    page = _open(browser, live_server, two_orgs)
    _row(page, names["top"]).get_by_role("button", name="Calculate ROI").click()
    roi = page.get_by_role("dialog", name="ROI Calculator")
    roi.get_by_label("Expected annual benefit").fill("60000")
    roi.get_by_role("button", name="Save", exact=True).click()
    roi.get_by_role("button", name="Close").last.click()

    dialog = _compare(page, names["top"], names["plain"])
    assert _chart(dialog, "comparisonRoiChart")[0] == -50
    payback = _chart(dialog, "comparisonPaybackChart")
    assert payback[0] == 24, payback  # months: the table says 2 years
    assert payback[1] is None
    assert _cells(dialog, "Payback Period")[0] == "2 years"


@pytest.mark.smoke
@pytest.mark.journey
def test_picker_and_table_stay_inside_the_dialog_on_a_phone(browser, live_server, two_orgs):
    names = two_orgs["names"]
    page = _open(browser, live_server, two_orgs, width=390, height=844)
    dialog = _compare(page, names["top"], names["plain"], names["plain"])
    page.wait_for_timeout(300)
    box = dialog.bounding_box()
    for select in dialog.locator("select").all():
        inner = select.bounding_box()
        assert inner["x"] >= box["x"] - 0.5 and inner["x"] + inner["width"] <= box["x"] + box["width"] + 0.5, (inner, box)
    table = dialog.locator("table").first
    holder = table.locator("xpath=..")
    scrolls = holder.evaluate("e => getComputedStyle(e).overflowX")
    assert scrolls in ("auto", "scroll"), scrolls
    # Every scenario column sits in its own place, left to right: none is pinned over another.
    heads = [h.bounding_box()["x"] for h in table.locator("thead th").all() if h.is_visible()]
    assert heads == sorted(heads) and len(set(heads)) == len(heads), heads
    # No cell is covered by the next: every cell is as wide as its own text.
    for cell in table.locator("tbody td, thead th").all():
        if cell.is_visible():
            assert cell.evaluate("e => e.scrollWidth <= e.clientWidth + 1"), cell.inner_text()

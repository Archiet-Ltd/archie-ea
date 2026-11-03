"""An architect exports and ranks scenarios and nothing in either is a figure nobody recorded.

Goal: "I compare capabilities, export the comparison and see why one is recommended."
The export says "Not scored" and "No risks linked" as the screen does, the recommendation says
how a scenario with no linked risks is ranked, and recording a benefit raises no script error.
"""
import csv
import io

import pytest
from playwright.sync_api import expect

from tests.smoke.test_investment_matrix_scores_journey import _row, two_orgs  # noqa: F401
from tests.smoke.test_investment_scenarios_no_invented_benefit import _cells, _compare, _open


def _download(page, dialog):
    with page.expect_download() as info:
        dialog.get_by_role("button", name="Export").click()
    text = open(info.value.path(), encoding="utf-8").read()
    return list(csv.reader(io.StringIO(text)))


@pytest.mark.smoke
@pytest.mark.journey
def test_export_says_not_scored_and_no_risks_linked_as_the_screen_does(browser, live_server, two_orgs):
    names = two_orgs["names"]
    page = _open(browser, live_server, two_orgs)
    dialog = _compare(page, names["top"], names["unrated"])
    on_screen = {"strategic": _cells(dialog, "Strategic Score"), "risk": _cells(dialog, "Risk Score")}
    rows = _download(page, dialog)
    header, body = rows[0], rows[1:]
    strategic, risk = header.index("Strategic Score"), header.index("Risk Score")
    assert [r[strategic] for r in body] == on_screen["strategic"]
    assert [r[risk] for r in body] == on_screen["risk"]
    by_name = {r[0]: r for r in body}
    assert by_name[names["unrated"]][strategic] == "Not scored"
    assert by_name[names["unrated"]][risk] == "No risks linked"


@pytest.mark.smoke
@pytest.mark.journey
def test_recording_a_benefit_and_comparing_raises_no_script_error_and_explains_the_ranking(browser, live_server, two_orgs):
    names = two_orgs["names"]
    page = _open(browser, live_server, two_orgs)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    for key in ("top",):
        _row(page, names[key]).get_by_role("button", name="Calculate ROI").click()
        roi = page.get_by_role("dialog", name="ROI Calculator")
        roi.get_by_label("Expected annual benefit").fill("60000")
        roi.get_by_role("button", name="Save", exact=True).click()
        roi.get_by_role("button", name="Close").last.click()
    dialog = _compare(page, names["top"], names["child"])
    page.wait_for_timeout(500)
    note = dialog.get_by_test_id("scenario-ranking-note")
    expect(note).to_be_visible()
    expect(note).to_contain_text("no risks linked")
    expect(note).to_contain_text("return alone")
    assert errors == [], errors

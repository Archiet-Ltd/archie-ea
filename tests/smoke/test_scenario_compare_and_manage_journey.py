"""An architect compares two scenarios on one element and sees how they differ, exports the table, and manages them.

In the Lantern Quay Systems demonstration organisation (RELEASE_WAVE 3):
- retires and replaces the same element, saves both, and Compare shows each run's own before and after figures,
  so the two columns are not the same; the export is a table with every cell quoted, and a name holding a comma,
  a quote and a leading = reaches the file as text;
- renames a saved scenario and deletes another, and both are still so after a reload;
- finds the picker above the link to the prioritised actions, and follows it to Impact Analysis;
- a new organisation with no elements is told its model is empty, offered Connect GitHub, and that link lands on a page with content.
"""
import csv
import io
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server
from .fresh_org import create_fresh_org, sign_in
from .test_simulation_journey import DEMO_EMAIL, ELEMENT, VIEWPORT_IDS, VIEWPORTS, _pick, demo  # noqa: F401
from .test_simulation_screen_and_saved_scenarios_journey import _context, _shot, _sign_in_as

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

HOSTILE = '=HYPERLINK("http://example.test"), "now"'
REPLACED = "Replace the ledger for the compare walk"
RENAME_FROM = "Compare walk rename me"
RENAME_TO = "Compare walk renamed"
DELETE_ME = "Compare walk delete me"
NAMES = (HOSTILE, REPLACED, RENAME_FROM, RENAME_TO, DELETE_ME)


@pytest.fixture(scope="module")
def server(request, app):
    return boot_live_server(request, None, app, extra_env={"RELEASE_WAVE": "3"})


def _run_and_save(page, server, name, change):
    page.goto(server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _pick(page, change=change)
    page.get_by_role("button", name="Run scenario").click()
    expect(page.get_by_test_id("simulation-panel")).to_be_visible(timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="Save Scenario for Comparison", exact=True).click()
    dialog = page.get_by_role("dialog", name="Save scenario")
    dialog.get_by_label("Scenario name").fill(name)
    dialog.get_by_role("button", name="Save scenario").click()
    expect(dialog).to_be_hidden()


def _open_compare(page, server):
    page.goto(server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="Compare Scenarios").click()
    dialog = page.get_by_role("dialog", name="Compare Impact Scenarios")
    expect(dialog).to_be_visible()
    return dialog


def _forget(app):
    from app import db
    from app.models.saved_scenario import SavedScenario

    with app.app_context():
        for name in NAMES:
            SavedScenario.query.filter_by(name=name).delete()
        db.session.commit()
        db.session.remove()


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_compare_shows_how_a_retirement_and_a_replacement_differ_and_the_export_matches(browser, server, app, demo, viewport, scheme, tmp_path):
    _forget(app)
    context = _context(browser, viewport, scheme)
    try:
        page = _sign_in_as(context, server, DEMO_EMAIL)
        _run_and_save(page, server, HOSTILE, "Decommission / Retirement")
        _run_and_save(page, server, REPLACED, "Full Replacement")
        dialog = _open_compare(page, server)
        dialog.get_by_text(HOSTILE, exact=True).first.click()
        dialog.get_by_text(REPLACED, exact=True).first.click()
        rows = dialog.get_by_test_id("compare-figure-row")
        expect(rows.first).to_be_visible()
        cells = {}
        for i in range(rows.count()):
            row = rows.nth(i)
            cells[row.get_by_role("cell").nth(0).inner_text().strip()] = [
                row.get_by_role("cell").nth(n).inner_text().strip() for n in (1, 2)]
        elements = cells["Elements in the model"]
        links = cells["Links between elements"]
        assert re.fullmatch(r"[\d,]+ to [\d,]+ \(Down 1\)", elements[0]), elements
        assert re.fullmatch(r"([\d,]+) to \1 \(No change\)", elements[1]), elements
        assert re.fullmatch(r"[\d,]+ to [\d,]+ \(Down [1-9][\d,]*\)", links[0]), links
        assert links[0] != links[1], links
        _shot(page, "compare-figures", viewport, scheme)
        table = dialog.locator("table")
        assert table.evaluate("t => t.scrollWidth <= t.parentElement.scrollWidth || getComputedStyle(t.parentElement).overflowX === 'auto'")

        with page.expect_download(timeout=PAGE_TIMEOUT) as download:
            dialog.get_by_role("button", name="Export").click()
        target = tmp_path / "compare.csv"
        download.value.save_as(str(target))
        text = target.read_text(encoding="utf-8")
        table_rows = list(csv.reader(io.StringIO(text)))
        assert len({len(r) for r in table_rows}) == 1 and len(table_rows[0]) == 3, table_rows[0]
        assert len(table_rows) >= 12, "the file is not one row for each metric"
        assert table_rows[0][1] == "'" + HOSTILE and table_rows[0][2] == REPLACED
        assert all(line.startswith('"') and line.endswith('"') for line in text.splitlines())
        by_metric = {r[0]: r[1:] for r in table_rows}
        for label, shown in cells.items():
            assert [re.sub(r"[£$€,]", "", c) for c in shown] == by_metric[label], (label, shown, by_metric[label])
    finally:
        context.close()
        _forget(app)


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_an_architect_renames_and_deletes_a_saved_scenario_and_it_stays_so(browser, server, app, demo, viewport, scheme):
    _forget(app)
    context = _context(browser, viewport, scheme)
    try:
        page = _sign_in_as(context, server, DEMO_EMAIL)
        _run_and_save(page, server, RENAME_FROM, "Minor Modification")
        _run_and_save(page, server, DELETE_ME, "Decommission / Retirement")
        dialog = _open_compare(page, server)
        card = dialog.locator("div.cursor-pointer").filter(has_text=RENAME_FROM)
        card.get_by_role("button", name="Rename " + RENAME_FROM).click()
        card.get_by_label("New name for the scenario").fill(RENAME_TO)
        card.get_by_role("button", name="Save name").click()
        expect(dialog.get_by_text(RENAME_TO, exact=True)).to_be_visible()
        doomed = dialog.locator("div.cursor-pointer").filter(has_text=DELETE_ME)
        doomed.get_by_role("button", name="Delete " + DELETE_ME).click()
        expect(doomed.get_by_text("Delete this saved scenario for everyone in your organisation?")).to_be_visible()
        doomed.get_by_role("button", name="Delete scenario").click()
        expect(dialog.get_by_text(DELETE_ME, exact=True)).to_have_count(0)
        _shot(page, "compare-manage", viewport, scheme)

        page.reload(wait_until="domcontentloaded")
        page.get_by_role("button", name="Compare Scenarios").click()
        dialog = page.get_by_role("dialog", name="Compare Impact Scenarios")
        expect(dialog.get_by_text(RENAME_TO, exact=True)).to_be_visible()
        expect(dialog.get_by_text(RENAME_FROM, exact=True)).to_have_count(0)
        expect(dialog.get_by_text(DELETE_ME, exact=True)).to_have_count(0)
    finally:
        context.close()
        _forget(app)


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_the_picker_comes_before_the_prioritised_actions(browser, server, demo, viewport, scheme):
    context = _context(browser, viewport, scheme)
    try:
        page = _sign_in_as(context, server, DEMO_EMAIL)
        page.goto(server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        # the tab title and the breadcrumb name the screen as the sidebar does: Scenarios, under Changes
        expect(page).to_have_title(re.compile(r"^Scenarios\b"))
        crumbs = page.get_by_label("Breadcrumb").first
        expect(crumbs).to_contain_text("Home")
        expect(crumbs.get_by_role("link", name="Changes")).to_have_count(0)
        assert re.sub(r"\s+", " ", crumbs.inner_text()).strip().endswith("Changes Scenarios"), crumbs.inner_text()
        expect(crumbs.get_by_text("Scenarios", exact=True)).to_have_attribute("aria-current", "page")
        picker = page.get_by_role("heading", name="Select Element")
        # the ranked list now lives on Impact Analysis; Scenarios carries no list, only the link, and it sits after the picker
        expect(page.get_by_test_id("priority-actions")).to_have_count(0)
        link = page.get_by_test_id("priority-actions-link")
        expect(link).to_be_visible()
        assert picker.bounding_box()["y"] < link.bounding_box()["y"], "the picker is below the link to the prioritised actions"
        assert picker.bounding_box()["y"] < viewport["height"], "a person scrolls past the link to the actions to reach the picker"
        link.click()
        # in this release the address moves to the Impact Analysis page the person is shown; either way the title names it
        page.wait_for_url(re.compile(r"/(strategic|enterprise/analysis)/impact-analysis#priority-actions$"), timeout=PAGE_TIMEOUT)
        expect(page).to_have_title(re.compile(r"^Impact Analysis\b"))
        # the ranked list is readable on the landing page
        expect(page.get_by_test_id("priority-action-row").first).to_be_visible()
    finally:
        context.close()


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_an_organisation_with_no_elements_is_told_its_model_is_empty_and_offered_the_next_step(browser, server, demo, viewport, scheme):
    fresh = create_fresh_org("enterprise_architect", org_admin=True)
    context = _context(browser, viewport, scheme)
    try:
        page = context.new_page()
        sign_in(page, server, fresh["emails"]["enterprise_architect"])
        page.goto(server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        empty = page.get_by_test_id("model-empty")
        expect(empty).to_contain_text("Your model has no elements yet")
        assert page.get_by_text("Nothing needs doing right now").count() == 0
        expect(page.get_by_role("button", name="Run scenario")).to_be_disabled()
        page.get_by_label("Type to search...").fill("anything")
        expect(page.get_by_role("status").filter(has_text="Your model has no elements yet")).to_be_visible()
        _shot(page, "scenarios-empty-model", viewport, scheme)
        link = empty.get_by_role("link", name="Connect GitHub")
        expect(link).to_be_visible()
        link.click()
        page.wait_for_url(re.compile(r"/github"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", level=1)).to_contain_text("Connect GitHub")
        assert len(page.locator("main").inner_text().strip()) > 40
    finally:
        context.close()

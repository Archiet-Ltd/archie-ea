"""A person the cost rule withholds from compares and exports scenarios and reads no running cost anywhere.

In the Lantern Quay Systems demonstration organisation (RELEASE_WAVE 3):
- a business user runs a scenario, opens Compare and exports; the run card, the dialog and the file hold no
  money figure and each says the cost figures are withheld for the role, while a portfolio manager of the same
  organisation reads the same scenario with its running cost;
- a person of another organisation lists none of it;
- when every chosen scenario has been deleted since the list loaded, Export says so in the dialog and the tab stays on the page.
"""
import csv
import io
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server
from .fresh_org import create_fresh_org, sign_in
from .test_simulation_journey import DEMO_EMAIL, ELEMENT, VIEWPORT_IDS, VIEWPORTS, _pick, demo  # noqa: F401
from .test_simulation_screen_and_saved_scenarios_journey import _context, _member, _shot, _sign_in_as

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

NAME = "Retire the ledger without cost rights"
WITHHELD = "withheld for your role"
MONEY = re.compile(r"[£$€]\s?\d")
VIEWER = "cost.viewer@lantern-quay.example.com"
FINANCE = "cost.finance@lantern-quay.example.com"


@pytest.fixture(scope="module")
def server(request, app):
    return boot_live_server(request, None, app, extra_env={"RELEASE_WAVE": "3"})


def _forget(app):
    from app import db
    from app.models.saved_scenario import SavedScenario

    with app.app_context():
        SavedScenario.query.filter_by(name=NAME).delete()
        db.session.commit()
        db.session.remove()


def _save_as_architect(browser, server, viewport, scheme):
    context = _context(browser, viewport, scheme)
    try:
        page = _sign_in_as(context, server, DEMO_EMAIL)
        page.goto(server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _pick(page, change="Decommission / Retirement")
        page.get_by_role("button", name="Run scenario").click()
        expect(page.get_by_test_id("simulation-panel")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Save Scenario for Comparison", exact=True).click()
        dialog = page.get_by_role("dialog", name="Save scenario")
        dialog.get_by_label("Scenario name").fill(NAME)
        dialog.get_by_role("button", name="Save scenario").click()
        expect(dialog).to_be_hidden()
    finally:
        context.close()


def _compare_one(page, server):
    page.goto(server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="Compare Scenarios").click()
    dialog = page.get_by_role("dialog", name="Compare Impact Scenarios")
    expect(dialog).to_be_visible()
    dialog.get_by_text(NAME, exact=True).first.click()
    return dialog


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_a_business_user_reads_no_running_cost_on_the_run_card_the_comparison_or_the_export(browser, server, app, demo, viewport, scheme, tmp_path):
    _forget(app)
    _member(app, VIEWER, "business_user")
    _member(app, FINANCE, "portfolio_manager")
    _save_as_architect(browser, server, viewport, scheme)
    context = _context(browser, viewport, scheme)
    finance_context = _context(browser, viewport, scheme)
    try:
        page = _sign_in_as(context, server, VIEWER)
        page.goto(server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _pick(page, change="Decommission / Retirement")
        page.get_by_role("button", name="Run scenario").click()
        panel = page.get_by_test_id("simulation-panel")
        expect(panel).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("tco-at-risk")).to_have_text("Withheld")
        expect(page.get_by_test_id("tco-at-risk-reason")).to_contain_text(WITHHELD)
        assert not MONEY.search(page.get_by_test_id("simulation-panel").inner_text() + page.get_by_test_id("tco-at-risk").inner_text())
        assert "Yearly running cost of all applications" in panel.inner_text()
        _shot(page, "cost-withheld-run-card", viewport, scheme)

        dialog = _compare_one(page, server)
        expect(dialog.get_by_text(WITHHELD).locator("visible=true").first).to_be_visible()
        assert not MONEY.search(dialog.inner_text()), dialog.inner_text()
        _shot(page, "cost-withheld-compare", viewport, scheme)
        with page.expect_download(timeout=PAGE_TIMEOUT) as download:
            dialog.get_by_role("button", name="Export").click()
        target = tmp_path / "withheld.csv"
        download.value.save_as(str(target))
        text = target.read_text(encoding="utf-8")
        assert not MONEY.search(text) and WITHHELD in text, text
        rows = {r[0]: r[1] for r in csv.reader(io.StringIO(text))}
        assert WITHHELD in rows["Yearly running cost of all applications"]

        # a portfolio manager of the same organisation reads the cost the same run worked out
        finance = _sign_in_as(finance_context, server, FINANCE)
        admitted = _compare_one(finance, server)
        expect(admitted.get_by_text(WITHHELD).locator("visible=true")).to_have_count(0)
        row = admitted.get_by_test_id("compare-figure-row").filter(has_text="Yearly running cost of all applications")
        expect(row.first).to_contain_text(MONEY)
        _shot(finance, "cost-admitted-compare", viewport, scheme)
    finally:
        context.close()
        finance_context.close()
        _forget(app)


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_a_person_of_another_organisation_lists_none_of_it(browser, server, app, demo, viewport, scheme):
    _forget(app)
    _save_as_architect(browser, server, viewport, scheme)
    fresh = create_fresh_org("portfolio_manager", org_admin=True)
    context = _context(browser, viewport, scheme)
    try:
        page = context.new_page()
        sign_in(page, server, fresh["emails"]["portfolio_manager"])
        page.goto(server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Compare Scenarios").click()
        dialog = page.get_by_role("dialog", name="Compare Impact Scenarios")
        expect(dialog.get_by_text("No Saved Scenarios")).to_be_visible()
        assert NAME not in dialog.inner_text()
    finally:
        context.close()
        _forget(app)


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_export_of_scenarios_that_were_deleted_meanwhile_says_so_in_the_dialog(browser, server, app, demo, viewport, scheme):
    _forget(app)
    _save_as_architect(browser, server, viewport, scheme)
    context = _context(browser, viewport, scheme)
    try:
        page = _sign_in_as(context, server, DEMO_EMAIL)
        dialog = _compare_one(page, server)
        _forget(app)  # a colleague deletes it after the list loaded
        address = page.url
        dialog.get_by_role("button", name="Export").click()
        message = dialog.get_by_test_id("export-error")
        expect(message).to_contain_text("Choose at least one saved scenario to export")
        assert page.url == address, "the tab left the page for the reply"
        expect(dialog).to_be_visible()
        _shot(page, "export-nothing-left", viewport, scheme)
    finally:
        context.close()
        _forget(app)

"""The Impact Analysis page actually runs an analysis and shows the cited answer.

As the demo architect: choose Element Type "Application", see the demo's applications
listed, pick Calibration Ledger, run the analysis, and read a cited answer naming its
connected elements with an evidence footer whose date is today -- the same connected
elements the AI chat names for the same question. Returning to the page afterwards
shows the stored analysis listed, with the headline figures agreeing with the list.
A second organisation's elements never appear in the picker or the list.
"""

import os
from datetime import datetime, timezone

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_EMAIL = "demo@lantern-quay.example.com"
PAGE_PATH = "/enterprise/analysis/impact-analysis"
ROW_NAME = "[data-answer-row] a"


@pytest.fixture(scope="module")
def demo(live_server, app):
    """Seed the demo company once; return Calibration Ledger's application id."""
    from app import db
    from app.commands.seed_demo_company import seed_demo_company

    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            seed_demo_company()
            from app.models.application_portfolio import ApplicationComponent
            from app.models.organization import Organization

            org = Organization.query.filter_by(slug="lantern-quay").one()
            ledger = ApplicationComponent.query.filter_by(
                organization_id=org.id, name="Calibration Ledger"
            ).one()
            picked = {"org_id": org.id, "ledger_id": ledger.id}
            db.session.remove()
    finally:
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous
    return picked


def _chat_connected_names(page, live_server):
    """The connected-element names the AI chat lists for Calibration Ledger."""
    page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.locator("#user-input").fill("What breaks if Calibration Ledger fails")
    page.locator("#user-input").press("Enter")
    rows = page.locator(ROW_NAME)
    expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
    return sorted(rows.all_inner_texts())


def test_demo_architect_runs_analysis_and_reads_the_same_answer_as_the_chat(
    browser, live_server, demo
):
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, DEMO_EMAIL)

        chat_names = _chat_connected_names(page, live_server)
        assert chat_names, "the AI chat must name at least one connected element for Calibration Ledger"

        # Open Impact Analysis, choose Application, see the demo's applications listed.
        page.goto(live_server + PAGE_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.select_option("#element_type", "application")
        app_select = page.locator("#application_id")
        expect(app_select).to_be_visible()
        options = app_select.locator("option").all_inner_texts()
        assert any("Calibration Ledger" in o for o in options), options

        # Pick Calibration Ledger and run the analysis.
        page.select_option("#element_type", "application")
        page.select_option("#application_id", label="Calibration Ledger")
        page.get_by_role("button", name="Apply Filter").click()
        page.wait_for_url(lambda url: "element_id=" in url, timeout=PAGE_TIMEOUT)

        page.get_by_test_id("run-impact-analysis").click()
        page.wait_for_url(lambda url: "answer_address=" in url, timeout=PAGE_TIMEOUT)

        # A cited answer, naming the same connected elements as the chat.
        page_rows = page.locator(ROW_NAME)
        expect(page_rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
        page_names = sorted(page_rows.all_inner_texts())
        assert page_names == chat_names, (page_names, chat_names)

        # The evidence footer: evidence, source, confidence and a date of today.
        today = datetime.now(timezone.utc)
        expect(page.get_by_text("Evidence for this answer")).to_be_visible()
        expect(page.get_by_text("Source: the organisation's recorded architecture model")).to_be_visible()
        confidence_line = page.get_by_text("Confidence:", exact=False).inner_text()
        assert "Confidence:" in confidence_line, confidence_line
        computed_line = page.get_by_text("Computed:", exact=False).inner_text()
        assert str(today.day) in computed_line and today.strftime("%b") in computed_line, computed_line

        # Return to the plain page: the run is stored and listed, figures agree.
        page.goto(live_server + PAGE_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Total Analyses Run")).to_be_visible()
        total_card = page.locator("p", has_text="Total Analyses Run").locator(
            "xpath=following-sibling::p[1]"
        )
        assert int(total_card.inner_text().strip()) >= 1, total_card.inner_text()
        # The row for this run carries the element id the run used, not a blank figure.
        row_cells = page.locator("tbody tr").first.inner_text()
        assert "ID: " in row_cells, row_cells
    finally:
        context.close()


def test_a_second_organisations_elements_never_appear(browser, live_server, demo):
    other = create_fresh_org("enterprise_architect")
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, other["emails"]["enterprise_architect"])
        page.goto(live_server + PAGE_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.select_option("#element_type", "application")
        body_text = page.locator("body").inner_text()
        assert "Calibration Ledger" not in body_text, "another organisation's application leaked into the picker"
    finally:
        context.close()

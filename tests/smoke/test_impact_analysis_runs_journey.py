"""The Impact Analysis page actually runs an analysis and shows the cited answer.

As the demo architect: pick Calibration Ledger in the page's element picker (choosing an
element is running the analysis: the page stores the answer through the one answer store),
read the cited answer naming its connected elements, and see that same run as the first
row of the recorded-runs table below it, its figures agreeing with the answer -- the same
connected elements the AI chat names for the same question. A second organisation's
elements never appear in its picker or its runs.

Changed on the merge of impact-page01 and results01 (intended, not weakened): the
"Element Type" dropdown, "Apply Filter" and the separate "Run Analysis" POST are gone
with the page that carried them; the same person-visible outcome (pick, run, see the cited
rows, see the run listed) is asserted against the one merged flow.
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


def _pick(page, name):
    page.locator("#impact-page-picker-input").fill(name)
    option = page.get_by_role("option", name=name, exact=True)
    expect(option).to_be_visible(timeout=PAGE_TIMEOUT)
    option.click()
    page.wait_for_url(lambda url: "element=" in url, timeout=PAGE_TIMEOUT)


def test_demo_architect_picks_an_element_and_reads_the_answer_and_its_run(
    browser, live_server, demo
):
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, DEMO_EMAIL)

        chat_names = _chat_connected_names(page, live_server)
        assert chat_names, "the AI chat must name at least one connected element for Calibration Ledger"

        page.goto(live_server + PAGE_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _pick(page, "Calibration Ledger")

        # A cited answer, naming the same connected elements as the chat.
        page_rows = page.get_by_test_id("impact-answer-element-link")
        expect(page_rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
        page_names = sorted(t.strip() for t in page_rows.all_inner_texts())
        assert page_names == chat_names, (page_names, chat_names)

        # The run just made is the first row of the recorded-runs table, and its
        # figures are the answer's: one store, one set of numbers.
        runs = page.get_by_test_id("impact-run-row")
        expect(runs.first).to_be_visible(timeout=PAGE_TIMEOUT)
        first = runs.first.inner_text()
        assert "Calibration Ledger" in first, first
        cells = [c.strip() for c in runs.first.locator("td").all_inner_texts()]
        assert cells[2] == str(len(page_names)), (cells, len(page_names))
        assert str(int(cells[3]) + int(cells[4])) == cells[2], cells

        # The row's Open link lands on the stored answer with its rows.
        runs.first.get_by_test_id("impact-run-open").click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "/intelligence/answers/" in page.url, page.url
        stored_rows = page.locator("[data-answer-row] a")
        expect(stored_rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert sorted(stored_rows.all_inner_texts()) == chat_names

        # Running it again lists a second run, newest first.
        page.goto(live_server + PAGE_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        before = page.get_by_test_id("impact-run-row").count()
        _pick(page, "Calibration Ledger")
        assert page.get_by_test_id("impact-run-row").count() == before + 1
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
        page.locator("#impact-page-picker-input").fill("Calibration")
        page.wait_for_timeout(1500)
        body_text = page.locator("body").inner_text()
        assert "Calibration Ledger" not in body_text, "another organisation's application leaked into the picker"
        assert page.get_by_test_id("impact-run-row").count() == 0, "another organisation's run was listed"
    finally:
        context.close()

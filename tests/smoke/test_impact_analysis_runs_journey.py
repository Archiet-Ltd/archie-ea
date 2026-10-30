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
            from app.models import ArchiMateElement

            element = ArchiMateElement.query.filter_by(
                organization_id=org.id, name="Calibration Ledger"
            ).first()
            picked = {
                "org_id": org.id,
                "ledger_id": ledger.id,
                "element_id": element.id,
            }
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


_SHOT_DIR = os.environ.get("JOURNEY_SHOT_DIR")


def _shot_answer(page, name):
    """Evidence: the answer with its save control in view."""
    if not _SHOT_DIR:
        return
    os.makedirs(_SHOT_DIR, exist_ok=True)
    page.get_by_test_id("impact-save-run").scroll_into_view_if_needed()
    page.screenshot(path=os.path.join(_SHOT_DIR, name + ".png"))


def _shot_runs(page, name):
    """Evidence for the report, only when JOURNEY_SHOT_DIR is set: the runs table in view."""
    if not _SHOT_DIR:
        return
    os.makedirs(_SHOT_DIR, exist_ok=True)
    table = page.get_by_test_id("impact-runs-table")
    table.scroll_into_view_if_needed()
    page.screenshot(path=os.path.join(_SHOT_DIR, name + ".png"))


def _save(page):
    """Press the page's Save control and wait for the run to be listed."""
    page.get_by_test_id("impact-save-run").click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("impact-run-row").first).to_be_visible(timeout=PAGE_TIMEOUT)


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
        listed_before = page.get_by_test_id("impact-run-row").count()
        _pick(page, "Calibration Ledger")

        # A cited answer, naming the same connected elements as the chat.
        page_rows = page.get_by_test_id("impact-answer-element-link")
        expect(page_rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
        page_names = sorted(t.strip() for t in page_rows.all_inner_texts())
        assert page_names == chat_names, (page_names, chat_names)

        # Viewing stored nothing; saving the run lists it as the first row of the
        # recorded-runs table, and its figures are the answer's: one store, one
        # set of numbers. (Changed with the lead's ruling of 30 Oct: a GET no
        # longer stores a run, so the run appears on Save, not on picking.)
        assert page.get_by_test_id("impact-run-row").count() == listed_before
        _save(page)
        runs = page.get_by_test_id("impact-run-row")
        expect(runs.first).to_be_visible(timeout=PAGE_TIMEOUT)
        first = runs.first.inner_text()
        assert "Calibration Ledger" in first, first
        cells = [c.strip() for c in runs.first.locator("td").all_inner_texts()]
        assert cells[2] == str(len(page_names)), (cells, len(page_names))
        assert str(int(cells[3]) + int(cells[4])) == cells[2], cells

        _shot_runs(page, "impact-runs-table-1440-light")

        # The row's Open link lands on the stored answer with its rows.
        runs.first.get_by_test_id("impact-run-open").click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "/intelligence/answers/" in page.url, page.url
        stored_rows = page.locator("tr[data-element-id]")
        expect(stored_rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert stored_rows.count() == len(chat_names), (stored_rows.count(), chat_names)
        assert page.locator("[data-row-total]").inner_text().strip() == str(len(chat_names))

        # Running it again lists a second run, newest first.
        page.goto(live_server + PAGE_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        before = page.get_by_test_id("impact-run-row").count()
        _pick(page, "Calibration Ledger")
        assert page.get_by_test_id("impact-run-row").count() == before
        _save(page)
        assert page.get_by_test_id("impact-run-row").count() == before + 1
    finally:
        context.close()


def _run_count(app, org_id):
    from app import db
    from app.models.answer_record import AnswerRecord

    with app.app_context():
        count = AnswerRecord.query.filter_by(
            organization_id=org_id, question_kind="impact"
        ).count()
        db.session.remove()
    return count


def test_viewing_never_stores_a_run_and_saving_stores_exactly_one(
    browser, live_server, app, demo
):
    """A CTO opens a shared link, reloads, goes back: the runs table does not grow.
    Only pressing Save adds a run, and it is the one the answer showed."""
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, DEMO_EMAIL)
        url = "%s%s?element=%d" % (live_server, PAGE_PATH, demo["element_id"])
        before = _run_count(app, demo["org_id"])
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        for _ in range(3):
            page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        answer_links = page.get_by_test_id("impact-answer-element-link")
        expect(answer_links.first).to_be_visible(timeout=PAGE_TIMEOUT)
        answered = answer_links.count()
        prefetch = context.request.get(url, headers={"Sec-Purpose": "prefetch"})
        assert prefetch.status == 200
        assert _run_count(app, demo["org_id"]) == before, "viewing stored a run"
        shown_before = page.get_by_test_id("impact-run-row").count()
        _shot_answer(page, "impact-page-answer-1440-light")

        _save(page)
        assert _run_count(app, demo["org_id"]) == before + 1
        runs = page.get_by_test_id("impact-run-row")
        assert runs.count() == shown_before + 1
        cells = [c.strip() for c in runs.first.locator("td").all_inner_texts()]
        assert "Calibration Ledger" in runs.first.inner_text()
        assert cells[2] == str(answered), (cells, answered)
        _shot_runs(page, "impact-runs-table-1440-light")
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


def test_the_runs_table_reads_at_390_in_dark(browser, live_server, demo):
    context = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, DEMO_EMAIL)
        page.goto(live_server + PAGE_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _pick(page, "Calibration Ledger")
        _shot_answer(page, "impact-page-answer-390-dark")
        _save(page)
        assert "Calibration Ledger" in page.get_by_test_id("impact-run-row").first.inner_text()
        _shot_runs(page, "impact-runs-table-390-dark")
    finally:
        context.close()

"""The first screen a person meets carries one message.

A founder opens the public home and sees one headline, one sentence, two buttons and a real
answer, and nothing else in the first 900 pixels. A new architect signs in to an empty workspace
and sees one message with its action and a question box, no figures or tabs. A second architect
signs in to a workspace that holds data and sees the question box with the first cited answer
before any figure; asking a question lands on the chat with that question waiting.
"""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_company_kind_first_screen_journey import _empty_organisation_user
from .test_try_sample_company_journey import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

FOLD = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}


def _page(browser, viewport):
    context = browser.new_context(viewport=viewport)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    return context, context.new_page()


def _top(page, selector):
    return page.locator(selector).first.evaluate("e => e.getBoundingClientRect().top")


def test_public_home_fold_is_one_message(browser, live_server):
    context, page = _page(browser, FOLD)
    try:
        page.goto(live_server + "/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert page.title() == "Entelim — See what breaks before you change it"
        hero = page.locator('[data-testid="home-hero"]')
        expect(hero.locator("h1")).to_have_count(1)
        expect(hero.locator("h1")).to_have_text("See what breaks before you change it.")
        # one sentence, two buttons
        expect(hero.locator("p.home-lead")).to_have_count(1)
        buttons = hero.locator('[data-testid="home-hero-start"], [data-testid="home-hero-pricing"]')
        expect(buttons).to_have_count(2)
        expect(hero.get_by_role("link")).to_have_count(2)
        # the proof is a real answer, loaded, and shows its subject
        proof = page.locator('[data-testid="home-hero-proof"]')
        assert proof.evaluate("e => e.complete && e.naturalWidth > 0")
        assert _top(page, '[data-testid="home-hero-proof"]') < FOLD["height"]
        assert "Calibration Ledger" in proof.get_attribute("alt")
        # nothing else: no licence line, and the next section starts below the fold
        assert "AGPL" not in hero.inner_text()
        assert _top(page, "section.home-section") >= FOLD["height"]
        # the two buttons land on pages with content
        for testid, expected in (("home-hero-pricing", "pricing"), ("home-hero-start", "register")):
            page.locator('[data-testid="%s"]' % testid).click()
            page.wait_for_url("**/*%s*" % expected, timeout=PAGE_TIMEOUT)
            assert len(page.locator("body").inner_text().strip()) > 100
            page.goto(live_server + "/", wait_until="domcontentloaded")
    finally:
        context.close()


def test_empty_workspace_first_screen_is_one_message(browser, live_server, seeded):
    email, org_id = _empty_organisation_user(live_server)
    context, page = _page(browser, FOLD)
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        message = page.locator('[data-testid="first-message"]')
        expect(message).to_contain_text("Connect GitHub or Jira")
        action = page.locator('[data-testid="first-action-sample"]')
        question = page.locator('[data-testid="first-question-input"]')
        for target in (message, action, question):
            expect(target).to_be_in_viewport()
        # nothing else: no figures, tabs or quick links until there is data
        for testid in ("kpi-tile-applications", "layer-tab-overview", "layer-tab-motivation", "architecture-health"):
            expect(page.locator('[data-testid="%s"]' % testid)).to_have_count(0)
        # this architect is not an administrator, so the page says who connects instead of a dead link
        expect(page.locator('[data-testid="first-action-connect"]')).to_have_count(0)
        expect(message).to_contain_text("organisation administrator")

        # the question lands on the chat with the question waiting
        question.fill("what breaks if payments fails?")
        page.locator('[data-testid="first-question-submit"]').click()
        page.wait_for_url("**/ai-chat**", timeout=PAGE_TIMEOUT)
        expect(page.locator("#user-input")).to_have_value("what breaks if payments fails?")

        # the sample button loads a populated workspace
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.locator('[data-testid="first-action-sample"]').click()
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)
        expect(page.locator('[data-testid="first-message"]')).to_contain_text("Ask your company a question")
    finally:
        context.close()


@pytest.mark.parametrize("viewport", [FOLD, PHONE], ids=["1440", "390"])
def test_populated_workspace_opens_on_question_and_cited_answer(browser, live_server, seeded, viewport):
    email = seeded["emails"]["enterprise_architect"]
    context, page = _page(browser, viewport)
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        message = page.locator('[data-testid="first-message"]')
        expect(message).to_contain_text("Ask your company a question")
        expect(page.locator('[data-testid="first-question-input"]')).to_be_in_viewport()
        # the first cited answer is part of the message, with rows that cite a source
        cascade = page.locator('[data-testid="overview-cascade"]')
        expect(cascade).to_be_visible()
        expect(cascade.locator('[data-testid="overview-answer-cascade"], #overview-answer-cascade').first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert len(cascade.inner_text().strip()) > 40
        # the figures and the nine tabs come after the message
        message_bottom = message.evaluate("e => e.getBoundingClientRect().bottom")
        assert _top(page, '[data-testid="kpi-tile-applications"]') >= message_bottom
        assert _top(page, '[data-testid="layer-tab-overview"], [data-testid="layer-select-mobile"]') >= message_bottom
        if viewport["width"] == FOLD["width"]:
            # a tile still lands on a list with content
            page.locator('[data-testid="kpi-tile-applications"]').click()
            page.wait_for_url("**/applications/**", timeout=PAGE_TIMEOUT)
            assert len(page.locator("main").first.inner_text().strip()) > 50
    finally:
        context.close()

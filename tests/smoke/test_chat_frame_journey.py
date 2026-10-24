"""A new startup CTO opens the chat and finds one question box, a greeting, four questions and past chats.

The person: a self-serve owner who has just signed up and loaded the sample company. The goal: ask
the first question without hunting through controls. The chat opens on a greeting and four
questions drawn from the sample company's own records; clicking one asks it and the answer comes
back with where each fact came from. Everything else (who answers, what they look at, approvals,
export, the context and query panels) is one "Chat options" sheet away.

Every journey starts at /account/register as a new person. The signed-in person is the startup CTO
(lens "cto"), never a platform administrator, asserted in each journey.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_sign_up_first_screen_journey import SIZES, _assert_persona, _sign_up

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("ASK_WALK_DIR")


def _open_chat(browser, live_server, viewport, load_sample=True):
    context = browser.new_context(viewport=viewport)
    context.set_default_timeout(PAGE_TIMEOUT)
    if os.environ.get("ASK_THEME") == "dark":
        context.add_init_script(
            "try { localStorage.setItem('theme', 'dark'); } catch (e) {} "
            "document.documentElement.classList.add('dark');"
        )
    page = context.new_page()
    email = _sign_up(page, live_server)
    _assert_persona(email)  # lens "cto", enterprise_role not platform_admin, not a platform admin
    if load_sample:
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.locator('[data-testid="first-action-sample"]').click()
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)
    page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    return context, page


def _shot(page, name, viewport_name):
    if WALK:
        page.wait_for_timeout(600)  # let the sheet finish sliding in
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, "%s-%s-%s.png" % (name, viewport_name, os.environ.get("ASK_THEME", "light"))))


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_chat_opens_on_a_greeting_and_four_questions_from_the_sample_company(browser, live_server, name, viewport):
    context, page = _open_chat(browser, live_server, viewport)
    try:
        heading = page.get_by_role("heading", level=1)
        expect(heading).to_have_text("How can I help you today?")
        # the greeting is plain foreground text, never a gradient or coloured heading
        style = heading.evaluate("e => { const s = getComputedStyle(e); return [s.backgroundImage, s.color, getComputedStyle(document.body).color]; }")
        assert style[0] == "none", style
        assert "text-foreground" in heading.get_attribute("class")

        cards = page.locator('[data-testid="first-question-suggestion"]')
        expect(cards).to_have_count(4, timeout=PAGE_TIMEOUT)
        for i in range(4):
            text = cards.nth(i).inner_text().strip()
            assert text.endswith("?") or "?" in text, text
            assert len(text) > 15, text
        # the history panel is there with New chat and search (a slide-over at 390)
        if name == "390":
            page.get_by_role("button", name="Show chat history").click()
        expect(page.get_by_role("button", name="Start a new chat")).to_be_visible()
        expect(page.get_by_label("Search chats")).to_be_visible()
        _shot(page, "history" if name == "390" else "welcome", name)
    finally:
        context.close()


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_at_most_three_controls_sit_above_the_composer_on_first_open(browser, live_server, name, viewport):
    context, page = _open_chat(browser, live_server, viewport)
    try:
        expect(page.locator('[data-testid="first-question-suggestion"]')).to_have_count(4, timeout=PAGE_TIMEOUT)
        header = page.locator('[data-testid="chat-header-row"]')
        visible = header.locator("button:visible, a:visible, input:visible, select:visible, textarea:visible")
        assert visible.count() <= 3, [visible.nth(i).get_attribute("aria-label") for i in range(visible.count())]
        # no select, no tab strip and no export control is on the page before the options sheet opens
        for selector in ("#persona-selector", "#domain-selector", "#model-selector"):
            assert not page.locator(selector).is_visible(), selector
        assert page.get_by_role("tab").filter(visible=True).count() == 0
        _shot(page, "first-open", name)
    finally:
        context.close()


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_every_moved_control_is_two_clicks_away_in_chat_options(browser, live_server, name, viewport):
    context, page = _open_chat(browser, live_server, viewport)
    try:
        page.get_by_role("button", name="Chat options").click()
        sheet = page.get_by_role("dialog", name="Chat options")
        expect(sheet).to_be_visible()
        _shot(page, "options", name)

        # the assistant and scope selects, and a pick that is kept
        expect(sheet.locator("#persona-selector")).to_be_visible()
        scope = sheet.locator("#domain-selector")
        expect(scope).to_be_visible()
        scope.select_option("technology")
        assert scope.input_value() == "technology"
        # approvals, export and the matching page
        expect(sheet.get_by_role("button", name="Approvals for review")).to_be_visible()
        expect(sheet.get_by_role("button", name="Export conversation")).to_be_visible()
        expect(sheet.get_by_role("button", name="Export context log")).to_be_visible()
        expect(sheet.get_by_role("link", name="Match entities")).to_have_attribute("href", re.compile(r"/ai-chat/.*match|/entity"))
        # context, query and alerts panels have content
        expect(sheet.get_by_role("tab", name="Context")).to_be_visible()
        expect(sheet.locator("#domain-context")).to_be_visible()
        sheet.get_by_role("tab", name="Query").click()
        expect(sheet.locator("#nl-query-input")).to_be_visible()
        expect(sheet.get_by_role("button", name="Create a new solution from a brief")).to_be_visible()
        sheet.get_by_role("tab", name=re.compile("Alerts")).click()
        expect(sheet.locator("#health-score")).to_be_visible()

        # Approvals opens the review dialog and closes the sheet (no popup on a popup)
        sheet.get_by_role("button", name="Approvals for review").click()
        expect(page.locator("#ai-chat-approvals-modal")).to_be_visible()
        expect(sheet).to_be_hidden()
        assert page.locator("#ai-chat-approvals-modal").inner_text().strip() != ""
    finally:
        context.close()


def test_clicking_a_suggestion_asks_it_and_the_answer_cites_the_records(browser, live_server):
    context, page = _open_chat(browser, live_server, SIZES[0][1])
    try:
        cards = page.locator('[data-testid="first-question-suggestion"]')
        expect(cards).to_have_count(4, timeout=PAGE_TIMEOUT)
        question = cards.first.get_attribute("data-question")
        assert question.startswith("What breaks if ") and question.endswith(" fails?"), question
        cards.first.click()
        # the question is asked as written, the welcome goes, and the answer cites its sources
        expect(page.locator("#messages-container")).to_contain_text(question, timeout=PAGE_TIMEOUT)
        expect(page.locator("#domain-welcome-grid")).to_be_hidden()
        answer = page.get_by_test_id("estate-answer")
        expect(answer).to_be_visible(timeout=PAGE_TIMEOUT)
        rows = answer.locator("[data-answer-row]")
        assert rows.count() >= 1
        for i in range(rows.count()):
            card = rows.nth(i).locator("xpath=ancestor::*[@data-slot='card'][1]")
            assert card.locator("[data-citation]").inner_text().strip() not in ("", "Source not recorded")
        _shot(page, "answer", "1440")

        # the chat is in the history panel after a reload, and search finds it
        page.reload(wait_until="networkidle")
        history = page.locator("#session-list .js-thr")
        expect(history.first).to_be_visible(timeout=PAGE_TIMEOUT)
        page.get_by_label("Search chats").fill("zzzz-no-such-chat")
        expect(page.locator("#session-list .js-thr:visible")).to_have_count(0)
        page.get_by_label("Search chats").fill("")
        expect(history.first).to_be_visible()
        history.first.locator(".js-thr-open").click()
        expect(page.locator("#messages-container")).to_contain_text(question, timeout=PAGE_TIMEOUT)
    finally:
        context.close()


def test_an_organisation_with_no_records_is_told_what_to_do_next(browser, live_server):
    context, page = _open_chat(browser, live_server, SIZES[0][1], load_sample=False)
    try:
        briefing = page.locator("#portfolio-briefing")
        expect(briefing).to_contain_text("Import a portfolio", timeout=PAGE_TIMEOUT)
        expect(briefing.get_by_role("link", name="Import a portfolio")).to_have_attribute("href", "/import")
        assert page.locator('[data-testid="first-question-suggestion"]').count() == 0
        for phrase in ("Nothing here yet", "No data", "N/A"):
            assert phrase not in page.locator("body").inner_text()
    finally:
        context.close()

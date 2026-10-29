"""A new startup CTO opens the chat and finds one question box, a greeting, four questions and past chats.

The person: a self-serve owner who has just signed up and loaded the sample company. The goal: ask
the first question without hunting through controls. The chat opens on a greeting and four
questions drawn from the sample company's own records; clicking one asks it and the answer comes
back with where each fact came from. Everything else (who answers, what they look at, approvals,
export, the context and query panels) is one "Chat options" sheet away.

Every journey starts at /account/register as a new person. The signed-in person is the startup CTO
(lens "cto"), never a platform administrator, asserted in each journey.
"""
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server
from .test_sign_up_first_screen_journey import SIZES, _skip_to_the_dashboard
from .test_signup_brings_the_company_in_journey import (  # noqa: F401  (people is a fixture)
    assert_is_a_startup_cto_not_an_administrator,
    people,
    sign_up,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("ASK_WALK_DIR")


def _open_chat(browser, live_server, people, app, viewport, load_sample=True, locale=None):
    context = browser.new_context(viewport=viewport, **({'locale': locale} if locale else {}))
    context.set_default_timeout(PAGE_TIMEOUT)
    if os.environ.get("ASK_THEME") == "dark":
        context.add_init_script(
            "try { localStorage.setItem('theme', 'dark'); } catch (e) {} "
            "document.documentElement.classList.add('dark');"
        )
    page = context.new_page()
    email, _ = sign_up(page, live_server, people)
    assert_is_a_startup_cto_not_an_administrator(app, email)  # lens "cto", enterprise_role not platform_admin, not a platform admin
    _skip_to_the_dashboard(page)
    if load_sample:
        page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("chat-first-sample").click()
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)
    page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    return context, page


def _shot(page, name, viewport_name):
    if WALK:
        page.wait_for_timeout(600)  # let the sheet finish sliding in
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, "%s-%s-%s.png" % (name, viewport_name, os.environ.get("ASK_THEME", "light"))))


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_chat_opens_on_a_greeting_and_four_questions_from_the_sample_company(browser, live_server, people, app, name, viewport):
    context, page = _open_chat(browser, live_server, people, app, viewport)
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
def test_at_most_three_controls_sit_above_the_composer_on_first_open(browser, live_server, people, app, name, viewport):
    context, page = _open_chat(browser, live_server, people, app, viewport)
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
def test_every_moved_control_is_two_clicks_away_in_chat_options(browser, live_server, people, app, name, viewport):
    context, page = _open_chat(browser, live_server, people, app, viewport)
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


def test_clicking_a_suggestion_asks_it_and_the_answer_cites_the_records(browser, live_server, people, app):
    context, page = _open_chat(browser, live_server, people, app, SIZES[0][1])
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


def test_a_new_startup_owner_with_nothing_recorded_sees_the_startup_next_step(browser, live_server, people, app):
    context, page = _open_chat(browser, live_server, people, app, SIZES[0][1], load_sample=False)
    try:
        briefing = page.locator("#portfolio-briefing")
        steps = briefing.get_by_test_id("startup-first-steps")
        expect(steps).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(steps.get_by_role("link", name="Connect GitHub")).to_be_visible()
        expect(steps.get_by_role("button", name="See a sample company")).to_be_visible()
        # four starter questions follow, and none of them is the enterprise portfolio prompt
        cards = briefing.get_by_test_id("first-question-suggestion")
        expect(cards).to_have_count(4)
        assert "Import a portfolio" not in page.locator("body").inner_text()
        assert "Nothing flagged in your portfolio" not in page.locator("body").inner_text()
        _shot(page, "startup-empty", "1440")
        # the link lands on the connection page with content
        steps.get_by_role("link", name="Connect GitHub").click()
        page.wait_for_load_state("networkidle")
        assert "/connect" in page.url or "github" in page.url, page.url
        assert page.get_by_role("heading").first.inner_text().strip()
        # the sample button loads the company; the chat then offers questions about its records
        page.goto(live_server + "/ai-chat", wait_until="networkidle")
        page.get_by_test_id("startup-first-steps").get_by_role("button", name="See a sample company").click()
        page.goto(live_server + "/ai-chat", wait_until="networkidle")
        first = page.locator('[data-testid="first-question-suggestion"]').first
        expect(first).to_contain_text("What breaks if", timeout=PAGE_TIMEOUT)
        assert page.get_by_test_id("startup-first-steps").count() == 0
    finally:
        context.close()


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_the_chat_fills_the_screen_with_the_composer_on_the_bottom_edge(browser, live_server, people, app, name, viewport):
    context, page = _open_chat(browser, live_server, people, app, viewport)
    try:
        expect(page.locator('[data-testid="first-question-suggestion"]')).to_have_count(4, timeout=PAGE_TIMEOUT)
        box = page.locator("#chat-form").evaluate("e => { const r = e.closest('.border-t').getBoundingClientRect(); return [r.bottom, window.innerHeight, document.documentElement.scrollHeight]; }")
        assert abs(box[0] - box[1]) <= 1, box          # the composer sits on the bottom edge
        assert box[2] <= box[1] + 1, box               # and the page itself does not scroll
    finally:
        context.close()


def test_at_390_the_unavailable_notice_is_one_line_and_the_first_screen_keeps_the_questions(browser, live_server, people, app):
    context, page = _open_chat(browser, live_server, people, app, SIZES[1][1])
    try:
        cards = page.locator('[data-testid="first-question-suggestion"]')
        expect(cards).to_have_count(4, timeout=PAGE_TIMEOUT)
        notice = page.get_by_test_id("model-unavailable-notice")
        if notice.count():
            assert notice.bounding_box()["height"] <= 34, notice.bounding_box()  # one line of text
        composer_top = page.locator("#chat-form").evaluate("e => e.closest('.border-t').getBoundingClientRect().top")
        for i in range(4):
            bottom = cards.nth(i).bounding_box()
            assert bottom["y"] + bottom["height"] <= composer_top + 1, (i, bottom, composer_top)
        expect(page.get_by_role("heading", level=1)).to_be_in_viewport()
        _shot(page, "first-screen", "390")
    finally:
        context.close()


def test_history_rows_use_the_body_font_and_a_locale_date(browser, live_server, people, app):
    context, page = _open_chat(browser, live_server, people, app, SIZES[0][1], locale="en-GB")
    try:
        cards = page.locator('[data-testid="first-question-suggestion"]')
        expect(cards).to_have_count(4, timeout=PAGE_TIMEOUT)
        cards.first.click()
        expect(page.get_by_test_id("estate-answer")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.reload(wait_until="networkidle")
        row = page.locator("#session-list .js-thr-open").first
        expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
        fonts = row.evaluate("e => [getComputedStyle(e).fontFamily, getComputedStyle(e.firstElementChild).fontFamily, getComputedStyle(document.body).fontFamily]")
        assert fonts[0] == fonts[2] and fonts[1] == fonts[2], fonts
        date = row.locator("div").nth(1).inner_text()
        assert re.match(r"^\d{1,2} [A-Z][a-z]{2}\b", date), date
    finally:
        context.close()


def test_the_context_panel_names_types_in_plain_words(browser, live_server, people, app):
    context, page = _open_chat(browser, live_server, people, app, SIZES[0][1])
    try:
        page.get_by_role("button", name="Chat options").click()
        sheet = page.get_by_role("dialog", name="Chat options")
        sheet.locator("#domain-selector").select_option("architecture")
        context_panel = sheet.locator("#domain-context")
        expect(context_panel).to_contain_text("Application", timeout=PAGE_TIMEOUT)
        text = context_panel.inner_text()
        assert "ApplicationComponent" not in text and "BusinessCapability" not in text, text
        _shot(page, "context-panel", "1440")
    finally:
        context.close()


# ---- the footer names the provider that answered -------------------------------------------


class _TwoProviders(BaseHTTPRequestHandler):
    """The first provider refuses with a rate limit; the second answers."""
    hits = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        _TwoProviders.hits.append(self.path)
        if self.path.startswith("/openrouter/"):
            body = json.dumps({"error": {"message": "free-models-per-day: 0 left"}}).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        base = {"id": "c1", "created": 0, "model": "answering-model", "object": "chat.completion.chunk"}
        text = "A monolith stops being enough when teams block each other."
        frames = [{**base, "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]},
                  {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
        payload = "".join("data: %s\n\n" % json.dumps(f) for f in frames).encode() + b"data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture(scope="module")
def two_provider_server(request, app):
    _TwoProviders.hits = []
    peer = ThreadingHTTPServer(("127.0.0.1", 0), _TwoProviders)
    threading.Thread(target=peer.serve_forever, daemon=True).start()
    request.addfinalizer(peer.shutdown)
    base = "http://127.0.0.1:%d" % peer.server_port
    return boot_live_server(request, None, app, extra_env={
        "OPENROUTER_API_KEY": "first-key", "NVIDIA_API_KEY": "second-key",
        "AI_PROVIDER_ORDER": "openrouter,nvidia",
        "AI_PROVIDER_BASE_URL_OPENROUTER": base + "/openrouter/v1",
        "AI_PROVIDER_BASE_URL_NVIDIA": base + "/nvidia/v1",
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
    })


def test_when_the_first_provider_refuses_the_footer_names_the_one_that_answered(browser, two_provider_server, people, app):
    try:
        _footer_journey(browser, two_provider_server, people, app)
    except Exception:
        print("\n[two providers] server log:\n%s" % two_provider_server.tail(60))
        raise


def _footer_journey(browser, two_provider_server, people, app):
    context, page = _open_chat(browser, two_provider_server, people, app, SIZES[0][1], load_sample=False)
    try:
        page.locator('[data-testid="first-question-suggestion"]', has_text="monolith").click()
        expect(page.locator("#messages-container")).to_contain_text("teams block each other", timeout=PAGE_TIMEOUT)
        footer = page.locator("#chat-model-name")
        expect(footer).to_contain_text("Nvidia")
        assert "Openrouter" not in footer.inner_text() and "OpenRouter" not in footer.inner_text()
        asked = [h.split("/")[1] for h in _TwoProviders.hits]
        assert asked[0] == "openrouter" and asked[-1] == "nvidia", asked  # the first refused, the second answered
    finally:
        context.close()

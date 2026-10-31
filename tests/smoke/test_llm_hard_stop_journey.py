"""A person asks the chat a question while the provider the platform pays for is at its ceiling and
reads a plain pause, not an error; with the ceiling raised the same question is answered, and the
admin API settings page shows what the month's answers cost: every call the question took, at the
configured prices.

Release-rule lines 5 (sign-up to first answer), 6 (evidence on every result) and 11 (nothing
advertised that is absent); Job 6. The provider is a stub that speaks Gemini's REST shape, so the
cost shown is the usage it reported at the configured prices: 10 input and 10 output tokens at
USD 1000 per million each, USD 0.0200 a call.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server
from .test_sign_up_first_screen_journey import _skip_to_the_dashboard
from .test_signup_brings_the_company_in_journey import SKIP_WELCOME, people, sign_up  # noqa: F401  (people is a fixture)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

STUB_KEY = "AIza-journey-platform-key-0001"
ANSWER = "A good first step is to write down the three systems the team depends on most."
QUESTION = "In one sentence, what is a good first step for a new team?"
PAUSED = "Answers are paused for now. Your model and your data are untouched; try again later."
WALK = os.environ.get("LLM_WALK_DIR")


class _Gemini(BaseHTTPRequestHandler):
    """Gemini's generateContent shape: an answer and the usage it cost, for the platform's key only."""

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.calls.append(self.path)
        ok = ":generateContent" in self.path and ("key=" + STUB_KEY) in self.path
        payload = (
            {
                "candidates": [{"content": {"parts": [{"text": ANSWER}]}}],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 10, "totalTokenCount": 20},
            }
            if ok
            else {"error": {"code": 403, "message": "key refused"}}
        )
        body = json.dumps(payload).encode()
        self.send_response(200 if ok else 403)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def gemini():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Gemini)
    server.calls = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def _platform_env(gemini, ceiling_usd):
    return {
        "LLM_PROVIDER_ORDER": "gemini,nvidia",
        "GEMINI_API_KEY": STUB_KEY,
        "GEMINI_API_BASE": "http://127.0.0.1:%d" % gemini.server_port,
        "NVIDIA_API_KEY": "",
        "OPENAI_API_KEY": "",
        "LLM_SPEND_CEILING_USD_GEMINI": ceiling_usd,
        "LLM_PRICE_PER_MTOK_IN_GEMINI": "1000",
        "LLM_PRICE_PER_MTOK_OUT_GEMINI": "1000",
        "ALLOW_EXTERNAL_API_CALLS": "true",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }


@pytest.fixture(scope="module")
def paused_server(request, app, gemini):
    """The ceiling is one cent and the month's ledger already stands at two cents."""
    return boot_live_server(request, None, app, extra_env=_platform_env(gemini, "0.01"))


@pytest.fixture(scope="module")
def open_server(request, app, gemini):
    """The same platform with the ceiling raised to five dollars."""
    return boot_live_server(request, None, app, extra_env=_platform_env(gemini, "5.00"))


def _set_month_spend(app, usd):
    """The platform's ledger row for Gemini this month, as the test needs it (None removes it)."""
    from sqlalchemy import text

    from app import db

    month = datetime.utcnow().strftime("%Y-%m")
    with app.app_context():
        with db.engine.begin() as connection:
            connection.execute(text("DELETE FROM llm_spend_ledger WHERE provider = 'gemini'"))
            if usd is not None:
                connection.execute(
                    text(
                        "INSERT INTO llm_spend_ledger (provider, month, usd_spent, requests, updated_at) "
                        "VALUES ('gemini', :month, :usd, 1, now())"
                    ),
                    {"month": month, "usd": usd},
                )


def _new_owner(browser, base, people, width=1440, dark=False):
    context = browser.new_context(viewport={"width": width, "height": 900 if width > 600 else 844})
    context.add_init_script(SKIP_WELCOME)
    if dark:
        context.add_init_script("document.documentElement.classList.add('dark');")
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_up(page, base, people)
    _skip_to_the_dashboard(page)
    return context, page


def _ask(page, base, question):
    page.goto(base + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    box = page.locator("#user-input")
    expect(box).to_be_visible(timeout=PAGE_TIMEOUT)
    box.fill(question)
    box.press("Enter")


def _shot(page, name):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


def test_at_the_ceiling_the_person_reads_a_plain_pause_and_the_provider_is_not_called(
    browser, paused_server, app, people, gemini
):
    _set_month_spend(app, 0.02)
    calls_before = len(gemini.calls)
    context, page = _new_owner(browser, str(paused_server), people)
    try:
        _ask(page, str(paused_server), QUESTION)
        reply = page.locator(".message-content", has_text="Answers are paused for now")
        expect(reply.first).to_be_visible(timeout=PAGE_TIMEOUT * 2)
        assert PAUSED in reply.first.inner_text()
        body = page.locator("body").inner_text()
        assert "Traceback" not in body and "Exception" not in body
        # The pause is the whole reply: nothing hangs under it, and the banner says paused, not "no model".
        assert "could not be completed" not in body, body
        expect(page.get_by_test_id("chat-provider-settings-link")).to_have_count(0)
        expect(page.get_by_test_id("model-unavailable-notice")).to_contain_text("AI paused. Records still answer.")
        # The footer under the pause names the pause and claims no source: nothing answered it.
        footer = page.locator(".js-evidence-toggle").last
        expect(footer).to_contain_text("Answers are paused; nothing was read for this reply.")
        assert "snapshot" not in footer.inner_text() and "No portfolio data was read" not in footer.inner_text()
        assert len(gemini.calls) == calls_before, "the provider was called although its ceiling was reached"
        _shot(page, "paused-1440-light")
    finally:
        context.close()

    context, page = _new_owner(browser, str(paused_server), people, width=390, dark=True)
    try:
        _ask(page, str(paused_server), QUESTION)
        reply = page.locator(".message-content", has_text="Answers are paused for now")
        expect(reply.first).to_be_visible(timeout=PAGE_TIMEOUT * 2)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        _shot(page, "paused-390-dark")
    finally:
        context.close()


def test_with_the_ceiling_raised_the_same_question_is_answered_and_the_cost_is_shown(
    browser, open_server, app, people, gemini
):
    _set_month_spend(app, None)
    calls_before = len(gemini.calls)
    context, page = _new_owner(browser, str(open_server), people)
    try:
        _ask(page, str(open_server), QUESTION)
        reply = page.locator(".message-content", has_text="three systems the team depends on most")
        expect(reply.first).to_be_visible(timeout=PAGE_TIMEOUT * 2)
        # One question costs more than one call: the interpreter that matches the question to the
        # records and the answer itself both go to the platform's provider, and each is on the ledger.
        made = len(gemini.calls) - calls_before
        assert made >= 1, gemini.calls[calls_before:]
        spent = 0.02 * made
        _shot(page, "answered-1440-light")

        page.goto(str(open_server) + "/admin/api-settings", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        ledger = page.get_by_test_id("llm-spend-ledger")
        expect(ledger).to_be_visible(timeout=PAGE_TIMEOUT)
        row = ledger.locator("[data-testid='llm-spend-row'][data-provider='gemini']")
        expect(row).to_have_count(1)
        expect(row.get_by_test_id("llm-spend-usd")).to_have_text("USD %.4f" % spent)
        expect(row.get_by_test_id("llm-spend-remaining")).to_have_text("USD %.4f" % (5.0 - spent))
        assert "Answering" in row.inner_text()
        _shot(page, "ledger-1440-light")

        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate("document.documentElement.classList.add('dark')")
        page.wait_for_timeout(300)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        _shot(page, "ledger-390-dark")
    finally:
        context.close()

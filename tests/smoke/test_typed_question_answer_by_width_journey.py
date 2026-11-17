"""A typed question gets the same kind of answer on a phone as on a desktop, and the history counts every message.

The person: the owner of a fresh workspace with the sample company loaded, asking the chat what the
estate runs on and which repository files say so. The server is set up as production is: the
DeepSeek provider order, key and spend ceiling, with the existing DeepSeek stub on a loopback port
as the provider (tests/deepseek_stub.py, the fixture the DeepSeek journey uses). When the platform
asks the provider which catalogue entry fits, the stub answers the way a model that over-reads the
word "repository" does: "what came from GitHub". Every other request gets the stub's sentence, so
the words on screen are the provider's reply reaching the person, not a list of records.

The goal, at 1440 light and at 390 dark:
- the typed question is answered in a sentence, not by a card of record names that repeats the
  question back;
- after three questions the chat history says how many messages the conversation holds;
- the first question the chat offers is answered from the connected records, with citations, or
  says plainly what is missing.
"""
import json
import os
import re

import pytest
from playwright.sync_api import expect

from tests.deepseek_stub import KEY, DeepSeekStub

from .conftest import PAGE_TIMEOUT, boot_live_server
from .test_ask_in_the_chat_journey import _login
from .test_try_sample_company_journey import _empty_organisation_user

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("PHONE_ANSWER_WALK_DIR")
REPLY = "Your estate runs on Docker Compose; the compose file in the repository says so."
TYPED = "What does Archie Ea run on and which repository files say so?"
OTHER_QUESTIONS = ("In one sentence, how would you describe our estate?", "Give me a short summary of our technology.")
OTHERS = ("OPENAI", "ANTHROPIC", "AZURE", "GEMINI", "GOOGLE", "NVIDIA", "OPENROUTER", "HUGGINGFACE", "CLAUDE")
WIDTHS = [(1440, False), (390, True)]


def _provider(model, text, has_tools):
    """Which catalogue entry fits: the over-reading answer. Anything else: the sentence."""
    if "Map the user's question onto exactly one of these catalogue entries" in text:
        return json.dumps({"entry_id": "github_sourced_records" if "repository files" in text else None, "params": {}})
    return None


@pytest.fixture(scope="module")
def stub():
    s = DeepSeekStub(reply=REPLY, respond=_provider)
    yield s
    s.close()


@pytest.fixture(scope="module")
def server(request, app, stub):
    env = {name + "_API_KEY": "" for name in OTHERS}
    env.update(
        LLM_PROVIDER_ORDER="deepseek", DEEPSEEK_API_KEY=KEY, DEEPSEEK_API_BASE=stub.base_url,
        LLM_SPEND_CEILING_USD_DEEPSEEK="62", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
    )
    return boot_live_server(request, None, app, extra_env=env)


def _shot(page, name, width, dark):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, "%s-%d-%s.png" % (name, width, "dark" if dark else "light")))


def _open_chat_with_sample(browser, base, width, dark):
    email, _org = _empty_organisation_user(base)
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    _login(page, base, email)
    page.goto(base + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.get_by_test_id("chat-first-sample").click()
    expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)
    page.goto(base + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    expect(page.locator("#user-input")).to_be_visible(timeout=PAGE_TIMEOUT)
    if dark:
        page.evaluate("document.documentElement.classList.add('dark')")
        assert page.evaluate("document.documentElement.classList.contains('dark')")
    return context, page


def _ask(page, question):
    page.locator("#user-input").fill(question)
    page.locator("#user-input").press("Enter")


@pytest.mark.parametrize("width,dark", WIDTHS)
def test_the_typed_question_is_answered_in_words_and_the_history_counts_every_message(browser, server, stub, width, dark):
    context, page = _open_chat_with_sample(browser, server, width, dark)
    try:
        # The typed question: a sentence from the provider, never the "What came from GitHub?" card.
        _ask(page, TYPED)
        expect(page.get_by_text(REPLY)).to_have_count(1, timeout=PAGE_TIMEOUT * 2)
        messages = page.locator("#messages-container").inner_text()
        assert "What came from GitHub?" not in messages, messages[:400]
        assert page.locator("[data-catalogue-cards]").count() == 0
        assert any("repository files" in r["text"] and "Map the user's question" not in r["text"] for r in stub.requests), (
            "the typed question never reached the provider as a chat turn"
        )
        _shot(page, "typed-answer", width, dark)

        # Two more questions in the same conversation.
        for index, question in enumerate(OTHER_QUESTIONS, start=2):
            _ask(page, question)
            expect(page.get_by_text(REPLY)).to_have_count(index, timeout=PAGE_TIMEOUT * 2)

        # Three questions, three answers: six messages, and the history says six.
        history = page.locator("#session-list")
        expect(history).to_contain_text("6 messages", timeout=PAGE_TIMEOUT)
        text = history.text_content()
        assert re.search(r"\b6 messages\b", text) and not re.search(r"\b[24] messages\b", text), text
        _shot(page, "history-count", width, dark)
    finally:
        context.close()


@pytest.mark.parametrize("width,dark", WIDTHS)
def test_the_first_offered_question_is_answered_from_the_records_with_citations(browser, server, width, dark):
    context, page = _open_chat_with_sample(browser, server, width, dark)
    try:
        first = page.get_by_test_id("first-questions").locator("button").first
        expect(first).to_be_visible(timeout=PAGE_TIMEOUT)
        offered = first.inner_text().strip()
        assert offered, "the first offered question has no words"
        first.click()
        answer = page.get_by_test_id("estate-answer").or_(page.locator("[data-catalogue-cards]")).first
        expect(answer).to_be_visible(timeout=PAGE_TIMEOUT * 2)
        text = answer.inner_text()
        assert "Nothing is recorded as connected to this yet" not in text, text[:300]
        links = answer.locator("a[href^='/']")
        assert links.count() > 0, "the first offered question cites no record: %s" % text[:300]
        _shot(page, "first-offered-question", width, dark)
        href = links.first.get_attribute("href")
        page.goto(page.url.split("/ai-chat")[0] + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator("main").inner_text().strip(), "the cited record opens an empty page"
    finally:
        context.close()

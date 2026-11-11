"""A startup CTO asks the AI chat about the estate on a server configured the way production is.

The person: the owner of a fresh workspace who has loaded the sample company. The server is set up
as production is: LLM_PROVIDER_ORDER=deepseek, a DeepSeek key, a spend ceiling. DeepSeek is a stub
on a loopback port (no real provider is reached). The goal: ask what breaks if the payments service
fails and read a cited answer naming the linked systems; ask the assistant a question only the model
can answer and read its reply; see the model named as DeepSeek's; and, with the month's spend at the
ceiling, read the plain pause instead of an error.
"""
import re

import pytest
from playwright.sync_api import expect

from tests.deepseek_stub import KEY, DeepSeekStub

from .conftest import PAGE_TIMEOUT, boot_live_server
from .test_ask_in_the_chat_journey import QUESTION, _login
from .test_try_sample_company_journey import _empty_organisation_user

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

REPLY = "Your estate is ten systems; the payments service sits at the centre."
OTHERS = ("OPENAI", "ANTHROPIC", "AZURE", "GEMINI", "GOOGLE", "NVIDIA", "OPENROUTER", "HUGGINGFACE", "CLAUDE")


@pytest.fixture(scope="module")
def stub():
    s = DeepSeekStub(reply=REPLY)
    yield s
    s.close()


@pytest.fixture(scope="module")
def deepseek_server(request, app, stub):
    env = {name + "_API_KEY": "" for name in OTHERS}
    env.update(
        LLM_PROVIDER_ORDER="deepseek", DEEPSEEK_API_KEY=KEY, DEEPSEEK_API_BASE=stub.base_url,
        LLM_SPEND_CEILING_USD_DEEPSEEK="62", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
    )
    return boot_live_server(request, None, app, extra_env=env)


@pytest.mark.parametrize("width", [1440, 390])
def test_the_cto_gets_a_cited_estate_answer_and_a_model_reply_through_deepseek(browser, deepseek_server, stub, width):
    base = deepseek_server
    email, _org = _empty_organisation_user(base)
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        _login(page, base, email)
        page.goto(base + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("chat-first-sample").click()
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)
        page.goto(base + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        expect(page.locator("#user-input")).to_be_visible(timeout=PAGE_TIMEOUT)

        # The scope line names the model that will answer: DeepSeek's, not "none configured".
        scope = page.get_by_test_id("chat-scope").inner_text()
        assert "deepseek" in scope.lower(), scope
        assert "DeepSeek" in scope and "Deepseek" not in scope, scope

        # What breaks if the payments service fails: linked systems, each with where it was read from.
        page.locator("#user-input").fill(QUESTION)
        page.locator("#user-input").press("Enter")
        answer = page.get_by_test_id("estate-answer")
        expect(answer).to_be_visible(timeout=PAGE_TIMEOUT)
        rows = answer.locator("[data-answer-row]")
        assert rows.count() >= 10
        for i in range(rows.count()):
            card = rows.nth(i).locator("xpath=ancestor::*[@data-slot='card'][1]")
            assert card.locator("[data-citation]").inner_text().strip() not in ("", "Source not recorded")
        assert re.search(r"\d+ connections? in your records", answer.inner_text())

        # A question only the model answers: the stub's reply reaches the person, through DeepSeek.
        page.locator("#user-input").fill("In one sentence, how would you describe our estate?")
        page.locator("#user-input").press("Enter")
        expect(page.get_by_text(REPLY)).to_be_visible(timeout=PAGE_TIMEOUT * 2)
        assert stub.requests, "the model question never reached DeepSeek"
        assert {r["model"] for r in stub.requests} == {"deepseek-flash"}
        # After the reply the scope line names who answered, with the same spelling as before it.
        expect(page.get_by_test_id("chat-scope")).to_contain_text("deepseek-flash", timeout=PAGE_TIMEOUT)
        after = page.get_by_test_id("chat-scope").inner_text()
        assert "DeepSeek" in after and "Deepseek" not in after and "deepseek-flash" in after, after
        assert "No enabled LLM provider" not in page.locator("body").inner_text()
    finally:
        context.close()


@pytest.fixture(scope="module")
def paused_server(request, app, stub):
    env = {name + "_API_KEY": "" for name in OTHERS}
    env.update(
        LLM_PROVIDER_ORDER="deepseek", DEEPSEEK_API_KEY=KEY, DEEPSEEK_API_BASE=stub.base_url,
        LLM_SPEND_CEILING_USD_DEEPSEEK="0.0001", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
    )
    return boot_live_server(request, None, app, extra_env=env)


def test_at_the_ceiling_the_chat_says_answers_are_paused_not_that_no_model_is_configured(browser, paused_server, app):
    from sqlalchemy import text

    from app import db
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    with app.app_context():
        with db.engine.begin() as connection:
            connection.execute(text("DELETE FROM llm_spend_ledger"))
        LLMService.record_spend("deepseek", "deepseek-flash", 100000, 100000)  # well over the ceiling
    email, _org = _empty_organisation_user(paused_server)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        _login(page, paused_server, email)
        page.goto(paused_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        scope = page.get_by_test_id("chat-scope").inner_text()
        assert "answers are paused" in scope and "none configured" not in scope, scope
        assert "Model: none, answers are paused" in scope, scope
    finally:
        context.close()
        with app.app_context():
            with db.engine.begin() as connection:
                connection.execute(text("DELETE FROM llm_spend_ledger"))


@pytest.mark.parametrize("width,dark", [(1440, False), (390, True)])
def test_after_a_paused_turn_the_scope_line_still_says_answers_are_paused(browser, paused_server, app, width, dark):
    from sqlalchemy import text

    from app import db
    from app.modules.ai_chat.services.llm_service_impl import PAUSED_MESSAGE, LLMService

    with app.app_context():
        with db.engine.begin() as connection:
            connection.execute(text("DELETE FROM llm_spend_ledger"))
        LLMService.record_spend("deepseek", "deepseek-flash", 100000, 100000)  # well over the ceiling
    email, _org = _empty_organisation_user(paused_server)
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        _login(page, paused_server, email)
        page.goto(paused_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        if dark:
            page.evaluate("document.documentElement.classList.add('dark')")
            assert page.evaluate("document.documentElement.classList.contains('dark')")
        page.locator("#user-input").fill("In one sentence, how would you describe our estate?")
        page.locator("#user-input").press("Enter")
        expect(page.get_by_text(PAUSED_MESSAGE.split(".")[0]).first).to_be_visible(timeout=PAGE_TIMEOUT * 2)
        scope = page.get_by_test_id("chat-scope").inner_text()
        assert "answers are paused" in scope and "no model answered" not in scope, scope
        assert "Model: none, answers are paused" in scope, scope
    finally:
        context.close()
        with app.app_context():
            with db.engine.begin() as connection:
                connection.execute(text("DELETE FROM llm_spend_ledger"))

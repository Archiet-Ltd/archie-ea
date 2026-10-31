"""The chat's model picker, the models endpoint and the runtime resolver name the same provider
for an organisation with no provider rows, under the platform rule.

Commit 1d2bd1e81 (``app/modules/ai_chat/services/llm_service_impl.py``) made the platform's own
keys (keys in the environment) usable only for a provider named in ``LLM_PROVIDER_ORDER`` and
within a spend ceiling (``LLM_SPEND_CEILING_USD_<PROVIDER>``); a bare key with no order and no
ceiling now resolves to nothing, which an earlier draft of this journey did not assert under.
Two cases, both read from the same three surfaces -- the Chat options sheet, ``GET
/ai-chat/models`` and the resolver verdict ``GET /health`` carries (``LLMService.
configuration_status()``, the function the models endpoint itself falls back to): a platform key
for a provider in the order with a ceiling, where all three name it; and a key for a provider
outside the order, where all three name none. Nothing is sent to any provider.
"""
import os

import pytest
from playwright.sync_api import expect

from .chat_ask import open_chat, sign_in
from .conftest import PAGE_TIMEOUT, boot_live_server  # noqa: F401
from .fresh_org import create_fresh_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PROVIDER_KEY = "journey-key-never-sent"
WALK = os.environ.get("LLM_WALK_DIR")


@pytest.fixture(scope="module")
def configured_server(request, app):
    """OpenAI named in the order with a spend ceiling -- the rule's one open case."""
    return boot_live_server(request, None, app, extra_env={
        "LLM_PROVIDER_ORDER": "openai",
        "OPENAI_API_KEY": PROVIDER_KEY,
        "LLM_SPEND_CEILING_USD_OPENAI": "5.00",
    })


@pytest.fixture(scope="module")
def unconfigured_server(request, app):
    """The same key, outside the order: the platform never calls it."""
    return boot_live_server(request, None, app, extra_env={"OPENAI_API_KEY": PROVIDER_KEY})


def _open_options_sheet(page, base):
    email = create_fresh_org("solution_architect")["emails"]["solution_architect"]
    sign_in(page, base, email)
    open_chat(page, base)
    page.get_by_role("button", name="Chat options", exact=True).click()
    sheet = page.get_by_role("dialog", name="Chat options")
    expect(sheet).to_be_visible()
    return sheet


def _shot(page, name):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


def test_the_sheet_the_endpoint_and_the_resolver_agree_on_a_platform_key_in_the_order_with_a_ceiling(
    browser, configured_server
):
    base = configured_server
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sheet = _open_options_sheet(page, base)
        expect(sheet.locator("#model-selector-empty-note")).to_contain_text("One AI model is configured")
        _shot(page, "configured-1440-light")

        body = page.request.get(base + "/ai-chat/models").json()
        assert body["success"] is True
        assert body["current_provider"] == "openai" and body["current_model"] == "gpt-4o", body
        assert [(m["provider"], m["model"]) for m in body["models"]] == [("openai", "gpt-4o")]

        # The resolver's own verdict, read the way /health reads it -- the same function
        # _get_configured_chat_models() falls back to, so the three surfaces cannot disagree.
        health = page.request.get(base + "/health").json()
        resolver = health["checks"]["llm_providers"]
        assert resolver["provider"] == "openai", resolver
    finally:
        context.close()


def test_the_sheet_the_endpoint_and_the_resolver_agree_on_a_platform_key_outside_the_order(
    browser, unconfigured_server
):
    base = unconfigured_server
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sheet = _open_options_sheet(page, base)
        expect(sheet.locator("#model-selector-empty-note")).to_contain_text("No AI models are configured")
        _shot(page, "unconfigured-1440-light")

        body = page.request.get(base + "/ai-chat/models").json()
        assert body["success"] is True
        assert body["current_provider"] is None and body["current_model"] is None, body
        assert body["models"] == []

        health = page.request.get(base + "/health").json()
        resolver = health["checks"]["llm_providers"]
        assert resolver["provider"] is None, resolver
    finally:
        context.close()

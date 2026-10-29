"""The chat's model picker and its runtime name the same provider for an organisation with no provider rows.

With one provider key in the server's environment and no provider configured for the organisation, the
Chat options sheet says one model is configured (the environment one), and the models endpoint names the
same provider and model the runtime resolves. Nothing is sent to the provider.
"""
import pytest
from playwright.sync_api import expect

from .chat_ask import open_chat, sign_in
from .conftest import PAGE_TIMEOUT, boot_live_server  # noqa: F401
from .fresh_org import create_fresh_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def environment_provider_server(request, app):
    return boot_live_server(request, None, app, extra_env={"OPENAI_API_KEY": "journey-key-never-sent"})


def test_the_picker_names_the_environment_provider_for_an_organisation_without_rows(browser, environment_provider_server):
    base = environment_provider_server
    email = create_fresh_org("solution_architect")["emails"]["solution_architect"]
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, base, email)
        open_chat(page, base)
        page.get_by_role("button", name="Chat options", exact=True).click()
        sheet = page.get_by_role("dialog", name="Chat options")
        expect(sheet).to_be_visible()
        expect(sheet.locator("#model-selector-empty-note")).to_contain_text("One AI model is configured")

        body = page.request.get(base + "/ai-chat/models").json()
        assert body["success"] is True
        assert body["current_provider"] == "openai" and body["current_model"] == "gpt-4o", body
        assert [(m["provider"], m["model"]) for m in body["models"]] == [("openai", "gpt-4o")]
    finally:
        context.close()

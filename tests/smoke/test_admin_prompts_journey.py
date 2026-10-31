"""A platform administrator edits a chat persona's system prompt and the chat uses it.

* Every field on the page has a label a person can find it by.
* An edited system prompt is saved, still there after a reload, and the chat's own
  prompt builder returns it for that persona (the model is never called).
* Saving with nothing filled in is refused in words and the typed text is kept.
* Reset returns the persona to its built-in prompt, in the page and in the chat.
"""
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PERSONA = "capability_architect"


def _clear_override(app):
    from app import db
    from app.models.ai_service import AIPromptTemplate

    with app.app_context():
        AIPromptTemplate.query.filter_by(name="persona_override_" + PERSONA).delete()
        db.session.commit()


def _chat_prompt(app):
    """The system prompt the chat would send for this persona (no model call)."""
    from app.modules.ai_chat.services.multi_domain_chat_service import MultiDomainChatService

    with app.app_context():
        return MultiDomainChatService()._get_persona_system_prompt(PERSONA, "capability", {})


def _open(page, live_server):
    page.goto(live_server + "/ai-chat/admin/prompts", timeout=PAGE_TIMEOUT)
    page.get_by_label("Persona", exact=True).wait_for(timeout=PAGE_TIMEOUT)
    expect(page.locator("#persona option").nth(1)).to_be_attached(timeout=PAGE_TIMEOUT)
    page.get_by_label("Persona", exact=True).select_option(PERSONA)
    expect(page.get_by_label("System prompt")).to_be_visible(timeout=PAGE_TIMEOUT)


def test_administrator_edits_a_system_prompt_and_the_chat_uses_it(app, browser, live_server, seeded):
    _clear_override(app)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        _open(page, live_server)

        # Every field is found by its label, as a person finds it.
        for label in ("Persona", "System prompt", "Description"):
            expect(page.get_by_label(label, exact=True)).to_be_visible()

        marker = "journey-" + uuid.uuid4().hex[:8]
        text = "You answer only in haiku. " + marker
        page.get_by_label("System prompt").fill(text)
        page.get_by_role("button", name=re.compile("save changes", re.I)).click()
        expect(page.locator("body")).to_contain_text("updated", timeout=PAGE_TIMEOUT)

        page.reload(timeout=PAGE_TIMEOUT)
        _open(page, live_server)
        assert page.get_by_label("System prompt").input_value() == text
        expect(page.locator("body")).to_contain_text("Overridden")

        assert marker in _chat_prompt(app), "the chat did not use the edited system prompt"
    finally:
        page.close()
        _clear_override(app)


def test_an_empty_save_is_refused_in_words_and_the_text_is_kept(app, browser, live_server, seeded):
    _clear_override(app)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        _open(page, live_server)
        page.get_by_label("Description").fill("")
        page.get_by_label("System prompt").fill("")
        while page.locator("[data-prompt-row]").count():
            page.locator("[data-prompt-row]").first.get_by_role("button", name="Remove sample prompt").click()
        page.get_by_role("button", name=re.compile("save changes", re.I)).click()
        expect(page.get_by_test_id("prompts-error")).to_contain_text("No fields provided", timeout=PAGE_TIMEOUT)
        assert page.get_by_label("System prompt").input_value() == ""
        assert _chat_prompt(app), "the built-in prompt must stay in use"
    finally:
        page.close()
        _clear_override(app)


def test_reset_returns_the_persona_to_its_built_in_prompt(app, browser, live_server, seeded):
    _clear_override(app)
    builtin = _chat_prompt(app)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        _open(page, live_server)
        marker = "reset-" + uuid.uuid4().hex[:8]
        page.get_by_label("System prompt").fill("Temporary prompt " + marker)
        page.get_by_role("button", name=re.compile("save changes", re.I)).click()
        expect(page.locator("body")).to_contain_text("updated", timeout=PAGE_TIMEOUT)
        assert marker in _chat_prompt(app)

        page.get_by_role("button", name="Reset to defaults").click()
        page.get_by_role("alertdialog").get_by_role("button", name="Reset persona").click()
        expect(page.locator("body")).to_contain_text("reset to defaults", timeout=PAGE_TIMEOUT)
        assert marker not in _chat_prompt(app)
        assert _chat_prompt(app) == builtin
    finally:
        page.close()
        _clear_override(app)


def test_the_page_holds_no_horizontal_scroll_on_a_phone(browser, live_server, seeded):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        _open(page, live_server)
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0, f"page scrolls sideways by {overflow}px"
    finally:
        page.close()


def test_a_saved_prompt_never_drops_the_evidence_rules(app, browser, live_server, seeded):
    """An override replaces the persona's tone, not the rule that keeps answers cited."""
    from app.modules.ai_chat.services.architect_persona_charters import _EVIDENCE_RULES

    _clear_override(app)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        _open(page, live_server)
        marker = "journey-" + uuid.uuid4().hex[:8]
        page.get_by_label("System prompt").fill("You answer only in haiku. " + marker)
        page.get_by_role("button", name=re.compile("save changes", re.I)).click()
        expect(page.locator("body")).to_contain_text("updated", timeout=PAGE_TIMEOUT)
        prompt = _chat_prompt(app)
        assert marker in prompt, "the chat did not use the saved prompt"
        assert "NO FABRICATION" in prompt and _EVIDENCE_RULES.strip() in prompt, (
            "the saved prompt dropped the evidence and no-fabrication rules"
        )

        expect(page.locator("body")).to_contain_text(
            "A saved prompt replaces the built-in one in every new chat; the evidence rules always apply."
        )
    finally:
        page.close()
        _clear_override(app)

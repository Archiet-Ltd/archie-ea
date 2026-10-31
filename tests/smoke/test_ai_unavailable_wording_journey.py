"""T-ai-plain-01: when no AI service is set up, every AI control says so in
one plain sentence a person can act on, never program text.

The person: the demo enterprise architect, who is not an organisation
administrator, opens two AI controls while no provider is configured for the
organisation (the seeded fixture's steady state -- see conftest.py's
`ai_protocol_stub`, which stays None unless SMOKE_AI_PROTOCOL_STUB=1). Each
control shows the same plain sentence and tells a non-administrator to ask
one, never a quoted code name or the words "LLM provider". An organisation
administrator reads the same sentence with a working link to the page that
fixes it.

The page guide widget -- the third control the brief names -- is covered in
tests/smoke/test_page_guide_history_journey.py's own dedicated invocation
(AI_PAGE_GUIDE_ENABLED=true), since the widget renders nowhere at all without
that flag and CI already runs that file as its own job.
"""
import os
import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ADMIN_SENTENCE_TAIL = "An administrator can add an AI key under Settings, AI and models."
ASK_SENTENCE_TAIL = "Ask your administrator to add an AI key under Settings, AI and models."
FORBIDDEN_SNIPPETS = ("LLM provider", "provider must be configured", "'suggestions'", "'chat'", "'entity_matching'")

# Opt-in walk screenshots, same pattern as tests/smoke/test_llm_hard_stop_journey.py's
# WALK/_shot: off by default so ordinary runs never write files, populated on
# request for the report's walk evidence.
WALK = os.environ.get("AIPLAIN01_WALK_DIR")


def _shot(page, name):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", force=True, no_wait_after=True)
    except TypeError:                     # newer Playwright dropped the kwarg
        page.locator("#submit").dispatch_event("click")
    try:
        page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


@pytest.fixture(scope="module")
def value_stream_id(app, seeded):
    """One value stream in the seeded organisation, created the way a real
    request does: ValueStream is TenantMixin, so organization_id comes from
    g.current_org_id, supplied explicitly inside a request context rather than
    guessed outside one. A real commit, not db_session's rollback-only
    transaction, because the live_server subprocess reads over its own
    database connection."""
    from app import db
    from flask import g
    from app.models.unified_capability import ValueStream

    with app.app_context():
        db.session.remove()
        with app.test_request_context("/"):
            g.current_org_id = seeded["ids"]["org"]
            vs = ValueStream(
                organization_id=seeded["ids"]["org"],
                name="AI wording journey value stream",
                code="AIPLAIN01-%s" % uuid.uuid4().hex[:8],
                value_stream_type="customer_facing",
            )
            db.session.add(vs)
            db.session.commit()
            created_id = vs.id
        db.session.remove()
    return created_id


def test_enterprise_architect_reads_the_plain_sentence(browser, live_server, seeded, value_stream_id):
    """Suggest mappings (the value stream card) and Match entities (the entity
    matching chat) both tell a non-administrator the same thing, in their own
    words -- not the card's banner and not the chat's generic failure line."""
    email = seeded["emails"]["enterprise_architect"]
    page = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme="dark")
    try:
        _login(page, live_server, email)

        # -- Suggest mappings -------------------------------------------------
        page.goto(
            live_server + "/value-streams/%d" % value_stream_id,
            wait_until="networkidle",
            timeout=PAGE_TIMEOUT,
        )
        suggest_button = page.get_by_role("button", name="Suggest capability mappings")
        suggest_button.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        suggest_button.click()

        title = page.locator('[data-testid="ai-suggestions-unavailable-title"]')
        title.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        description = page.locator('[data-testid="ai-suggestions-unavailable-description"]')
        card_text = title.inner_text() + " " + description.inner_text()

        assert "AI suggestions are not set up for your organisation yet." in card_text
        assert ASK_SENTENCE_TAIL in card_text
        for bad in FORBIDDEN_SNIPPETS:
            assert bad not in card_text, card_text
        assert page.get_by_role("link", name="Settings, AI and models").count() == 0, (
            "a non-administrator must not be offered the settings link"
        )
        _shot(page, "suggest-mappings-ask-390-dark")

        # -- Match entities -----------------------------------------------------
        page.goto(
            live_server + "/ai-chat/entity-matching",
            wait_until="networkidle",
            timeout=PAGE_TIMEOUT,
        )
        page.get_by_placeholder("Type your message or paste document text here...").fill(
            "We are integrating a new payment gateway with the order service."
        )
        with page.expect_response(
            lambda r: "/ai-chat/entity-matching" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ) as resp:
            page.get_by_role("button", name="Send message").click()
        assert resp.value.status == 503, resp.value.text()

        reply = page.locator(".message.assistant .message-content").last
        reply.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        reply_text = reply.inner_text()
        assert "Entity matching is not set up for your organisation yet." in reply_text
        assert ASK_SENTENCE_TAIL in reply_text
        _shot(page, "entity-matching-ask-390-dark")
        for bad in FORBIDDEN_SNIPPETS:
            assert bad not in reply_text, reply_text
        assert reply.locator("a").count() == 0, "a non-administrator must not be offered a settings link"
    finally:
        page.close()


def test_entity_matching_buttons_use_the_library(browser, live_server, seeded):
    """R36b: the Match entities chat page's action controls render through the
    library's button component, not a hand-written <button>. The behaviour a
    person relies on must survive the conversion: Send submits by click, a
    message sends by click and by Enter, Shift+Enter adds exactly one new line
    without sending, focus returns to the composer once a turn finishes, and
    every icon-only button keeps its aria-label."""
    email = seeded["emails"]["enterprise_architect"]
    page = browser.new_page(viewport={"width": 1440, "height": 900}, color_scheme="light")
    try:
        _login(page, live_server, email)
        page.goto(
            live_server + "/ai-chat/entity-matching",
            wait_until="networkidle",
            timeout=PAGE_TIMEOUT,
        )

        box = page.get_by_placeholder("Type your message or paste document text here...")
        send = page.get_by_role("button", name="Send message")

        # -- a click on Send sends the message -----------------------------------
        box.fill("We are integrating a new payment gateway with the order service.")
        with page.expect_response(
            lambda r: "/ai-chat/entity-matching" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ):
            send.click()
        page.locator(".message.assistant .message-content").last.wait_for(
            state="visible", timeout=PAGE_TIMEOUT
        )

        # -- focus returns to the composer once the turn finishes ---------------
        assert page.evaluate(
            "() => document.activeElement === document.querySelector('[x-ref=\"chatInput\"]')"
        ), "focus must return to the message box after sending"

        # -- Enter sends; Shift+Enter adds exactly one new line and does not send
        before = page.locator(".message.user").count()
        box.click()
        box.type("First line")
        box.press("Shift+Enter")
        box.type("Second line")
        page.wait_for_timeout(300)
        assert page.locator(".message.user").count() == before, (
            "Shift+Enter must not send"
        )
        assert box.input_value() == "First line\nSecond line", (
            "Shift+Enter must add exactly one new line"
        )
        with page.expect_response(
            lambda r: "/ai-chat/entity-matching" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ):
            box.press("Enter")
        page.wait_for_function(
            "n => document.querySelectorAll('.message.user').length === n",
            arg=before + 1,
            timeout=PAGE_TIMEOUT,
        )
        page.locator(".message.assistant .message-content").last.wait_for(
            state="visible", timeout=PAGE_TIMEOUT
        )
        assert box.input_value() == "", "the box must clear after Enter sends"
        assert page.evaluate(
            "() => document.activeElement === document.querySelector('[x-ref=\"chatInput\"]')"
        ), "focus must return to the message box after Enter sends"

        # -- every icon-only button keeps its aria-label -------------------------
        buttons = page.locator("button").all()
        assert buttons, "the page must render at least one button"
        for el in buttons:
            if (el.inner_text() or "").strip():
                continue
            assert (el.get_attribute("aria-label") or "").strip(), (
                "an icon-only button must carry an aria-label"
            )
        assert send.get_attribute("aria-label") == "Send message"
    finally:
        page.close()


def test_organisation_administrator_gets_a_working_settings_link(browser, live_server, seeded, value_stream_id):
    """An organisation administrator reads the admin half of the sentence, and
    the link next to it lands on the page that actually fixes the gap, on
    both controls."""
    email = seeded["emails"]["platform_admin"]
    page = browser.new_page(viewport={"width": 1440, "height": 900}, color_scheme="light")
    try:
        _login(page, live_server, email)

        # -- Suggest mappings -------------------------------------------------
        page.goto(
            live_server + "/value-streams/%d" % value_stream_id,
            wait_until="networkidle",
            timeout=PAGE_TIMEOUT,
        )
        suggest_button = page.get_by_role("button", name="Suggest capability mappings")
        suggest_button.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        suggest_button.click()

        title = page.locator('[data-testid="ai-suggestions-unavailable-title"]')
        title.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        description = page.locator('[data-testid="ai-suggestions-unavailable-description"]')
        card_text = title.inner_text() + " " + description.inner_text()
        assert "AI suggestions are not set up for your organisation yet." in card_text
        assert ADMIN_SENTENCE_TAIL in card_text
        for bad in FORBIDDEN_SNIPPETS:
            assert bad not in card_text, card_text
        _shot(page, "suggest-mappings-admin-1440-light")

        link = page.get_by_role("link", name="Settings, AI and models")
        link.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        link.click()
        page.wait_for_load_state("domcontentloaded")
        assert "/admin/api-settings" in page.url
        assert len(page.locator("main").inner_text().strip()) > 40, (
            "the settings link must land on a page with real content, not an empty one"
        )

        # -- Match entities -----------------------------------------------------
        page.goto(
            live_server + "/ai-chat/entity-matching",
            wait_until="networkidle",
            timeout=PAGE_TIMEOUT,
        )
        page.get_by_placeholder("Type your message or paste document text here...").fill(
            "We are integrating a new payment gateway with the order service."
        )
        with page.expect_response(
            lambda r: "/ai-chat/entity-matching" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ) as resp:
            page.get_by_role("button", name="Send message").click()
        assert resp.value.status == 503, resp.value.text()

        reply = page.locator(".message.assistant .message-content").last
        reply.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        reply_text = reply.inner_text()
        assert "Entity matching is not set up for your organisation yet." in reply_text
        assert ADMIN_SENTENCE_TAIL in reply_text
        _shot(page, "entity-matching-admin-1440-light")
        for bad in FORBIDDEN_SNIPPETS:
            assert bad not in reply_text, reply_text

        entity_link = reply.get_by_role("link", name="Settings, AI and models")
        entity_link.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        entity_link.click()
        page.wait_for_load_state("domcontentloaded")
        assert "/admin/api-settings" in page.url
    finally:
        page.close()

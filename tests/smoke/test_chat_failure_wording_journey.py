"""When the assistant cannot answer, a person is told plainly and can try again.

The person: an enterprise architect who asks the assistant a question while the provider is
failing. The goal: understand that nothing is lost and press Retry, without being sent to
administration settings they cannot open. An administrator also gets a secondary link to the
provider settings, and that link lands on a page with content.
"""
import json
import pathlib
import re

import pytest

from .conftest import PAGE_TIMEOUT
from .test_ai_chat_journey import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PLAIN = "The assistant could not answer just now. Your question is saved; try again in a moment."
SIZES = [("1440", {"width": 1440, "height": 900}), ("390", {"width": 390, "height": 844})]
FAILURES = {
    "provider-not-configured": (503, {"error": "service_unavailable", "message": "AI feature 'chat' is not available. LLM provider must be configured.", "feature": "chat"}),
    "provider-error": (500, {"error": "Failed to process message. Please try again.", "error_type": "unknown"}),
}


def _assert_role(email, expected, admin):
    from app import create_app
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == expected, user.enterprise_role
        assert bool(user.is_admin()) is admin


def _ask_while_failing(browser, live_server, email, viewport, failure):
    status, body = FAILURES[failure]
    ctx = browser.new_context(viewport=viewport)
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    _login(page, live_server, email)
    page.route("**/ai-chat/estate-answer", lambda r: r.fulfill(status=200, content_type="application/json", body='{"found": false}'))
    page.route("**/ai-chat/message/stream", lambda r: r.fulfill(status=status, content_type="application/json", body=json.dumps(body)))
    page.route("**/ai-chat/message", lambda r: r.fulfill(status=status, content_type="application/json", body=json.dumps(body)))
    page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1500)
    page.fill("#user-input", "Which applications have no owner?")
    page.press("#user-input", "Enter")
    card = page.locator('#messages-container [role="alert"]').last
    card.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    return ctx, page, card


@pytest.mark.parametrize("failure", list(FAILURES))
@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_non_administrator_gets_a_plain_message_and_a_working_retry(browser, live_server, seeded, name, viewport, failure):
    email = seeded["emails"]["enterprise_architect"]
    _assert_role(email, "enterprise_architect", admin=False)
    ctx, page, card = _ask_while_failing(browser, live_server, email, viewport, failure)
    try:
        text = card.inner_text()
        assert PLAIN in text, text
        assert "API Settings" not in text and "Admin" not in text, text
        assert "API Settings" not in page.locator("#messages-container").inner_text()
        assert page.locator('[data-testid="chat-provider-settings-link"]').count() == 0
        retry = card.get_by_role("button", name="Retry")
        assert retry.is_visible()
        with page.expect_request(lambda r: "/ai-chat/message" in r.url and r.method == "POST", timeout=PAGE_TIMEOUT):
            retry.click()
    finally:
        ctx.close()


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_administrator_also_gets_a_provider_settings_link_that_lands_on_content(browser, live_server, seeded, name, viewport):
    email = seeded["emails"]["platform_admin"]
    _assert_role(email, "platform_admin", admin=True)
    ctx, page, card = _ask_while_failing(browser, live_server, email, viewport, "provider-not-configured")
    try:
        assert PLAIN in card.inner_text()
        assert card.get_by_role("button", name="Retry").is_visible()
        link = card.get_by_role("link", name="Check the AI provider settings")
        assert link.is_visible()
        link.click()
        page.wait_for_load_state("domcontentloaded")
        assert "/admin/api-settings" in page.url
        assert len(page.locator("main").inner_text().strip()) > 40
    finally:
        ctx.close()


def test_no_customer_visible_string_sends_a_person_to_api_settings():
    """The strings the chat returns or renders never name API Settings; only the admin link may point there."""
    root = pathlib.Path(__file__).resolve().parents[2]
    files = [
        "app/modules/ai_chat/routes/chat_core.py",
        "app/modules/ai_chat/routes/chat_workflows.py",
        "app/modules/ai_chat/routes/document_routes.py",
        "app/modules/ai_chat/routes/entity_routes.py",
        "app/modules/ai_chat/services/agent_runner.py",
        "app/modules/ai_chat/services/multi_domain_chat_service.py",
        "app/error_handlers/llm_error_handler.py",
        "app/static/js/ai_chat/render.js",
        "app/static/js/ai_chat/app.js",
    ]
    pattern = re.compile(r"Admin\s*(?:>|→|->)\s*API Settings|Open Admin")
    hits = []
    for rel in files:
        for n, line in enumerate((root / rel).read_text().splitlines(), 1):
            if pattern.search(line) and not line.lstrip().startswith(("#", "/*", "*", "//")):
                hits.append("%s:%d: %s" % (rel, n, line.strip()))
    assert not hits, "\n".join(hits)


def test_every_failure_route_answers_with_the_plain_sentence():
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.failure_messages import ASSISTANT_UNAVAILABLE

    assert ASSISTANT_UNAVAILABLE == PLAIN
    runner = AgentRunner.__new__(AgentRunner)
    for reason in ("no api key", "boom"):
        assert runner._fallback(reason)["response"] == PLAIN

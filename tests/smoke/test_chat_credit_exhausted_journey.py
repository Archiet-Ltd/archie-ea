"""When the AI provider's credit runs out, the chat says so and who can fix it.

The person: an enterprise architect who asks the assistant a question while the provider
reports credit exhaustion. The goal: understand that the AI service's credit has run out
and that their organisation administrator (or Entelim support for the platform's key) can
restore it. A true rate limit still says to try again shortly.
"""
import json

import pytest

from .conftest import PAGE_TIMEOUT
from .test_ai_chat_journey import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SIZES = [("1440", {"width": 1440, "height": 900}), ("390", {"width": 390, "height": 844})]

# Credit-balance error shapes from real providers (what the server returns after processing)
CREDIT_BALANCE_FAILURES = {
    "openai-credit-exhausted": (
        402,
        {"error": "The AI service's credit has run out. Your organisation administrator (or Entelim support for the platform's key) can restore it.", "error_type": "credit_exhausted"},
    ),
    "anthropic-credit-balance": (
        402,
        {"error": "The AI service's credit has run out. Your organisation administrator (or Entelim support for the platform's key) can restore it.", "error_type": "credit_exhausted"},
    ),
    "openrouter-quota-exceeded": (
        402,
        {"error": "The AI service's credit has run out. Your organisation administrator (or Entelim support for the platform's key) can restore it.", "error_type": "credit_exhausted"},
    ),
    "billing-error": (
        402,
        {"error": "The AI service's credit has run out. Your organisation administrator (or Entelim support for the platform's key) can restore it.", "error_type": "credit_exhausted"},
    ),
}

# True rate limit error shapes from real providers (what the server returns after processing)
RATE_LIMIT_FAILURES = {
    "openai-rate-limit": (
        429,
        {"error": "AI service rate limit exceeded. Please wait a moment and try again.", "error_type": "rate_limit"},
    ),
    "anthropic-rate-limit": (
        429,
        {"error": "AI service rate limit exceeded. Please wait a moment and try again.", "error_type": "rate_limit"},
    ),
    "openrouter-free-daily-limit": (
        429,
        {"error": "AI service rate limit exceeded. Please wait a moment and try again.", "error_type": "rate_limit"},
    ),
}


def _ask_while_failing(browser, live_server, email, viewport, failure_dict, failure_key):
    status, body = failure_dict[failure_key]
    ctx = browser.new_context(viewport=viewport)
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    _login(page, live_server, email)
    # Mock the estate-answer endpoint to avoid side effects
    page.route("**/ai-chat/estate-answer", lambda r: r.fulfill(status=200, content_type="application/json", body='{"found": false}'))
    # Mock the chat message endpoint to return the failure
    page.route("**/ai-chat/message/stream", lambda r: r.fulfill(status=status, content_type="application/json", body=json.dumps(body)))
    page.route("**/ai-chat/message", lambda r: r.fulfill(status=status, content_type="application/json", body=json.dumps(body)))
    page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1500)
    page.fill("#user-input", "Which applications have no owner?")
    page.press("#user-input", "Enter")
    card = page.locator('#messages-container [role="alert"]').last
    card.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    return ctx, page, card


@pytest.mark.parametrize("failure_key", list(CREDIT_BALANCE_FAILURES))
@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_credit_exhausted_shows_credit_message_and_who_can_fix(browser, live_server, seeded, name, viewport, failure_key):
    """Credit-balance errors say credit ran out and who can fix it (org admin or Entelim support)."""
    email = seeded["emails"]["enterprise_architect"]
    ctx, page, card = _ask_while_failing(browser, live_server, email, viewport, CREDIT_BALANCE_FAILURES, failure_key)
    try:
        text = card.inner_text()
        # Should mention credit ran out
        assert "credit" in text.lower(), f"Expected 'credit' in message: {text}"
        assert "run out" in text.lower() or "exhausted" in text.lower(), f"Expected 'run out' or 'exhausted' in message: {text}"
        # Should mention who can fix it
        assert "administrator" in text.lower() or "support" in text.lower(), f"Expected 'administrator' or 'support' in message: {text}"
        # Should NOT say "rate limit" or "try again" for credit errors
        assert "rate limit" not in text.lower(), f"Credit error should not mention rate limit: {text}"
        assert "try again" not in text.lower(), f"Credit error should not say try again: {text}"
        # Should NOT have a Retry button for credit errors (they won't resolve by retrying)
        assert card.get_by_role("button", name="Retry").count() == 0, f"Credit error should not have Retry button: {text}"
    finally:
        ctx.close()


@pytest.mark.parametrize("failure_key", list(RATE_LIMIT_FAILURES))
@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_rate_limit_shows_retry_message(browser, live_server, seeded, name, viewport, failure_key):
    """True rate limit errors say to try again shortly and offer Retry."""
    email = seeded["emails"]["enterprise_architect"]
    ctx, page, card = _ask_while_failing(browser, live_server, email, viewport, RATE_LIMIT_FAILURES, failure_key)
    try:
        text = card.inner_text()
        # Should mention rate limit
        assert "rate limit" in text.lower() or "busy" in text.lower(), f"Expected rate limit message: {text}"
        # Should say to try again or wait
        assert "try again" in text.lower() or "wait" in text.lower() or "moment" in text.lower(), f"Expected retry guidance: {text}"
        # Should have a Retry button
        retry = card.get_by_role("button", name="Retry")
        assert retry.is_visible(), f"Rate limit error should have Retry button: {text}"
        # Clicking Retry should issue a new request
        with page.expect_request(lambda r: "/ai-chat/message" in r.url and r.method == "POST", timeout=PAGE_TIMEOUT):
            retry.click()
    finally:
        ctx.close()


def test_no_customer_visible_string_sends_a_person_to_api_settings_for_credit_errors():
    """The strings the chat returns for credit errors never name API Settings; only the admin link may point there."""
    from pathlib import Path
    import re

    root = Path(__file__).resolve().parents[2]
    files = [
        "app/modules/ai_chat/routes/chat_core.py",
        "app/modules/ai_chat/routes/chat_workflows.py",
        "app/modules/ai_chat/services/agent_runner.py",
        "app/modules/ai_chat/services/failure_messages.py",
    ]
    pattern = re.compile(r"Admin\s*(?:>|→|->)\s*API Settings|Open Admin")
    hits = []
    for rel in files:
        for n, line in enumerate((root / rel).read_text().splitlines(), 1):
            if pattern.search(line) and not line.lstrip().startswith(("#", "/*", "*", "//")):
                hits.append("%s:%d: %s" % (rel, n, line.strip()))
    assert not hits, "\n".join(hits)
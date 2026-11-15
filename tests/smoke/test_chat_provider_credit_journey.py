"""A real provider refusal reaches the person in the chat as the right sentence.

The person: an enterprise architect who asks the assistant a question while the AI provider
refuses. Here the provider is the loopback test peer answering in each provider's own error
shape, so the refusal travels the whole server path (provider client, failover, error
classification, reply) before the browser renders it. A spent balance says the credit ran out
and who can restore it, with no Retry; a per-minute limit says to wait and offers Retry.

Run with SMOKE_AI_PROTOCOL_STUB=1 like the other enabled-AI journeys.
"""
import json
import os

import pytest

from .ai_protocol_stub import REFUSALS
from .conftest import PAGE_TIMEOUT
from .test_ai_protocol_journeys import _csrf, protocol_context, required_protocol  # noqa: F401

# The live server reads these when it boots: without a provider order and a spend ceiling the
# platform never calls its own key, and the chat answers "paused" before any provider is asked.
os.environ.setdefault("LLM_PROVIDER_ORDER", "openai")
os.environ.setdefault("LLM_SPEND_CEILING_USD_OPENAI", "62")

pytestmark = [pytest.mark.smoke, pytest.mark.journey, pytest.mark.ai_protocol]

CREDIT_SENTENCE = (
    "The AI service's credit has run out. "
    "Your organisation administrator (or Entelim support for the platform's key) can restore it."
)
CREDIT_CASES = ["credit_openrouter", "credit_openai"]


def _prompt(name):
    return REFUSALS[name]["marker"] + ": Which applications have no owner?"


@pytest.fixture
def page(protocol_context):  # noqa: F811
    page = protocol_context.new_page()
    page.route("**/ai-chat/estate-answer", lambda r: r.fulfill(
        status=200, content_type="application/json", body='{"found": false}'))
    yield page
    page.close()


def _ask(page, live_server, name):
    page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1500)
    page.fill("#user-input", _prompt(name))
    page.press("#user-input", "Enter")
    return page.locator("#messages-container")


@pytest.mark.parametrize("size", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}], ids=["1440", "390"])
@pytest.mark.parametrize("name", CREDIT_CASES)
def test_a_provider_with_no_credit_tells_the_person_who_can_restore_it(page, live_server, required_protocol, name, size):  # noqa: F811
    page.set_viewport_size(size)
    before = len(required_protocol.records)
    messages = _ask(page, live_server, name)
    messages.get_by_text("credit has run out").first.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    text = messages.inner_text()
    assert "organisation administrator" in text and "Entelim support" in text, text
    assert "rate limit" not in text.lower() and "try again" not in text.lower(), text
    assert messages.get_by_role("button", name="Retry").count() == 0, text
    folder = os.environ.get("SMOKE_SCREENSHOT_DIR")
    if folder:
        os.makedirs(folder, exist_ok=True)
        page.screenshot(path=os.path.join(folder, "credit-%s-%d.png" % (name, size["width"])), full_page=True)
    sent = [r for r in required_protocol.records[before:] if r["scenario"] == name]
    assert sent, "the refusal never came from the provider"


def test_a_per_minute_limit_is_not_called_a_spent_balance(page, live_server, required_protocol):  # noqa: F811
    messages = _ask(page, live_server, "rate_per_minute")
    messages.get_by_role("button", name="Retry").first.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    text = messages.inner_text()
    folder = os.environ.get("SMOKE_SCREENSHOT_DIR")
    if folder:
        os.makedirs(folder, exist_ok=True)
        page.screenshot(path=os.path.join(folder, "rate-limit-1440.png"), full_page=True)
    assert "credit" not in text.lower(), text
    assert "busy" in text.lower() or "wait" in text.lower() or "try again" in text.lower(), text


@pytest.mark.parametrize("name", CREDIT_CASES)
def test_the_plain_reply_carries_the_credit_sentence_with_status_200(page, live_server, required_protocol, name):  # noqa: F811
    page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    res = page.request.post(
        live_server + "/ai-chat/message",
        data=json.dumps({"message": _prompt(name)}),
        headers={**_csrf(page), "Content-Type": "application/json"},
    )
    body = res.json()
    assert res.status == 200, body
    assert body["response"] == CREDIT_SENTENCE, body
    assert body["success"] is False and body.get("agent_error"), body
    assert "user_id" not in json.dumps(body)

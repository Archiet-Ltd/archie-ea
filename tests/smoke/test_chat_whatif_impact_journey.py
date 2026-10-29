"""What the chat shows today for "What if we retire Payments service?".

Finding, not a pass of the what-if handler fixed in multi_domain_chat_service.py:
the ordinary /ai-chat compose box is served by AgentRunner (app/modules/ai_chat/
services/agent_runner.py), which never calls MultiDomainChatService.process_message
-- its own code says so ("MultiDomainChatService._get_persona_system_prompt <-
process_message, which this path never calls"). AgentRunner nudges the model to
call its own canonical `simulate_impact` tool only when
answer_envelope.classify_estate_question() recognises the question as an estate
question; that regex matches "if X fails", "is retired", "is decommissioned" and
similar, but not "what if we retire X". So this exact phrasing reaches no tool
and no citation today, independent of the fix in this brief's file scope.

This journey is kept as the evidence for that reachability finding and as a
regression anchor: once a follow-up brief teaches classify_estate_question this
phrasing, this test's "answered from ... in context, no lookup ran" assertion is
the one that should go red.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server
from .test_try_sample_company_journey import _empty_organisation_user, _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("WHATIF_WALK_DIR")
QUESTION = "What if we retire Payments service?"


class _EchoProvider(BaseHTTPRequestHandler):
    """Echoes the last chat message's content back as the answer, so the
    rendered page shows exactly what the assistant was handed, with no real
    model. Speaks the OpenAI-compatible, non-streaming chat-completions shape
    the NVIDIA path calls through the openai SDK client, the one provider
    path in this codebase whose base URL is read from an environment
    variable (LLMService.base_url_for) rather than hardcoded.
    """

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages") or []
        content = messages[-1].get("content", "") if messages else ""
        payload = json.dumps({
            "id": "echo-1", "created": 0, "model": "echo-model", "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture(scope="module")
def echo_provider_server(request, app):
    peer = ThreadingHTTPServer(("127.0.0.1", 0), _EchoProvider)
    threading.Thread(target=peer.serve_forever, daemon=True).start()
    request.addfinalizer(peer.shutdown)
    base = "http://127.0.0.1:%d" % peer.server_port
    return boot_live_server(request, None, app, extra_env={
        "NVIDIA_API_KEY": "echo-key",
        "NVIDIA_API_BASE": base + "/v1",
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
    })


def _load_sample_from_chat(page, base):
    page.goto(base + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.get_by_test_id("chat-first-sample").click()
    expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)


def _ask_what_if(page, live_server):
    page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.locator("#user-input").fill(QUESTION)
    page.locator("#user-input").press("Enter")
    expect(page.locator("#messages-container")).to_contain_text(
        QUESTION, timeout=PAGE_TIMEOUT * 2
    )
    # the turn finishes (the evidence line always appears once the reply lands)
    expect(page.locator("#messages-container")).to_contain_text(
        "Answered from", timeout=PAGE_TIMEOUT * 2
    )


def _assert_todays_live_behaviour_is_uncited(page):
    """Documents the reachability finding: today this exact phrasing is
    answered from context, with no tool run and no citation -- the real,
    separate defect this build found, outside this brief's file scope."""
    text = page.locator("#messages-container").inner_text()
    assert "No lookup ran" in text
    assert "Answered from" in text
    # never the frozen fixed-value scenario service's report shape, were it
    # ever (wrongly) reachable from this surface
    assert "RETIREMENT IMPACT ANALYSIS" not in text
    assert "migration cost" not in text.lower()


def test_what_if_retire_is_answered_uncited_today_at_1440_light(
    browser, echo_provider_server
):
    email, _org_id = _empty_organisation_user(echo_provider_server)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, echo_provider_server, email)
        _load_sample_from_chat(page, echo_provider_server)
        _ask_what_if(page, echo_provider_server)
        _assert_todays_live_behaviour_is_uncited(page)
        if WALK:
            os.makedirs(WALK, exist_ok=True)
            page.screenshot(path=os.path.join(WALK, "whatif-1440-light.png"))
    finally:
        context.close()


def test_what_if_retire_is_answered_uncited_today_at_390_dark(
    browser, echo_provider_server
):
    email, _org_id = _empty_organisation_user(echo_provider_server)
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    context.add_init_script("try { localStorage.setItem('theme', 'dark') } catch (e) {}")
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, echo_provider_server, email)
        _load_sample_from_chat(page, echo_provider_server)
        _ask_what_if(page, echo_provider_server)
        _assert_todays_live_behaviour_is_uncited(page)
        assert page.evaluate("document.documentElement.classList.contains('dark')")
        if WALK:
            os.makedirs(WALK, exist_ok=True)
            page.screenshot(path=os.path.join(WALK, "whatif-390-dark.png"))
    finally:
        context.close()

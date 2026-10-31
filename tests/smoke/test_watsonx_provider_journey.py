"""An administrator sets up IBM watsonx.ai with a Granite model in API Settings, tests it, and the chat answers
through it; when the service is down the person reads a plain message naming it, not another provider's answer.

Job 6 (the cited-answer layer gains a provider). The service is a stub speaking watsonx's REST shape: the IAM
token exchange, the Granite model list and the chat call, each recorded so the test can say what was sent.
"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server
from .test_llm_hard_stop_journey import _ask, _new_owner
from .test_signup_brings_the_company_in_journey import people  # noqa: F401  (people is a fixture)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

API_KEY = "ibm-cloud-journey-key-0001"
PROJECT = "journey-project-0001"
ANSWER = "Watsonx stub: start with the three systems the team depends on most."
QUESTION = "In one sentence, what is a good first step for a new team?"
WALK = os.environ.get("LLM_WALK_DIR")
MODELS = [
    {"model_id": "ibm/granite-3-8b-instruct", "label": "granite-3-8b-instruct"},
    {"model_id": "ibm/granite-3-3-8b-instruct", "label": "granite-3-3-8b-instruct"},
    {"model_id": "ibm/granite-13b-chat-v2", "label": "granite-13b-chat-v2"},
    {"model_id": "meta-llama/llama-3-3-70b-instruct", "label": "llama"},
]


class _Watsonx(BaseHTTPRequestHandler):
    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.server.calls.append(("specs", self.path, None))
        self._send(200, {"resources": MODELS})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
        if "/identity/token" in self.path:
            self.server.calls.append(("iam", self.path, raw))
            ok = "apikey=" + API_KEY in raw
            return self._send(200 if ok else 400, {"access_token": "journey-token", "expiration": time.time() + 3600} if ok else {})
        body = json.loads(raw or "{}")
        self.server.calls.append(("chat", self.path, body))
        if self.headers.get("Authorization") != "Bearer journey-token":
            return self._send(401, {})
        if self.server.down:
            return self._send(503, {"detail": "maintenance"})
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": ANSWER}}],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 10}})

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def watsonx():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Watsonx)
    server.calls = []
    server.down = False
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


@pytest.fixture(scope="module")
def live(request, app, watsonx):
    return boot_live_server(request, None, app, extra_env={
        "WATSONX_IAM_URL": "http://127.0.0.1:%d/identity/token" % watsonx.server_port,
        "WATSONX_API_KEY": "", "OPENAI_API_KEY": "", "NVIDIA_API_KEY": "", "GEMINI_API_KEY": "",
        "ALLOW_EXTERNAL_API_CALLS": "true",
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
    })


def _shot(page, name):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


def _chats(watsonx):
    return [c[2] for c in watsonx.calls if c[0] == "chat"]


def test_administrator_sets_up_watsonx_tests_it_and_the_chat_answers_through_it(browser, live, people, watsonx):
    base = str(live)
    service = "http://127.0.0.1:%d" % watsonx.server_port
    watsonx.down = False
    context, page = _new_owner(browser, base, people)
    try:
        page.goto(base + "/admin/api-settings", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.locator("#provider").select_option("watsonx")
        fields = page.get_by_test_id("watsonx-fields")
        expect(fields).to_be_visible()
        page.locator("#api_key").fill(API_KEY)
        page.locator("#watsonx_project_id").fill(PROJECT)
        page.locator("#watsonx_url").fill(service)

        page.get_by_test_id("watsonx-load-models").click()
        dropdown = page.get_by_test_id("watsonx-model")
        expect(dropdown.locator("option")).to_have_count(2, timeout=PAGE_TIMEOUT)
        assert dropdown.locator("option").all_inner_texts() == ["ibm/granite-3-3-8b-instruct", "ibm/granite-3-8b-instruct"]
        expect(page.locator("#watsonx-status")).to_contain_text("2 Granite models loaded")
        dropdown.select_option("ibm/granite-3-3-8b-instruct")
        _shot(page, "watsonx-form-1440-light")

        page.get_by_role("button", name="Test Connection").click()
        page.wait_for_load_state("domcontentloaded")
        banner = page.locator("#main-content").get_by_text("Connection test successful: Connection successful. Model: ibm/granite-3-3-8b-instruct")
        expect(banner).to_be_visible(timeout=PAGE_TIMEOUT)
        test_call = _chats(watsonx)[0]
        assert test_call["project_id"] == PROJECT and test_call["model_id"] == "ibm/granite-3-3-8b-instruct"

        # Saved: the provider is listed with its model and the edit link opens the same settings.
        page.reload(wait_until="domcontentloaded")
        entry = page.locator("div.flex-1", has_text="Watsonx").first
        expect(entry).to_contain_text("ibm/granite-3-3-8b-instruct")
        expect(entry).to_contain_text("Tested")
        page.get_by_label("Edit watsonx settings").click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.get_by_test_id("watsonx-fields")).to_be_visible()
        expect(page.locator("#watsonx_project_id")).to_have_value(PROJECT)
        expect(page.locator("#watsonx_url")).to_have_value(service)
        expect(page.get_by_test_id("watsonx-model")).to_have_value("ibm/granite-3-3-8b-instruct", timeout=PAGE_TIMEOUT)
        _shot(page, "watsonx-edit-1440-light")

        # The row's own test button reports the real result.
        page.goto(base + "/admin/api-settings", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_label("Test connection for watsonx").click()
        expect(page.locator("#main-content").get_by_text("Connection test successful")).to_be_visible(timeout=PAGE_TIMEOUT)

        # The chat answers through watsonx, with the saved model and project.
        before = len(_chats(watsonx))
        _ask(page, base, QUESTION)
        reply = page.locator(".message-content", has_text="three systems the team depends on most")
        expect(reply.first).to_be_visible(timeout=PAGE_TIMEOUT * 2)
        asked = _chats(watsonx)[before:]
        assert asked, "the chat never reached watsonx"
        assert all(c["project_id"] == PROJECT and c["model_id"] == "ibm/granite-3-3-8b-instruct" for c in asked)
        assert any(QUESTION in json.dumps(c["messages"]) for c in asked)
        _shot(page, "watsonx-chat-1440-light")
    finally:
        context.close()


def test_when_watsonx_is_down_the_person_reads_a_plain_message_naming_it(browser, live, people, watsonx):
    base = str(live)
    service = "http://127.0.0.1:%d" % watsonx.server_port
    watsonx.down = False
    context, page = _new_owner(browser, base, people, width=390, dark=True)
    try:
        page.goto(base + "/admin/api-settings", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.locator("#provider").select_option("watsonx")
        page.locator("#api_key").fill(API_KEY)
        page.locator("#watsonx_project_id").fill(PROJECT)
        page.locator("#watsonx_url").fill(service)
        page.get_by_test_id("watsonx-load-models").click()
        expect(page.get_by_test_id("watsonx-model").locator("option")).to_have_count(2, timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Save Settings").click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("#main-content").get_by_text("API settings for watsonx saved successfully.")).to_be_visible(timeout=PAGE_TIMEOUT)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        _shot(page, "watsonx-saved-390-dark")

        watsonx.down = True
        try:
            _ask(page, base, QUESTION)
            message = page.locator("body")
            expect(message).to_contain_text("IBM watsonx.ai is not responding (HTTP 503)", timeout=PAGE_TIMEOUT * 2)
            text = message.inner_text()
            assert ANSWER not in text and API_KEY not in text and "maintenance" not in text
            assert "Traceback" not in text
            _shot(page, "watsonx-down-390-dark")
        finally:
            watsonx.down = False
    finally:
        context.close()

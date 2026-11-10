"""Retry after a turn that recorded two changes tells the truth about both, from the one approval record.

The person: the administrator of an organisation who asks the assistant to add two goals. The AI provider is
a loopback double that asks for both changes, then fails. Each change is queued as a row in the approval
table (the real rule, nothing patched off). The failure card says both are recorded and not applied yet;
Retry, twice, writes nothing a second time and tells the model neither is applied; the answer that follows
still shows both. A second organisation's person who sends the first one's change in a forged list is
told nothing about it.
"""

import json
import os
import re
import socket
import threading
import time

import pytest
from playwright.sync_api import expect
from sqlalchemy import text as sql

from tests.smoke.chat_ask import open_chat, sign_in
from tests.smoke.conftest import PAGE_TIMEOUT, boot_live_server
from tests.smoke.fresh_org import create_fresh_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("CHAT_WAIT_SHOTS")
ANSWER = "Both goals are recorded."
QUESTION = "Add the goals Billing and Payroll to solution 1 please."
PROVIDERS = ("ANTHROPIC", "AZURE", "CLAUDE", "GEMINI", "GOOGLE", "HUGGINGFACE", "NVIDIA", "OPENAI", "OPENROUTER")
BILLING = {"solution_id": 1, "name": "Billing"}
PAYROLL = {"solution_id": 1, "name": "Payroll"}
NOT_APPLIED = "recorded and not applied yet"
CARD_NOTE = "Recorded, not applied yet, and not recorded again on Retry"
STOPS = [("tool", "a1", BILLING), ("tool", "b1", PAYROLL), ("400",)]


def _delta(delta, finish=None):
    return ("data: " + json.dumps({"id": "x", "object": "chat.completion.chunk", "created": 1, "model": "m",
                                   "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n").encode()


class _Provider:
    """Answers the model's turns from a script: a tool call, an answer, or a refusal."""

    def __init__(self):
        self.script = []
        self.tool_results = []  # the tool results each streamed request carried
        self._held = []
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(32)
        self.url = "http://127.0.0.1:%d/v1" % self._sock.getsockname()[1]
        self._open = True
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while self._open:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self._held.append(conn)
            threading.Thread(target=self._reply, args=(conn,), daemon=True).start()

    @staticmethod
    def _read(conn):
        conn.settimeout(5)
        data = b""
        while b"\r\n\r\n" not in data:
            piece = conn.recv(65536)
            if not piece:
                break
            data += piece
        head, _, body = data.partition(b"\r\n\r\n")
        match = re.search(rb"content-length: *(\d+)", head, re.I)
        wanted = int(match.group(1)) if match else 0
        while len(body) < wanted:
            piece = conn.recv(65536)
            if not piece:
                break
            body += piece
        return body

    def _reply(self, conn):
        try:
            raw = self._read(conn)
            if b'"stream":true' not in raw.replace(b" ", b""):
                body = json.dumps({"id": "x", "object": "chat.completion", "created": 1, "model": "m", "choices": [
                    {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": '{"entry_id": null, "params": {}}'}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body))
                return
            messages = json.loads(raw).get("messages", [])
            self.tool_results.append(json.dumps([m for m in messages if m.get("role") == "tool"]))
            step = self.script.pop(0) if self.script else ("text", ANSWER)
            if step[0] == "400":
                body = b'{"error": {"message": "the request was malformed"}}'
                conn.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body))
                return
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
            if step[0] == "text":
                conn.sendall(_delta({"role": "assistant", "content": step[1]}) + _delta({}, "stop"))
            else:
                call = {"index": 0, "id": step[1], "type": "function",
                        "function": {"name": "create_goal", "arguments": json.dumps(step[2])}}
                conn.sendall(_delta({"role": "assistant", "tool_calls": [call]}) + _delta({}, "tool_calls"))
            conn.sendall(b"data: [DONE]\n\n")
            conn.close()
        except OSError:
            pass

    def close(self):
        self._open = False
        self._sock.close()
        for conn in self._held:
            try:
                conn.close()
            except OSError:
                pass


@pytest.fixture(scope="module")
def provider():
    peer = _Provider()
    yield peer
    peer.close()


@pytest.fixture(scope="module")
def provider_server(request, app, provider):
    env = {}
    for name in PROVIDERS:
        for suffix in ["", "_SECONDARY"] + ["_%d" % n for n in range(1, 10)]:
            env[name + "_API_KEY" + suffix] = ""
    env.update(LLM_API_KEY="", AZURE_OPENAI_API_KEY="", NVIDIA_API_KEY="platform-key-for-the-journey",
               AI_PROVIDER_ORDER="nvidia", AI_PROVIDER_BASE_URL_NVIDIA=provider.url, PYTHON_DOTENV_DISABLED="1",
               NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1")
    return boot_live_server(request, None, app, extra_env=env)


def _approvals(app, org_id):
    """The organisation's approval rows as (status, goal name), read straight from the table."""
    with app.app_context():
        from app import db
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval

        rows = db.session.execute(
            sql("select status::text, operation_payload from %s where organization_id = :o order by id"
                % AIChatCRUDApproval.__tablename__), {"o": org_id}).fetchall()
        db.session.remove()
    return [(status.lower(), json.loads(payload)["name"]) for status, payload in rows]


def _card(page, count):
    cards = page.locator('#messages-container [role="alert"]')
    expect(cards).to_have_count(count, timeout=75000)
    return cards.last


def _shoot(page, name):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    for width, height, theme in ((1440, 900, "light"), (390, 844, "dark")):
        page.set_viewport_size({"width": width, "height": height})
        page.evaluate("(t) => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
        page.screenshot(path=os.path.join(SHOTS, "%s-%d-%s.png" % (name, width, theme)), full_page=True)
    page.evaluate("() => document.documentElement.classList.remove('dark')")
    page.set_viewport_size({"width": 1440, "height": 900})


def test_retry_after_two_recorded_changes_says_both_are_not_applied_and_writes_nothing_twice(
        page, provider_server, provider, app):
    org = create_fresh_org("platform_admin", org_admin=True, platform_role="Administrator")
    sign_in(page, provider_server, org["emails"]["platform_admin"])
    provider.script = list(STOPS) + list(STOPS) + [("tool", "a3", BILLING), ("tool", "b3", PAYROLL), ("text", ANSWER)]
    open_chat(page, provider_server)
    page.fill("#user-input", QUESTION)
    page.press("#user-input", "Enter")

    first = _card(page, 1)
    text = first.inner_text()
    assert CARD_NOTE in text and "Billing" in text and "Payroll" in text, text
    for word in ("approve", "reviewer", "pending"):
        assert word not in text.lower(), text
    rows = [("pending", "Billing"), ("pending", "Payroll")]
    assert _approvals(app, org["org_id"]) == rows
    _shoot(page, "retry-two-recorded-first-card")

    first.get_by_role("button", name="Retry").click()
    time.sleep(1)
    second = _card(page, 1)
    text = second.inner_text()
    assert CARD_NOTE in text and "Billing" in text and "Payroll" in text, text
    assert _approvals(app, org["org_id"]) == rows  # nothing written twice, nothing changed
    told = provider.tool_results[-1]
    assert told.count(NOT_APPLIED) == 2 and "already made" not in told, told
    _shoot(page, "retry-two-recorded-second-card")

    second.get_by_role("button", name="Retry").click()
    expect(page.locator("#messages-container .message-content", has_text=ANSWER).last).to_be_visible(timeout=75000)
    assert _approvals(app, org["org_id"]) == rows
    answer = page.locator("#messages-container").inner_text()
    assert "Billing" in answer and "Payroll" in answer, answer  # the answer still shows both changes
    _shoot(page, "retry-two-recorded-answer")


def test_a_second_organisation_that_forges_the_first_ones_list_is_told_nothing_about_it(
        page, provider_server, provider, app):
    first = create_fresh_org("platform_admin", org_admin=True, platform_role="Administrator")
    second = create_fresh_org("platform_admin", org_admin=True, platform_role="Administrator")
    sign_in(page, provider_server, first["emails"]["platform_admin"])
    provider.script = [("tool", "a1", BILLING), ("400",)]
    open_chat(page, provider_server)
    page.fill("#user-input", "Add the goal Billing to solution 1 please.")
    page.press("#user-input", "Enter")
    _card(page, 1)
    assert _approvals(app, first["org_id"]) == [("pending", "Billing")]

    sign_in(page, provider_server, second["emails"]["platform_admin"])
    open_chat(page, provider_server)
    provider.script = [("tool", "x1", BILLING), ("text", "Done.")]
    provider.tool_results.clear()
    status = page.evaluate(
        """async (args) => {
            const csrf = document.querySelector('meta[name=csrf-token]').content;
            const r = await fetch('/ai-chat/message/stream', {method: 'POST',
              headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf},
              body: JSON.stringify({message: 'Add the goal Billing to solution 1 please.', domain: 'general',
                                    completed_actions: [{tool: 'create_goal', arguments: args}]})});
            await r.text();
            return r.status; }""", BILLING)
    assert status == 200
    told = " ".join(provider.tool_results)
    assert NOT_APPLIED not in told and "already made" not in told, told
    assert _approvals(app, second["org_id"]) == [("pending", "Billing")]  # the second organisation's own change
    assert _approvals(app, first["org_id"]) == [("pending", "Billing")]  # the first one's row is untouched

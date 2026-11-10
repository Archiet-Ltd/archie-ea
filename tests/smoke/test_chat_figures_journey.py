"""A figure in a chat answer is a figure the assistant's own lookup returned.

The person: an enterprise architect who asks the assistant what depends on an
application. The assistant looks it up (13 connections are recorded) and its
first draft says 21. The goal: read an answer whose numbers agree with the
records, never the invented one. If the assistant corrects itself the person
reads the corrected answer; if it does not, the person reads the answer without
the number, and a plain line saying a figure could not be confirmed.

The provider is a local peer that speaks the chat-completions protocol, asks for
the impact lookup, then answers with a wrong count (21).
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("NUMCITE_WALK_DIR")
QUESTION = "Give me a quick summary of Orders Hub for the board."
DEPENDENTS = 13
WRONG = 21
NOT_CONFIRMED = "I could not confirm one or more figures in this answer"
SIZES = [("1440", {"width": 1440, "height": 900}), ("390", {"width": 390, "height": 844})]

# What the peer says; set by each test before it asks.
SCRIPT = {"root_id": None, "after_reason": "right", "seen": []}


def _draft(count):
    return "Orders Hub [ref:%s] has %d connections downstream, so a failure there reaches them all." % (
        SCRIPT["root_id"], count)


class _Provider(BaseHTTPRequestHandler):
    """Looks up the impact of the record, then answers with 21; when the reply
    comes back with the reason, it either corrects itself or repeats 21."""

    def log_message(self, *args):
        pass

    def _send(self, body, stream):
        base = {"id": "peer-1", "created": 0, "model": "peer-model"}
        if not stream:
            payload = json.dumps({**base, "object": "chat.completion", "choices": [{
                "index": 0, "message": body, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        delta = {"content": body["content"]} if body.get("content") else {
            "tool_calls": [{"index": 0, **body["tool_calls"][0]}]}
        chunks = [
            {**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        frames = [("data: " + json.dumps(c) + "\n\n").encode() for c in chunks] + [b"data: [DONE]\n\n"]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(sum(map(len, frames))))
        self.end_headers()
        for frame in frames:
            self.wfile.write(frame)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages") or []
        stream = bool(body.get("stream"))
        last = messages[-1] if messages else {}
        looked_up = any(m.get("role") == "tool" for m in messages)
        reason = last.get("role") == "user" and "no tool returned" in str(last.get("content"))
        if not body.get("tools"):
            self._send({"role": "assistant", "content": "Orders Hub summary"}, stream)
        elif reason:
            SCRIPT["seen"].append("revision")
            self._send({"role": "assistant", "content": _draft(
                DEPENDENTS if SCRIPT["after_reason"] == "right" else WRONG)}, stream)
        elif looked_up:
            SCRIPT["seen"].append("draft")
            self._send({"role": "assistant", "content": _draft(WRONG)}, stream)
        else:
            SCRIPT["seen"].append("lookup")
            self._send({"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function", "function": {
                    "name": "answer", "arguments": json.dumps({"question": "impact", "element_id": SCRIPT["root_id"]})}}]}, stream)


@pytest.fixture(scope="module")
def own_server(request, app):
    peer = ThreadingHTTPServer(("127.0.0.1", 0), _Provider)
    threading.Thread(target=peer.serve_forever, daemon=True).start()
    request.addfinalizer(peer.shutdown)
    base = "http://127.0.0.1:%d" % peer.server_port
    return boot_live_server(request, None, app, extra_env={
        "NVIDIA_API_KEY": "peer-key",
        "NVIDIA_API_BASE": base + "/v1",
        "ASSISTANT_CITATION_GATE_MODE": "enforce",
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
    })


def _estate_with_dependents(org_id, count):
    """Orders Hub and *count* applications that depend on it; returns the hub's id."""
    from app import create_app, db
    from app.models import ArchiMateElement, ArchiMateRelationship

    app = create_app("testing")
    with app.app_context():
        hub = ArchiMateElement(name="Orders Hub", type="ApplicationComponent", layer="application",
                               organization_id=org_id)
        db.session.add(hub)
        db.session.flush()
        for index in range(count):
            dependent = ArchiMateElement(name="Orders Client %02d" % (index + 1),
                                         type="ApplicationComponent", layer="application",
                                         organization_id=org_id)
            db.session.add(dependent)
            db.session.flush()
            db.session.add(ArchiMateRelationship(source_id=hub.id, target_id=dependent.id,
                                                 type="Serving", organization_id=org_id))
        db.session.commit()
        hub_id = hub.id
        db.session.remove()
    return hub_id


def _ask(browser, server, viewport, after_reason):
    org = create_fresh_org("enterprise_architect")
    SCRIPT.update(root_id=_estate_with_dependents(org["org_id"], DEPENDENTS), after_reason=after_reason, seen=[])
    context = browser.new_context(viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_in(page, server, org["emails"]["enterprise_architect"])
    page.goto(server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.locator("#user-input").fill(QUESTION)
    page.locator("#user-input").press("Enter")
    return context, page, page.locator("#messages-container")


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_a_wrong_count_is_corrected_before_the_person_reads_it(browser, own_server, name, viewport):
    context, page, reply = _ask(browser, own_server, viewport, "right")
    try:
        expect(reply).to_contain_text("%d connections downstream" % DEPENDENTS, timeout=PAGE_TIMEOUT * 2)
        text = reply.inner_text()
        assert "%d connections" % (WRONG) not in text, text
        assert NOT_CONFIRMED not in text, text
        assert SCRIPT["seen"] == ["lookup", "draft", "revision"], SCRIPT["seen"]
        if WALK:
            os.makedirs(WALK, exist_ok=True)
            page.screenshot(path=os.path.join(WALK, "figures-corrected-%s.png" % name))
    finally:
        context.close()


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_a_count_that_stays_wrong_is_left_out_and_the_answer_says_so(browser, own_server, name, viewport):
    context, page, reply = _ask(browser, own_server, viewport, "wrong")
    try:
        expect(reply).to_contain_text(NOT_CONFIRMED, timeout=PAGE_TIMEOUT * 2)
        text = reply.inner_text()
        assert str(WRONG) not in text, text
        assert "Not recorded:" not in text, text
        assert SCRIPT["seen"] == ["lookup", "draft", "revision"], SCRIPT["seen"]
        if WALK:
            os.makedirs(WALK, exist_ok=True)
            page.screenshot(path=os.path.join(WALK, "figures-removed-%s.png" % name))
    finally:
        context.close()

"""From the send, a typed question gets an answer or a plain failure with Retry inside a minute.

The person: the administrator of an organisation who types a question no stored answer matches, so the
chat first looks for a stored answer (asking the AI to place the question) and then asks the AI itself.
Nothing is stubbed between the send and the turn: the AI provider is a loopback double the test switches
between never answering, answering after forty seconds, and failing at once. The time is measured in the
browser from pressing Enter to the answer or the failure card, then Retry is pressed once the provider is
well again and the answer appears.
"""

import json
import os
import re
import socket
import threading
import time

import pytest
from playwright.sync_api import expect

from tests.smoke.chat_ask import open_chat
from tests.smoke.conftest import PAGE_TIMEOUT, boot_live_server
from tests.smoke.fresh_org import create_fresh_org, sign_in
from tests.smoke.intelligence_graph import seed_impact_graph

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("CHAT_WAIT_SHOTS")
QUESTION = "Write two sentences on why single sign-on helps staff."
ANSWER = "Single sign-on means one password for staff to remember."
BUSY = "The assistant is busy right now. Wait a minute, then press Retry."
PROVIDERS = ("ANTHROPIC", "AZURE", "CLAUDE", "GEMINI", "GOOGLE", "HUGGINGFACE", "NVIDIA", "OPENAI", "OPENROUTER")
LATE_SECONDS = 40.0
BUDGET_SECONDS = 60.0
STALL_AFTER_SECONDS = 20.0
PLAIN = "The assistant could not answer just now. Your question is saved; try again in a moment."
LOOKUP_NONE = json.dumps({"id": "x", "object": "chat.completion", "created": 1, "model": "m", "choices": [
    {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": '{"entry_id": null, "params": {}}'}}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()


def _chunk(text):
    body = {"id": "x", "object": "chat.completion.chunk", "created": 1, "model": "m",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}]}
    return ("data: " + json.dumps(body) + "\n\n").encode()


class _Provider:
    """A loopback chat-completions provider whose behaviour the test switches: hang, late, fail, answer."""

    def __init__(self):
        self.mode = "hang"
        # What the stored-answer lookup (a plain request) gets in the stream-only modes: "hang" leaves it
        # waiting, "none" answers it at once with no match, which the server then keeps for next time.
        self.lookup = "hang"
        self.calls = 0
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
            self.calls += 1
            self._held.append(conn)
            threading.Thread(target=self._reply, args=(conn, self.mode), daemon=True).start()

    @staticmethod
    def _read_request(conn):
        """The whole request: its headers, then as many body bytes as Content-Length says."""
        data = b""
        while b"\r\n\r\n" not in data:
            piece = conn.recv(65536)
            if not piece:
                return data
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

    def _reply(self, conn, mode):
        try:
            conn.settimeout(5)
            request = self._read_request(conn)
            streamed = b'"stream":true' in request.replace(b" ", b"")
            if mode == "trickle":
                # Bytes forever on every request, the stored-answer lookup's plain request included.
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: %s\r\n\r\n" % (b"text/event-stream" if streamed else b"application/json"))
                while self._open:
                    conn.sendall(b": keep-alive\n\n" if streamed else b" \n")
                    time.sleep(1)
                return
            if mode in ("keepalive", "stall"):
                if streamed:
                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
                    if mode == "stall":
                        time.sleep(STALL_AFTER_SECONDS)
                        conn.sendall(_chunk("Partial "))
                        time.sleep(LATE_SECONDS * 4)
                    while self._open and mode == "keepalive":
                        conn.sendall(b": keep-alive\n\n")
                        time.sleep(1)
                    return
                if self.lookup == "none":
                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s" % (len(LOOKUP_NONE), LOOKUP_NONE))
                return
            if mode == "bad":
                body = b'{"error": {"message": "the request was malformed"}}'
                conn.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\n"
                             b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
                return
            if mode == "answer" and not streamed and self.lookup == "none":
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s" % (len(LOOKUP_NONE), LOOKUP_NONE))
                return
            if mode == "answer" and streamed:
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n" + _chunk(ANSWER))
                done = {"id": "x", "object": "chat.completion.chunk", "created": 1, "model": "m",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                conn.sendall(("data: " + json.dumps(done) + "\n\ndata: [DONE]\n\n").encode())
                conn.close()  # the stream has no length: closing the connection is how it ends
                return
            if mode == "hang":
                return
            if mode == "late":
                time.sleep(LATE_SECONDS)
            if mode == "fail":
                body = b'{"error": {"message": "the provider is down"}}'
                conn.sendall(b"HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\n"
                             b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
                return
            body = json.dumps({
                "id": "x", "object": "chat.completion", "created": 1, "model": "m",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": ANSWER}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }).encode()
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body))
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
    """The application on its own port, whose only AI provider is the switchable double."""
    env = {}
    for name in PROVIDERS:
        for suffix in ["", "_SECONDARY"] + ["_%d" % n for n in range(1, 10)]:
            env[name + "_API_KEY" + suffix] = ""
    env.update(
        LLM_API_KEY="", AZURE_OPENAI_API_KEY="", NVIDIA_API_KEY="platform-key-for-the-journey",
        NVIDIA_API_KEY_SECONDARY="second-platform-key-for-the-journey",
        AI_PROVIDER_ORDER="nvidia", AI_PROVIDER_BASE_URL_NVIDIA=provider.url,
        AI_PROVIDER_BASE_URL_OPENAI=provider.url, OPENAI_BASE_URL=provider.url, PYTHON_DOTENV_DISABLED="1",
        NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1",
    )
    return boot_live_server(request, None, app, extra_env=env)


def shoot(page, name):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    for width, height, theme in ((1440, 900, "light"), (1440, 900, "dark"), (390, 844, "dark")):
        page.set_viewport_size({"width": width, "height": height})
        page.evaluate("(t) => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
        page.screenshot(path=os.path.join(SHOTS, "%s-%d-%s.png" % (name, width, theme)), full_page=True)
    page.evaluate("() => document.documentElement.classList.remove('dark')")
    page.set_viewport_size({"width": 1440, "height": 900})


def _send(page, base, question=QUESTION, warm_lookup=None):
    open_chat(page, base)
    if warm_lookup is not None:
        # Ask the stored-answer lookup once while the provider answers it, so the next ask finds it kept.
        behaviour = (warm_lookup.mode, warm_lookup.lookup)
        warm_lookup.mode, warm_lookup.lookup = "answer", "none"
        page.evaluate(
            """async (q) => { await fetch('/ai-chat/estate-answer', {method: 'POST', headers: {'Content-Type': 'application/json',
            'X-CSRFToken': window.csrfToken}, body: JSON.stringify({message: q, remember: false})}); }""", question)
        warm_lookup.mode, warm_lookup.lookup = behaviour
    box = page.locator("#user-input")
    box.fill(question)
    sent = time.monotonic()
    box.press("Enter")
    return sent


def _result(page, sent):
    """Wait for an answer or a failure card and return (kind, seconds from the send)."""
    answer = page.locator("#messages-container .message-content", has_text=ANSWER)
    card = page.locator('#messages-container [role="alert"]')
    deadline = sent + BUDGET_SECONDS + 15
    while time.monotonic() < deadline:
        if answer.count():
            return "answer", time.monotonic() - sent
        if card.count():
            return "failure", time.monotonic() - sent
        time.sleep(0.25)
    raise AssertionError("neither an answer nor a failure card after %.0f seconds" % (time.monotonic() - sent))


@pytest.mark.parametrize("mode", ["hang", "late", "fail"])
def test_a_question_ends_in_an_answer_or_a_failure_with_retry_within_a_minute_of_the_send(
    page, provider_server, provider, mode
):
    org = create_fresh_org("platform_admin", org_admin=True, platform_role="Administrator")
    sign_in(page, provider_server, org["emails"]["platform_admin"])
    provider.mode = mode
    sent = _send(page, provider_server)
    kind, seconds = _result(page, sent)
    print("SEND_TO_RESULT %s %s %.1f" % (mode, kind, seconds))
    assert seconds < BUDGET_SECONDS, (mode, kind, seconds)
    if kind == "failure":
        card = page.locator('#messages-container [role="alert"]').last
        assert BUSY in card.inner_text() or "could not answer" in card.inner_text(), card.inner_text()
        retry = card.get_by_role("button", name="Retry")
        expect(retry).to_be_visible()
        shoot(page, "failure-" + mode)
        # The provider is well again: Retry gets the answer.
        provider.mode = "answer"
        retry.click()
        expect(page.locator("#messages-container .message-content", has_text=ANSWER).last).to_be_visible(timeout=30000)
    provider.mode = "hang"


# Every provider behaviour the review tried that is not a plain hang, late answer or error: what the provider
# does with the streamed turn, what the stored-answer lookup gets, whether that lookup was kept from before,
# and the sentence the person reads on the failure card.
CASES = {
    "keepalive-bytes-only": ("keepalive", "hang", False, BUSY),
    "one-token-then-silence-lookup-not-kept": ("stall", "hang", False, BUSY),
    "one-token-then-silence-lookup-kept": ("stall", "none", True, BUSY),
    "not-retryable-error": ("bad", "hang", False, PLAIN),
    "bytes-forever-on-the-stored-answer-lookup": ("trickle", "hang", False, BUSY),
}


@pytest.mark.parametrize("case", list(CASES))
def test_a_provider_that_trickles_stalls_or_refuses_still_ends_in_a_failure_card_with_retry_by_the_minute(
    page, provider_server, provider, case
):
    mode, lookup, warm, sentence = CASES[case]
    org = create_fresh_org("platform_admin", org_admin=True, platform_role="Administrator")
    sign_in(page, provider_server, org["emails"]["platform_admin"])
    provider.mode, provider.lookup = mode, lookup
    sent = _send(page, provider_server, question="Write two sentences on %s for staff." % case.replace("-", " "),
                 warm_lookup=provider if warm else None)
    kind, seconds = _result(page, sent)
    print("SEND_TO_RESULT %s %s %.1f" % (case, kind, seconds))
    assert kind == "failure", (case, kind)
    assert seconds < BUDGET_SECONDS, (case, seconds)
    card = page.locator('#messages-container [role="alert"]').last
    text = card.inner_text()
    assert sentence in text, text
    assert "error detail" not in text and "Admin" not in text and "API Settings" not in text, text
    retry = card.get_by_role("button", name="Retry")
    expect(retry).to_be_visible()
    shoot(page, "failure-" + case)
    provider.mode, provider.lookup = "answer", "hang"
    retry.click()
    expect(page.locator("#messages-container .message-content", has_text=ANSWER).last).to_be_visible(timeout=30000)
    provider.mode, provider.lookup = "hang", "hang"


# Colours as the browser resolves them, laid over what is behind them, then WCAG relative luminance.
CONTRAST_SCRIPT = """
(el) => {
  const parse = (c) => { const m = c.match(/[\\d.]+/g).map(Number); return { r: m[0], g: m[1], b: m[2], a: m.length > 3 ? m[3] : 1 }; };
  const behind = (node) => {
    let layers = [];
    for (let n = node; n; n = n.parentElement) {
      const c = parse(getComputedStyle(n).backgroundColor);
      if (c.a > 0) { layers.push(c); if (c.a >= 1) break; }
    }
    let base = { r: 255, g: 255, b: 255 };
    for (const c of layers.reverse()) base = { r: c.r * c.a + base.r * (1 - c.a), g: c.g * c.a + base.g * (1 - c.a), b: c.b * c.a + base.b * (1 - c.a) };
    return base;
  };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const ratio = (node) => {
    const fg = parse(getComputedStyle(node).color), bg = behind(node);
    const f = { r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a) };
    const a = lum(f), b = lum(bg);
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  };
  const first = (sel) => el.querySelector(sel);
  return { body: ratio(first('p')), link: ratio(first('a')), code: ratio(first('code')) };
}
"""


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
def test_a_written_answer_reads_at_aa_contrast_in_dark_mode(page, live_server, width, height):
    org = create_fresh_org("platform_admin", org_admin=True, platform_role="Administrator")
    graph = seed_impact_graph(org["org_id"])
    sign_in(page, live_server, org["emails"]["platform_admin"])
    page.goto("%s/intelligence/answers/element/%s" % (live_server, graph["service"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_url(re.compile(r".*/intelligence/answers/[0-9a-f]{32}"), timeout=PAGE_TIMEOUT)
    address = re.search(r"/intelligence/answers/([0-9a-f]{32})", page.url).group(1)
    text = (
        "The service runs from `docker-compose.yml` and its answer is kept at `/intelligence/answers/%s`.\n\n"
        "It is recorded [ref:%s]." % (address, graph["service"])
    )
    stream = "".join("data: %s\n\n" % json.dumps(e) for e in (
        {"type": "token", "text": text},
        {"type": "done", "response": text, "sources": [], "actions_taken": [], "pending_approvals": []},
    ))
    page.route("**/ai-chat/estate-answer", lambda r: r.fulfill(status=200, content_type="application/json", body='{"found": false}'))
    page.route("**/ai-chat/message/stream", lambda r: r.fulfill(status=200, content_type="text/event-stream", body=stream))
    page.set_viewport_size({"width": width, "height": height})
    open_chat(page, live_server)
    page.evaluate("() => document.documentElement.classList.add('dark')")
    page.fill("#user-input", "What does the service depend on?")
    page.press("#user-input", "Enter")
    message = page.locator("#messages-container .message-content").last
    expect(message).to_contain_text("It is recorded", timeout=PAGE_TIMEOUT)
    # A person who switches to dark mode with the answer on screen sees the same: measure after the switch.
    page.evaluate("() => document.documentElement.classList.remove('dark')")
    light = message.evaluate(CONTRAST_SCRIPT)
    page.evaluate("() => document.documentElement.classList.add('dark')")
    page.wait_for_timeout(100)
    immediately = message.evaluate(CONTRAST_SCRIPT)
    page.wait_for_timeout(1500)
    ratios = message.evaluate(CONTRAST_SCRIPT)
    print("CONTRAST light %s immediately-after-switch %s" % (light, immediately))
    print("CONTRAST %dx%d dark %s" % (width, height, ratios))
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, "cited-%d-dark.png" % width), full_page=True)
    for name, value in ratios.items():
        assert value >= 4.5, (name, ratios)

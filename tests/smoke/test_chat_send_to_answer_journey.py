"""From the send, a typed question gets an answer or a plain failure with Retry inside a minute.

The person: the administrator of an organisation who types a question no stored answer matches, so the
chat first looks for a stored answer (asking the AI to place the question) and then asks the AI itself.
Nothing is stubbed between the send and the turn: the AI provider is a loopback double the test switches
between never answering, answering after forty seconds, and failing at once. The time is measured in the
browser from pressing Enter to the answer or the failure card, then Retry is pressed once the provider is
well again and the answer appears.
"""

import os
import socket
import threading
import time

import pytest
from playwright.sync_api import expect

from tests.smoke.chat_ask import open_chat
from tests.smoke.conftest import PAGE_TIMEOUT, boot_live_server
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("CHAT_WAIT_SHOTS")
QUESTION = "Write two sentences on why single sign-on helps staff."
ANSWER = "Single sign-on means one password for staff to remember."
BUSY = "The assistant is busy right now. Wait a minute, then press Retry."
PROVIDERS = ("ANTHROPIC", "AZURE", "CLAUDE", "GEMINI", "GOOGLE", "HUGGINGFACE", "NVIDIA", "OPENAI", "OPENROUTER")
LATE_SECONDS = 40.0
BUDGET_SECONDS = 60.0


class _Provider:
    """A loopback chat-completions provider whose behaviour the test switches: hang, late, fail, answer."""

    def __init__(self):
        self.mode = "hang"
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

    def _reply(self, conn, mode):
        import json

        try:
            conn.settimeout(5)
            conn.recv(65536)
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


def _send(page, base):
    open_chat(page, base)
    box = page.locator("#user-input")
    box.fill(QUESTION)
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

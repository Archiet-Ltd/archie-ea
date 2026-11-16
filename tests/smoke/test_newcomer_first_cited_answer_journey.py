"""A newcomer who connects nothing still gets a first answer that cites its source.

The person: a founder who signed up, skipped every source, kept the company step's defaults and
finished. The goal: ask the first question the chat offers and see which records the answer
rests on. The first cards ask about what the organisation already holds (its company profile and the
canvases it picked), not about general method, so the answer's footer cites records and each cited
link opens a page with content. The first card is Connect GitHub, which lands on the connector page.
Set NEWCOMER_SHOTS to save screenshots.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server
from .test_connect_github_to_cited_answer_journey import _SITECUSTOMIZE
from .test_sign_up_first_screen_journey import (  # noqa: F401  (people is a fixture)
    SIZES,
    _open,
    _skip_to_the_dashboard,
    people,
    sign_up,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("NEWCOMER_SHOTS")


ANSWER = "Your organisation recorded these details when it signed up."


class _Provider(BaseHTTPRequestHandler):
    """The AI provider the built-in AI talks to: it answers every question with one fixed sentence."""

    def do_POST(self):
        asked = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        base = {"id": "x", "object": "chat.completion", "created": 0, "model": "gpt-4o-mini"}
        if asked.get("stream"):
            chunk = dict(base, object="chat.completion.chunk", choices=[{"index": 0, "delta": {"role": "assistant", "content": ANSWER}, "finish_reason": None}])
            last = dict(base, object="chat.completion.chunk", choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}])
            body = ("data: %s\n\ndata: %s\n\ndata: [DONE]\n\n" % (json.dumps(chunk), json.dumps(last))).encode()
            kind = "text/event-stream"
        else:
            body = json.dumps(dict(base, choices=[{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": ANSWER}}],
                                   usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})).encode()
            kind = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def provider():
    peer = ThreadingHTTPServer(("127.0.0.1", 0), _Provider)
    threading.Thread(target=peer.serve_forever, daemon=True).start()
    yield peer
    peer.shutdown()


@pytest.fixture(scope="module")
def live_server(request, app, tmp_path_factory, provider):
    folder = tmp_path_factory.mktemp("newcomer_peer")
    (folder / "sitecustomize.py").write_text(_SITECUSTOMIZE)
    path = os.pathsep.join(p for p in (str(folder), os.environ.get("PYTHONPATH", "")) if p)
    return boot_live_server(
        request, None, app,
        extra_env={
            "PYTHONPATH": path,
            "ALLOW_EXTERNAL_API_CALLS": "true",
            "LLM_PROVIDER_ORDER": "openai",
            "LLM_SPEND_CEILING_USD_OPENAI": "5.00",
            "OPENAI_API_KEY": "sk-journey-built-in-0001",
            "OPENAI_BASE_URL": "http://127.0.0.1:%d/v1" % provider.server_port,
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        },
    )


def _shot(page, name, size):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, "%s-%s.png" % (name, size)), full_page=True)


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_first_card_gives_a_cited_answer_and_connect_github_lands_on_the_connector(
    browser, live_server, people, app, name, viewport
):
    context, page = _open(browser, viewport)
    try:
        email, _ = sign_up(page, live_server, people)
        _skip_to_the_dashboard(page)
        page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        cards = page.get_by_test_id("first-question-suggestion")
        expect(cards.first).to_be_visible(timeout=PAGE_TIMEOUT)
        # the canvases load in the background: wait for a card about one, then reload to read them
        for _ in range(20):
            texts = cards.all_inner_texts()
            if any("Canvas" in t for t in texts):
                break
            page.wait_for_timeout(3000)
            page.reload(wait_until="networkidle")
            expect(cards.first).to_be_visible(timeout=PAGE_TIMEOUT)
        texts = cards.all_inner_texts()
        assert any("Connect GitHub to see who owns what and what breaks" in t for t in texts), texts
        assert any("signed up" in t for t in texts), texts
        assert any("Canvas" in t for t in texts), texts
        _shot(page, "chat-fresh", name)

        # the Connect GitHub card lands on the connector page, which has content
        cards.filter(has_text="Connect GitHub").click()
        page.wait_for_url("**/admin/connectors/github", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="GitHub").first).to_be_visible()
        page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        expect(cards.first).to_be_visible(timeout=PAGE_TIMEOUT)

        card = cards.filter(has_text="signed up")
        card.click()
        message = page.locator("#messages-container .message-content").last
        expect(message).to_be_visible(timeout=PAGE_TIMEOUT)
        try:
            page.wait_for_function(
                "() => document.querySelector('#messages-container').innerText.includes('cited')", timeout=40000
            )
        except Exception:
            raise AssertionError("no cited answer: " + page.locator("#messages-container").inner_text()[:800])
        feed = page.locator("#messages-container").inner_text()
        assert "No lookup ran" not in feed, feed
        assert "record cited" in feed or "records cited" in feed, feed
        _shot(page, "cited-answer", name)
        links = page.locator("#messages-container a[href]").filter(has_text="Company profile")
        links.first.wait_for(state="attached", timeout=PAGE_TIMEOUT)
        assert links.count() >= 1
        for href in {links.nth(i).get_attribute("href") for i in range(links.count())}:
            page.goto(live_server + href, wait_until="networkidle", timeout=PAGE_TIMEOUT)
            expect(page.locator("main")).not_to_be_empty()
            body = page.locator("main").inner_text()
            assert "Startup" in body, body[:300]
            assert "404" not in page.title()
    finally:
        context.close()

"""A startup CTO asks the AI chat who their main competitors are and how they differ, and gets cited records.

The analysis was accepted into the model, so the chat reads it through the strategy part of the model like any
other. The scripted model reads that part, then answers from what it returned, citing each competitor record by id.
It also cites a competitor that is not in the model; that sentence must not reach the person. Every competitor named
is a link to its column of the comparison, which says the same thing. A second company with near-identical names has
competitors of its own: none of them reach the answer or the provider.
"""
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import expect

from tests.smoke.chat_ask import sign_in
from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD, boot_live_server
from tests.smoke.fresh_org import create_fresh_org
from tests.strategy_seed import COMPANY, HARBOR, SWIFTLINE, FakeStrategyModel, seed_company

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

KEY = "sk-strategy-journey-0001"
QUESTION = "Who are our main competitors and how do we differ?"
SKIP_WELCOME = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
SHOTS = os.environ.get("STRATEGY_JOURNEY_SHOTS")
INVENTED = "strategy:competitor:999999"
OTHER_RIVAL = "Harbour Logistics"


class _Model(BaseHTTPRequestHandler):
    """First turn: read the strategy part of the model. Second turn: answer from what was read."""

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        messages = body.get("messages") or []
        self.server.sent.append(" ".join(str(m.get("content")) for m in messages))
        stream = bool(body.get("stream"))
        tools = [m for m in messages if m.get("role") == "tool"]
        if self.headers.get("Authorization") != "Bearer " + KEY:
            return self._send({"error": {"message": "bad key"}}, 401)
        if not tools:
            calls = [{"id": "call_a", "name": "get_genome_slice",
                      "arguments": {"domain": "strategy", "filters": {"kind": "competitor"}}}]
            return self._reply(stream, None, calls)
        return self._reply(stream, self._answer(tools), None)

    @staticmethod
    def _nodes(tools):
        out = []
        for message in tools:
            content = str(message.get("content"))
            start = content.find('{"success"')
            if start >= 0:
                out += (json.JSONDecoder().raw_decode(content[start:])[0].get("result") or {}).get("nodes", [])
        return out

    def _answer(self, tools):
        lines = ["Your main competitors, highest threat first:"]
        for node in sorted(self._nodes(tools), key=lambda n: {"High": 0, "Medium": 1}.get(n.get("threat"), 2)):
            differs = "; ".join(d["text"] for d in node.get("differences") or []) or "no difference recorded"
            lines.append("- [ref:%s] is a %s threat. How you differ: %s." % (node["id"], (node.get("threat") or "unrated").lower(), differs))
        lines.append("- Phantom Freight is also a rival [ref:%s]." % INVENTED)
        return "\n".join(lines)

    def _reply(self, stream, text, calls):
        base = {"id": "chatcmpl-strategy", "created": 0, "model": "gpt-4o-mini"}
        if not stream:
            message = {"role": "assistant", "content": text}
            if calls:
                message["tool_calls"] = [{"id": c["id"], "type": "function",
                                          "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}} for c in calls]
            return self._send({**base, "object": "chat.completion", "choices": [
                {"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}, 200)
        chunks = []
        if calls:
            for i, c in enumerate(calls):
                chunks.append({"tool_calls": [{"index": i, "id": c["id"], "type": "function",
                                               "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}]})
        else:
            for start in range(0, len(text), 40):
                chunks.append({"content": text[start:start + 40]})
        frames = [("data: " + json.dumps({**base, "object": "chat.completion.chunk", "choices": [
            {"index": 0, "delta": delta, "finish_reason": None}]}) + "\n\n").encode() for delta in chunks]
        frames.append(("data: " + json.dumps({**base, "object": "chat.completion.chunk", "choices": [
            {"index": 0, "delta": {}, "finish_reason": "tool_calls" if calls else "stop"}]}) + "\n\n").encode())
        frames.append(b"data: [DONE]\n\n")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(sum(map(len, frames))))
        self.end_headers()
        for frame in frames:
            self.wfile.write(frame)

    def _send(self, payload, status):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def model_peer():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Model)
    server.sent = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


@pytest.fixture(scope="module")
def server(request, app, model_peer):
    base = "http://127.0.0.1:%d/v1" % model_peer.server_port
    env = {"ALLOW_EXTERNAL_API_CALLS": "true", "OPENAI_API_KEY": KEY, "OPENAI_BASE_URL": base,
           "AI_PROVIDER_BASE_URL_OPENAI": base, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
    return boot_live_server(request, None, app, extra_env=env)


@pytest.fixture(scope="module")
def world(server, app):
    """Two founders, each with an organisation made by sign-up and an accepted competitor analysis."""
    from app import db
    from app.models.organization import Organization
    from app.models.user import User
    from app.services import strategy_draft_service as engine

    persona = "enterprise_architect"
    one, two = create_fresh_org(persona), create_fresh_org(persona)
    for who in (one, two):
        who["email"], who["user_id"] = who["emails"][persona], who["user_ids"][persona]
    with app.app_context():
        for who, company, rivals, site in ((one, COMPANY, [HARBOR, SWIFTLINE], "https://northwind-freight.example"),
                                           (two, COMPANY + " Two", [OTHER_RIVAL], "https://two.example")):
            seed_company(who["org_id"], who["user_id"], company=company, rivals=rivals, site=site,
                         signals=None if who is one else {company: [], OTHER_RIVAL: [
                             ("news", "NewsAPI", "Two's rival grows", "Growth.", "https://two.example/news", "2026-10-02")]})
            engine.run_drafts(who["org_id"], llm=FakeStrategyModel())
            engine.accept_drafts(db.session.get(Organization, who["org_id"]), db.session.get(User, who["user_id"]))
        db.session.remove()
    yield {"base": str(server), "one": one, "two": two}


def test_the_chat_names_the_competitors_with_how_we_differ_and_every_name_lands_on_its_column(browser, world, model_peer):
    base, mine = world["base"], world["one"]
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, base, mine["email"])
        page.goto(base + "/ai-chat", wait_until="domcontentloaded")
        box = page.locator("#user-input")
        expect(box).to_be_visible()
        box.fill(QUESTION)
        box.press("Enter")
        answer = page.locator("[data-genome-answer]")
        expect(answer).to_be_visible(timeout=PAGE_TIMEOUT)
        links = answer.locator("a[data-genome-link]")
        named = [links.nth(i).inner_text().strip() for i in range(links.count())]
        assert named == [HARBOR, SWIFTLINE], named  # highest threat first, exactly the two in the model
        text = answer.inner_text()
        assert re.search(r"%s\s+is a high threat" % HARBOR, text), text
        assert re.search(r"%s\s+is a medium threat" % SWIFTLINE, text), text
        assert text.count("We plan routes around refrigeration needs") == 2
        assert "Phantom Freight" not in text and INVENTED not in text and "ref:" not in text
        assert OTHER_RIVAL not in text
        assert OTHER_RIVAL not in " ".join(model_peer.sent), "another company's competitor was sent to the model"
        expect(answer.locator("[data-genome-notice]")).to_contain_text("Answer written by AI from your model")
        if SHOTS:
            os.makedirs(SHOTS, exist_ok=True)
            page.screenshot(path=os.path.join(SHOTS, "chat-answer-1440.png"), full_page=True)
        # each name lands on its column of the comparison, which carries the same facts with their sources
        for i, name in enumerate(named):
            href = links.nth(i).get_attribute("href")
            assert href.startswith("/strategy/competitors#competitor-"), href
            landing = context.new_page()
            landing.goto(base + href, wait_until="domcontentloaded")
            column = landing.locator("#" + href.split("#")[1])
            expect(column).to_contain_text(name)
            expect(landing.locator("main")).to_contain_text("We plan routes around refrigeration needs")
            assert landing.get_by_test_id("source-item").count() >= 1
            landing.close()
    finally:
        context.close()


def test_another_company_gets_only_its_own_competitor(browser, world, model_peer):
    base, theirs = world["base"], world["two"]
    model_peer.sent.clear()
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, base, theirs["email"])
        page.goto(base + "/ai-chat", wait_until="domcontentloaded")
        box = page.locator("#user-input")
        box.fill(QUESTION)
        box.press("Enter")
        answer = page.locator("[data-genome-answer]")
        expect(answer).to_be_visible(timeout=PAGE_TIMEOUT)
        links = answer.locator("a[data-genome-link]")
        assert [links.nth(i).inner_text().strip() for i in range(links.count())] == [OTHER_RIVAL]
        sent = " ".join(model_peer.sent)
        assert HARBOR not in sent and SWIFTLINE not in sent
    finally:
        context.close()

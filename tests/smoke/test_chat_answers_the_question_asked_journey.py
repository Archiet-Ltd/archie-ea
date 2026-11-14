"""The chat answers the question asked, searches the model before saying a fact is missing, and says it searched only when it did.

The person: the founder of an organisation whose model holds a Goal for the pre-seed raise. Their goal:
put board questions to the chat, one per message, and read an answer that covers what was asked, with a
footer that agrees with the words above it. The AI provider is a loopback double that reads the request
and, like a careless model, claims "I searched" in every reply and repeats back the Goal when the
platform's own search handed it to the model.
"""

import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import expect

from tests.smoke.chat_ask import open_chat
from tests.smoke.conftest import PAGE_TIMEOUT, boot_live_server
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("CHAT_ROUTE_SHOTS")
RAISE = "Pre-seed raise of up to USD 250,000, landing April 2027"
PROVIDERS = ("ANTHROPIC", "AZURE", "CLAUDE", "GEMINI", "GOOGLE", "HUGGINGFACE", "NVIDIA", "OPENAI", "OPENROUTER")
FUNDING = "what funding do we plan to raise, how much, and when?"
BOARD = (
    "Search the model and answer as our strategy adviser: what is our monthly burn, what funding "
    "are we raising and when, and what revenue goals have we set with their dates?"
)
RISK = "Which risks could stop our paid launch and who owns each one?"
SCENARIO = (
    "Run a scenario: if paid launch slips three months and Ifiok's AI cost is 35% of its revenue, "
    "what breaks if Ifiok fails?"
)
COST = "What does it cost us to serve one customer?"


class _Model(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        size = int(self.headers.get("Content-Length") or 0)
        request = json.loads(self.rfile.read(size) or b"{}")
        text = json.dumps(request.get("messages"))
        if RAISE in text:
            reply = "I searched the model. The plan is: %s." % RAISE
        else:
            reply = "I searched and found no such element. Nothing is missing from this answer."
        if request.get("stream"):
            chunks = [{"choices": [{"index": 0, "delta": {"content": reply}, "finish_reason": None}]},
                      {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
            body = "".join("data: %s\n\n" % json.dumps(c) for c in chunks) + "data: [DONE]\n\n"
            kind = "text/event-stream"
        else:
            body = json.dumps({"id": "x", "object": "chat.completion", "created": 1, "model": "m",
                               "choices": [{"index": 0, "finish_reason": "stop",
                                            "message": {"role": "assistant", "content": reply}}],
                               "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
            kind = "application/json"
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def model_double():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/v1" % server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def server(request, app, model_double):
    env = {}
    for name in PROVIDERS:
        for suffix in ["", "_SECONDARY"] + ["_%d" % n for n in range(1, 10)]:
            env[name + "_API_KEY" + suffix] = ""
    env.update(
        LLM_API_KEY="", AZURE_OPENAI_API_KEY="", NVIDIA_API_KEY="platform-key-for-the-journey",
        AI_PROVIDER_ORDER="nvidia", AI_PROVIDER_BASE_URL_NVIDIA=model_double,
        AI_PROVIDER_BASE_URL_OPENAI=model_double, OPENAI_BASE_URL=model_double, PYTHON_DOTENV_DISABLED="1",
        NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1",
    )
    return boot_live_server(request, None, app, extra_env=env)


def _seed(org_id):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement

    app = create_app("testing")
    with app.app_context():
        for name, kind in ((RAISE, "Goal"), ("Monthly burn", "Metric")):
            db.session.add(ArchiMateElement(name=name, type=kind, layer="motivation", organization_id=org_id))
        db.session.commit()
        db.session.remove()


def shoot(page, name):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    for width, height, theme in ((1440, 900, "light"), (390, 844, "dark")):
        page.set_viewport_size({"width": width, "height": height})
        page.evaluate("(t) => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
        page.screenshot(path=os.path.join(SHOTS, "%s-%d-%s.png" % (name, width, theme)), full_page=True)
    page.evaluate("() => document.documentElement.classList.remove('dark')")
    page.set_viewport_size({"width": 1440, "height": 900})


def _ask(page, base, question, label):
    """Ask in a new chat; return the reply's words and its footer line."""
    open_chat(page, base)
    box = page.locator("#user-input")
    box.fill(question)
    box.press("Enter")
    message = page.locator("#messages-container .message-content").last
    expect(message).not_to_be_empty(timeout=PAGE_TIMEOUT)
    footer = page.locator("#messages-container .js-evidence-toggle").last
    expect(footer).to_be_visible(timeout=PAGE_TIMEOUT)
    words, line = message.inner_text(), footer.inner_text()
    shoot(page, label)
    return words, line


@pytest.fixture
def founder(server):
    org = create_fresh_org("enterprise_architect", org_admin=True)
    _seed(org["org_id"])
    return org


def test_a_funding_question_runs_a_lookup_and_the_answer_names_the_goal(page, server, founder):
    sign_in(page, server, founder["emails"]["enterprise_architect"])
    words, footer = _ask(page, server, FUNDING, "funding")
    assert RAISE in words, words
    assert "No lookup ran" not in footer and "lookup" in footer, footer


def test_the_three_part_board_question_is_searched_and_never_says_nothing_is_missing_without_a_lookup(
    page, server, founder
):
    sign_in(page, server, founder["emails"]["enterprise_architect"])
    words, footer = _ask(page, server, BOARD, "board")
    assert RAISE in words, words
    assert "No lookup ran" not in footer, footer
    assert "Monthly burn element" not in words and "No initiatives recorded" not in words


@pytest.mark.parametrize("question,label", [(RISK, "risk"), (SCENARIO, "scenario"), (COST, "cost")])
def test_a_reply_claims_a_search_only_when_the_footer_shows_a_lookup(page, server, founder, question, label):
    sign_in(page, server, founder["emails"]["enterprise_architect"])
    words, footer = _ask(page, server, question, label)
    searched = "I searched" in words or "Nothing is missing" in words or "found no such" in words
    if searched:
        assert "No lookup ran" not in footer and "lookup" in footer, (words, footer)
    else:
        assert "No search ran" in words, words
    assert "What breaks if Ifiok fails" not in words
    assert "Which applications" not in words

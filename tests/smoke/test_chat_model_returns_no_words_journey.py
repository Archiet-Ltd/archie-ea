"""A model that answers with no words never leaves a person with an empty answer card.

An organisation uses NVIDIA with two models. The first answers HTTP 200 with no words, as one NVIDIA
model has done; the second answers properly after looking up the organisation's applications. A person
types a question and reads one sentence, opens what it was built from, and follows the link to the
application. A second organisation, whose own models both return no words, reads one plain sentence
with a retry, never a blank card and never the same lookup line twice. Both are read at 1440 and 390.
"""

import json
import os
import re
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import expect

from .chat_ask import open_chat, sign_in
from .conftest import PAGE_TIMEOUT, PASSWORD, boot_live_server  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("SMOKE_SCREENSHOT_DIR") or os.path.join(tempfile.gettempdir(), "chat-no-words-walk")
EMPTY_MODEL = "stub-returns-no-words"
ANSWER_MODEL = "stub-answers"
QUESTION = "Which applications do we run today?"


class _Peer(ThreadingHTTPServer):
    """A stand-in for the model provider: the first model sends no words, the second answers."""

    daemon_threads = True

    def __init__(self):
        self.models_asked = []
        super().__init__(("127.0.0.1", 0), _Handler)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        model = body.get("model")
        self.server.models_asked.append(model)
        messages = body.get("messages") or []
        looked_up = any(m.get("role") == "tool" for m in messages)
        message = {"role": "assistant", "content": ""}
        finish = "stop"
        if model == ANSWER_MODEL and looked_up:
            names = re.findall(r'"name": "([^"]+)"', " ".join(str(m.get("content")) for m in messages if m.get("role") == "tool"))
            message["content"] = "You run %s." % " and ".join(sorted(set(names)))
        elif model == ANSWER_MODEL:
            message["tool_calls"] = [{"id": "call_1", "type": "function",
                                      "function": {"name": "find_applications", "arguments": "{}"}}]
            finish = "tool_calls"
        base = {"id": "chatcmpl-peer", "created": 0, "model": model}
        if body.get("stream"):
            delta = dict(message)
            if "tool_calls" in delta:
                delta["tool_calls"] = [{**delta["tool_calls"][0], "index": 0}]
            chunks = [
                {**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
                {**base, "object": "chat.completion.chunk", "choices": [],
                 "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}},
            ]
            payload = "".join("data: %s\n\n" % json.dumps(c) for c in chunks) + "data: [DONE]\n\n"
            content_type = "text/event-stream"
        else:
            payload = json.dumps({**base, "object": "chat.completion",
                                  "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                                  "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}})
            content_type = "application/json"
        data = payload.encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True


@pytest.fixture(scope="module")
def peer():
    server = _Peer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def server(request, app, peer):
    return boot_live_server(request, None, app, extra_env={
        "AI_PROVIDER_BASE_URL_NVIDIA": "http://127.0.0.1:%d/v1" % peer.server_address[1],
    })


def _seed(label, models, applications):
    """An organisation using NVIDIA with *models* (comma separated) and its own applications."""
    from app import create_app, db
    from app.models.models import APISettings
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="%s Org %s" % (label, suffix), slug="%s-%s" % (label.lower(), suffix))
        db.session.add(org)
        db.session.commit()
        user = User(email="%s.%s@example.com" % (label.lower(), suffix), first_name="Grace",
                    last_name="Hopper", organization_id=org.id,
                    enterprise_role="enterprise_architect", confirmed=True)
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.add(APISettings(provider="nvidia", api_key="peer-key-never-real", enabled=True,
                                   default_model=models, organization_id=org.id))
        ids = {}
        for name in applications:
            row = ApplicationComponent(name="%s %s" % (name, suffix), organization_id=org.id,
                                       lifecycle_status="operational")
            db.session.add(row)
            db.session.flush()
            ids["%s %s" % (name, suffix)] = row.id
        db.session.commit()
        out = {"email": user.email, "apps": ids}
        db.session.remove()
        return out


@pytest.fixture(scope="module")
def world(server):
    return {
        "works": _seed("Works", "%s,%s" % (EMPTY_MODEL, ANSWER_MODEL), ["Billing Hub", "Claims Desk"]),
        "silent": _seed("Silent", "%s,%s" % (EMPTY_MODEL, EMPTY_MODEL + "-two"), ["Ledger Mirror"]),
    }


@pytest.fixture(params=[("desktop", 1440, 900, "light"), ("phone", 390, 844, "dark")], ids=["1440-light", "390-dark"])
def page(browser, request):
    _, width, height, scheme = request.param
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    ctx.set_default_timeout(PAGE_TIMEOUT)
    pg = ctx.new_page()
    pg.shot_tag = "%d-%s" % (width, scheme)
    yield pg
    ctx.close()


def _shot(page, name):
    os.makedirs(WALK, exist_ok=True)
    page.screenshot(path="%s/%s-%s.png" % (WALK, name, page.shot_tag), full_page=True)


def _ask(page, base, email):
    sign_in(page, base, email)
    open_chat(page, base)
    box = page.locator("#user-input")
    box.fill(QUESTION)
    box.press("Enter")
    return page.locator(".message-content").last


def test_second_model_answers_when_the_first_sends_no_words(page, server, peer, world):
    ours, theirs = world["works"], world["silent"]
    answer = _ask(page, server, ours["email"])
    names = sorted(ours["apps"])
    expect(answer).to_contain_text("You run %s and %s." % tuple(names), timeout=PAGE_TIMEOUT)
    text = answer.inner_text()
    assert text.strip() and "completed the available steps" not in text
    assert EMPTY_MODEL in peer.models_asked and ANSWER_MODEL in peer.models_asked
    assert peer.models_asked.index(EMPTY_MODEL) < peer.models_asked.index(ANSWER_MODEL)
    assert not any(n in page.locator("main").inner_text() for n in theirs["apps"])

    page.get_by_role("button", name=re.compile(r"Show what this answer was built from")).last.click()
    for name in names:
        link = page.get_by_role("link", name=name, exact=True)
        expect(link).to_have_count(1)
    _shot(page, "answer-with-source")
    first = names[0]
    page.get_by_role("link", name=first, exact=True).click()
    page.wait_for_url(re.compile(r"/applications/%d(\D|$)" % ours["apps"][first]))
    expect(page.locator("main")).to_contain_text(first)
    assert not any(n in page.locator("main").inner_text() for n in theirs["apps"])


def test_every_model_sending_no_words_shows_one_plain_sentence_and_a_retry(page, server, world):
    theirs = world["silent"]
    _ask(page, server, theirs["email"])
    notice = page.get_by_text("The assistant could not answer this time. Press Retry.")
    expect(notice).to_have_count(1)
    assert page.locator("main").inner_text().count("Found 1 application") == 0
    for blank in page.locator(".message-content").all():
        assert blank.inner_text().strip()
    expect(page.get_by_role("button", name=re.compile("Retry", re.I)).first).to_be_visible()
    _shot(page, "every-model-silent")

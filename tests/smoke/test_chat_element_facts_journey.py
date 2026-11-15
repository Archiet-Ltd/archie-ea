"""A person asks the chat about a Value and a Goal and reads what the model records about each.

The person: an enterprise architect in an organisation whose model holds a Value ("Startup plan") with
a description, a price property imported from a file, and a Goal ("Grow the customer base") with a
description and a date property. A second organisation holds elements of the same names with other
facts. The goal: ask "What does our Startup plan cost?" and "When is the goal Grow the customer base
due?", read the description, the property and where it came from in the answer, open the source the
answer was built from, and land on the element's own page showing the same facts; and never read the
other organisation's facts.
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

WALK = os.environ.get("SMOKE_SCREENSHOT_DIR") or os.path.join(tempfile.gettempdir(), "chat-element-facts-walk")
MODEL = "stub-reads-the-element"
VALUE = "Startup plan"
GOAL = "Grow the customer base"
FACTS = {
    "mine": {
        VALUE: ("The monthly plan for teams of ten", "price", "49 USD per month", "prices.csv"),
        GOAL: ("Double the number of paying teams", "target date", "within 12 months", "plan.csv"),
    },
    "theirs": {
        VALUE: ("A plan for a rival market", "price", "9999 EUR per year", "rival.csv"),
        GOAL: ("Shrink the customer base", "target date", "never", "rival.csv"),
    },
}
QUESTIONS = (
    ("What does our Startup plan cost?", VALUE),
    ("When is the goal Grow the customer base due?", GOAL),
)


class _Peer(ThreadingHTTPServer):
    """A stand-in for the model provider: it asks for the named element, then repeats what the lookup returned."""

    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        messages = body.get("messages") or []
        tool_texts = [str(m.get("content")) for m in messages if m.get("role") == "tool"]
        asked = " ".join(str(m.get("content")) for m in messages if m.get("role") == "user")
        message = {"role": "assistant", "content": ""}
        finish = "stop"
        if tool_texts:
            # The lookup arrives fenced as reference material; the sentence it holds is the "message" member.
            found = re.search(r'"message": "((?:[^"\\]|\\.)*)"', tool_texts[-1])
            message["content"] = json.loads('"%s"' % found.group(1)) if found else ""
        elif body.get("tools"):
            name = GOAL if "goal" in asked.lower() else VALUE
            message["tool_calls"] = [{"id": "call_1", "type": "function", "function": {
                "name": "explain_element", "arguments": json.dumps({"element_name": name})}}]
            finish = "tool_calls"
        base = {"id": "chatcmpl-peer", "created": 0, "model": body.get("model")}
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
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def server(request, app, peer):
    return boot_live_server(request, None, app, extra_env={
        "AI_PROVIDER_BASE_URL_NVIDIA": "http://127.0.0.1:%d/v1" % peer.server_address[1],
    })


def _seed(label, facts):
    """An organisation on the stub provider, with a Value and a Goal carrying *facts*."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement
    from app.models.models import APISettings
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="%s Org %s" % (label, suffix), slug="%s-%s" % (label.lower(), suffix))
        db.session.add(org)
        db.session.commit()
        user = User(email="%s.%s@example.com" % (label.lower(), suffix), first_name="Grace", last_name="Hopper",
                    organization_id=org.id, enterprise_role="enterprise_architect", confirmed=True)
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.add(APISettings(provider="nvidia", api_key="peer-key-never-real", enabled=True,
                                   default_model=MODEL, organization_id=org.id))
        ids = {}
        for name, kind in ((VALUE, "Value"), (GOAL, "Goal")):
            description, prop, value, source = facts[name]
            element = ArchiMateElement(
                name=name, type=kind, layer="motivation", organization_id=org.id, description=description,
                custom_properties={prop: value}, source_kind="import", source_label=source,
            )
            db.session.add(element)
            db.session.flush()
            ids[name] = element.id
        db.session.commit()
        out = {"email": user.email, "ids": ids}
        db.session.remove()
        return out


@pytest.fixture(scope="module")
def world(server):
    return {"mine": _seed("Mine", FACTS["mine"]), "theirs": _seed("Theirs", FACTS["theirs"])}


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


@pytest.mark.parametrize("question,name", QUESTIONS, ids=["value", "goal"])
def test_the_answer_shows_the_description_the_property_and_its_source(page, server, world, question, name):
    mine, theirs = FACTS["mine"][name], FACTS["theirs"][name]
    description, prop, value, source = mine
    sign_in(page, server, world["mine"]["email"])
    open_chat(page, server)
    box = page.locator("#user-input")
    box.fill(question)
    box.press("Enter")
    answer = page.locator(".message-content").last

    expect(answer).to_contain_text(description, timeout=PAGE_TIMEOUT)
    text = answer.inner_text()
    assert "%s: %s" % (prop, value) in text
    assert source in text, "the answer does not say where the property came from: %r" % text
    assert text.index(description) < text.index(value)
    assert theirs[0] not in text and theirs[2] not in text
    assert "explain_element" not in text

    page.get_by_role("button", name=re.compile(r"Show what this answer was built from")).last.click()
    link = page.get_by_role("link", name=name, exact=True)
    expect(link).to_have_count(1)
    _shot(page, "answer-%s" % name.split()[0].lower())

    link.click()
    page.wait_for_url(re.compile(r"/%d(\D|$)" % world["mine"]["ids"][name]))
    main = page.locator("main")
    expect(main).to_contain_text(name)
    expect(main).to_contain_text(description)
    assert theirs[0] not in main.inner_text()
    _shot(page, "element-%s" % name.split()[0].lower())

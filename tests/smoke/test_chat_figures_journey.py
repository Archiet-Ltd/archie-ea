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

WALK = os.environ.get("SMOKE_WALK_DIR")
QUESTION = "Give me a quick summary of Orders Hub for the board."
DEPENDENTS = 13
WRONG = 21
NOT_CONFIRMED = "I could not confirm one or more figures in this answer"
SIZES = [("1440", {"width": 1440, "height": 900}), ("390", {"width": 390, "height": 844})]

# What the peer says; set by each test before it asks.
SCRIPT = {"root_id": None, "after_reason": "right", "seen": [], "plan": None}


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

    @staticmethod
    def _planned(messages, plan):
        """The reply for the person's latest planned question: its lookup first,
        then its draft; when the draft comes back with the reason, the revision
        (or the same draft when none is planned)."""
        asked = [i for i, m in enumerate(messages) if m.get("role") == "user"
                 and any(q in str(m.get("content")) for q in plan)]
        if not asked:
            return {"role": "assistant", "content": "I have nothing planned for that."}
        index = asked[-1]
        entry = next(v for q, v in plan.items() if q in str(messages[index].get("content")))
        later = messages[index + 1:]
        if any(m.get("role") == "user" for m in later):
            SCRIPT["seen"].append("revision")
            return {"role": "assistant", "content": entry.get("revised") or entry["draft"]}
        if any(m.get("role") == "tool" for m in later):
            SCRIPT["seen"].append("draft")
            return {"role": "assistant", "content": entry["draft"]}
        SCRIPT["seen"].append("lookup")
        name, arguments = entry["tool"]
        return {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages") or []
        stream = bool(body.get("stream"))
        last = messages[-1] if messages else {}
        if SCRIPT["plan"] and body.get("tools"):
            self._send(self._planned(messages, SCRIPT["plan"]), stream)
            return
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


def _scheme(viewport):
    """The walk: 1440 wide in light, 390 wide in dark."""
    return "dark" if viewport["width"] == 390 else "light"


def _apply_scheme(page, viewport):
    if _scheme(viewport) == "dark":
        page.evaluate("() => document.documentElement.classList.add('dark')")


def _ask(browser, server, viewport, after_reason, draft=None):
    org = create_fresh_org("enterprise_architect")
    SCRIPT.update(root_id=_estate_with_dependents(org["org_id"], DEPENDENTS), after_reason=after_reason,
                  seen=[], plan=None)
    if draft:
        SCRIPT["plan"] = {QUESTION: {
            "tool": ("answer", {"question": "impact", "element_id": SCRIPT["root_id"]}),
            "draft": draft % SCRIPT["root_id"]}}
    context = browser.new_context(viewport=viewport, color_scheme=_scheme(viewport))
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_in(page, server, org["emails"]["enterprise_architect"])
    page.goto(server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    _apply_scheme(page, viewport)
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
        assert "The records I read do show Orders Hub" in text, text
        assert "Ask me to list the records behind this question" in text, text
        assert SCRIPT["seen"] == ["lookup", "draft", "revision"], SCRIPT["seen"]
        if WALK:
            os.makedirs(WALK, exist_ok=True)
            page.screenshot(path=os.path.join(WALK, "figures-removed-%s.png" % name))
    finally:
        context.close()


# --------------------------------------------------------------------------- #
# Ordinary questions: a true figure is never taken out                          #
# --------------------------------------------------------------------------- #

ISOLATION_COUNT = 21  # organisation B's hub has this many dependents; A's has 13
APPLICATIONS = 12


def _portfolio(org_id, dependents, applications):
    """The organisation's estate: a hub with *dependents* applications downstream,
    a control record whose name holds a number, *applications* applications and
    two top-level capabilities of which one has an application mapped. Returns
    the ids and the count of the organisation's ArchiMate elements."""
    from app import create_app, db
    from app.models import ArchiMateElement
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capability import BusinessCapability

    hub_id = _estate_with_dependents(org_id, dependents)
    app = create_app("testing")
    with app.app_context():
        control = ArchiMateElement(name="Control Set 77", type="ApplicationComponent", layer="application",
                                   organization_id=org_id)
        db.session.add(control)
        capabilities = [BusinessCapability(name="Capability %d" % n, level=1, organization_id=org_id)
                        for n in range(2)]
        db.session.add_all(capabilities)
        applications_made = [ApplicationComponent(name="Portfolio App %02d" % n, organization_id=org_id)
                             for n in range(applications)]
        db.session.add_all(applications_made)
        db.session.flush()
        db.session.add(ApplicationCapabilityMapping(
            application_component_id=applications_made[0].id,
            business_capability_id=capabilities[0].id, organization_id=org_id))
        db.session.commit()
        out = {
            "hub": hub_id,
            "control": control.id,
            "elements": db.session.query(db.func.count(ArchiMateElement.id)).filter(
                ArchiMateElement.organization_id == org_id).scalar(),
        }
        db.session.remove()
    return out


@pytest.fixture(scope="module")
def default_server(request, app):
    """The server as shipped: the citation gate mode is not set."""
    peer = ThreadingHTTPServer(("127.0.0.1", 0), _Provider)
    threading.Thread(target=peer.serve_forever, daemon=True).start()
    request.addfinalizer(peer.shutdown)
    base = "http://127.0.0.1:%d" % peer.server_port
    env = {
        "NVIDIA_API_KEY": "peer-key",
        "NVIDIA_API_BASE": base + "/v1",
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
    }
    os.environ.pop("ASSISTANT_CITATION_GATE_MODE", None)
    return boot_live_server(request, None, app, extra_env=env)


def _send(page, text):
    page.locator("#user-input").fill(text)
    page.locator("#user-input").press("Enter")


def test_ten_ordinary_questions_keep_every_true_figure_and_a_wrong_count_goes(browser, default_server):
    org = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    mine = _portfolio(org["org_id"], DEPENDENTS, APPLICATIONS)
    _portfolio(other["org_id"], ISOLATION_COUNT, 3)
    hub, control = mine["hub"], mine["control"]
    ask = {
        "count": "Give me a quick summary of Orders Hub for the board.",
        "percent": "Summarise the portfolio for the board: applications and capability coverage.",
        "cost": "Summarise whether a budget of £950,000 is enough to retire Orders Hub.",
        "year": "Summarise when Orders Hub should be reviewed next.",
        "standard": "Summarise which standards Orders Hub is assessed against.",
        "name": "Summarise the record called Control Set 77.",
        "id": "Summarise the register entry for Orders Hub, with its record id.",
        "version": "Summarise the notation and platform Orders Hub is drawn in.",
        "scaled": "Summarise whether £2m would cover the move of Orders Hub.",
        "typed": "Summarise whether Orders Hub can reach 45% availability this year.",
        "wrong": "Summarise the downstream connections of Orders Hub, all told.",
    }
    answer = ("answer", {"question": "impact", "element_id": hub})
    dashboard = ("get_executive_dashboard", {})
    plan = {
        ask["count"]: {"tool": answer, "draft": "Orders Hub [ref:%d] has %d connections downstream." % (hub, DEPENDENTS)},
        ask["percent"]: {"tool": dashboard, "draft": "The portfolio holds %d applications and %d elements, and capability coverage is 50%%." % (APPLICATIONS, mine["elements"])},
        ask["cost"]: {"tool": answer, "draft": "The £950,000 you named is the budget to test for Orders Hub [ref:%d]." % hub},
        ask["year"]: {"tool": answer, "draft": "Orders Hub [ref:%d] should be reviewed in 2027." % hub},
        ask["standard"]: {"tool": answer, "draft": "Orders Hub [ref:%d] is assessed against ISO 27001, TOGAF 9.2 and NIST SP 800-53." % hub},
        ask["name"]: {"tool": ("search_archimate_elements", {"name_contains": "Control Set 77"}), "draft": "Control Set 77 [ref:%d] is recorded as an application component." % control},
        ask["id"]: {"tool": answer, "draft": "Orders Hub [ref:%d] has the record id %d in the register." % (hub, hub)},
        ask["version"]: {"tool": answer, "draft": "Orders Hub [ref:%d] is drawn in ArchiMate 3.2 on Java 17." % hub},
        ask["scaled"]: {"tool": answer, "draft": "The £2m you named is the amount to plan the move of Orders Hub [ref:%d] against." % hub},
        ask["typed"]: {"tool": answer, "draft": "The 45%% you named is the availability target for Orders Hub [ref:%d]." % hub},
        ask["wrong"]: {"tool": answer, "draft": "Orders Hub [ref:%d] has %d connections downstream." % (hub, ISOLATION_COUNT)},
    }
    SCRIPT.update(root_id=hub, after_reason="wrong", seen=[], plan=plan)
    expected = [
        ("count", "%d connections downstream" % DEPENDENTS),
        ("percent", "%d applications" % APPLICATIONS),
        ("percent", "%d elements" % mine["elements"]),
        ("percent", "coverage is 50%"),
        ("cost", "£950,000"),
        ("year", "reviewed in 2027"),
        ("standard", "TOGAF 9.2"),
        ("standard", "NIST SP 800-53"),
        ("name", "Control Set 77"),
        ("id", "record id %d" % hub),
        ("version", "ArchiMate 3.2"),
        ("version", "Java 17"),
        ("scaled", "The £2m you named"),
        ("typed", "The 45% you named"),
    ]
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, default_server, org["emails"]["enterprise_architect"])
        page.goto(default_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        reply = page.locator("#messages-container")
        for kind in ("count", "percent", "cost", "year", "standard", "name", "id", "version", "scaled", "typed"):
            _send(page, ask[kind])
            for asked, figure in expected:
                if asked == kind:
                    expect(reply).to_contain_text(figure, timeout=PAGE_TIMEOUT * 2)
            assert NOT_CONFIRMED not in reply.inner_text(), "a true figure was taken out of: " + ask[kind]
        _send(page, ask["wrong"])
        expect(reply).to_contain_text(NOT_CONFIRMED, timeout=PAGE_TIMEOUT * 2)
        text = reply.inner_text()
        assert text.count(NOT_CONFIRMED) == 1, text
        assert "%d connections downstream" % ISOLATION_COUNT not in text, "another organisation's count was kept"
    finally:
        context.close()


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_one_wrong_figure_is_taken_out_and_the_rest_of_the_answer_stays(browser, default_server, name, viewport):
    draft = "Orders Hub [ref:%%s] has %d connections downstream, and %d more sit behind them." % (DEPENDENTS, WRONG)
    context, page, reply = _ask(browser, default_server, viewport, "wrong", draft=draft)
    try:
        expect(reply).to_contain_text("%d connections downstream" % DEPENDENTS, timeout=PAGE_TIMEOUT * 2)
        expect(reply).to_contain_text(NOT_CONFIRMED, timeout=PAGE_TIMEOUT * 2)
        text = reply.inner_text()
        assert str(WRONG) not in text, text
        assert "more sit behind them" not in text, text
        if WALK:
            os.makedirs(WALK, exist_ok=True)
            page.screenshot(path=os.path.join(WALK, "figures-one-out-%s.png" % name))
    finally:
        context.close()

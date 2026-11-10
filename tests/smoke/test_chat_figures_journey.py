"""A figure in a chat answer is the engine's rendering of a value the assistant's own lookup returned.

The persona: an enterprise architect who asks the assistant about their organisation.
The provider is a local peer that speaks the chat-completions protocol. It asks for a lookup, reads
the keys of the figures it was shown, and then answers the way the case says: it writes the
references; it writes the digits itself (the true ones, or wrong ones) even after being sent back;
or it corrects itself. The person must read the true figure with its unit, scale and noun, never a
digit the model wrote, never a raw reference, and every record link must land on a page that shows
the same figure.

Two organisations exist: A, whose person asks, and B, with the same record names and different
figures. The server runs in enforce mode and is set so by the test.
"""
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("SMOKE_WALK_DIR")
NOT_CONFIRMED = "I could not confirm one or more figures in this answer"
NEXT_STEP = "Ask me to list the records behind this question to see the exact figure."
SIZES = [("1440", {"width": 1440, "height": 900}), ("390", {"width": 390, "height": 844})]

# What the peer says; set by each test before it asks: the plan for each question asked.
SCRIPT = {"plan": {}, "seen": []}


def _find_figure(text, field, name=None):
    """The ``{"fig", "reads"}`` the tool result text shows for *field* (of the row called *name*).
    Read from the text as sent: the outbound scrubber may break the JSON around a long digit run."""
    pattern = re.compile(r'"%s": \{"fig": "(F\d+)", "reads": "([^"]*)"\}' % re.escape(field))
    for match in pattern.finditer(str(text)):
        if name is None:
            return {"fig": match.group(1), "reads": match.group(2)}
        row = str(text)[str(text).rfind("{", 0, match.start()):match.end()]
        if json.dumps(name) in row:
            return {"fig": match.group(1), "reads": match.group(2)}
    return None


class _Provider(BaseHTTPRequestHandler):
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
    def _text(template, messages, as_text):
        tool_results = [str(m.get("content")) for m in messages if m.get("role") == "tool"]
        system = " ".join(str(m.get("content")) for m in messages if m.get("role") == "system")

        def figure(match):
            spec = match.group(1)
            if spec.startswith("summary:"):
                found = re.search(r'\[fig:(F\d+)\] reads "(%s[^"]*)"' % spec[len("summary:"):], system)
                key, reads = found.group(1), found.group(2)
            else:
                field, _, name = spec.partition(":")
                got = next((_find_figure(r, field, name or None) for r in tool_results if r), None)
                key, reads = got["fig"], got["reads"]
            return reads if as_text else "[fig:%s]" % key

        return re.sub(r"\{fig:([^}]+)\}", figure, template)

    def _planned(self, messages, plan):
        asked = [i for i, m in enumerate(messages) if m.get("role") == "user"
                 and any(q in str(m.get("content")) for q in plan)
                 and "cannot be shown as written" not in str(m.get("content"))]
        if not asked:
            return {"role": "assistant", "content": "I have nothing planned for that."}
        index = asked[-1]
        entry = next(v for q, v in plan.items() if q in str(messages[index].get("content")))
        later = messages[index + 1:]
        revising = any(m.get("role") == "user" for m in later)
        if entry.get("tool") and not any(m.get("role") == "tool" for m in later):
            SCRIPT["seen"].append("lookup")
            name, arguments = entry["tool"]
            return {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}
        SCRIPT["seen"].append("revision" if revising else "draft")
        mode = entry.get("mode", "right")
        if mode == "corrects":
            mode = "right" if revising else "wrong"
        if mode == "right":
            text = self._text(entry["right"], messages, False)
        elif mode == "ignores":
            text = self._text(entry["right"], messages, True)
        else:
            text = entry["wrong"]
        return {"role": "assistant", "content": text}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages") or []
        stream = bool(body.get("stream"))
        if not body.get("tools"):
            self._send({"role": "assistant", "content": "Summary"}, stream)
            return
        self._send(self._planned(messages, SCRIPT["plan"]), stream)


@pytest.fixture(scope="module")
def server(request, app):
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


# --------------------------------------------------------------------------- #
# The organisations                                                            #
# --------------------------------------------------------------------------- #


def _fixture():
    from app.modules.intelligence.services.gold_replay import load_set

    return load_set("figures")[1]


def _estate(persona, variant):
    """A fresh organisation with *persona* signed in and the gold estate of *variant*."""
    from app import create_app, db

    org = create_fresh_org(persona)
    app = create_app("testing")
    with app.app_context():
        org["ids"] = _fixture().build_estate(org["org_id"], variant)
        db.session.commit()
        db.session.remove()
    org["persona"] = persona
    return org


def _walk(page, name, size):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, "figures-%s-%s.png" % (name, size)), full_page=True)


def _open(browser, server, org, viewport):
    dark = viewport["width"] == 390
    context = browser.new_context(viewport=viewport, color_scheme="dark" if dark else "light")
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_in(page, server, org["emails"][org["persona"]])
    page.goto(server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    if dark:
        page.evaluate("() => document.documentElement.classList.add('dark')")
    return context, page, page.locator("#messages-container")


def _ask(page, text):
    page.locator("#user-input").fill(text)
    page.locator("#user-input").press("Enter")


def _no_raw_tokens(text):
    assert "[fig:" not in text and "fig:F" not in text, text


def _plan(**asked):
    SCRIPT.update(plan=asked, seen=[])


# --------------------------------------------------------------------------- #
# J1: what an application costs, and the page the answer links to              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_an_architect_asks_what_an_application_costs_and_the_record_page_shows_the_same_amount(
    browser, server, name, viewport
):
    org = _estate("enterprise_architect", "A")
    app_id = org["ids"]["applications"]["Field Service Management"]
    ask = "What does Field Service Management cost?"
    _plan(**{ask: {
        "tool": ("find_applications", {"limit": 50}),
        "right": "Field Service Management [ref:%d] has {fig:annual_cost:Field Service Management}." % app_id,
    }})
    context, page, reply = _open(browser, server, org, viewport)
    try:
        _ask(page, ask)
        expect(reply).to_contain_text("an annual cost of £110,000", timeout=PAGE_TIMEOUT * 2)
        text = reply.inner_text()
        _no_raw_tokens(text)
        assert "£470,000" not in text and "£250,000" not in text, "another row's cost"
        _walk(page, "j1-cost", name)
        link = reply.get_by_role("link", name="Field Service Management").first
        expect(link).to_be_visible()
        link.click()
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        # The record page keeps its figures on tabs: the cost is on the Cost tab.
        page.get_by_text("Cost", exact=True).first.click()
        expect(page.locator("body")).to_contain_text("110,000", timeout=PAGE_TIMEOUT)
        landing = page.locator("body").inner_text()
        _walk(page, "j1-record-page", name)
        assert "Field Service Management" in landing, landing[:500]
        assert "110,000" in landing, "the record page shows the amount the answer showed"
    finally:
        context.close()


# --------------------------------------------------------------------------- #
# J2: how many applications, and a model that answers 999 without a lookup     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_an_architect_asks_how_many_applications_and_a_made_up_count_is_never_shown(browser, server, name, viewport):
    org = _estate("enterprise_architect", "A")
    ask = "How many applications do we have?"
    _plan(**{ask: {"tool": None, "right": "You have {fig:summary:[0-9]+ application} in the register.",
                   "wrong": "You have 999 applications.", "mode": "wrong"}})
    context, page, reply = _open(browser, server, org, viewport)
    try:
        _ask(page, ask)
        expect(reply).to_contain_text("20 applications", timeout=PAGE_TIMEOUT * 2)
        text = reply.inner_text()
        assert "999" not in text, text
        assert NOT_CONFIRMED in text and NEXT_STEP in text
        assert SCRIPT["seen"] == ["draft", "revision"], SCRIPT["seen"]
        _no_raw_tokens(text)
        _walk(page, "j2-no-tool", name)
    finally:
        context.close()


def test_a_summary_reference_written_without_a_lookup_is_shown_as_the_true_count(browser, server):
    org = _estate("enterprise_architect", "A")
    ask = "How many applications do we have?"
    _plan(**{ask: {"tool": None, "right": "You have {fig:summary:[0-9]+ application} in the register."}})
    context, page, reply = _open(browser, server, org, SIZES[0][1])
    try:
        _ask(page, ask)
        expect(reply).to_contain_text("You have 20 applications in the register.", timeout=PAGE_TIMEOUT * 2)
        assert NOT_CONFIRMED not in reply.inner_text()
        _no_raw_tokens(reply.inner_text())
    finally:
        context.close()


# --------------------------------------------------------------------------- #
# J3: a score keeps its scale when the model ignores the instruction twice     #
# --------------------------------------------------------------------------- #


def _solution(org_id, maturity):
    from app import create_app, db
    from app.models.solution_models import Solution

    app = create_app("testing")
    with app.app_context():
        solution = Solution(name="Journey Solution", organization_id=org_id, maturity_current=maturity)
        db.session.add(solution)
        db.session.commit()
        ident = solution.id
        db.session.remove()
    return ident


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_a_score_keeps_its_scale_when_the_model_ignores_the_instruction_twice(browser, server, name, viewport):
    org = _estate("enterprise_architect", "A")
    solution = _solution(org["org_id"], 3)
    ask = "How mature is Journey Solution?"
    _plan(**{ask: {
        "tool": ("get_solution_summary", {"solution_id": solution}),
        "right": "Journey Solution [ref:%d] has {fig:maturity_level}." % solution,
        "mode": "ignores",
    }})
    context, page, reply = _open(browser, server, org, viewport)
    try:
        _ask(page, ask)
        expect(reply).to_contain_text("a maturity of 3 out of 5", timeout=PAGE_TIMEOUT * 2)
        text = reply.inner_text()
        assert NOT_CONFIRMED in text and "What the records show:" in text
        assert SCRIPT["seen"] == ["lookup", "draft", "revision"], SCRIPT["seen"]
        _no_raw_tokens(text)
        _walk(page, "j3-score-scale", name)
        link = reply.get_by_role("link", name="Journey Solution").first
        expect(link).to_be_visible()
        link.click()
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        assert "Journey Solution" in page.locator("body").inner_text()
    finally:
        context.close()


# --------------------------------------------------------------------------- #
# J4: a ranked list of twelve keeps every row                                  #
# --------------------------------------------------------------------------- #


def _capabilities(org_id, count):
    """*count* capabilities that the capability-gap lookup returns; capability n has n % 4 supporting applications."""
    from app import create_app, db
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability

    app = create_app("testing")
    with app.app_context():
        apps = [ApplicationComponent(name="Rank App %d" % n, organization_id=org_id) for n in range(3)]
        caps = [BusinessCapability(name="Rank Capability %02d" % (n + 1), organization_id=org_id,
                                   current_maturity_level=1, target_maturity_level=3) for n in range(count)]
        db.session.add_all(apps + caps)
        db.session.flush()
        supporting = {}
        for n, cap in enumerate(caps):
            for application in apps[: n % 4]:
                db.session.add(ApplicationCapabilityMapping(
                    organization_id=org_id, application_component_id=application.id, business_capability_id=cap.id))
            supporting[cap.name] = (cap.id, n % 4)
        db.session.commit()
        db.session.remove()
    return supporting


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_a_ranked_list_of_twelve_keeps_every_row_with_its_figure(browser, server, name, viewport):
    org = _estate("enterprise_architect", "A")
    rows = _capabilities(org["org_id"], 12)
    ask = "Rank our twelve capability gaps by supporting applications."
    lines = "\n".join(
        "%d. Rank Capability %02d [ref:%d] - {fig:supporting_apps:Rank Capability %02d}" % (
            n + 1, n + 1, rows["Rank Capability %02d" % (n + 1)][0], n + 1)
        for n in range(12))
    _plan(**{ask: {"tool": ("query_capability_gaps", {"limit": 50}), "right": "Ranked:\n" + lines,
                   "mode": "ignores"}})
    context, page, reply = _open(browser, server, org, viewport)
    try:
        _ask(page, ask)
        expect(reply).to_contain_text("Rank Capability 12", timeout=PAGE_TIMEOUT * 2)
        text = reply.inner_text()
        _no_raw_tokens(text)
        for n in range(12):
            cap = "Rank Capability %02d" % (n + 1)
            count = rows[cap][1]
            noun = "supporting application" if count == 1 else "supporting applications"
            assert cap in text, cap
            assert "%d %s" % (count, noun) in text, (cap, count)
        # The list markers are positions: one to twelve.
        items = reply.locator("ol > li")
        if items.count():
            assert items.count() >= 12 or "What the records show:" in text
        _walk(page, "j4-ranked-twelve", name)
    finally:
        context.close()


# --------------------------------------------------------------------------- #
# J5: organisation A never sees organisation B's figure                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_organisation_a_never_sees_organisation_bs_figure(browser, server, name, viewport):
    org_a = _estate("enterprise_architect", "A")
    _estate("enterprise_architect", "B")
    ask = "How many connections does Billing have?"
    element = org_a["ids"]["elements"]["Billing"]
    right = "Billing [ref:%d] has {fig:total}." % element
    _plan(**{ask: {"tool": ("answer", {"question": "impact", "element_id": element}), "right": right}})
    context, page, reply = _open(browser, server, org_a, viewport)
    try:
        _ask(page, ask)
        expect(reply).to_contain_text("4 connections downstream", timeout=PAGE_TIMEOUT * 2)
        assert "9 connections" not in reply.inner_text()
        _no_raw_tokens(reply.inner_text())
        # The same question, answered with the other organisation's value: never shown.
        _plan(**{"How many dependants does Billing have?": {
            "tool": ("answer", {"question": "impact", "element_id": element}),
            "right": right, "wrong": "Billing [ref:%d] has 9 connections downstream." % element, "mode": "wrong"}})
        _ask(page, "How many dependants does Billing have?")
        expect(reply).to_contain_text(NOT_CONFIRMED, timeout=PAGE_TIMEOUT * 2)
        text = reply.inner_text()
        assert text.count("9 connections downstream") == 0, text
        assert text.count("4 connections downstream") >= 2, "the true count is shown again from the records"
        _walk(page, "j5-other-organisation-refused", name)
    finally:
        context.close()


# --------------------------------------------------------------------------- #
# J6: standards, versions and typed amounts stay                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_standards_versions_and_typed_amounts_stay_in_the_answer(browser, server, name, viewport):
    org = _estate("enterprise_architect", "A")
    ask = ("Does our £2m budget stretch to a $1.5B comparison, and which of ISO 27001, "
           "NIST SP 800-53 and TOGAF 9.2 should we read first?")
    answer = ("Your £2m budget is far below a $1.5B programme, so compare nothing by cost. Read ISO 27001 "
              "first, then NIST SP 800-53, then TOGAF 9.2.")
    # The key is a stretch the outbound scrubber leaves alone ("800-53" reads as a phone number).
    _plan(**{"Does our": {"tool": None, "right": answer}})
    context, page, reply = _open(browser, server, org, viewport)
    try:
        _ask(page, ask)
        expect(reply).to_contain_text("compare nothing by cost", timeout=PAGE_TIMEOUT * 2)
        for kept in ("ISO 27001", "NIST SP 800-53", "TOGAF 9.2", "£2m", "$1.5B"):
            expect(reply).to_contain_text(kept, timeout=PAGE_TIMEOUT * 2)
        text = reply.inner_text()
        assert NOT_CONFIRMED not in text and "Not recorded:" not in text, text
        assert SCRIPT["seen"] == ["draft"], "no revision for an answer with no figure of its own"
        _walk(page, "j6-names-and-typed", name)
    finally:
        context.close()


# --------------------------------------------------------------------------- #
# J7: the cases from the earlier rounds                                        #
# --------------------------------------------------------------------------- #


def _impact_case(mode, wrong, name_size, case):
    org = _estate("enterprise_architect", "A")
    hub = org["ids"]["elements"]["Orders Hub"]
    ask = "Give me a quick summary of Orders Hub for the board."
    _plan(**{ask: {"tool": ("answer", {"question": "impact", "element_id": hub}),
                   "right": "Orders Hub [ref:%d] has {fig:total}, so a failure there reaches them all." % hub,
                   "wrong": wrong.replace("%d", str(hub)), "mode": mode}})
    return org, ask


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_a_wrong_count_is_corrected_on_revision_before_the_person_reads_it(browser, server, name, viewport):
    org, ask = _impact_case(
        "corrects", "Orders Hub [ref:%d] has 21 connections downstream, so a failure there reaches them all.",
        name, "corrected")
    context, page, reply = _open(browser, server, org, viewport)
    try:
        _ask(page, ask)
        expect(reply).to_contain_text("13 connections downstream", timeout=PAGE_TIMEOUT * 2)
        text = reply.inner_text()
        assert "21" not in text and NOT_CONFIRMED not in text, text
        assert SCRIPT["seen"] == ["lookup", "draft", "revision"], SCRIPT["seen"]
        _walk(page, "j7-corrected", name)
    finally:
        context.close()


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_one_clause_with_a_wrong_figure_is_taken_out_and_the_rest_stays(browser, server, name, viewport):
    org, ask = _impact_case(
        "wrong", "Orders Hub [ref:%d] has 13 connections downstream, and 21 more sit behind them.", name, "clause")
    context, page, reply = _open(browser, server, org, viewport)
    try:
        _ask(page, ask)
        expect(reply).to_contain_text(NOT_CONFIRMED, timeout=PAGE_TIMEOUT * 2)
        text = reply.inner_text()
        assert "21" not in text and "more sit behind them" not in text, text
        assert "13 connections downstream" in text, "the true figure is shown by the engine"
        assert "Orders Hub" in text
        _walk(page, "j7-one-clause-out", name)
    finally:
        context.close()


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_when_nothing_is_left_the_person_reads_the_plain_line_the_records_and_the_next_step(
    browser, server, name, viewport
):
    org, ask = _impact_case("wrong", "About 21 connections depend on it.", name, "nothing")
    context, page, reply = _open(browser, server, org, viewport)
    try:
        _ask(page, ask)
        expect(reply).to_contain_text(NEXT_STEP, timeout=PAGE_TIMEOUT * 2)
        text = reply.inner_text()
        assert "21" not in text and NOT_CONFIRMED in text and "What the records show:" in text, text
        assert "13 connections downstream" in text
        link = reply.get_by_role("link", name="Orders Hub").first
        expect(link).to_be_visible()
        link.click()
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        landing = page.locator("body").inner_text()
        assert "Orders Hub" in landing and "13" in landing, "the record link lands on a page with content"
        _walk(page, "j7-nothing-left", name)
    finally:
        context.close()

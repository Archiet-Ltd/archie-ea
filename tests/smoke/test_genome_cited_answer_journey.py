"""A startup CTO asks the AI chat which capabilities nothing supports, and gets the model's own answer.

The person opens the AI chat in an organisation whose model holds linked capabilities,
applications, plateaus, work packages and gaps, and asks "Which of our capabilities does nothing
support, and what is planned to close them?". The answer must name exactly the capabilities the
Capability Coverage page shows with no supporting application, and for each the work package the
roadmap shows closing it (or say plainly that none is planned); every name is a link to a page of
that record; the answer says it was written by AI from the model, by which provider, and when the
facts were read. A second organisation with near-identical names holds records of its own: none of
them reaches the answer or the provider.

The model is a scripted peer that behaves like a careful reader: it asks for the two slices of the
model, then writes the answer from what they returned, citing each record by id. It also tries to
cite a record that does not exist; the answer must not carry that sentence.
"""

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import expect

from tests.genome_cited_seed import seed_cited_model
from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD, boot_live_server
from tests.smoke.chat_ask import sign_in
from tests.smoke.test_connect_github_to_cited_answer_journey import remove_organisations

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

KEY = "sk-genome-journey-0001"
QUESTION = "Which of our capabilities does nothing support, and what is planned to close them?"
SKIP_WELCOME = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
SHOTS = os.environ.get("GENOME_JOURNEY_SHOTS")
INVENTED = "business:cap:999999"


class _Model(BaseHTTPRequestHandler):
    """An OpenAI-compatible peer. First turn: read the model. Second turn: answer from what was read."""

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
            calls = [
                {"id": "call_a", "name": "get_genome_slice",
                 "arguments": {"domain": "business", "filters": {"kind": "cap", "supported": False}}},
                {"id": "call_b", "name": "get_genome_slice",
                 "arguments": {"domain": "implementation", "filters": {"kind": "wp"}}},
            ]
            return self._reply(stream, text=None, calls=calls)
        return self._reply(stream, text=self._answer(tools), calls=None)

    @staticmethod
    def _slices(tools):
        out = []
        for message in tools:
            content = str(message.get("content"))
            start = content.find('{"success"')
            if start >= 0:
                out.append(json.JSONDecoder().raw_decode(content[start:])[0])
        return out

    def _answer(self, tools):
        nodes = [n for r in self._slices(tools) for n in (r.get("result") or {}).get("nodes", [])]
        caps = sorted((n for n in nodes if n["kind"] == "cap"), key=lambda n: n["name"])
        packages = [n for n in nodes if n["kind"] == "wp"]
        lines = ["These capabilities have no application supporting them:"]
        for cap in caps:
            closing = [w for w in packages if w.get("capability") == cap["id"]]
            if closing:
                lines.append("- [ref:%s] is planned to be closed by [ref:%s]." % (cap["id"], closing[0]["id"]))
            else:
                lines.append("- [ref:%s] has no work package planned to close it." % cap["id"])
        lines.append("- Phantom Treasury is also unsupported [ref:%s]." % INVENTED)
        return "\n".join(lines)

    def _reply(self, stream, text, calls):
        base = {"id": "chatcmpl-genome", "created": 0, "model": "gpt-4o-mini"}
        if not stream:
            message = {"role": "assistant", "content": text}
            if calls:
                message["tool_calls"] = [
                    {"id": c["id"], "type": "function",
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
           "AI_PROVIDER_BASE_URL_OPENAI": base,
           "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
    return boot_live_server(request, None, app, extra_env=env)


def _sign_up(app, label):
    """A new startup founder, registered the way the sign-up form registers them: their own organisation,
    created by the product, with whatever it gives a new organisation (nothing is seeded here)."""
    import uuid

    from app import db
    from app.models.user import User
    from app.modules.account.services.account_service import AccountService

    email = "cto.%s@example.com" % uuid.uuid4().hex[:8]
    with app.test_request_context("/"):
        AccountService.register_user("Casey", label, email, PASSWORD, confirmed=True)
    with app.app_context():
        org_id = User.query.filter_by(email=email).one().organization_id
        db.session.remove()
    return {"email": email, "org_id": org_id}


@pytest.fixture(scope="module")
def world(server, app):
    """Two founders, each with an organisation of their own made by sign-up and a linked model each;
    the second spells its records almost the same."""
    from app import db

    one = _sign_up(app, "One")
    two = _sign_up(app, "Two")
    with app.app_context():
        seed_one = seed_cited_model(db.session, one["org_id"])
        seed_two = seed_cited_model(db.session, two["org_id"], suffix=" 2")
        db.session.commit()
        db.session.remove()
    yield {"base": str(server), "one": one, "two": two, "seed_one": seed_one, "seed_two": seed_two}
    _remove_what_the_chat_wrote(app, [one["org_id"], two["org_id"]])
    remove_organisations(app, [one["org_id"], two["org_id"]])


def _remove_what_the_chat_wrote(app, org_ids):
    """Asking the chat stores threads that hang off the person, not the organisation."""
    from sqlalchemy import text

    from app import db

    with app.app_context():
        ids = {"ids": list(org_ids)}
        user_ids = [r[0] for r in db.session.execute(text("SELECT id FROM users WHERE organization_id = ANY(:ids)"), ids)]
        referrers = db.session.execute(text(
            "SELECT kcu.table_name, kcu.column_name FROM information_schema.referential_constraints rc "
            "JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = rc.constraint_name "
            "JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = rc.unique_constraint_name "
            "WHERE ccu.table_name = 'users' AND ccu.column_name = 'id' AND kcu.table_schema = 'public' "
            "AND kcu.table_name = 'conversation_threads'"
        )).all()
        db.session.execute(text("DELETE FROM conversation_messages WHERE thread_id IN "
                                "(SELECT id FROM conversation_threads WHERE user_id = ANY(:u))"), {"u": user_ids})
        for table, column in referrers:
            with db.session.begin_nested():
                db.session.execute(text('DELETE FROM "%s" WHERE "%s" = ANY(:u)' % (table, column)), {"u": user_ids})
        # Rows of the seeded model that name an organisation's records through no organisation column.
        db.session.execute(text("DELETE FROM unified_application_capability_mapping WHERE application_component_id IN "
                                "(SELECT id FROM application_components WHERE organization_id = ANY(:ids))"), ids)
        # The audit trail is append-only by application convention (no update()/delete()
        # methods are exposed), not by a database trigger, so a plain delete needs no switch.
        db.session.execute(text("DELETE FROM soc2_audit_log WHERE organization_id = ANY(:ids)"), ids)
        # Role grants name their person twice (the holder and whoever granted it), so they go before the people.
        db.session.execute(text("DELETE FROM org_roles WHERE user_id = ANY(:u) OR granted_by = ANY(:u)"), {"u": user_ids})
        db.session.execute(text("DELETE FROM users WHERE organization_id = ANY(:ids)"), ids)
        db.session.commit()
        db.session.remove()


def _shoot(page, name):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    for width, height in ((1440, 900), (390, 844)):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(300)
        page.screenshot(path=os.path.join(SHOTS, "%s-%d.png" % (name, width)), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})


def _coverage_page_unsupported(page, base):
    """The capabilities the Capability Coverage page shows with no supporting application."""
    page.goto(base + "/genome/coverage/", wait_until="domcontentloaded")
    page.get_by_role("button", name=re.compile("Generate coverage matrix", re.I)).click()
    rows = page.locator("th[data-capability-id]")
    expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
    names = []
    for i in range(rows.count()):
        row = rows.nth(i).locator("xpath=..")
        if row.locator(".genome-cov-cell").count() == 0:
            names.append(rows.nth(i).text_content().strip())
    return sorted(names)


def test_a_startup_cto_asks_what_nothing_supports_and_every_name_in_the_answer_is_a_page_of_their_model(
    browser, world, model_peer
):
    base, mine, other = world["base"], world["one"], world["two"]
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    second = None
    try:
        sign_in(page, base, mine["email"])
        unsupported = _coverage_page_unsupported(page, base)
        assert unsupported == ["Fraud Screening", "Partner Payouts"], unsupported
        page.goto(base + "/genome/roadmap/", wait_until="domcontentloaded")
        roadmap = page.locator("main").inner_text()
        assert world["seed_one"]["names"]["wp"] in roadmap
        assert world["seed_two"]["names"]["wp"] not in roadmap

        # Ask in the AI chat, the way a person does.
        page.goto(base + "/ai-chat", wait_until="domcontentloaded")
        box = page.locator("#user-input")
        expect(box).to_be_visible()
        box.fill(QUESTION)
        box.press("Enter")
        answer = page.locator("[data-genome-answer]")
        expect(answer).to_be_visible(timeout=PAGE_TIMEOUT)

        # The capabilities named are exactly the ones the coverage page shows with nothing supporting them.
        links = answer.locator("a[data-genome-link]")
        named = [links.nth(i).inner_text().strip() for i in range(links.count())]
        caps = sorted(n for n in named if n in unsupported)
        assert caps == unsupported, named
        # Each is followed by what the roadmap shows closing it, or plainly by none planned.
        text = answer.inner_text()
        planned = world["seed_one"]["names"]["wp"]
        assert re.search(r"Fraud Screening\s+is planned to be closed by\s+%s" % re.escape(planned), text), text
        assert re.search(r"Partner Payouts\s+has no work package planned to close it", text), text
        assert planned in named

        # The invented record's sentence is gone, and so is everything of the other organisation.
        assert "Phantom Treasury" not in text and INVENTED not in text
        assert "ref:" not in text
        for name in world["seed_two"]["names"].values():
            assert name not in text, name
        everything_sent = " ".join(model_peer.sent)
        for name in world["seed_two"]["names"].values():
            assert name not in everything_sent, "%s was sent to the model" % name

        # The notice, the provider and when the facts were read.
        notice = answer.locator("[data-genome-notice]")
        expect(notice).to_contain_text("Answer written by AI from your model")
        expect(notice).to_contain_text("OpenAI")
        expect(notice.locator("[data-genome-as-of]")).to_contain_text(re.compile(r"Model as of \d{2} \w{3} \d{4}, \d{2}:\d{2} UTC"))
        # The strip under the answer tells the truth about what was read.
        assert page.get_by_text("No lookup ran").count() == 0
        expect(page.get_by_text(re.compile(r"^2 lookups"))).to_be_visible()
        _shoot(page, "genome-answer")

        # Every name is a link to a page of that record, and the page says what the answer said about it.
        hrefs = [(links.nth(i).inner_text().strip(), links.nth(i).get_attribute("href")) for i in range(links.count())]
        assert len(hrefs) == 3, hrefs
        planned = world["seed_one"]["names"]["wp"]
        for name, href in hrefs:
            assert href and href.startswith("/"), (name, href)
            landing = context.new_page()
            landing.goto(base + href, wait_until="domcontentloaded")
            body = landing.locator("main").inner_text()
            assert name.lower() in body.lower() and len(body.strip()) > len(name) + 20, (name, href, body[:200])
            if name in unsupported:
                # the capability's row: its support (no application) and what is planned for it
                row = landing.locator("tr").filter(has=landing.locator("th[data-capability-id]", has_text=name))
                expect(row).to_have_count(1)
                assert not row.locator(".genome-cov-cell").count(), "an unsupported capability shows support"
                cell = row.locator("td[data-planned-work-for]").inner_text()
                if name == "Fraud Screening":
                    assert planned in cell, cell
                    assert row.locator("td[data-planned-work-for] a").first.get_attribute("href").endswith(
                        "/work-packages/%d" % world["seed_one"]["wp_id"])
                else:
                    assert "None planned" in cell, cell
            else:
                # the work package's own page names the capability it changes
                assert landing.get_by_test_id("wp-capability").inner_text().strip() == "Fraud Screening", body[:300]
                assert planned in landing.locator("h1").inner_text() or planned in body
            _shoot(landing, "genome-link-" + re.sub(r"\W+", "-", name.lower()))
            landing.close()

        # The other organisation's person sees only their own names through the same question.
        model_peer.sent.clear()
        second = browser.new_context(viewport={"width": 1440, "height": 900})
        second.add_init_script(SKIP_WELCOME)
        second.set_default_timeout(PAGE_TIMEOUT * 2)
        page = second.new_page()
        sign_in(page, base, other["email"])
        page.goto(base + "/ai-chat", wait_until="domcontentloaded")
        page.locator("#user-input").fill(QUESTION)
        page.locator("#user-input").press("Enter")
        theirs = page.locator("[data-genome-answer]")
        expect(theirs).to_be_visible(timeout=PAGE_TIMEOUT)
        their_links = [theirs.locator("a[data-genome-link]").nth(i).inner_text().strip()
                       for i in range(theirs.locator("a[data-genome-link]").count())]
        assert sorted(n for n in their_links if "Fraud Screening" in n or "Partner Payouts" in n) == ["Fraud Screening 2", "Partner Payouts 2"], their_links
        first_org_wp = re.escape(world["seed_one"]["names"]["wp"]) + r"(?! 2)"
        assert not re.search(first_org_wp, theirs.inner_text())
        assert not re.search(first_org_wp, " ".join(model_peer.sent))
    finally:
        context.close()
        if second is not None:
            second.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_coverage_page_reads_in_full_on_a_phone_in_light_and_dark_and_in_plain_words(browser, world, scheme):
    """QA-039 and QA-043: at 390 wide every column of the coverage table can be read (the table slides inside its
    card, the page itself does not overflow), the planned work is whole, and no engineering name shows."""
    base, mine = world["base"], world["one"]
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script(SKIP_WELCOME)
    context.add_init_script("localStorage.setItem('theme', '%s');" % scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, base, mine["email"])
        page.goto(base + "/genome/coverage/generate", wait_until="domcontentloaded")
        scroller = page.locator("[data-coverage-scroll]")
        expect(scroller).to_be_visible()
        assert page.evaluate("document.documentElement.classList.contains('dark')") == (scheme == "dark")
        # the page itself does not spill sideways; the table slides inside its card
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        box = scroller.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 391, box
        headers = scroller.locator("thead th")
        assert headers.count() == 3  # capability, the one application, planned work
        for i in range(headers.count()):
            header = headers.nth(i)
            header.scroll_into_view_if_needed()
            hb, sb = header.bounding_box(), scroller.bounding_box()
            assert hb["width"] > 20 and hb["x"] >= sb["x"] - 1 and hb["x"] + hb["width"] <= sb["x"] + sb["width"] + 1, (i, hb, sb)
        scroller.evaluate("el => { el.scrollLeft = el.scrollWidth; }")
        scroller.evaluate("el => { el.scrollLeft = 0; }")
        # no cell shows less text than it holds
        clipped = page.evaluate("""() => [...document.querySelectorAll('[data-coverage-scroll] th, [data-coverage-scroll] td')]
            .filter(c => c.scrollWidth > c.clientWidth + 1).map(c => c.innerText.trim())""")
        assert clipped == [], clipped
        for label in ("Customer Onboarding", "Fraud Screening", "Partner Payouts", "Ledger Hub"):
            assert scroller.get_by_text(label, exact=False).count() >= 1, label
        planned = world["seed_one"]["names"]["wp"]
        link = scroller.get_by_role("link", name=planned)
        link.scroll_into_view_if_needed()
        # the whole name is there, not cut: the link's text is not wider than its box
        assert page.evaluate("(el) => el.scrollWidth <= el.clientWidth + 1", link.element_handle())
        lb, sb = link.bounding_box(), scroller.bounding_box()
        assert lb["x"] >= sb["x"] - 1 and lb["x"] + lb["width"] <= sb["x"] + sb["width"] + 1, (lb, sb)
        assert link.inner_text().strip() == planned
        text = page.locator("main").inner_text().lower()
        for engineering in ("unified_capabilities", "business_capability", "sha256", "zero llm", "archimate element ids", "deterministic"):
            assert engineering not in text, engineering
        scroller.evaluate("el => { el.scrollLeft = 0; }")
        if SHOTS:
            os.makedirs(SHOTS, exist_ok=True)
            page.screenshot(path=os.path.join(SHOTS, "coverage-390-%s-start.png" % scheme), full_page=True)
            scroller.evaluate("el => { el.scrollLeft = el.scrollWidth; }")
            page.screenshot(path=os.path.join(SHOTS, "coverage-390-%s-end.png" % scheme), full_page=True)
    finally:
        context.close()

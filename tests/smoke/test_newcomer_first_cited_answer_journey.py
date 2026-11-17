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
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import expect

from app.extensions import db

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


def cards_a_newcomer_is_shown(app, email):
    """The questions the first screen shows this organisation: what the server offers, less the GitHub
    card, which the next-step button already carries, at most four."""
    with app.app_context():
        from app.models.user import User
        from app.modules.ai_chat.routes.analytics_routes import own_record_questions

        org_id = User.query.filter_by(email=email).one().organization_id
        return [q["question"] for q in own_record_questions(org_id) if not q.get("href")][:4]


def _record_on_a_canvas(app, email):
    """Record one element in a box the single-box questions do not ask about (a Lean Canvas Problem line), for the
    person's organisation, and place it on its canvas: the card that follows must still lead to that entry."""
    from app.models import ArchiMateElement
    from app.models.business_model import BusinessModelCanvas
    from app.models.user import User
    from app.modules.business_model_canvas import model_service
    from app.modules.intelligence.services.query_catalogue import CANVAS_BOX_QUESTIONS

    with app.app_context():
        org_id = User.query.filter_by(email=email).one().organization_id
        canvas = None
        for _ in range(40):
            db.session.rollback()
            canvas = BusinessModelCanvas.query.filter_by(organization_id=org_id).order_by(BusinessModelCanvas.id).first()
            if canvas is not None:
                break
            time.sleep(3)
        assert canvas is not None, "the sign-up canvases have not loaded"
        box = next(b for b in model_service.element_boxes(canvas) if b not in CANVAS_BOX_QUESTIONS)
        name = "Journey entry %d" % org_id
        element = ArchiMateElement(
            name=name, type=model_service.zones(canvas)[box]["element_types"][0], layer="business", organization_id=org_id
        )
        db.session.add(element)
        db.session.flush()
        model_service.place(canvas, box, element)
        db.session.commit()
        return name


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
        # the sign-up canvases load in the background and start empty: no card asks about an empty page
        for _ in range(20):
            if any("signed up" in t for t in cards.all_inner_texts()):
                break
            page.wait_for_timeout(3000)
            page.reload(wait_until="networkidle")
            expect(cards.first).to_be_visible(timeout=PAGE_TIMEOUT)
        texts = cards.all_inner_texts()
        assert any("signed up" in t for t in texts), texts
        expect(cards).to_have_count(len(cards_a_newcomer_is_shown(app, email)))
        assert not any("Canvas" in t for t in texts), texts
        assert not any("Company profile" in t for t in texts), texts
        assert "then general questions" not in page.locator("#portfolio-briefing").inner_text()
        # Connect GitHub is offered once, as the button, and lands on the connector page
        assert page.get_by_test_id("chat-first-connect").count() == 1
        assert not any("Connect GitHub" in t for t in texts), texts
        _shot(page, "chat-fresh", name)
        page.get_by_test_id("chat-first-connect").click()
        page.wait_for_url("**/admin/connectors/github", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="GitHub").first).to_be_visible()

        # once a canvas holds an entry it is offered as a card
        entry = _record_on_a_canvas(app, email)
        page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        expect(cards.first).to_be_visible(timeout=PAGE_TIMEOUT)
        canvas_card = cards.filter(has_text="Canvas")
        expect(canvas_card.first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(cards).to_have_count(len(cards_a_newcomer_is_shown(app, email)))
        assert len(cards_a_newcomer_is_shown(app, email)) == 2
        _shot(page, "chat-with-canvas", name)

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
        links = page.locator("#messages-container a[href]").filter(has_text="Your sign-up answers")
        links.first.wait_for(state="attached", timeout=PAGE_TIMEOUT)
        assert links.count() >= 1
        for href in {links.nth(i).get_attribute("href") for i in range(links.count())}:
            page.goto(live_server + href, wait_until="networkidle", timeout=PAGE_TIMEOUT)
            expect(page.locator("main")).not_to_be_empty()
            body = page.locator("main").inner_text()
            assert "Startup" in body, body[:300]
            assert "404" not in page.title()

        # the canvas card's question is answered from what is on the canvas, and links to the canvas
        page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        if name == "390":  # the history panel is a slide-over on a phone
            page.get_by_role("button", name="Show chat history").click()
        page.get_by_role("button", name="Start a new chat").click()
        expect(canvas_card.first).to_be_visible(timeout=PAGE_TIMEOUT)
        canvas_card.first.click()
        try:
            page.wait_for_function(
                "(entry) => document.querySelector('#messages-container').innerText.includes(entry)", arg=entry, timeout=60000
            )
        except Exception:
            raise AssertionError("the canvas answer does not quote an entry: " + page.locator("#messages-container").inner_text()[-800:])
        _shot(page, "canvas-answer", name)
        canvas_link = page.locator("#messages-container a[href*='/business-model/']").first
        canvas_link.wait_for(state="attached", timeout=PAGE_TIMEOUT)
        page.goto(live_server + canvas_link.get_attribute("href"), wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert entry in page.locator("main").inner_text()
    finally:
        context.close()

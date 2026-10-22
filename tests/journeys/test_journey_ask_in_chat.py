"""Journey: one place to ask. A signed-in person opens the AI chat, clicks a starter and gets
Ask's cited answer card; the old Ask address with an element and a question lands in the chat
showing that same answer.

The person's goal: find out what breaks if a system they own fails, in plain words, with the
sources and dates they need to trust the answer, from one place.

The real pages are rendered for a signed-in user and driven in a browser. Every request the
page makes is answered by the Flask app itself (static files from disk), so the answer card
is the one the server draws from the stored answer. No model is configured, so a starter
that reached a model would show a refusal instead of a card.
"""
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey

STATIC = Path(__file__).resolve().parents[2] / "app" / "static"
BASE = "http://app.test"


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        chromium = p.chromium.launch()
        yield chromium
        chromium.close()


def _seed_estate(db, org_id):
    """A payments system that two services depend on, one of them through the other."""
    from app.models import ArchiMateElement, ArchiMateRelationship

    def element(name):
        row = ArchiMateElement(name=name, type="ApplicationComponent", layer="application",
                               organization_id=org_id)
        db.session.add(row)
        db.session.flush()
        return row

    root = element("Ledger Core")
    mid = element("Invoice Service")
    leaf = element("Customer Portal")
    for source, target in ((root, mid), (mid, leaf)):
        db.session.add(ArchiMateRelationship(source_id=source.id, target_id=target.id,
                                             type="Serving", organization_id=org_id))
    db.session.commit()
    return root.id


@pytest.fixture
def person(app, client):
    from app import db

    with app.app_context():
        org_id = make_org(db, "AskInChat")
        user_id = make_user(db, org_id, "asker", "enterprise_architect", role_name="Architect")
        root_id = _seed_estate(db, org_id)
    login(client, user_id)
    return {"element_id": root_id, "element_name": "Ledger Core"}


def _page(browser, client):
    """A browser page whose every request is answered by the Flask app."""
    pg = browser.new_page(viewport={"width": 1440, "height": 900})

    def handle(route):
        request = route.request
        url = urlparse(request.url)
        if url.path.startswith("/static/"):
            f = STATIC / url.path[len("/static/"):]
            return route.fulfill(path=str(f)) if f.is_file() else route.fulfill(status=404, body="")
        target = url.path + ("?" + url.query if url.query else "")
        if request.method == "GET":
            r = client.get(target, follow_redirects=False)
        else:
            r = client.post(target, data=request.post_data_buffer,
                            content_type=request.headers.get("content-type", "application/json"))
        headers = {k: v for k, v in r.headers.items() if k.lower() in ("location", "content-type")}
        return route.fulfill(status=r.status_code, headers=headers, body=r.get_data())

    pg.route(BASE + "/**", handle)
    return pg


def _card(pg):
    pg.wait_for_selector("[data-answer-card]", timeout=20000)
    card = pg.locator("[data-answer-card]").first
    pg.wait_for_function(
        "() => { const c = document.querySelector('[data-answer-card]');"
        " return c && /as of/.test(c.innerText); }", timeout=20000)
    return card


def _rows(card):
    """The names the card lists as affected, in order."""
    rows = card.locator("[data-cascade] li[data-kind]")
    return [(rows.nth(i).get_attribute("data-depth"), rows.nth(i).inner_text().strip().splitlines()[0])
            for i in range(rows.count())]


def _assert_a_cited_answer(card, name):
    text = card.inner_text()
    # The answer: what depends on the chosen system, by name.
    assert "Invoice Service" in text, "the card does not name what breaks: %r" % text
    assert "Customer Portal" in text, "the card does not name the indirect dependent: %r" % text
    # Evidence: where it came from, when, and whether it was recorded or worked out.
    assert re.search(r"as of \S+", text), "the card shows no date: %r" % text
    assert "relationships recorded in the model" in text, "the card names no source: %r" % text
    assert re.search(r"[Ww]orked out|[Rr]ecorded", text), "the card does not say recorded or worked out: %r" % text
    assert card.get_attribute("data-state") == "answered"
    assert re.fullmatch(r"[0-9a-f]{32}", card.get_attribute("data-answer-address") or ""), \
        "the answer has no permanent address"
    assert int(card.get_attribute("data-citation-count")) >= 2, "the answer cites fewer sources than rows"
    return card.get_attribute("data-answer-address")


def test_a_starter_in_the_chat_answers_with_a_cited_card_and_the_ask_address_lands_on_that_answer(
        app, browser, client, person):
    pg = _page(browser, client)
    try:
        pg.goto(BASE + "/ai-chat")
        pg.wait_for_selector("[data-starters='empty-state'] [data-starter='impact']", timeout=20000)

        # Click the starter, name the system, pick the match.
        start = pg.locator("[data-starters='empty-state']")
        start.locator("[data-starter='impact']").click()
        picker = start.locator("input[role=combobox]")
        picker.fill("Ledger")
        pg.get_by_role("option", name=person["element_name"]).first.click()

        card = _card(pg)
        address = _assert_a_cited_answer(card, person["element_name"])
        assert "Ledger Core" in pg.locator("#messages-container").inner_text(), \
            "the question put to the chat does not name the system"

        # The old Ask address, with an element and a question, lands in the chat on that answer.
        # The browser is sent where the server says; the page harness does not replay a
        # redirect hop, so the server's answer is read and followed here.
        old_address = "/intelligence/ask?element=%d&question=impact" % person["element_id"]
        sent = client.get(old_address, follow_redirects=False)
        assert sent.status_code == 302, "the Ask address no longer redirects"
        where = urlparse(sent.headers["Location"])
        assert where.path.rstrip("/") == "/ai-chat", "the Ask address does not open the chat: %s" % where.path
        pg.goto(BASE + where.path + ("?" + where.query if where.query else ""))
        assert "/intelligence/ask" not in pg.url
        card_again = _card(pg)
        address_again = _assert_a_cited_answer(card_again, person["element_name"])
        # Each ask is stored at its own permanent address; what must agree is the answer itself.
        assert address_again != "", "the Ask address showed an answer with no permanent address"
        assert _rows(card_again) == _rows(card), "the Ask address showed a different answer than the chat starter"
        assert card_again.get_attribute("data-citation-count") == card.get_attribute("data-citation-count")
    finally:
        pg.close()

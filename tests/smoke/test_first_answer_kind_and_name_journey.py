"""A typed question about a card's element gives the card's figure, and a follow-up keeps the question's kind.

The person is the administrator of an organisation whose records hold two elements named orders-api (the
older with no link, the newer with three) and two named Billing (one link and five). They type the card's
words in lower case and with a space for the hyphen and read the card's figure back. They ask which risks
reach Billing, press "Work them out now" and still read a risk answer; an impact answer stays an impact
answer. In a second organisation one of Payments' three neighbours is deleted: the overview, the card and
the answer all say two, and no row of the answer is nameless or leads to a missing page. An id outside the
database's range is ignored like any invalid id. At 390 wide the whole note under the questions of an
organisation with applications and no links is showing without scrolling.
"""

import re

import pytest
from playwright.sync_api import expect

from tests.smoke.chat_ask import ask, open_chat, sign_in
from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org
from tests.smoke.test_connect_github_to_cited_answer_journey import SKIP_WELCOME, remove_organisations
from tests.smoke.test_first_questions_few_links_journey import _cards, shoot
from tests.smoke.test_first_questions_same_name_journey import _seed

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def world(live_server, app):
    one = create_fresh_org("solution_architect", org_admin=True)
    two = create_fresh_org("solution_architect", org_admin=True)
    three = create_fresh_org("solution_architect", org_admin=True)
    peers = lambda prefix, count: [("%s%d" % (prefix, i), "%s Peer %d" % (prefix.upper(), i)) for i in range(count)]  # noqa: E731
    orders, billing = peers("o", 3), peers("b", 5)
    ids_one = _seed(
        app,
        one["org_id"],
        applications=[("orders_old", "orders-api"), ("orders_new", "orders-api"), ("billing_few", "Billing"), ("billing_many", "Billing")],
        peers=orders + billing + [("lone", "Lone Peer")],
        links=[("orders_new", key) for key, _ in orders] + [("billing_many", key) for key, _ in billing] + [("billing_few", "lone")],
        own=("billing_many",),
    )
    pay, other = peers("p", 3), peers("q", 1)
    ids_two = _seed(
        app,
        two["org_id"],
        applications=[("payments", "Payments"), ("ledger", "Ledger")],
        peers=pay + other,
        links=[("payments", key) for key, _ in pay] + [("ledger", "q0")],
    )
    _seed(app, three["org_id"], applications=[("mainframe", "Mainframe"), ("treasury", "Treasury")], peers=[], links=[])
    from app import db
    from app.models import ArchiMateElement
    from app.models.organization import Organization
    from datetime import datetime, timezone

    with app.app_context():
        org = db.session.get(Organization, three["org_id"])
        settings = dict(org.settings or {})
        settings["company_context"] = {"kind": "enterprise"}
        org.settings = settings
        gone = db.session.get(ArchiMateElement, ids_two["p0"])
        gone.deleted_at = datetime.now(timezone.utc)
        db.session.commit()
        db.session.remove()
    yield {"base": str(live_server), "one": one, "two": two, "three": three, "ids_one": ids_one, "ids_two": ids_two}
    remove_organisations(app, [one["org_id"], two["org_id"], three["org_id"]])


def _page(browser, world, which, width=1440, height=900):
    context = browser.new_context(viewport={"width": width, "height": height})
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_in(page, world["base"], world[which]["emails"]["solution_architect"])
    return context, page


def _text(answer):
    return " ".join(answer.inner_text().split())


def test_1_typing_a_cards_words_in_lower_case_or_with_a_space_gives_the_cards_figure(browser, world):
    context, page = _page(browser, world, "one")
    try:
        open_chat(page, world["base"])
        card = [c for c in _cards(page) if c.get_attribute("data-question") == "What breaks if orders-api fails?"]
        assert len(card) == 1
        assert "3 relationships recorded" in " ".join(card[0].inner_text().split())
        for typed in ("What breaks if orders-api fails?", "what breaks if orders-api fails?", "what breaks if orders api fails?", "What breaks if Orders_API fails?"):
            answer = ask(page, world["base"], typed)
            text = _text(answer)
            assert "0 in your records" not in text and "Nothing is recorded" not in text, (typed, text)
            assert "3 in your records" in text or "orders-api has 3 relationships recorded" in text, (typed, text)
        shoot(page, "typed-lower-case")
    finally:
        context.close()


def test_2_a_follow_up_keeps_the_kind_of_the_question(browser, world):
    context, page = _page(browser, world, "one")
    try:
        for question, kind in (("Which risks reach Billing?", "risk_propagation"), ("What breaks if Billing fails?", "impact")):
            answer = ask(page, world["base"], question)
            cards = answer.locator("[data-answer-cards]").first
            assert cards.get_attribute("data-answer-kind") == kind, (question, _text(answer))
            if kind == "risk_propagation":
                assert cards.get_attribute("data-element-id") is None
                shoot(page, "risk-answer")
            button = answer.locator("[data-recompute-button]")
            expect(button.first).to_be_visible(timeout=PAGE_TIMEOUT)
            button.first.click()
            expect(page.locator("[data-recompute-line]")).to_have_count(0, timeout=PAGE_TIMEOUT)
            after = page.get_by_test_id("estate-answer").last
            holder = after.locator("[data-answer-cards]").first
            assert holder.get_attribute("data-answer-kind") == kind, (question, _text(after))
            if kind == "risk_propagation":
                assert _text(after).startswith("Risks that reach Billing"), _text(after)
                shoot(page, "risk-answer-after-work-out")
            else:
                assert _text(after).startswith("What breaks if Billing fails"), _text(after)
    finally:
        context.close()


def test_3_a_deleted_neighbour_is_counted_and_listed_nowhere(browser, world):
    context, page = _page(browser, world, "two")
    try:
        page.goto(world["base"] + "/dashboard/", wait_until="domcontentloaded")
        panel = page.get_by_test_id("card-relationships")
        expect(panel).to_be_visible()
        top = re.search(r"(.+?) is the most connected, with (\d+) relationships", " ".join(panel.inner_text().split()))
        assert top and top.group(1).strip().endswith("Payments") and int(top.group(2)) == 2, panel.inner_text()
        open_chat(page, world["base"])
        card = [c for c in _cards(page) if c.get_attribute("data-question") == "What breaks if Payments fails?"]
        assert len(card) == 1 and "2 relationships recorded" in " ".join(card[0].inner_text().split())
        card[0].click()
        answer = page.get_by_test_id("estate-answer")
        expect(answer).to_have_count(1, timeout=PAGE_TIMEOUT)
        text = _text(answer)
        assert "Payments has 2 relationships recorded" in text, text
        assert "P Peer 0" not in text, text
        assert answer.get_by_role("link", name="Open relationship record").count() == 2, text
        shoot(page, "deleted-neighbour")
        links = answer.locator("a[href]")
        hrefs = [h for h in (links.nth(i).get_attribute("href") for i in range(links.count())) if h and h.startswith("/")]
        assert hrefs, text
        for href in hrefs:
            reply = page.request.get(world["base"] + href)
            assert reply.status == 200 and reply.text().strip(), (href, reply.status)
    finally:
        context.close()


def test_4_an_id_outside_the_database_range_is_ignored(browser, world):
    context, page = _page(browser, world, "one")
    try:
        open_chat(page, world["base"])
        for bad in (2**31, 2**40):
            reply = page.evaluate(
                "(id) => ArchieChat.transport.askEstate('What breaks if orders-api fails?', {remember: false, elementId: id})", bad
            )
            assert reply.get("found") and "orders-api" in (reply.get("html") or ""), reply
    finally:
        context.close()


def test_5_at_390_the_whole_linkless_note_shows_without_scrolling_under_the_notice(browser, world):
    context, page = _page(browser, world, "three", width=390, height=844)
    try:
        page.evaluate("() => document.documentElement.classList.add('dark')")
        open_chat(page, world["base"])
        _cards(page)
        page.evaluate("() => document.documentElement.classList.add('dark')")
        note = page.locator("[data-linkless-note]")
        expect(note).to_be_visible(timeout=PAGE_TIMEOUT)
        notice = page.get_by_test_id("model-unavailable-notice")
        expect(notice).to_be_visible(timeout=PAGE_TIMEOUT)
        seen = page.evaluate(
            """() => {
                const box = document.getElementById('messages-container');
                box.scrollTop = 0;
                const note = document.querySelector('[data-linkless-note]').getBoundingClientRect();
                const notice = document.querySelector('[data-testid="model-unavailable-notice"]').getBoundingClientRect();
                return {noteBottom: note.bottom, boxBottom: box.getBoundingClientRect().bottom, noticeTop: notice.top};
            }"""
        )
        assert seen["noteBottom"] <= seen["boxBottom"] + 1 and seen["noteBottom"] <= seen["noticeTop"] + 1, seen
        shoot(page, "linkless-note-390")
    finally:
        context.close()

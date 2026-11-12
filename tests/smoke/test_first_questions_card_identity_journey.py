"""A first-question card opens the answer about the element it names, whatever that element is called.

The person is the administrator of an organisation whose records hold two elements named orders-api
(the older with no link), a Data Lineage Hub, a lower-case payments and a lower-case web-api. They
click every card: each answer is about the card's element and gives the card's relationship figure.
A second organisation holds two elements named Billing (the application's own record with one link,
a second copy with five) and two named Stock: the overview, the Billing card and the Billing answer
give one figure, and typing a card's exact words answers the element the click does. An element id
from another organisation is ignored. Organisations of the other kind see questions that fit them,
and at 390 wide no card's text sits under the notice that no model is set up.
"""

import os
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

SHOTS = os.environ.get("FIRST_QUESTIONS_SHOTS")


def _peers(count, prefix):
    return [("%s%d" % (prefix, i), "%s Peer %d" % (prefix.upper(), i)) for i in range(count)]


@pytest.fixture(scope="module")
def world(live_server, app):
    one = create_fresh_org("solution_architect", org_admin=True)
    two = create_fresh_org("solution_architect", org_admin=True)
    three = create_fresh_org("solution_architect", org_admin=True)
    four = create_fresh_org("solution_architect", org_admin=True)  # nothing recorded at all
    orders = _peers(3, "o")
    lineage = _peers(2, "l")
    pay = _peers(2, "p")
    web = _peers(1, "w")
    ids_one = _seed(
        app,
        one["org_id"],
        applications=[
            ("orders_old", "orders-api"),
            ("orders_new", "orders-api"),
            ("lineage", "Data Lineage Hub"),
            ("payments", "payments"),
            ("web", "web-api"),
        ],
        peers=orders + lineage + pay + web,
        links=(
            [("orders_new", key) for key, _ in orders]
            + [("lineage", key) for key, _ in lineage]
            + [("payments", key) for key, _ in pay]
            + [("web", key) for key, _ in web]
        ),
    )
    billing = _peers(5, "b")
    ids_two = _seed(
        app,
        two["org_id"],
        applications=[
            ("billing_own", "Billing"),
            ("billing_copy", "Billing"),
            ("stock_old", "Stock"),
            ("stock_new", "Stock"),
        ],
        peers=billing + [("s0", "Stock Peer")],
        links=[("billing_own", "b0")] + [("billing_copy", key) for key, _ in billing] + [("stock_new", "s0")],
        own=("billing_own",),
    )
    # An organisation of the enterprise kind with applications and no links.
    _seed(app, three["org_id"], applications=[("mainframe", "Mainframe"), ("treasury", "Treasury")], peers=[], links=[])
    from app import db
    from app.models.organization import Organization

    with app.app_context():
        org = db.session.get(Organization, three["org_id"])
        settings = dict(org.settings or {})
        settings["company_context"] = {"kind": "enterprise"}
        org.settings = settings
        db.session.commit()
        db.session.remove()
    yield {"base": str(live_server), "one": one, "two": two, "three": three, "four": four, "ids_one": ids_one, "ids_two": ids_two}
    remove_organisations(app, [one["org_id"], two["org_id"], three["org_id"], four["org_id"]])


def _page(browser, world, which, width=1440, height=900):
    context = browser.new_context(viewport={"width": width, "height": height})
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_in(page, world["base"], world[which]["emails"]["solution_architect"])
    return context, page


def _fresh_chat(page, base):
    open_chat(page, base)
    new_chat = page.get_by_role("button", name="Start a new chat")
    if new_chat.count() and new_chat.first.is_visible():
        new_chat.first.click()


def _figure(card):
    return int(re.search(r"(\d+) relationships? recorded", card.inner_text()).group(1))


def _sentence(answer):
    return " ".join(answer.locator("[data-recorded-links]").inner_text().split())


def _card(page, name):
    found = [c for c in _cards(page) if c.get_attribute("data-question") == "What breaks if %s fails?" % name]
    assert len(found) == 1, (name, [c.get_attribute("data-question") for c in _cards(page)])
    return found[0]


def test_1_every_card_opens_the_answer_about_its_element_with_its_figure(browser, world):
    context, page = _page(browser, world, "one")
    try:
        expected = {"orders-api": 3, "Data Lineage Hub": 2, "payments": 2, "web-api": 1}
        for name, count in expected.items():
            _fresh_chat(page, world["base"])
            card = _card(page, name)
            assert _figure(card) == count, (name, card.inner_text())
            card.click()
            answer = page.get_by_test_id("estate-answer")
            expect(answer).to_have_count(1, timeout=PAGE_TIMEOUT)
            text = answer.inner_text()
            assert "Nothing is recorded as connected to this yet" not in text, (name, text)
            assert "0 in your records" not in text, (name, text)
            sentence = _sentence(answer)
            assert sentence.startswith("%s has %d relationship" % (name, count)), (name, sentence)
            if name == "Data Lineage Hub":
                shoot(page, "identity-answer-lineage")
            if name == "orders-api":
                shoot(page, "identity-answer-orders-api")
    finally:
        context.close()


def test_2_the_overview_the_card_and_the_answer_give_billing_one_figure(browser, world):
    context, page = _page(browser, world, "two")
    try:
        page.goto(world["base"] + "/dashboard/", wait_until="domcontentloaded")
        panel = page.get_by_test_id("card-relationships")
        expect(panel).to_be_visible()
        top = re.search(r"(.+?) is the most connected, with (\d+) relationships", " ".join(panel.inner_text().split()))
        assert top, panel.inner_text()
        assert top.group(1).strip().endswith("Billing") and int(top.group(2)) == 5, top.group(0)
        open_chat(page, world["base"])
        card = _card(page, "Billing")
        assert _figure(card) == 5
        shoot(page, "identity-offered-billing")
        card.click()
        answer = page.get_by_test_id("estate-answer")
        expect(answer).to_have_count(1, timeout=PAGE_TIMEOUT)
        assert _sentence(answer).startswith("Billing has 5 relationships recorded"), _sentence(answer)
        shoot(page, "identity-answer-billing")
    finally:
        context.close()


def test_3_typing_a_cards_exact_words_answers_the_element_the_click_does(browser, world):
    context, page = _page(browser, world, "two")
    try:
        open_chat(page, world["base"])
        for name, count in (("Billing", 5), ("Stock", 1)):
            answer = ask(page, world["base"], "What breaks if %s fails?" % name)
            text = answer.inner_text()
            assert "Nothing is recorded as connected to this yet" not in text, (name, text)
            assert _sentence(answer).startswith("%s has %d relationship" % (name, count)), (name, _sentence(answer))
    finally:
        context.close()


def test_4_an_element_id_from_another_organisation_is_ignored(browser, world):
    context, page = _page(browser, world, "one")
    try:
        open_chat(page, world["base"])
        theirs = world["ids_two"]["billing_copy"]
        reply = page.evaluate(
            "(id) => ArchieChat.transport.askEstate('What breaks if web-api fails?', {remember: false, elementId: id})",
            theirs,
        )
        html = reply.get("html") or ""
        assert "web-api" in html and "Billing" not in html and "B Peer" not in html, html[:300]
    finally:
        context.close()


def test_5_an_enterprise_organisation_with_no_links_is_not_asked_startup_questions(browser, world):
    context, page = _page(browser, world, "three")
    try:
        open_chat(page, world["base"])
        host = page.locator("#portfolio-briefing")
        expect(host).not_to_have_attribute("aria-busy", "true", timeout=PAGE_TIMEOUT)
        text = host.inner_text()
        assert len(_cards(page)) == 4
        assert "startup" not in text.lower(), text
        assert "Your applications are recorded, but not how they connect" in text, text
    finally:
        context.close()


def _no_card_sits_under_the_notice(page):
    host = page.locator("#messages-container")
    notice = page.get_by_test_id("model-unavailable-notice")
    expect(notice).to_be_visible(timeout=PAGE_TIMEOUT)
    areas = page.evaluate(
        """() => {
            const box = document.getElementById('messages-container');
            const area = box.getBoundingClientRect();
            const notice = document.querySelector('[data-testid="model-unavailable-notice"]').getBoundingClientRect();
            box.scrollTop = box.scrollHeight;
            return {areaBottom: area.bottom, noticeTop: notice.top,
                    cards: Array.from(document.querySelectorAll('[data-testid="first-question-suggestion"], #portfolio-briefing p'))
                        .map((c) => c.getBoundingClientRect().bottom)};
        }"""
    )
    assert areas["noticeTop"] >= areas["areaBottom"] - 1, areas
    assert host.count() == 1
    return areas


def test_6_at_390_in_the_dark_every_card_is_whole_above_the_no_model_notice(browser, world):
    for which in ("three", "four", "one"):
        context, page = _page(browser, world, which, width=390, height=844)
        try:
            page.evaluate("() => document.documentElement.classList.add('dark')")
            open_chat(page, world["base"])
            _cards(page)
            page.evaluate("() => document.documentElement.classList.add('dark')")
            areas = _no_card_sits_under_the_notice(page)
            assert len(areas["cards"]) >= 4, areas
            # After scrolling to the end every card ends above the notice, and without scrolling the four fit.
            assert max(areas["cards"]) <= areas["noticeTop"] + 1, areas
            if which == "one":
                shoot(page, "identity-390-notice-%s" % which)
                continue
            first = page.evaluate(
                """() => { const box = document.getElementById('messages-container'); box.scrollTop = 0;
                    return Array.from(document.querySelectorAll('[data-testid="first-question-suggestion"]'))
                      .map((c) => c.getBoundingClientRect().bottom - box.getBoundingClientRect().bottom); }"""
            )
            assert max(first) <= 1, first
            shoot(page, "identity-390-notice-%s" % which)
        finally:
            context.close()

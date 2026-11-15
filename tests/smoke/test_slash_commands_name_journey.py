"""A person types a recorded name after /dependencies or /portfolio-check and reads the card's figure.

The person is the administrator of an organisation whose records hold Billing (the application's own
record) with three live neighbours and one deleted one, beside a second organisation whose Billing has
one live neighbour and one deleted one. They type /dependencies Billing and read the three live
neighbours, type /portfolio-check Billing and read the relationship count the question card and the
typed question give, and type a beginning of the name ("Billin") and click the closest recorded name
offered. A name nothing resembles says what to type. In an organisation with applications and no links,
which reads as a startup, the note under the questions and the notice above the composer stack at 390
and 1440 wide with neither covering the other.
"""

import re
from datetime import datetime, timezone

import pytest
from playwright.sync_api import expect

from tests.smoke.chat_ask import ask, open_chat, sign_in
from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org
from tests.smoke.test_connect_github_to_cited_answer_journey import SKIP_WELCOME, remove_organisations
from tests.smoke.test_first_questions_few_links_journey import _cards, shoot
from tests.smoke.test_first_questions_same_name_journey import _seed

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _peers(prefix, count):
    return [("%s%d" % (prefix, i), "%s Peer %d" % (prefix.upper(), i)) for i in range(count)]


@pytest.fixture(scope="module")
def world(live_server, app):
    from app import db
    from app.models import ArchiMateElement

    one = create_fresh_org("solution_architect", org_admin=True)
    two = create_fresh_org("solution_architect", org_admin=True)
    three = create_fresh_org("solution_architect", org_admin=True)
    live_one, gone_one = _peers("a", 3), _peers("x", 1)
    ids_one = _seed(
        app,
        one["org_id"],
        applications=[("billing", "Billing"), ("gateway", "Billing Gateway")],
        peers=live_one + gone_one,
        links=[("billing", key) for key, _ in live_one + gone_one],
        own=("billing",),
    )
    live_two, gone_two = _peers("b", 1), _peers("y", 1)
    ids_two = _seed(
        app,
        two["org_id"],
        applications=[("billing", "Billing")],
        peers=live_two + gone_two,
        links=[("billing", key) for key, _ in live_two + gone_two],
        own=("billing",),
    )
    from app.models.models import APISettings
    from tests.smoke.ai_protocol_stub import MODEL, TOKEN

    with app.app_context():
        # commands are run by the chat endpoint that needs a model configured; the model is never asked
        for org in (one, two):
            db.session.add(
                APISettings(provider="openai", key_label="slash-journey", api_key=TOKEN, enabled=True, default_model=MODEL, organization_id=org["org_id"])
            )
        db.session.commit()
        db.session.remove()
    _seed(app, three["org_id"], applications=[("mainframe", "Mainframe"), ("treasury", "Treasury")], peers=[], links=[])
    with app.app_context():
        for ids, key in ((ids_one, "x0"), (ids_two, "y0")):
            db.session.get(ArchiMateElement, ids[key]).deleted_at = datetime.now(timezone.utc)
        db.session.commit()
        db.session.remove()
    yield {"base": str(live_server), "one": one, "two": two, "three": three}
    remove_organisations(app, [one["org_id"], two["org_id"], three["org_id"]])


def _page(browser, world, which, width=1440, height=900):
    context = browser.new_context(viewport={"width": width, "height": height})
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_in(page, world["base"], world[which]["emails"]["solution_architect"])
    return context, page


def _type_command(page, command):
    """Send *command* and return the text of the newest assistant message that answers it."""
    messages = page.locator("#messages-container .message-content")
    before = messages.count()
    box = page.locator("#user-input")
    box.fill(command)
    box.press("Enter")
    expect(messages).to_have_count(before + 1, timeout=PAGE_TIMEOUT)
    last = messages.last
    expect(last).not_to_have_text("", timeout=PAGE_TIMEOUT)
    return last


def _words(locator):
    return " ".join(locator.inner_text().split())


def test_1_dependencies_lists_the_live_neighbours_of_a_recorded_name(browser, world):
    for which, prefix, live in (("one", "A", 3), ("two", "B", 1)):
        context, page = _page(browser, world, which)
        try:
            open_chat(page, world["base"])
            answer = _type_command(page, "/dependencies Billing")
            expect(answer).to_contain_text("Downstream dependents of Billing", timeout=PAGE_TIMEOUT)
            text = _words(answer)
            assert "Element not found" not in text and "Nothing recorded" not in text, text
            assert len(re.findall(r"\b%s Peer \d\b" % prefix, text)) == live, (which, text)
            assert "X Peer" not in text and "Y Peer" not in text, text
            assert "%d total downstream elements" % live in text, text
            if which == "one":
                shoot(page, "dependencies-billing")
        finally:
            context.close()


def test_2_portfolio_check_gives_the_figure_the_card_and_the_typed_answer_give(browser, world):
    for which, live in (("one", 3), ("two", 1)):
        context, page = _page(browser, world, which)
        try:
            open_chat(page, world["base"])
            card = [c for c in _cards(page) if c.get_attribute("data-question") == "What breaks if Billing fails?"]
            assert len(card) == 1, which
            card_text = " ".join(card[0].inner_text().split())
            assert re.search(r"\b%d relationships? recorded" % live, card_text), card_text
            answer = _type_command(page, "/portfolio-check Billing")
            expect(answer).to_contain_text("Portfolio Impact Check: Billing", timeout=PAGE_TIMEOUT)
            text = _words(answer)
            assert "Relationships: %d" % live in text and "Outgoing Relationships: %d" % live in text, (which, text)
            assert "X Peer" not in text and "Y Peer" not in text, text
            typed = ask(page, world["base"], "What breaks if Billing fails?", fresh=False)
            assert re.search(r"\b%d (relationships? recorded|in your records)" % live, _words(typed)), _words(typed)
            if which == "one":
                shoot(page, "portfolio-check-billing")
        finally:
            context.close()


def test_3_a_beginning_of_a_name_offers_the_closest_recorded_names_and_a_click_runs_one(browser, world):
    context, page = _page(browser, world, "one")
    try:
        open_chat(page, world["base"])
        answer = _type_command(page, "/dependencies Billin")
        expect(answer).to_contain_text("Nothing recorded is called", timeout=PAGE_TIMEOUT)
        text = _words(answer)
        assert "Type /dependencies and one of these names, or click one." in text, text
        offered = answer.locator("[data-run-command]")
        assert sorted(offered.all_inner_texts()) == ["/dependencies Billing", "/dependencies Billing Gateway"], text
        shoot(page, "closest-names")
        offered.filter(has_text=re.compile(r"^/dependencies Billing$")).click()
        result = page.locator("#messages-container .message-content").last
        expect(result).to_contain_text("Downstream dependents of Billing", timeout=PAGE_TIMEOUT)
        assert len(re.findall(r"\bA Peer \d\b", _words(result))) == 3, _words(result)
        nothing = _type_command(page, "/portfolio-check Zephyr Quillborne")
        expect(nothing).to_contain_text("Nothing similar is recorded", timeout=PAGE_TIMEOUT)
        assert "Type /portfolio-check and the name of a recorded element." in _words(nothing)
    finally:
        context.close()


def test_4_another_organisations_names_are_never_offered(browser, world):
    context, page = _page(browser, world, "two")
    try:
        open_chat(page, world["base"])
        answer = _type_command(page, "/portfolio-check Billin")
        expect(answer).to_contain_text("Nothing recorded is called", timeout=PAGE_TIMEOUT)
        assert answer.locator("[data-run-command]").all_inner_texts() == ["/portfolio-check Billing"]
        assert "Gateway" not in _words(answer)
    finally:
        context.close()


@pytest.mark.parametrize("width,height,dark", [(390, 844, True), (1440, 900, False)])
def test_5_the_linkless_note_and_the_notice_stack_without_covering_each_other(browser, world, width, height, dark):
    context, page = _page(browser, world, "three", width=width, height=height)
    try:
        if dark:
            page.evaluate("() => document.documentElement.classList.add('dark')")
        open_chat(page, world["base"])
        _cards(page)
        if dark:
            page.evaluate("() => document.documentElement.classList.add('dark')")
        note = page.locator("[data-linkless-note]")
        notice = page.get_by_test_id("model-unavailable-notice")
        expect(note).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(notice).to_be_visible(timeout=PAGE_TIMEOUT)
        seen = page.evaluate(
            """() => {
                const box = document.getElementById('messages-container');
                box.scrollTop = 0;
                const rect = (el) => el.getBoundingClientRect();
                const note = rect(document.querySelector('[data-linkless-note]'));
                const notice = rect(document.querySelector('[data-testid="model-unavailable-notice"]'));
                return {noteTop: note.top, noteBottom: note.bottom, boxTop: rect(box).top, boxBottom: rect(box).bottom,
                        noticeTop: notice.top, noticeBottom: notice.bottom};
            }"""
        )
        assert seen["noteTop"] >= seen["boxTop"] - 1 and seen["noteBottom"] <= seen["boxBottom"] + 1, seen
        assert seen["noteBottom"] <= seen["noticeTop"] + 1 or seen["noticeBottom"] <= seen["noteTop"] + 1, seen
        shoot(page, "linkless-note-and-notice")
    finally:
        context.close()

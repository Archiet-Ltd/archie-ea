"""An organisation with one recorded relationship is offered only questions that get answers.

The person is the administrator of an organisation whose records hold four applications and exactly
one relationship between two of them. They open the chat and see the two linked applications as
questions, then general questions for the rest, never a question about an application with nothing
recorded. Each card's count of relationships is the figure on the overview and the figure in the
answer. The answer they saw is the one a reloaded conversation keeps. A second organisation with
applications and no relationship is offered nothing about its applications and told what to do next.
"""

import os
import re

import pytest
from playwright.sync_api import expect

from tests.smoke.chat_ask import open_chat, sign_in
from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org
from tests.smoke.test_connect_github_to_cited_answer_journey import SKIP_WELCOME, remove_organisations

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("FIRST_QUESTIONS_SHOTS")
LINKED = ("Payments Service", "Customer Portal")
UNLINKED = ("Auth Gateway", "Reporting Job")


def _seed(app, org_id, names, links):
    from app import db
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.models.application_portfolio import ApplicationComponent

    with app.app_context():
        ids = {}
        for name in names:
            # The application list the chat once filled its cards from holds each one too.
            db.session.add(ApplicationComponent(name=name, organization_id=org_id))
            element = ArchiMateElement(name=name, type="ApplicationComponent", layer="Application", organization_id=org_id)
            db.session.add(element)
            db.session.flush()
            ids[name] = element.id
        for source, target in links:
            db.session.add(
                ArchiMateRelationship(type="Serving", source_id=ids[source], target_id=ids[target], organization_id=org_id)
            )
        db.session.commit()
        db.session.remove()


@pytest.fixture(scope="module")
def world(live_server, app):
    one = create_fresh_org("solution_architect", org_admin=True)
    two = create_fresh_org("solution_architect", org_admin=True)
    _seed(app, one["org_id"], LINKED + UNLINKED, [("Payments Service", "Customer Portal")])
    _seed(app, two["org_id"], UNLINKED, [])
    yield {"base": str(live_server), "one": one, "two": two}
    remove_organisations(app, [one["org_id"], two["org_id"]])


def _page(browser, world, which, width=1440, height=900):
    context = browser.new_context(viewport={"width": width, "height": height})
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_in(page, world["base"], world[which]["emails"]["solution_architect"])
    return context, page


def shoot(page, name):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    for width, height, theme in ((1440, 900, "light"), (390, 844, "dark")):
        page.set_viewport_size({"width": width, "height": height})
        page.evaluate("(t) => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
        page.wait_for_timeout(500)  # let the colour transition finish
        page.screenshot(path=os.path.join(SHOTS, "%s-%d-%s.png" % (name, width, theme)), full_page=True)
    page.evaluate("() => document.documentElement.classList.remove('dark')")
    page.set_viewport_size({"width": 1440, "height": 900})


def _cards(page):
    host = page.locator("#portfolio-briefing")
    expect(host.get_by_test_id("first-question-suggestion").first).to_be_visible(timeout=PAGE_TIMEOUT)
    cards = host.get_by_test_id("first-question-suggestion")
    return [cards.nth(i) for i in range(cards.count())]


def test_1_two_linked_applications_are_offered_then_general_questions(browser, world):
    context, page = _page(browser, world, "one")
    try:
        open_chat(page, world["base"])
        cards = _cards(page)
        questions = [c.get_attribute("data-question") for c in cards]
        assert len(questions) == 4, questions
        about = [q for q in questions if q.startswith("What breaks if ")]
        assert sorted(about) == sorted("What breaks if %s fails?" % n for n in LINKED), questions
        assert not any(n in q for q in questions for n in UNLINKED), questions
        shoot(page, "few-links-offered")
    finally:
        context.close()


def test_2_each_linked_card_and_its_answer_give_the_overviews_relationship_figure(browser, world):
    context, page = _page(browser, world, "one")
    try:
        page.goto(world["base"] + "/dashboard/", wait_until="domcontentloaded")
        card = page.get_by_test_id("card-relationships")
        expect(card).to_be_visible()
        top = re.search(r"is the most connected, with (\d+) relationship", card.inner_text())
        assert top, card.inner_text()
        overview = int(top.group(1))
        assert overview == 1
        for name in LINKED:
            open_chat(page, world["base"])
            new_chat = page.get_by_role("button", name="Start a new chat")
            if new_chat.count() and new_chat.first.is_visible():
                new_chat.first.click()
            offered = [c for c in _cards(page) if c.get_attribute("data-question") == "What breaks if %s fails?" % name]
            assert len(offered) == 1, name
            on_card = int(re.search(r"(\d+) relationships? recorded", offered[0].inner_text()).group(1))
            offered[0].click()
            answer = page.get_by_test_id("estate-answer")
            expect(answer).to_have_count(1, timeout=PAGE_TIMEOUT)
            text = answer.inner_text()
            assert "Nothing is recorded as connected to this yet" not in text, name
            in_answer = int(re.search(r"has (\d+) relationships? recorded", text).group(1))
            assert on_card == in_answer == overview, (name, on_card, in_answer, overview)
            rows = answer.locator("[data-answer-row]")
            assert rows.count() == 1, name
            shoot(page, "few-links-answer-%s" % name.split()[0].lower())
            link = answer.get_by_test_id("answer-open")
            link.click()
            page.wait_for_url(re.compile(r"/answers?/"), timeout=PAGE_TIMEOUT)
            assert "payments service" in page.locator("body").inner_text().lower(), name
    finally:
        context.close()


def test_3_a_reload_keeps_the_answer_the_person_saw(browser, world):
    context, page = _page(browser, world, "one")
    try:
        open_chat(page, world["base"])
        offered = [c for c in _cards(page) if "Customer Portal" in c.get_attribute("data-question")]
        offered[0].click()
        answer = page.get_by_test_id("estate-answer")
        expect(answer).to_have_count(1, timeout=PAGE_TIMEOUT)
        seen = answer.inner_text()
        assert "connected to" in seen or "connection" in seen
        page.reload(wait_until="domcontentloaded")
        expect(page.locator("#messages-container")).to_contain_text("Answer from your records", timeout=PAGE_TIMEOUT)
        kept = page.locator("#messages-container").inner_text()
        assert "0 connections" not in kept, kept
        assert re.search(r"[1-9]\d* (other element|connection)", kept), kept
        link = page.locator("#messages-container").get_by_role("link", name="Open this answer").last
        link.click()
        page.wait_for_url(re.compile(r"/answers?/"), timeout=PAGE_TIMEOUT)
        assert "payments service" in page.locator("body").inner_text().lower()
    finally:
        context.close()


def test_4_an_organisation_with_no_link_is_offered_no_question_about_its_applications(browser, world):
    context, page = _page(browser, world, "two")
    try:
        open_chat(page, world["base"])
        host = page.locator("#portfolio-briefing")
        expect(host).not_to_have_attribute("aria-busy", "true", timeout=PAGE_TIMEOUT)
        text = host.inner_text()
        assert not any(n in text for n in UNLINKED + LINKED), text
        assert "General questions to start with" in text
        assert host.get_by_text("Connect GitHub").first.is_visible()
        assert len(_cards(page)) == 4
        shoot(page, "no-links")
        offered = page.request.get(world["base"] + "/ai-chat/first-questions").json()
        assert offered == {"questions": []}
    finally:
        context.close()


def test_5_another_organisation_never_sees_the_first_organisations_applications(browser, world):
    context, page = _page(browser, world, "two")
    try:
        names = page.request.get(world["base"] + "/ai-chat/first-questions").text()
        assert not any(n in names for n in LINKED)
    finally:
        context.close()

"""A first question opens the answer about the element it names, and says what its figures are made of.

The person is the administrator of an organisation whose records hold two elements named Stock (the
older with no link), two named Orders (one the application's own record, one a second copy), and a
Customer Portal with four relationships of which one leads from it. They click each card and read the
answer: it is about the element the card named, and the sentence under the figures gives the numbers
the stored links give. A second organisation holds elements with the same names and other links, and
never appears in the first's answers. An organisation with applications and no links is offered the
general questions and the two ways to record how its applications connect, and a failed read of the
first questions is a plain error whose Retry works.
"""

import os
import re

import pytest
from playwright.sync_api import expect

from tests.smoke.chat_ask import open_chat
from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org
from tests.smoke.test_connect_github_to_cited_answer_journey import remove_organisations
from tests.smoke.test_first_questions_few_links_journey import _cards, _page, shoot

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("FIRST_QUESTIONS_SHOTS")


def _seed(app, org_id, applications, peers, links, own=()):
    """Elements by key: *applications* are (key, name) application components, *peers* are (key, name)
    application services, *links* are (source key, target key). Applications named in *own* are the
    record the application list points at."""
    from app import db
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.models.application_portfolio import ApplicationComponent

    with app.app_context():
        ids = {}
        for kind, group in (("ApplicationComponent", applications), ("ApplicationService", peers)):
            for key, name in group:
                element = ArchiMateElement(name=name, type=kind, layer="Application", organization_id=org_id)
                db.session.add(element)
                db.session.flush()
                ids[key] = element.id
                if key in own:
                    db.session.add(ApplicationComponent(name=name, organization_id=org_id, archimate_element_id=element.id))
        for source, target in links:
            db.session.add(
                ArchiMateRelationship(type="Serving", source_id=ids[source], target_id=ids[target], organization_id=org_id)
            )
        db.session.commit()
        db.session.remove()
        return ids


@pytest.fixture(scope="module")
def world(live_server, app):
    one = create_fresh_org("solution_architect", org_admin=True)
    two = create_fresh_org("solution_architect", org_admin=True)
    three = create_fresh_org("solution_architect", org_admin=True)
    ids = _seed(
        app,
        one["org_id"],
        applications=[
            ("stock_old", "Stock"),
            ("stock_new", "Stock"),
            ("orders_copy", "Orders"),
            ("orders_own", "Orders"),
            ("portal", "Customer Portal"),
        ],
        peers=[
            ("carts", "Carts"),
            ("billing", "Billing Api"),
            ("auth", "Auth Hub"),
            ("ledger", "Ledger"),
            ("pay", "Payments Hub"),
        ],
        links=[
            ("stock_new", "carts"),
            ("orders_own", "carts"),
            ("orders_own", "billing"),
            ("orders_copy", "carts"),
            ("auth", "portal"),
            ("ledger", "portal"),
            ("pay", "portal"),
            ("portal", "billing"),
        ],
        own=("orders_own",),
    )
    # The same names in another organisation, with other links.
    _seed(
        app,
        two["org_id"],
        applications=[("stock", "Stock"), ("portal", "Customer Portal")],
        peers=[("theirs", "Org Two Peer"), ("theirs_too", "Org Two Other Peer")],
        links=[("stock", "theirs"), ("portal", "theirs"), ("portal", "theirs_too")],
    )
    _seed(app, three["org_id"], applications=[("mainframe", "Mainframe"), ("treasury", "Treasury")], peers=[], links=[])
    yield {"base": str(live_server), "one": one, "two": two, "three": three, "ids": ids}
    remove_organisations(app, [one["org_id"], two["org_id"], three["org_id"]])


def _card(page, name):
    found = [c for c in _cards(page) if c.get_attribute("data-question") == "What breaks if %s fails?" % name]
    assert len(found) == 1, (name, [c.get_attribute("data-question") for c in _cards(page)])
    return found[0]


def _answer_after_click(page, name):
    card = _card(page, name)
    figure = int(re.search(r"(\d+) relationships? recorded", card.inner_text()).group(1))
    card.click()
    answer = page.get_by_test_id("estate-answer")
    expect(answer).to_have_count(1, timeout=PAGE_TIMEOUT)
    return answer, figure


def _sentence(answer):
    return " ".join(answer.locator("[data-recorded-links]").inner_text().split())


def test_1_two_elements_with_one_name_are_offered_once_and_each_card_carries_its_own_figure(browser, world):
    context, page = _page(browser, world, "one")
    try:
        open_chat(page, world["base"])
        questions = [c.get_attribute("data-question") for c in _cards(page)]
        assert len(questions) == len(set(questions)), questions
        assert questions[:3] == [
            "What breaks if Customer Portal fails?",
            "What breaks if Orders fails?",
            "What breaks if Stock fails?",
        ], questions
        details = [" ".join(c.inner_text().split()) for c in _cards(page)][:3]
        assert details == [
            "What breaks if Customer Portal fails? 4 relationships recorded",
            "What breaks if Orders fails? 2 relationships recorded",
            "What breaks if Stock fails? 1 relationship recorded",
        ], details
        shoot(page, "same-name-offered")
    finally:
        context.close()


def test_2_the_stock_card_opens_the_answer_about_the_linked_stock(browser, world):
    context, page = _page(browser, world, "one")
    try:
        open_chat(page, world["base"])
        answer, figure = _answer_after_click(page, "Stock")
        text = answer.inner_text()
        assert "Nothing is recorded as connected to this yet" not in text, text
        assert "Org Two Peer" not in text
        assert figure == 1
        assert _sentence(answer) == "Stock has 1 relationship recorded: 1 leads from it and 0 lead to it, reaching 1 other element."
        assert "Carts" in answer.locator("[data-answer-row]").first.inner_text()
        shoot(page, "same-name-answer-stock")
        answer.get_by_test_id("answer-open").click()
        page.wait_for_url(re.compile(r"/answers?/"), timeout=PAGE_TIMEOUT)
        body = page.locator("body").inner_text().lower()
        assert "carts" in body and "stock" in body, body[:300]
    finally:
        context.close()


def test_3_the_orders_card_opens_the_applications_own_record_and_its_figure_matches(browser, world):
    context, page = _page(browser, world, "one")
    try:
        open_chat(page, world["base"])
        answer, figure = _answer_after_click(page, "Orders")
        assert figure == 2
        assert _sentence(answer) == "Orders has 2 relationships recorded: 2 lead from it and 0 lead to it, reaching 2 other elements."
        names = answer.locator("[data-answer-row] a").all_inner_texts()
        assert sorted(names) == ["Billing Api", "Carts"], names
        shoot(page, "same-name-answer-orders")
    finally:
        context.close()


def test_4_one_connection_and_four_relationships_are_explained_by_their_directions(browser, world):
    context, page = _page(browser, world, "one")
    try:
        open_chat(page, world["base"])
        answer, figure = _answer_after_click(page, "Customer Portal")
        assert figure == 4
        assert "1 connection in your records" in answer.inner_text()
        said = _sentence(answer)
        assert said == "Customer Portal has 4 relationships recorded: 1 leads from it and 3 lead to it, reaching 4 other elements.", said
        assert "your overview gives" not in answer.inner_text()
        assert "also count the systems" not in answer.inner_text()
        shoot(page, "same-name-answer-portal")
        row = answer.locator("[data-answer-row] a").first
        assert row.inner_text() == "Billing Api"
        row.click()
        page.wait_for_url(re.compile(r"element=\d+"), timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).not_to_have_text("", timeout=PAGE_TIMEOUT)
    finally:
        context.close()


def test_5_the_other_organisation_is_offered_and_answered_only_its_own_elements(browser, world):
    context, page = _page(browser, world, "two")
    try:
        open_chat(page, world["base"])
        offered = page.request.get(world["base"] + "/ai-chat/first-questions").json()
        ids = world["ids"]
        assert ids["stock_new"] not in [q["element_id"] for q in offered["questions"]]
        answer, figure = _answer_after_click(page, "Stock")
        assert figure == 1
        text = answer.inner_text()
        assert "Org Two Peer" in text and "Carts" not in text, text
        assert _sentence(answer) == "Stock has 1 relationship recorded: 1 leads from it and 0 lead to it, reaching 1 other element."
    finally:
        context.close()


def test_6_applications_without_links_are_offered_general_questions_and_the_true_next_steps(browser, world):
    context, page = _page(browser, world, "three")
    try:
        open_chat(page, world["base"])
        host = page.locator("#portfolio-briefing")
        expect(host).not_to_have_attribute("aria-busy", "true", timeout=PAGE_TIMEOUT)
        text = host.inner_text()
        assert len(_cards(page)) == 4
        assert "Once your applications are in" not in text, text
        assert "Your applications are recorded, but not how they connect" in text, text
        assert "Nothing flagged in your portfolio yet" not in text
        assert "Import a portfolio" not in text
        assert page.request.get(world["base"] + "/ai-chat/first-questions").json() == {"questions": [], "applications": 2}
        shoot(page, "linkless-offered")
        record = host.get_by_role("link", name="Record how they connect")
        record.click()
        page.wait_for_url(re.compile(r"/composer"), timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).not_to_have_text("", timeout=PAGE_TIMEOUT)
        open_chat(page, world["base"])
        host = page.locator("#portfolio-briefing")
        expect(host.get_by_role("link", name="Connect GitHub")).to_be_visible(timeout=PAGE_TIMEOUT)
        host.get_by_role("link", name="Connect GitHub").click()
        page.wait_for_url(re.compile(r"github"), timeout=PAGE_TIMEOUT)
        assert "github" in page.locator("body").inner_text().lower()
    finally:
        context.close()


def test_7_a_failed_read_of_the_first_questions_is_a_plain_error_and_retry_brings_them_back(browser, world):
    context, page = _page(browser, world, "one")
    try:
        page.route("**/ai-chat/first-questions", lambda route: route.fulfill(status=500, json={"error": "down"}))
        open_chat(page, world["base"])
        host = page.locator("#portfolio-briefing")
        expect(host).to_contain_text("Could not read your portfolio", timeout=PAGE_TIMEOUT)
        text = host.inner_text()
        assert "This is an error, not an empty portfolio" in text
        assert host.get_by_test_id("first-question-suggestion").count() == 0
        assert "Nothing flagged" not in text and "Once your applications are in" not in text
        expect(page.locator("#user-input")).to_be_enabled()
        shoot(page, "first-questions-failed")
        page.unroute("**/ai-chat/first-questions")
        host.get_by_role("button", name="Retry").click()
        expect(host.get_by_test_id("first-question-suggestion").first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert "Could not read your portfolio" not in host.inner_text()
        assert "What breaks if Customer Portal fails?" in host.inner_text()
    finally:
        context.close()

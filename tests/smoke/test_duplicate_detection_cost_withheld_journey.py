"""The duplicate detection pages and the replies behind them show money only to people the one cost rule allows.

Goal: "I open the duplicate detection dashboard and the group detail. If my role may see spend I read the
estimated savings; if it may not, I read one sentence saying cost figures are withheld for my role, and no
amount in the page or in the replies behind it."

Two organisations each carry their own savings; the finance person and the solution architect of the first
read the pages, and nothing of the second shows anywhere.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .cost_withheld_support import HELD_BACK, holds_none_of, open_page, seed_organisation, shot, shown_text
from .fresh_org import api

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SIZES = [(1440, "light"), (390, "dark")]
MONEY = ("group_savings", "run_savings", "app_tco", "savings")


@pytest.fixture(scope="module")
def organisations(live_server):
    return seed_organisation(1), seed_organisation(2)


def _amounts(org):
    return [org["figures"][key] for key in MONEY]


def _said_once(page):
    return shown_text(page).count(HELD_BACK) == 1


def _reply(page, path):
    status, body = api(page, "GET", path)
    assert status == 200, (path, status)
    return body


@pytest.mark.parametrize("width,scheme", SIZES)
def test_a_solution_architect_reads_one_sentence_and_no_amount_on_duplicate_detection(
        browser, live_server, organisations, width, scheme):
    mine, theirs = organisations
    context, page = open_page(browser, live_server, mine["emails"]["solution_architect"], width, scheme)
    try:
        everything = _amounts(mine) + _amounts(theirs)

        # The dashboard: the savings card gives way to the sentence.
        page.goto(live_server + "/duplicate-detection/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("costs-withheld")).to_have_text(HELD_BACK, timeout=PAGE_TIMEOUT)
        assert _said_once(page), "the sentence is said once on the dashboard"
        assert "£" not in shown_text(page)
        assert holds_none_of(page.content(), everything) == []
        shot(page, "architect-duplicate-dashboard", width, scheme)

        # The replies behind the dashboard.
        for path in ("/duplicate-detection/enterprise/groups",
                     "/duplicate-detection/simple/groups"):
            body = _reply(page, path)
            assert holds_none_of(str(body), everything) == [], path
            assert body.get("costs_withheld_sentence") == HELD_BACK, path

        # The group detail page: no savings figure.
        page.goto(live_server + "/duplicate-detection/simple/group/%d" % mine["group_id"],
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert _said_once(page), "the sentence is said once on the group detail"
        assert holds_none_of(page.content(), everything) == []
        shot(page, "architect-duplicate-group-detail", width, scheme)
    finally:
        context.close()


def test_a_finance_person_reads_the_duplicate_detection_figures_unchanged(browser, live_server, organisations):
    mine, theirs = organisations
    fig = mine["figures"]
    context, page = open_page(browser, live_server, mine["emails"]["finance"], 1440, "light")
    try:
        page.goto(live_server + "/duplicate-detection/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert HELD_BACK not in shown_text(page)
        assert holds_none_of(page.content(), _amounts(theirs)) == []
        shot(page, "finance-duplicate-dashboard", 1440, "light")

        # The replies carry the savings for this organisation only.
        for path in ("/duplicate-detection/enterprise/groups",
                     "/duplicate-detection/simple/groups"):
            body = _reply(page, path)
            assert holds_none_of(str(body), _amounts(theirs)) == [], path
            assert "costs_withheld_sentence" not in body, path

        # The group detail shows its savings.
        page.goto(live_server + "/duplicate-detection/simple/group/%d" % mine["group_id"],
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        text = shown_text(page)
        assert "{:,.0f}".format(fig["group_savings"]) in text, text
        assert holds_none_of(page.content(), _amounts(theirs)) == []
    finally:
        context.close()


def test_the_refused_view_fits_a_phone(browser, live_server, organisations):
    mine, _ = organisations
    page = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme="dark")
    try:
        from .test_archetype_journeys import _login
        _login(page, live_server, mine["emails"]["solution_architect"])
        assert page.goto(live_server + "/duplicate-detection/", timeout=PAGE_TIMEOUT).status == 200
        expect(page.get_by_test_id("costs-withheld")).to_contain_text(HELD_BACK, timeout=PAGE_TIMEOUT)
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        shot(page, "architect-duplicate-phone", 390, "dark")
    finally:
        page.close()

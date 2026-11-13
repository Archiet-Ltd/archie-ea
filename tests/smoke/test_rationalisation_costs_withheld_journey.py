"""The rationalisation pages and the replies behind them show money only to people the one cost rule allows.

Goal: "I open the rationalisation pages: the overview, the tracking page and the scorecard. If my role may see
spend I read the savings, the benefits and the cost tiers; if it may not, I read one sentence saying cost figures
are withheld for my role, and no amount in the page or in the replies behind it."

Two organisations each carry their own amounts; the finance person and the solution architect of the first
read the pages, and nothing of the second shows anywhere.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .cost_withheld_support import (
    HELD_BACK, holds_none_of, open_page, seed_organisation, shot, shown_text, wait_until_said)
from .fresh_org import api

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

MONEY_IN_WORDS = re.compile(r"[£$]\s?\d")
SIZES = [(1440, "light"), (390, "dark")]
MONEY = ("savings", "investment", "tco", "benefit_projected", "benefit_actual", "entry_savings", "entry_migration",
         "group_savings", "run_savings", "app_tco")


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


def _json_replies(org):
    app_id = org["app_ids"][0]
    return (
        "/applications/rationalization/api/benefits/%d" % app_id,
        "/applications/rationalization/api/executive-summary",
        "/applications/rationalization/api/groups",
        "/applications/rationalization/api/portfolio-scores",
        "/applications/rationalization/api/enrich-candidates",
        "/dashboard/api/tco/cost-tiers",
    )


@pytest.mark.parametrize("width,scheme", SIZES)
def test_a_solution_architect_reads_one_sentence_and_no_amount_on_the_rationalisation_pages(
        browser, live_server, organisations, width, scheme):
    mine, theirs = organisations
    context, page = open_page(browser, live_server, mine["emails"]["solution_architect"], width, scheme)
    try:
        everything = _amounts(mine) + _amounts(theirs)
        for name, path in (("overview", "/dashboard/rationalization"), ("start", "/applications/rationalization"),
                           ("tracking", "/applications/rationalization/tracking")):
            page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            wait_until_said(page)
            assert _said_once(page), (path, "the sentence is said once")
            assert not MONEY_IN_WORDS.search(shown_text(page)), (path, MONEY_IN_WORDS.search(shown_text(page)))
            assert holds_none_of(page.content(), everything) == [], path
            shot(page, "architect-" + name, width, scheme)

        # the overview keeps saying it once after a dossier is opened
        page.goto(live_server + "/dashboard/rationalization", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        search = page.locator("#dossier-app-search")
        search.scroll_into_view_if_needed()
        search.fill(mine["app_names"][0][:9])
        page.get_by_role("option", name=mine["app_names"][0]).click(timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Load decision dossier for selected application").click()
        expect(page.locator("[x-data='decisionDossier()']").get_by_text(HELD_BACK)).to_have_count(1, timeout=PAGE_TIMEOUT)
        assert _said_once(page), "the sentence is still said once with a dossier open"

        # the scorecard: the cost tiers give way to the sentence
        page.goto(live_server + "/dashboard/rationalization/scorecard", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("costs-withheld")).to_have_text(HELD_BACK, timeout=PAGE_TIMEOUT)
        assert _said_once(page)
        assert "Very Low" not in shown_text(page) and "Medium" not in shown_text(page)
        assert holds_none_of(page.content(), everything) == []
        shot(page, "architect-scorecard", width, scheme)

        # the replies behind the pages
        for path in _json_replies(mine):
            body = _reply(page, path)
            assert holds_none_of(str(body), everything) == [], path
        tiers = _reply(page, "/dashboard/api/tco/cost-tiers")
        assert tiers["data"] == [] and tiers["costs_withheld_sentence"] == HELD_BACK
        benefits = _reply(page, "/applications/rationalization/api/benefits/%d" % mine["app_ids"][0])
        assert benefits["benefits"]["projected_annual_savings"] is None
        assert benefits["benefits"]["actual_annual_savings"] is None
        assert benefits["costs_withheld_sentence"] == HELD_BACK
        scores = _reply(page, "/applications/rationalization/api/portfolio-scores")
        assert scores and all(row["estimated_annual_savings"] is None for row in scores)
    finally:
        context.close()


@pytest.mark.parametrize("width,scheme", SIZES)
def test_a_finance_person_reads_the_rationalisation_figures_unchanged(browser, live_server, organisations, width,
                                                                       scheme):
    mine, theirs = organisations
    fig = mine["figures"]
    context, page = open_page(browser, live_server, mine["emails"]["finance"], width, scheme)
    try:
        page.goto(live_server + "/applications/rationalization/tracking", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        text = shown_text(page)
        assert "{:,.0f}".format(fig["benefit_projected"]) in text and "{:,.0f}".format(fig["benefit_actual"]) in text
        assert HELD_BACK not in text
        assert holds_none_of(page.content(), _amounts(theirs)) == []
        shot(page, "finance-tracking", width, scheme)

        page.goto(live_server + "/applications/rationalization", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("stat-savings-value")).to_contain_text("£")
        assert HELD_BACK not in shown_text(page)
        shot(page, "finance-start", width, scheme)

        page.goto(live_server + "/dashboard/rationalization", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        card = page.locator("#savings-stat")
        expect(card).to_contain_text("£")
        assert HELD_BACK not in shown_text(page)
        shot(page, "finance-overview", width, scheme)

        page.goto(live_server + "/dashboard/rationalization/scorecard", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        expect(page.locator("#cost-tiers-container")).to_contain_text("Low", timeout=PAGE_TIMEOUT)
        assert HELD_BACK not in shown_text(page)
        shot(page, "finance-scorecard", width, scheme)

        benefits = _reply(page, "/applications/rationalization/api/benefits/%d" % mine["app_ids"][0])
        assert benefits["benefits"]["projected_annual_savings"] == float(fig["benefit_projected"])
        assert benefits["benefits"]["actual_annual_savings"] == float(fig["benefit_actual"])
        assert "costs_withheld" not in benefits
        scores = _reply(page, "/applications/rationalization/api/portfolio-scores")
        assert {row["estimated_annual_savings"] for row in scores if row["app_id"] in mine["app_ids"]} == {
            float(fig["savings"])}
        enrich = _reply(page, "/applications/rationalization/api/enrich-candidates")
        assert float(fig["app_tco"]) in {row["total_cost_of_ownership"] for row in enrich["applications"]}
        summary = _reply(page, "/applications/rationalization/api/executive-summary")
        assert summary["financial"]["total_projected_savings"] == float(fig["savings"] * 2)
        tiers = _reply(page, "/dashboard/api/tco/cost-tiers")
        assert tiers["data"] and "costs_withheld_sentence" not in tiers
        for path in _json_replies(mine):
            assert holds_none_of(str(_reply(page, path)), _amounts(theirs)) == [], path
    finally:
        context.close()

"""An architect scans the application list and opens a row.

Goal: find an application, open it, and find the same facts (owner, vendor,
cost), its risks and its dependencies there. Data: six linked applications
(owners, vendors, costs, risks, dependencies) and a second organisation that
holds an application with the same name as the billing application here.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import sign_in
from .linked_portfolio import linked_world  # noqa: F401  (the one shared fixture)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _signed_in(browser, live_server, world):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, world["org"]["emails"]["enterprise_architect"])
    return context, page


def _list(page, base):
    page.goto(base + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)


def _names_in_order(page, noun):
    return [t.strip() for t in page.locator("[data-testid^='app-row-'] td p.font-medium").all_inner_texts()
            if noun in t]


def test_list_search_and_sort_show_only_this_organisation_rows(browser, live_server, linked_world):
    noun, mine = linked_world["noun"], linked_world["mine"]
    context, page = _signed_in(browser, live_server, linked_world)
    try:
        _list(page, live_server)
        rows = page.locator("[data-testid^='app-row-']")
        expect(rows).to_have_count(len(mine["apps"]), timeout=PAGE_TIMEOUT)
        # The look-alike in the other organisation is not a second billing row.
        assert _names_in_order(page, noun).count("%s Billing" % noun) == 1
        billing = page.locator("[data-testid='app-row-%d']" % mine["apps"]["billing"]["id"])
        expect(billing).to_contain_text("Active")
        expect(billing).to_contain_text("%s Take payment" % noun)
        expect(page.locator("body")).not_to_contain_text("Zed Outsider")

        # Sort by application name, both directions.
        page.get_by_test_id("th-sort-name").click()
        page.wait_for_load_state("domcontentloaded")
        ascending = _names_in_order(page, noun)
        assert ascending == sorted(ascending), ascending
        page.get_by_test_id("th-sort-name").click()
        page.wait_for_load_state("domcontentloaded")
        descending = _names_in_order(page, noun)
        assert descending == sorted(descending, reverse=True), descending

        # Search narrows to that application and to nothing else.
        search = page.get_by_test_id("filter-search")
        search.fill("Ledger")
        page.wait_for_url(re.compile("search=Ledger"), timeout=PAGE_TIMEOUT)
        expect(rows).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(rows.first).to_contain_text("%s Ledger" % noun)
    finally:
        context.close()


@pytest.mark.xfail(strict=True, reason=(
    "Searching the application list for an owner name finds no application, although that owner "
    "is recorded on two of them."))
def test_searching_for_an_owner_finds_that_owner_applications(browser, live_server, linked_world):
    noun = linked_world["noun"]
    context, page = _signed_in(browser, live_server, linked_world)
    try:
        _list(page, live_server)
        page.get_by_test_id("filter-search").fill("Marcus Webb")
        page.wait_for_url(re.compile("search=Marcus"), timeout=PAGE_TIMEOUT)
        expect(page.locator("[data-testid^='app-row-']")).to_have_count(2, timeout=PAGE_TIMEOUT)
        assert sorted(_names_in_order(page, noun)) == ["%s Gateway" % noun, "%s Ledger" % noun]
    finally:
        context.close()


@pytest.mark.xfail(strict=True, reason=(
    "The application list shows no owner, vendor or cost for any application, so a person scanning "
    "it cannot see who owns one or what it costs."))
def test_each_row_shows_its_owner_and_vendor(browser, live_server, linked_world):
    mine = linked_world["mine"]
    context, page = _signed_in(browser, live_server, linked_world)
    try:
        _list(page, live_server)
        for application in mine["apps"].values():
            row = page.locator("[data-testid='app-row-%d']" % application["id"])
            expect(row).to_contain_text(application["vendor"])
            if application["owner"]:
                expect(row).to_contain_text(application["owner"])
    finally:
        context.close()


def test_opening_first_and_last_rows_shows_the_same_facts_the_row_showed(browser, live_server, linked_world):
    noun, mine = linked_world["noun"], linked_world["mine"]
    context, page = _signed_in(browser, live_server, linked_world)
    try:
        _list(page, live_server)
        ids = [int(re.sub(r"\D", "", r.get_attribute("data-testid")))
               for r in page.locator("[data-testid^='app-row-']").all()]
        by_id = {a["id"]: a for a in mine["apps"].values()}
        for app_id in (ids[0], ids[-1]):
            expected = by_id[app_id]
            _list(page, live_server)
            row = page.locator("[data-testid='app-row-%d']" % app_id)
            row_text = row.inner_text()
            row.get_by_test_id("btn-view-%d" % app_id).click()
            page.wait_for_url(re.compile(r"/applications/%d$" % app_id), timeout=PAGE_TIMEOUT)
            main = page.locator("#main-content, main").first
            expect(page.get_by_role("heading", level=1)).to_contain_text(expected["name"], timeout=PAGE_TIMEOUT)
            # What the row showed is what the page shows: lifecycle and mapped capability.
            assert "Active" in row_text
            expect(main).to_contain_text("Active")
            expect(main.get_by_text("Capabilities", exact=False).first).to_be_visible()
            if expected["owner"]:
                expect(main).to_contain_text(expected["owner"])
            # The cost tab states the recorded annual cost.
            main.locator("button, a, [role=tab]").filter(has_text=re.compile(r"^\s*Cost\s*$")).first.click()
            expect(main).to_contain_text(re.compile("%s|%s" % (expected["cost_text"], expected["cost"])),
                                         timeout=PAGE_TIMEOUT)
            # Never another organisation record of the same name.
            expect(main).not_to_contain_text("Zed Outsider")
            expect(main).not_to_contain_text("999,999")
    finally:
        context.close()


def test_an_application_with_no_owner_says_so_instead_of_a_bare_dash(browser, live_server, linked_world):
    unowned = linked_world["mine"]["apps"]["reports"]
    context, page = _signed_in(browser, live_server, linked_world)
    try:
        page.goto(live_server + "/applications/%d" % unowned["id"], wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        main = page.locator("#main-content, main").first
        expect(page.get_by_role("heading", level=1)).to_contain_text(unowned["name"], timeout=PAGE_TIMEOUT)
        expect(main).to_contain_text(re.compile(
            r"no owner|not assigned|unowned|none recorded|not recorded|assign an owner|add an owner", re.I))
    finally:
        context.close()


def test_the_fact_sheet_links_lead_to_this_application_risks_and_dependencies(browser, live_server, linked_world):
    noun, mine = linked_world["noun"], linked_world["mine"]
    billing = mine["apps"]["billing"]
    context, page = _signed_in(browser, live_server, linked_world)
    try:
        _list(page, live_server)
        page.locator("[data-testid='app-row-%d']" % billing["id"]).get_by_test_id(
            "btn-factsheet-%d" % billing["id"]).click()
        page.wait_for_url(re.compile(r"/applications/%d/fact-sheet" % billing["id"]), timeout=PAGE_TIMEOUT)
        main = page.locator("#main-content, main").first
        expect(page.get_by_role("heading", level=1)).to_contain_text(billing["name"], timeout=PAGE_TIMEOUT)
        # Its risk, and only its risk.
        expect(main).to_contain_text(mine["risks"]["billing"]["title"])
        expect(main).not_to_contain_text(mine["risks"]["ledger"]["title"])
        # Its dependencies: what it needs, and who needs it.
        expect(main).to_contain_text("%s Ledger" % noun)
        expect(main).to_contain_text("%s Gateway" % noun)
        expect(main).to_contain_text("%s Portal" % noun)
        # The risk link opens the register, which lists that risk.
        main.get_by_role("link", name=mine["risks"]["billing"]["title"]).click()
        page.wait_for_url(re.compile(r"/risks/?$"), timeout=PAGE_TIMEOUT)
        expect(page.locator("#main-content, main").first).to_contain_text(mine["risks"]["billing"]["title"])
        # The impact graph link opens a drawn graph, not the empty state.
        page.go_back()
        page.get_by_role("link", name="View impact graph").click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("#impact-counts")).not_to_contain_text("Loading", timeout=PAGE_TIMEOUT)
        expect(page.locator("#impact-empty")).to_be_hidden()
        expect(page.locator("#impact-svg")).to_contain_text(billing["name"].split()[-1])
    finally:
        context.close()

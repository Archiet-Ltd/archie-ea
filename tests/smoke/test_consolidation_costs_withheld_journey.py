"""The consolidation list shows money only to people the one cost rule allows.

Goal: "I open the consolidation list. If my role may see spend I read the annual saving and the migration cost
of each entry, the cost cards and the financial breakdown; if it may not, I read one sentence saying cost
figures are withheld for my role, and no amount in the page or in the replies behind it. When I edit an entry
I cannot see the amounts, and saving my edit leaves the recorded amounts as they were."

Two organisations each carry their own amounts; the finance person and the solution architect of the first
read the list, and nothing of the second shows anywhere.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .cost_withheld_support import HELD_BACK, holds_none_of, open_page, seed_organisation, shot, shown_text
from .fresh_org import api

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SIZES = [(1440, "light"), (390, "dark")]
MONEY = ("entry_savings", "entry_migration", "group_savings", "maintenance", "licence", "app_tco", "savings", "tco",
         "contract")


@pytest.fixture(scope="module")
def organisations(live_server):
    """Two organisations, each with one active contract stating its own annual cost.

    The contract is what makes the Portfolio Annual Cost card state a figure, so the card's value can be
    checked at every width against the figure the reply carries.
    """
    from datetime import date, timedelta

    from app import create_app, db
    from app.models.application_portfolio import VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    orgs = []
    for base in (1, 2):
        org = seed_organisation(base)
        contract = 515000 + base * 1357
        with create_app("testing").app_context():
            vendor = VendorOrganization(name="Consolidation Vendor %s" % org["tag"])
            db.session.add(vendor)
            db.session.flush()
            db.session.add(VendorContract(
                contract_name="Consolidation contract %s" % org["tag"], organization_id=org["org_id"],
                vendor_id=vendor.id, start_date=date.today() - timedelta(days=120),
                end_date=date.today() + timedelta(days=240), annual_cost=float(contract), status="active"))
            db.session.commit()
        org["figures"]["contract"] = contract
        orgs.append(org)
    return orgs[0], orgs[1]


def _amounts(org):
    return [org["figures"][key] for key in MONEY]


def _open_list(page, live_server, org):
    page.goto(live_server + "/consolidation-list/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_text(org["app_names"][0]).first).to_be_visible(timeout=PAGE_TIMEOUT)


@pytest.mark.parametrize("width,scheme", SIZES)
def test_a_solution_architect_reads_one_sentence_and_no_amount_on_the_consolidation_list(
        browser, live_server, organisations, width, scheme):
    mine, theirs = organisations
    context, page = open_page(browser, live_server, mine["emails"]["solution_architect"], width, scheme)
    try:
        _open_list(page, live_server, mine)
        text = shown_text(page)
        assert text.count(HELD_BACK) == 1, "the sentence is said once"
        assert "Annual Saving" not in text and "Migration Cost" not in text
        for card in ("Portfolio Annual Cost", "Projected Savings", "Migration Investment"):
            assert card not in text, card
        assert "£" not in text
        everything = _amounts(mine) + _amounts(theirs)
        assert holds_none_of(page.content(), everything) == []
        shot(page, "architect-consolidation", width, scheme)

        # opening an entry shows its details without the financial breakdown
        page.get_by_text(mine["app_names"][0]).first.click()
        expect(page.get_by_text(mine["app_names"][0] + " — Details")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Capabilities Supported")).to_be_visible(timeout=PAGE_TIMEOUT)
        assert "Financial Breakdown" not in shown_text(page) and "License Cost" not in shown_text(page)
        assert holds_none_of(page.content(), everything) == []
        assert shown_text(page).count(HELD_BACK) == 1
        shot(page, "architect-consolidation-entry", width, scheme)

        # the replies behind the page
        for path in ("/consolidation-list/api/entries",
                     "/consolidation-list/api/entry/%d/detail" % mine["entry_ids"][0]):
            status, body = api(page, "GET", path)
            assert status == 200, path
            assert holds_none_of(str(body), everything) == [], path
            assert body["costs_withheld_sentence"] == HELD_BACK
        _, listing = api(page, "GET", "/consolidation-list/api/entries")
        assert listing["entries"] and all(row["estimated_savings"] is None and row["migration_cost"] is None
                                          for row in listing["entries"])
        assert all(listing["summary"][key] is None for key in (
            "total_estimated_savings", "total_migration_cost", "portfolio_annual_cost"))
    finally:
        context.close()


def test_editing_an_entry_without_seeing_its_amounts_leaves_the_amounts_alone(browser, live_server, organisations):
    mine, _ = organisations
    fig = mine["figures"]
    context, page = open_page(browser, live_server, mine["emails"]["solution_architect"], 1440, "light")
    try:
        _open_list(page, live_server, mine)
        row = page.locator("tr", has_text=mine["app_names"][0]).first
        row.get_by_title("Edit", exact=True).click()
        expect(page.locator("#edit-notes")).to_be_visible(timeout=PAGE_TIMEOUT)
        for field in ("#edit-estimated-savings", "#edit-migration-cost", "#edit-annual-cost"):
            assert page.locator(field).count() == 0, field
        assert "Estimated Annual Savings" not in page.content() and "Annual Operating Cost" not in page.content()
        page.locator("#edit-notes").fill("Checked with the ledger owner.")
        page.get_by_role("button", name="Save Changes").click()
        expect(page.get_by_text("Entry updated successfully")).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()

    context, page = open_page(browser, live_server, mine["emails"]["finance"], 1440, "light")
    try:
        page.goto(live_server + "/consolidation-list/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        status, listing = api(page, "GET", "/consolidation-list/api/entries")
        assert status == 200
        mine_rows = [row for row in listing["entries"] if row["application_id"] in mine["app_ids"]]
        assert {row["estimated_savings"] for row in mine_rows} == {float(fig["entry_savings"])}
        assert {row["migration_cost"] for row in mine_rows} == {float(fig["entry_migration"])}
        assert any(row["notes"] == "Checked with the ledger owner." for row in mine_rows)
    finally:
        context.close()


@pytest.mark.parametrize("width,scheme", SIZES)
def test_a_finance_person_reads_the_consolidation_figures_unchanged(browser, live_server, organisations, width,
                                                                     scheme):
    mine, theirs = organisations
    fig = mine["figures"]
    context, page = open_page(browser, live_server, mine["emails"]["finance"], width, scheme)
    try:
        _open_list(page, live_server, mine)
        text = shown_text(page)
        assert HELD_BACK not in text
        for heading in ("Projected Savings", "Migration Investment", "Portfolio Annual Cost"):
            assert heading in text, heading
        # The Portfolio Annual Cost card reads the one figure the reply carries — at every width, so an
        # allowed person never reads a different value on a phone than on a desktop.
        status, listing = api(page, "GET", "/consolidation-list/api/entries")
        assert status == 200
        portfolio = listing["summary"]["portfolio_annual_cost"]
        assert portfolio == float(fig["contract"]), (width, portfolio)
        card = page.locator("div.bg-card", has_text="Portfolio Annual Cost").first
        expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
        assert card.locator("p.text-xl").inner_text() == "£{:,.2f}".format(portfolio), (width, card.inner_text())
        row = page.locator("tr", has_text=mine["app_names"][0]).first
        if width >= 1024:
            assert "{:,.0f}".format(fig["entry_savings"]) in row.inner_text()
            assert "{:,.0f}".format(fig["entry_migration"]) in row.inner_text()
        assert holds_none_of(page.content(), _amounts(theirs)) == []
        shot(page, "finance-consolidation", width, scheme)

        page.get_by_text(mine["app_names"][0]).first.click()
        expect(page.get_by_text("Financial Breakdown")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Total Annual")).to_be_visible(timeout=PAGE_TIMEOUT)
        shot(page, "finance-consolidation-entry", width, scheme)

        status, listing = api(page, "GET", "/consolidation-list/api/entries")
        assert status == 200 and "costs_withheld_sentence" not in listing
        mine_rows = [row for row in listing["entries"] if row["application_id"] in mine["app_ids"]]
        assert {row["estimated_savings"] for row in mine_rows} == {float(fig["entry_savings"])}
        assert listing["summary"]["total_estimated_savings"] == float(fig["entry_savings"] * 2)
        assert listing["summary"]["total_migration_cost"] == float(fig["entry_migration"] * 2)
        status, detail = api(page, "GET", "/consolidation-list/api/entry/%d/detail" % mine["entry_ids"][0])
        assert detail["financial"]["estimated_savings"] == float(fig["entry_savings"])
        assert detail["financial"]["migration_cost"] == float(fig["entry_migration"])
        assert detail["financial"]["maintenance_cost"] == float(fig["maintenance"])
        assert holds_none_of(str(listing) + str(detail), _amounts(theirs)) == []
    finally:
        context.close()


def test_the_annual_cost_card_at_390_reads_the_one_figure_once_the_reply_arrives(
        browser, live_server, organisations):
    """At 390 dark an allowed person reads the same Portfolio Annual Cost figure as at 1440 light.

    While the entries reply is still on its way the card states no value at all — never "Not recorded"
    for a figure the reply is carrying — and once the reply lands the narrow width reads the one figure.
    """
    mine, _ = organisations
    fig = mine["figures"]
    context, page = open_page(browser, live_server, mine["emails"]["finance"], 390, "dark")
    try:
        held = []
        page.route(re.compile(r"/consolidation-list/api/entries"), lambda route: held.append(route))
        page.goto(live_server + "/consolidation-list/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        card = page.locator("div.bg-card", has_text="Portfolio Annual Cost").first
        expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
        assert held, "the list never asked for its entries"
        # the reply is held back: the card must not read "Not recorded" (or any other value) at 390
        assert "Not recorded" not in card.inner_text(), card.inner_text()
        assert card.locator("p.text-xl").inner_text().strip() == "", card.inner_text()
        held[0].continue_()
        expect(page.get_by_text(mine["app_names"][0]).first).to_be_visible(timeout=PAGE_TIMEOUT)
        # the reply has landed: the phone reads the one figure the organisation's contract states
        expect(card).to_contain_text("£{:,.2f}".format(fig["contract"]), timeout=PAGE_TIMEOUT)
        assert "Not recorded" not in card.inner_text(), card.inner_text()
        shot(page, "finance-consolidation-card", 390, "dark")
    finally:
        context.close()

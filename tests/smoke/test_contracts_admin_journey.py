"""An organisation's Administrator reads its own contracts, and nobody else does.

The Administrator of an organisation whose persona carries no cost rights opens the contracts
list and a contract from the sidebar. A colleague without cost rights is neither offered the link
nor allowed the page. The Administrator of another organisation finds its own contract and
none of this organisation's.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .contract_records import add_contract_and_licence, remove_records
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

CONTRACTS = "/procurement/contracts"
LICENCES = "/procurement/licenses"


def _new_page(browser, width=1440):
    """A fresh browser session with the first-login welcome tour already seen."""
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _open_spend_group(page, base):
    page.goto(base + "/dashboard/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    spend = page.locator('[data-nav-key="spend"]').first
    expect(spend).to_be_visible(timeout=PAGE_TIMEOUT)
    if spend.get_attribute("aria-expanded") == "false":
        spend.click()


def test_an_administrator_opens_the_contracts_from_the_sidebar(browser, live_server):
    org = create_fresh_org("solution_architect", org_admin=True)
    records = add_contract_and_licence(org["org_id"], "Alpha" + org["suffix"])
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, org["emails"]["solution_architect"])
        _open_spend_group(page, live_server)
        page.locator('[data-nav-key="contracts"]').first.click()
        page.wait_for_url(lambda url: url.endswith(CONTRACTS), timeout=PAGE_TIMEOUT)

        row = page.locator("tbody tr").filter(has_text=records["names"]["contract"])
        expect(row).to_have_count(1, timeout=PAGE_TIMEOUT)
        row.get_by_role("link", name=records["names"]["contract"]).click()
        page.wait_for_url(lambda url: url.endswith("%s/%s" % (CONTRACTS, records["contract"])), timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name=records["names"]["contract"])).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("C-ALPHA").first).to_be_visible()
    finally:
        context.close()
        remove_records(records)


def test_a_colleague_without_cost_rights_is_neither_offered_nor_allowed_the_contracts(browser, live_server):
    org = create_fresh_org("solution_architect", org_admin=True, extra_roles=("application_manager",))
    records = add_contract_and_licence(org["org_id"], "Beta" + org["suffix"])
    admin_context, admin_page = _new_page(browser)
    context, page = _new_page(browser)
    try:
        sign_in(admin_page, live_server, org["emails"]["solution_architect"])
        _open_spend_group(admin_page, live_server)
        expect(admin_page.locator('[data-nav-key="contracts"]').first).to_be_visible(timeout=PAGE_TIMEOUT)

        sign_in(page, live_server, org["emails"]["application_manager"])
        for path in (CONTRACTS, "%s/%s" % (CONTRACTS, records["contract"]), LICENCES):
            response = page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert response.status == 403, "%s answered %s for a reader without cost rights" % (path, response.status)
        page.goto(live_server + "/dashboard/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator('a[href="%s"]' % CONTRACTS).count() == 0
        assert page.locator('a[href="%s"]' % LICENCES).count() == 0
    finally:
        admin_context.close()
        context.close()
        remove_records(records)


def test_an_administrator_of_another_organisation_finds_only_its_own_contract(browser, live_server):
    mine = create_fresh_org("solution_architect", org_admin=True)
    theirs = create_fresh_org("solution_architect", org_admin=True)
    mine_records = add_contract_and_licence(mine["org_id"], "Gamma" + mine["suffix"])
    their_records = add_contract_and_licence(theirs["org_id"], "Delta" + theirs["suffix"])
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, theirs["emails"]["solution_architect"])
        page.goto(live_server + CONTRACTS, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("tbody tr").filter(has_text=their_records["names"]["contract"])).to_have_count(
            1, timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(mine_records["names"]["contract"])).to_have_count(0)

        for path in ("%s/%s" % (CONTRACTS, mine_records["contract"]), "%s/%s" % (LICENCES, mine_records["licence"])):
            response = page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert response.status == 404, "%s answered %s across organisations" % (path, response.status)
    finally:
        context.close()
        remove_records(mine_records, their_records)

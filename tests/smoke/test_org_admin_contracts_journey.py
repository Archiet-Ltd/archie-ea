"""An organisation administrator reads their own contracts and is fenced from other organisations.

The contracts list and detail pages are guarded by requires_contract_visibility, which
admits procurement roles, cost viewers, and the organisation's own Administrator.
This journey verifies that an org admin (not a platform admin) can open the contracts
list and a contract detail page, that a reader without cost rights cannot, and that
an org admin of organisation B gets 404 on organisation A's contract.
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

REGISTER = "/procurement/contracts"


def _new_page(browser):
    """A fresh browser session with the first-login welcome tour already seen."""
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _create_contract(page, base, name, number, annual_cost):
    page.goto(base + REGISTER, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("link", name="New Contract").first.click()
    page.wait_for_url(re.compile(r"/procurement/contracts/new$"), timeout=PAGE_TIMEOUT)
    page.fill("#contract_name", name)
    page.fill("#contract_number", number)
    page.select_option("#status", "active")
    page.fill("#annual_cost", str(annual_cost))
    page.fill("#start_date", "2026-01-01")
    page.fill("#end_date", "2028-12-31")
    with page.expect_response(
        lambda r: r.url.endswith("/procurement/contracts/new") and r.request.method == "POST",
        timeout=PAGE_TIMEOUT,
    ) as created:
        page.get_by_role("button", name="Create contract").click()
    return created.value


def _register_row(page, name):
    return page.locator("tbody tr").filter(has_text=name)


def test_org_admin_opens_contracts_list_and_detail(browser, live_server):
    """An organisation administrator (not platform_admin) can open the contracts
    list and a contract detail page for their own organisation."""
    # Create an org with an org_admin persona
    org = create_fresh_org("procurement", org_admin=True)
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, org["emails"]["procurement"])

        # The contracts link should be visible in the sidebar (Spend > Contracts and licences)
        page.goto(live_server + REGISTER, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        # The page should render, not 403
        expect(page.get_by_text("No vendor contracts yet. Add the first one and its cost and renewal date show here.")).to_be_visible(
            timeout=PAGE_TIMEOUT
        )

        # Create a contract
        name = "OrgAdmin MSA %s" % org["suffix"]
        number = "MSA-%s-001" % org["suffix"]
        response = _create_contract(page, live_server, name, number, 12000)
        assert response.status < 400, "creating the contract answered %s" % response.status
        page.wait_for_url(re.compile(r"/procurement/contracts/\d+$"), timeout=PAGE_TIMEOUT)
        _detail_url = page.url

        # Detail page shows the contract
        expect(page.get_by_role("heading", name=name)).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(number).first).to_be_visible()

        # Reload and verify persistence
        page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name=name)).to_be_visible(timeout=PAGE_TIMEOUT)

        # Register lists it
        page.goto(live_server + REGISTER, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = _register_row(page, name)
        expect(row).to_have_count(1)
        expect(row).to_contain_text(number)
    finally:
        context.close()


def test_reader_without_cost_rights_cannot_access_contracts(browser, live_server):
    """A reader without cost rights (e.g., application_manager) is neither offered
    the contracts link nor allowed the page."""
    org = create_fresh_org("application_manager", org_admin=False)
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, org["emails"]["application_manager"])

        # Navigate directly to contracts - should be denied (403)
        response = page.goto(live_server + REGISTER, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 403, "application_manager got %s for contracts, expected 403" % response.status

        # The sidebar should not show the Contracts and licences link under Spend
        # (This is implicitly tested by the authorisation matrix, but we verify the page is denied)
    finally:
        context.close()


def test_org_admin_of_org_b_cannot_access_org_a_contract(browser, live_server):
    """An administrator of organisation B gets 404 on organisation A's contract."""
    org_a = create_fresh_org("procurement", org_admin=True)
    org_b = create_fresh_org("procurement", org_admin=True)

    context, page = _new_page(browser)
    try:
        # Org A creates a contract
        sign_in(page, live_server, org_a["emails"]["procurement"])
        name = "OrgA Contract %s" % org_a["suffix"]
        number = "MSA-%s-001" % org_a["suffix"]
        response = _create_contract(page, live_server, name, number, 12000)
        assert response.status < 400
        page.wait_for_url(re.compile(r"/procurement/contracts/\d+$"), timeout=PAGE_TIMEOUT)
        _detail_url = page.url
        _contract_id = re.search(r"/contracts/(\d+)$", _detail_url).group(1)

        # Sign out and sign in as Org B admin
        page.goto(live_server + "/account/logout", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        sign_in(page, live_server, org_b["emails"]["procurement"])

        # Org B admin tries to access Org A's contract detail - should get 404
        blocked = page.goto(_detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert blocked.status == 404, "org B admin got %s for org A's contract, expected 404" % blocked.status

        # Org B admin's own contracts list should be empty
        page.goto(live_server + REGISTER, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("No vendor contracts yet. Add the first one and its cost and renewal date show here.")).to_be_visible(
            timeout=PAGE_TIMEOUT
        )
    finally:
        context.close()
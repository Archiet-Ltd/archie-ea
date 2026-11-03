"""An organisation's Administrator, whose persona carries no cost rights, reads its licences as well.

The sidebar entry is "Contracts and licences", so the Administrator who is offered it reaches the
licences page, a licence, and every link those pages offer, each landing on a page with content.
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
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _every_link_in_the_page_opens(page, live_server):
    """Follow each link in the page body as the signed-in person; each must answer with a page."""
    hrefs = page.eval_on_selector_all(
        "#main-content a[href]", "els => els.map(e => e.getAttribute('href'))")
    followed = sorted({h for h in hrefs if h.startswith("/") and not h.startswith("//") and "logout" not in h})
    assert followed, "the page offers no links to follow"
    for href in followed:
        response = page.request.get(live_server + href)
        assert response.status == 200, "%s is offered on %s but answers %s" % (href, page.url, response.status)
        assert len(response.text().strip()) > 200, "%s answers with an empty page" % href
    return followed


def test_an_administrator_follows_the_licences_from_the_contracts_page(browser, live_server):
    org = create_fresh_org("solution_architect", org_admin=True)
    records = add_contract_and_licence(org["org_id"], "Omega" + org["suffix"])
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, org["emails"]["solution_architect"])
        page.goto(live_server + CONTRACTS, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        followed = _every_link_in_the_page_opens(page, live_server)
        assert LICENCES in followed

        page.get_by_role("link", name="Licences", exact=True).first.click()
        page.wait_for_url(lambda url: url.endswith(LICENCES), timeout=PAGE_TIMEOUT)
        row = page.locator("tbody tr").filter(has_text=records["names"]["licence"])
        expect(row).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(row).to_contain_text("31")
        followed = _every_link_in_the_page_opens(page, live_server)
        assert "%s/%s" % (LICENCES, records["licence"]) in followed
        assert "/procurement/compliance" not in followed

        page.get_by_role("link", name=records["names"]["licence"]).first.click()
        page.wait_for_url(lambda url: url.endswith("%s/%s" % (LICENCES, records["licence"])), timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(records["names"]["licence"]).first).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()
        remove_records(records)


def test_the_licences_are_reachable_on_a_narrow_screen(browser, live_server):
    org = create_fresh_org("solution_architect", org_admin=True)
    records = add_contract_and_licence(org["org_id"], "Sigma" + org["suffix"])
    context, page = _new_page(browser, width=390)
    try:
        sign_in(page, live_server, org["emails"]["solution_architect"])
        page.goto(live_server + CONTRACTS, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name="Licences", exact=True).first.click()
        page.wait_for_url(lambda url: url.endswith(LICENCES), timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(records["names"]["licence"]).first).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()
        remove_records(records)

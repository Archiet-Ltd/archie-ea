"""A procurement lead records licence entitlements in a brand-new organisation.

The procurement lead of a new organisation records a contract, then two
licence entitlements under it from the licence register's "New Licence"
form: one product with entitled, deployed and used counts, and one whose
deployment and usage are not known yet. After a reload the register and each
detail page show the entitlement position per product - entitled against
consumed and used - and the product with no recorded usage reads "—" for
consumed, used, available and utilisation rather than a zero.

Before this, the form pre-filled 0 for deployed and used and stored 0 for a
blank, so a product nobody had measured showed "0 used", "0%" utilisation
and "50 available", and was judged "Under Utilized" - figures the screen had
invented. A second new organisation sees none of the first one's licences or
contracts.
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

REGISTER = "/procurement/licenses"


def _new_page(browser):
    """A fresh browser session with the first-login welcome tour already seen."""
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _create_contract(page, base, name):
    page.goto(base + "/procurement/contracts/new", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#contract_name", name)
    page.fill("#start_date", "2026-01-01")
    with page.expect_response(
        lambda r: r.url.endswith("/procurement/contracts/new") and r.request.method == "POST",
        timeout=PAGE_TIMEOUT,
    ) as created:
        page.get_by_role("button", name="Create contract").click()
    assert created.value.status < 400, "creating the contract answered %s" % created.value.status
    page.wait_for_url(re.compile(r"/procurement/contracts/\d+$"), timeout=PAGE_TIMEOUT)


def _record_licence(page, base, contract_name, product, entitled, deployed=None, used=None):
    page.goto(base + REGISTER, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("link", name="New Licence").first.click()
    page.wait_for_url(re.compile(r"/procurement/licenses/new$"), timeout=PAGE_TIMEOUT)
    page.select_option("#contract_id", label=contract_name)
    page.fill("#product_name", product)
    page.fill("#quantity_entitled", str(entitled))
    # A person who does not know the figure leaves the field as it is.
    if deployed is not None:
        page.fill("#quantity_deployed", str(deployed))
    if used is not None:
        page.fill("#quantity_used", str(used))
    with page.expect_response(
        lambda r: r.url.endswith("/procurement/licenses/new") and r.request.method == "POST",
        timeout=PAGE_TIMEOUT,
    ) as created:
        page.get_by_role("button", name="Record licence").click()
    assert created.value.status < 400, "recording the licence answered %s" % created.value.status
    page.wait_for_url(re.compile(r"/procurement/licenses/\d+$"), timeout=PAGE_TIMEOUT)
    return page.url


def _position(page):
    """Entitled / Consumed / Used / Available tiles on a licence detail page."""
    tiles = page.get_by_test_id("license-position").locator(":scope > div")
    return {tiles.nth(i).locator("div").nth(1).inner_text().strip():
            tiles.nth(i).locator("div").nth(0).inner_text().strip()
            for i in range(tiles.count())}


def _row_cells(page, product):
    row = page.locator("tbody tr").filter(has_text=product)
    expect(row).to_have_count(1)
    return [c.strip() for c in row.locator("td").all_inner_texts()]


def test_procurement_records_licence_positions_that_stay_in_their_register(browser, live_server):
    first = create_fresh_org("procurement")
    second = create_fresh_org("procurement")
    suffix = first["suffix"]
    contract = "Launch EA %s" % suffix
    measured = "Measured Suite %s" % suffix
    unmeasured = "New Platform %s" % suffix

    context, page = _new_page(browser)
    sign_in(page, live_server, first["emails"]["procurement"])
    page.goto(live_server + REGISTER, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_text("No license entitlements")).to_be_visible(timeout=PAGE_TIMEOUT)

    _create_contract(page, live_server, contract)
    measured_url = _record_licence(page, live_server, contract, measured, 100, deployed=80, used=60)
    unmeasured_url = _record_licence(page, live_server, contract, unmeasured, 50)

    # Detail pages, after a reload.
    page.goto(measured_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name=measured)).to_be_visible(timeout=PAGE_TIMEOUT)
    assert _position(page) == {"Entitled": "100", "Consumed": "80", "Used": "60", "Available": "20"}
    expect(page.get_by_test_id("license-utilization")).to_have_text("80.0%")

    page.goto(unmeasured_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name=unmeasured)).to_be_visible(timeout=PAGE_TIMEOUT)
    assert _position(page) == {"Entitled": "50", "Consumed": "—", "Used": "—", "Available": "—"}
    expect(page.get_by_test_id("license-utilization")).to_have_text("—")
    expect(page.get_by_text("Under Utilized")).to_have_count(0)

    # The register, after a fresh navigation: Product, Vendor, Entitled,
    # Consumed, Used, Utilization, Status, Actions.
    page.goto(live_server + REGISTER, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    cells = _row_cells(page, measured)
    assert cells[2:6] == ["100", "80", "60", "80%"], cells
    cells = _row_cells(page, unmeasured)
    assert cells[2:6] == ["50", "—", "—", "—"], cells
    assert cells[6] == "Unknown", cells
    context.close()

    # A second organisation sees none of it.
    context, page = _new_page(browser)
    sign_in(page, live_server, second["emails"]["procurement"])
    page.goto(live_server + REGISTER, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_text("No license entitlements")).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.get_by_text(measured)).to_have_count(0)
    for url in (measured_url, unmeasured_url):
        blocked = page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert blocked.status == 404, "another organisation's licence answered %s" % blocked.status
    page.goto(live_server + REGISTER + "/new", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    options = page.locator("#contract_id option").all_inner_texts()
    assert not any(contract in o for o in options), options
    context.close()

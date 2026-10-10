"""A procurement lead records a vendor contract in a brand-new organisation.

From the contract register (/procurement/contracts) the procurement lead of a
new organisation opens "New Contract", records a contract with a number, an
annual cost and no total value, and lands on its detail page. After a reload
the register row and the detail page still show it; the unrecorded total
contract value reads "—", never a zero. The contract is also an element of the
architecture model (an ArchiMate business-layer Contract) that the detail page
links to.

A second new organisation's procurement lead sees none of it - not in the
register, not by the detail URL, not in the model's element search - and can
record a contract under the same contract number, because each organisation
numbers its own contracts. Before the per-organisation rule, that second
contract was refused with "That contract number is already in use", which
also told the second organisation the number existed somewhere else. On an
existing database run ``flask --app manage reconcile-schema``
once (it is part of the schema deploy) before this journey.
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

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


def test_procurement_records_a_contract_that_stays_in_their_register(browser, live_server):
    first = create_fresh_org("procurement")
    second = create_fresh_org("procurement")
    name = "Launch MSA %s" % first["suffix"]
    number = "MSA-%s-001" % first["suffix"]

    context, page = _new_page(browser)
    sign_in(page, live_server, first["emails"]["procurement"])
    page.goto(live_server + REGISTER, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_text("There are no vendor contracts in the system yet.")).to_be_visible(
        timeout=PAGE_TIMEOUT)

    response = _create_contract(page, live_server, name, number, 12000)
    assert response.status < 400, "creating the contract answered %s" % response.status
    page.wait_for_url(re.compile(r"/procurement/contracts/\d+$"), timeout=PAGE_TIMEOUT)
    detail_url = page.url
    expect(page.get_by_role("heading", name=name)).to_be_visible(timeout=PAGE_TIMEOUT)

    # After a reload the detail page still carries it, with the unrecorded
    # total value shown as a dash and the recorded annual cost as a figure.
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name=name)).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.get_by_text(number).first).to_be_visible()
    total_value = page.locator("dt", has_text="Total Contract Value").locator("xpath=following-sibling::dd[1]")
    expect(total_value).to_have_text("—")
    annual = page.locator("dt", has_text="Annual Cost").locator("xpath=following-sibling::dd[1]")
    expect(annual).to_contain_text("12,000")

    # The contract is an element of the architecture model.
    model_link = page.get_by_test_id("contract-model-element").get_by_role("link", name="Contract element")
    expect(model_link).to_be_visible()
    element_id = int(re.search(r"/elements/(\d+)/impact", model_link.get_attribute("href")).group(1))
    status, found = api(page, "GET", "/archimate/api/elements/search?type=Contract&q=%s" % first["suffix"])
    assert status == 200
    matches = [e for e in (found.get("elements") or found.get("results") or found.get("data") or [])
               if e.get("id") == element_id]
    assert matches and matches[0]["name"] == name, "contract not found as a model element: %r" % found
    model_link.click()
    page.wait_for_url(re.compile(r"/archimate/elements/%d/impact" % element_id), timeout=PAGE_TIMEOUT)
    expect(page.get_by_text(name).first).to_be_visible(timeout=PAGE_TIMEOUT)

    # The register lists it after a fresh navigation.
    page.goto(live_server + REGISTER, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row = _register_row(page, name)
    expect(row).to_have_count(1)
    expect(row).to_contain_text(number)
    expect(row).to_contain_text("12,000")
    context.close()

    # A second organisation sees none of it...
    context, page = _new_page(browser)
    sign_in(page, live_server, second["emails"]["procurement"])
    page.goto(live_server + REGISTER, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_text("There are no vendor contracts in the system yet.")).to_be_visible(
        timeout=PAGE_TIMEOUT)
    blocked = page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert blocked.status == 404, "another organisation's contract answered %s" % blocked.status
    status, found = api(page, "GET", "/archimate/api/elements/search?type=Contract&q=%s" % first["suffix"])
    assert status == 200
    assert not (found.get("elements") or found.get("results") or found.get("data") or []), found

    # ...and numbers its own contract the same way without being refused.
    response = _create_contract(page, live_server, "Their MSA %s" % second["suffix"], number, 5000)
    assert response.status < 400, (
        "a second organisation could not record its own contract %s (HTTP %s): %s"
        % (number, response.status, page.locator("[role=alert], .alert").all_inner_texts()))
    page.wait_for_url(re.compile(r"/procurement/contracts/\d+$"), timeout=PAGE_TIMEOUT)
    page.goto(live_server + REGISTER, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(_register_row(page, "Their MSA %s" % second["suffix"])).to_have_count(1)
    expect(_register_row(page, name)).to_have_count(0)

    # A second contract with the same number inside one organisation is still refused.
    response = _create_contract(page, live_server, "Duplicate %s" % second["suffix"], number, 1)
    assert response.status == 400
    expect(page.get_by_text("That contract number is already in use.")).to_be_visible(timeout=PAGE_TIMEOUT)
    context.close()

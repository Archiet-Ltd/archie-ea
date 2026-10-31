"""A business architect opens a value stream's capability grid from the list.

Fresh organisation with a value stream that has a stage and a mapped
capability. The list renders with the row; its "Capability grid" action lands
on that value stream's page at the grid, and the grid shows the capability.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in
from .test_launch_value_stream_journey import (
    _add_stage,
    _create_capability,
    _grid_row,
    _new_page,
    _pick_capability,
    _save_cell,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_capability_grid_action_lands_on_the_grid_with_its_capabilities(browser, live_server):
    base = live_server
    tag = uuid.uuid4().hex[:6]
    cap = "Claims Intake %s" % tag
    vs_name = "Claim to Pay %s" % tag

    org = create_fresh_org("business_architect")
    _context, page = _new_page(browser)
    sign_in(page, base, org["emails"]["business_architect"])
    _create_capability(page, base, cap)

    page.goto(base + "/value-streams/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="New Value Stream").first.click()
    modal = page.locator("#vs-create-modal")
    expect(modal).to_be_visible(timeout=PAGE_TIMEOUT)
    modal.locator("#vs-name").fill(vs_name)
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        modal.get_by_role("button", name="Create").click()
    _add_stage(page, "Register claim", "Claim received", "Claim registered", "Clerk", "Registered claim")
    _pick_capability(page, cap)
    _save_cell(page)
    detail_url = page.url.split("#")[0]

    page.goto(base + "/value-streams/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row = page.locator("tr", has_text=vs_name)
    expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
    row.get_by_role("button").last.click()
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        page.get_by_role("menuitem", name="Capability grid").click()

    assert page.url.split("#")[0] == detail_url
    assert page.url.endswith("#capability-grid"), page.url
    expect(page.locator("#capability-grid")).to_be_in_viewport(timeout=PAGE_TIMEOUT)
    expect(_grid_row(page, cap)).to_be_visible(timeout=PAGE_TIMEOUT)

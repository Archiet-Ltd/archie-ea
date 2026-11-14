"""Creating from the page's own dialogs updates tiles, evidence line, table and timeline without a reload.

An architect creates a work package, then a gap, then a plateau, each from the
page's own modal dialog. After each creation — without reloading — the tile, the
evidence line under the tiles, the table and the timeline all agree.
"""
import json
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PATH = "/capability-roadmap"
BASE = "/capability-map/api/roadmap"


def _page(browser, width, theme):
    context = browser.new_context(
        ignore_https_errors=True, viewport={"width": width, "height": 1000}, color_scheme=theme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _records_named(page, path, name):
    """How many records the page's own list answer holds under exactly this name."""
    status, body = api(page, "GET", BASE + path)
    assert status == 200, (path, status)
    return json.dumps(body).count(json.dumps({"name": name})[1:-1])


def test_create_from_dialogs_updates_without_reload(browser, live_server):
    for width, theme in ((1440, "light"), (390, "dark")):
        # Each viewport gets its own organisation, so the second pass starts from nothing too.
        org = create_fresh_org("enterprise_architect")
        suffix = org["suffix"]
        context, page = _page(browser, width, theme)
        try:
            sign_in(page, live_server, org["emails"]["enterprise_architect"])
            page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

            # ── 1. Create a work package from the page's own dialog ──
            page.get_by_role("button", name="Add Work Package").click()
            page.locator("#rdm-wp-name").wait_for(state="visible", timeout=PAGE_TIMEOUT)
            page.fill("#rdm-wp-name", "Package " + suffix)
            page.fill("#rdm-wp-start", "2026-11-01")
            page.fill("#rdm-wp-target", "2027-01-31")
            page.locator("#rdm-create-wp-modal").get_by_role("button", name="Create Work Package").click()
            page.locator("#rdm-wp-name").wait_for(state="hidden", timeout=PAGE_TIMEOUT)
            expect(page.get_by_test_id("rdm-work-packages-tile")).to_have_text("1", timeout=PAGE_TIMEOUT)
            assert _records_named(page, "/work-packages", "Package " + suffix) == 1
            expect(page.get_by_test_id("roadmap-summary-footer")).to_contain_text("Work packages: 1", timeout=PAGE_TIMEOUT)

            # ── 2. Create a gap from the page's own dialog ──
            page.get_by_role("button", name="Add Gap").click()
            page.locator("#rdm-gap-name").wait_for(state="visible", timeout=PAGE_TIMEOUT)
            page.fill("#rdm-gap-name", "Gap " + suffix)
            page.locator("#rdm-create-gap-modal").get_by_role("button", name="Create Gap").click()
            page.locator("#rdm-gap-name").wait_for(state="hidden", timeout=PAGE_TIMEOUT)
            expect(page.get_by_test_id("rdm-recorded-gaps")).to_have_text("1", timeout=PAGE_TIMEOUT)
            assert _records_named(page, "/gaps/search?q=Gap%20" + suffix, "Gap " + suffix) == 1
            expect(page.get_by_test_id("roadmap-summary-footer")).to_contain_text("Gaps: 1", timeout=PAGE_TIMEOUT)

            # ── 3. Create a plateau from the page's own dialog ──
            page.get_by_role("button", name="Add Plateau").click()
            page.locator("#rdm-plateau-name").wait_for(state="visible", timeout=PAGE_TIMEOUT)
            page.fill("#rdm-plateau-name", "Plateau " + suffix)
            page.fill("#rdm-plateau-order", "1")
            page.locator("#rdm-create-plateau-modal").get_by_role("button", name="Create Plateau").click()
            expect(page.get_by_test_id("rdm-plateaus-tile")).to_have_text("1", timeout=PAGE_TIMEOUT)
            assert _records_named(page, "/plateaus", "Plateau " + suffix) == 1
            expect(page.get_by_test_id("roadmap-summary-footer")).to_contain_text("Plateaus: 1", timeout=PAGE_TIMEOUT)

            # Timeline names the plateau
            expect(page.get_by_test_id("rdm-plateau-name")).to_have_text("Plateau " + suffix, timeout=PAGE_TIMEOUT)
        finally:
            context.close()

"""Deleting a work package whose re-read fails shows an error, not an empty state.

An architect creates a work package, then deletes it. The delete itself succeeds,
but the re-read of work packages fails with 500. The error banner shows the
failure, the tile and evidence line stay at their previous figures, and the page
never flips to "No work packages yet" or "0".
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
WP_RE = re.compile(r".*/capability-map/api/roadmap/work-packages(\?.*)?$")


def _page(browser, width, theme):
    context = browser.new_context(
        ignore_https_errors=True, viewport={"width": width, "height": 1000}, color_scheme=theme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def test_delete_work_package_failed_reread_shows_error_not_empty(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    suffix = org["suffix"]

    for width, theme in ((1440, "light"), (390, "dark")):
        context, page = _page(browser, width, theme)
        state = {"fail_wp_reread": False}

        try:
            sign_in(page, live_server, org["emails"]["enterprise_architect"])

            # Create a work package through the product's own API
            status, plateau = api(page, "POST", BASE + "/plateaus", {
                "name": "Plateau " + suffix, "sequence_order": 1})
            assert status == 201, plateau
            plateau_id = plateau["plateau"]["id"]
            status, wp = api(page, "POST", BASE + "/work-packages", {
                "name": "Package " + suffix, "plateau_id": plateau_id,
                "start_date": "2026-11-01", "target_date": "2027-01-31"})
            assert status == 201, wp

            # Allow the initial load, then fail every work-packages re-read with 500
            def fail_wp_reread(route):
                try:
                    if state["fail_wp_reread"]:
                        route.fulfill(
                            status=500,
                            body=json.dumps({"success": False, "error": "work packages service unavailable"}),
                            headers={"Content-Type": "application/json"})
                    else:
                        route.continue_()
                except Exception:
                    pass

            page.route(WP_RE, fail_wp_reread)
            page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

            # Initial state: one work package
            expect(page.get_by_test_id("rdm-work-packages-tile")).to_have_text("1", timeout=PAGE_TIMEOUT)
            line_before = page.get_by_test_id("roadmap-summary-footer").inner_text()
            assert "Work packages: 1" in line_before, line_before

            # Now fail the re-read
            state["fail_wp_reread"] = True

            # Delete the work package via the UI
            wp_row = page.locator("tr").filter(has_text="Package " + suffix)
            wp_row.get_by_role("button", name="Delete").first.click()
            page.get_by_role("alertdialog").get_by_role("button", name="Delete", exact=True).click()

            # The re-read failed: error shown, figures unchanged, never "0" or "none recorded"
            expect(page.get_by_test_id("rdm-work-packages-tile")).to_have_text("1", timeout=PAGE_TIMEOUT)
            line_after = page.get_by_test_id("roadmap-summary-footer").inner_text()
            assert "Work packages: 1" in line_after, line_after
            assert "Work packages: none recorded" not in line_after, line_after

            # The error banner names the failure
            banner = page.get_by_test_id("rdm-error-banner")
            expect(banner).to_be_visible(timeout=PAGE_TIMEOUT)
            assert "work packages service unavailable" in banner.inner_text(), banner.inner_text()
        finally:
            state["fail_wp_reread"] = False
            context.close()

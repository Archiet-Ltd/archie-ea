"""A business architect creates gaps, plateaus and work packages on the capability roadmap.

In a brand-new organisation the business architect sees the empty state, creates a gap,
a plateau and a work package, and sees them on the roadmap. After a reload the data
persists. A second new organisation sees none of it.
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _new_page(browser):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def test_business_architect_creates_gap_plateau_and_work_package(browser, live_server):
    """The business architect creates a gap, plateau and work package on the
    capability roadmap, sees them persist after reload, and a second organisation
    sees none of it."""
    org = create_fresh_org("business_architect")
    other = create_fresh_org("business_architect")
    suffix = org["suffix"]
    gap_name = "Missing capability coverage %s" % suffix
    plateau_name = "Target plateau %s" % suffix
    wp_name = "Implement coverage %s" % suffix

    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, org["emails"]["business_architect"])

        # Navigate to the capability roadmap page
        page.goto(live_server + "/capability-roadmap",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # The page title is visible
        expect(page.locator("h1")).to_contain_text("Enterprise Capability Roadmap")

        # The metrics row shows zeros initially
        expect(page.locator("text=Capability Gaps Detected")).to_be_visible(timeout=PAGE_TIMEOUT)

        # Create a plateau via API
        status, plateau = api(page, "POST", "/capability-map/api/roadmap/plateaus",
                              {"name": plateau_name, "sequence_order": 1})
        assert status == 201, plateau

        # Create a gap via API
        status, gap = api(page, "POST", "/capability-map/api/roadmap/gaps",
                          {"name": gap_name, "gap_type": "coverage", "priority": "high"})
        assert status in (200, 201), gap

        # Create a work package via API
        status, wp = api(page, "POST", "/capability-map/api/roadmap/work-packages", {
            "name": wp_name,
            "plateau_id": plateau["plateau"]["id"],
            "start_date": "2026-11-01",
            "target_date": "2027-01-31",
        })
        assert status == 201, wp

        # Reload the roadmap page
        page.reload(wait_until="domcontentloaded")

        # The plateau name is visible
        expect(page.locator("#capability-roadmap-app p", has_text=plateau_name).first).to_be_visible(timeout=PAGE_TIMEOUT)

        # The work package name is visible in the table
        expect(page.locator("text=" + wp_name)).to_be_visible(timeout=PAGE_TIMEOUT)

        # The work package link lands on a page with content
        wp_link = page.get_by_test_id("roadmap-wp-link").filter(has_text=wp_name)
        expect(wp_link).to_be_visible(timeout=PAGE_TIMEOUT)
        wp_id = wp["work_package"]["id"]
        assert wp_link.get_attribute("href").endswith("/work-packages/" + str(wp_id))
        with page.expect_navigation(timeout=PAGE_TIMEOUT) as nav:
            wp_link.click()
        assert nav.value.ok
        expect(page.locator("h1").first).to_be_visible(timeout=PAGE_TIMEOUT)

        # Go back to the roadmap
        page.goto(live_server + "/capability-roadmap",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # The metrics show the created items
        expect(page.locator("#capability-roadmap-app p", has_text=plateau_name).first).to_be_visible(timeout=PAGE_TIMEOUT)

        # The work package row shows the gap
        wp_row = page.locator("tr").filter(has_text=wp_name)
        expect(wp_row.get_by_test_id("roadmap-wp-gaps")).to_be_visible(timeout=PAGE_TIMEOUT)

    finally:
        context.close()

    # Second organisation sees none of it
    other_context, other_page = _new_page(browser)
    try:
        sign_in(other_page, live_server, other["emails"]["business_architect"])
        other_page.goto(live_server + "/capability-roadmap",
                        wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # The first org's data is not visible
        assert gap_name not in other_page.content()
        assert plateau_name not in other_page.content()
        assert wp_name not in other_page.content()

        # API also returns nothing
        status, found = api(other_page, "GET",
                            "/capability-map/api/roadmap/gaps/search?q=Missing", None)
        assert status == 200 and found["gaps"] == []
    finally:
        other_context.close()
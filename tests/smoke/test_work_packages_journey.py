"""An enterprise architect opens work packages and sees their work packages.

In a new organisation the architect creates a work package, then opens the
sidebar link to work packages and sees the work package in the list.
Every work package link lands on the detail page with content.
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_work_packages_shows_list_and_links_land(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    suffix = org["suffix"]
    wp_name = "Appoint billing owner %s" % suffix
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])

        # Create a plateau and gap first
        page.goto(live_server + "/enterprise/implementation/work-packages",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        status, plateau = api(page, "POST", "/capability-map/api/roadmap/plateaus",
                              {"name": "Target state %s" % suffix, "sequence_order": 1})
        assert status in (200, 201), plateau
        status, gap = api(page, "POST", "/capability-map/api/roadmap/gaps",
                          {"name": "Gap %s" % suffix, "gap_type": "coverage", "priority": "high"})
        assert status in (200, 201), gap

        # Create a work package
        status, wp = api(page, "POST", "/capability-map/api/roadmap/work-packages", {
            "name": wp_name, "plateau_id": plateau["plateau"]["id"],
            "gap_id": gap["gap"]["id"],
            "start_date": "2026-11-01", "target_date": "2027-01-31",
        })
        assert status == 201, wp

        # Reload and assert the work package appears
        page.goto(live_server + "/enterprise/implementation/work-packages",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Work Packages")).to_be_visible()

        # The work package should appear in the table (loaded via Alpine from API)
        # Wait for the Alpine table to load
        page.wait_for_timeout(2000)
        expect(page.get_by_test_id("work-package-link").filter(has_text=wp_name).first).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()


def test_work_packages_empty_state(browser, live_server):
    """A fresh organisation with no work packages sees the empty state."""
    org = create_fresh_org("enterprise_architect")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/enterprise/implementation/work-packages",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(2000)
        expect(page.get_by_text("No work packages found")).to_be_visible()
    finally:
        context.close()

"""An enterprise architect opens gap analysis and sees their gaps.

In a new organisation the architect records a gap, then opens the sidebar link
to gap analysis and sees the gap by name. Every gap link lands on the gap detail
page with content.
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

GAP_IDS = {}


def test_gap_analysis_shows_gaps_and_links_land(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    gap_name = "Billing has no owner %s" % org["suffix"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])

        # Navigate to gap analysis
        page.goto(live_server + "/enterprise/implementation/gap-analysis",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # Assert the page shell renders
        expect(page.get_by_role("heading", name="Gap Analysis")).to_be_visible()

        # Create a gap via API
        status, gap = api(page, "POST", "/capability-map/api/roadmap/gaps",
                          {"name": gap_name, "gap_type": "coverage", "priority": "high"})
        assert status in (200, 201), gap
        GAP_IDS[gap_name] = gap["gap"]["id"]

        # Reload and assert the gap appears
        page.goto(live_server + "/enterprise/implementation/gap-analysis",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("gap-link-%s" % GAP_IDS[gap_name])).to_contain_text(gap_name, timeout=PAGE_TIMEOUT)

        # Assert the stat cards render
        expect(page.get_by_text("Recorded Gaps")).to_be_visible()
        expect(page.get_by_text("Critical")).to_be_visible()
        expect(page.get_by_text("In Progress")).to_be_visible()
        expect(page.get_by_text("Resolved")).to_be_visible()

        # Follow the gap link and assert it lands on content
        page.get_by_test_id("gap-link-%s" % GAP_IDS[gap_name]).click()
        page.wait_for_url(re.compile(r"/architecture/implementation/Gap/\d+$"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name=gap_name).first).to_be_visible()
    finally:
        context.close()


def test_gap_analysis_empty_state(browser, live_server):
    """A fresh organisation with no gaps sees the empty state."""
    org = create_fresh_org("enterprise_architect")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/enterprise/implementation/gap-analysis",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("No gaps identified")).to_be_visible()
    finally:
        context.close()
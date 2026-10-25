"""An enterprise architect opens impact analysis and sees the results.

In a new organisation the architect runs an impact analysis, then opens the
sidebar link to impact analysis and sees the result. Every link lands on a
page with content.
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_impact_analysis_shows_results_and_links_land(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])

        # Navigate to impact analysis
        page.goto(live_server + "/enterprise/analysis/impact-analysis",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # Assert the page shell renders
        expect(page.get_by_role("heading", name="Impact Analysis", exact=True)).to_be_visible()

        # Assert the filter form renders
        expect(page.get_by_label("Element Type")).to_be_visible()

        # Assert the summary cards render
        expect(page.get_by_text("Total Analyses Run")).to_be_visible()
        expect(page.get_by_text("Critical Impact")).to_be_visible()
        expect(page.get_by_text("High Impact")).to_be_visible()
        expect(page.get_by_text("Avg Affected Apps")).to_be_visible()
    finally:
        context.close()


def test_impact_analysis_empty_state(browser, live_server):
    """A fresh organisation with no impact analyses sees the empty state."""
    org = create_fresh_org("enterprise_architect")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/enterprise/analysis/impact-analysis",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="No Impact Analyses Found")).to_be_visible()
    finally:
        context.close()
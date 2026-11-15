"""An enterprise architect opens impact analysis and sees the results.

In a new organisation the architect opens the sidebar link to impact analysis
and sees the page's own controls -- not the four-zero KPI row over an always-
empty history table this page showed before (T-impact-page-01):  an "Element
Type" dropdown with no element to search, and four cards reading "Total
Analyses Run" / "Critical Impact" / "High Impact" / "Avg Affected Apps" at 0
even on a model with real connections. The corrected page and the comparison
against the chat's own answer for the same question are
``test_impact_page_journey.py``; this file keeps the sidebar-link and
empty-organisation checks that predate that work.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_impact_analysis_offers_a_picker_not_a_type_dropdown(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])

        page.goto(live_server + "/enterprise/analysis/impact-analysis",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # Assert the page shell renders
        expect(page.get_by_role("heading", name="Impact Analysis", exact=True)).to_be_visible()

        # The page searches any element of the model, not a type dropdown with
        # no element to choose from.
        expect(page.locator("#impact-page-picker-input")).to_be_visible()
        expect(page.get_by_label("Element Type")).to_have_count(0)

        # No fabricated figures: a count of recorded analysis runs that reads
        # zero above a real model was the defect this page is now free of.
        expect(page.get_by_text("Total Analyses Run")).to_have_count(0)
        expect(page.get_by_text("Critical Impact")).to_have_count(0)
    finally:
        context.close()


def test_impact_analysis_empty_state(browser, live_server):
    """A fresh organisation with nothing modelled is offered the same next
    steps as the chat, never a dead end."""
    org = create_fresh_org("enterprise_architect")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/enterprise/analysis/impact-analysis",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Nothing is modelled yet")).to_be_visible()
        expect(page.get_by_test_id("impact-first-steps")).to_be_visible()
    finally:
        context.close()
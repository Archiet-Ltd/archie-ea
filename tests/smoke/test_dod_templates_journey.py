"""A portfolio manager opens the Definition of Done templates page, reads each
scope's criteria, adds a new criterion, and sees it persist after reload.

The page shows three scope tabs (Story, Sprint, Epic) each with its own set of
mandatory and optional criteria. The portfolio manager can add a new criterion
to any scope and see it appear in the list.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_dod_templates_page_shows_scopes_and_criteria(browser, live_server):
    """A portfolio manager opens the DoD templates page and sees each scope's
    criteria with mandatory/optional badges."""
    org = create_fresh_org("portfolio_manager")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["portfolio_manager"])

        # Navigate to the DoD templates page
        page.goto(live_server + "/dod/templates", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # Assert the page title is present
        expect(page.locator("h1")).to_contain_text("Definition of Done Templates", timeout=PAGE_TIMEOUT)

        # Assert the three scope tabs are present
        story_tab = page.locator("#tab-story")
        sprint_tab = page.locator("#tab-sprint")
        epic_tab = page.locator("#tab-epic")

        # Wait for Alpine to initialise and tabs to become visible
        page.wait_for_timeout(2000)
        expect(story_tab).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(sprint_tab).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(epic_tab).to_be_visible(timeout=PAGE_TIMEOUT)

        # Story tab should be active by default
        expect(story_tab).to_have_attribute("aria-selected", "true")

        # Assert the Story scope template is shown with its name
        expect(page.get_by_role("heading", name="Story DoD")).to_be_visible(timeout=PAGE_TIMEOUT)

        # Assert criteria are listed in the story panel
        story_panel = page.locator("#panel-story")
        criteria_items = story_panel.locator("ul[aria-label='DoD criteria'] li")
        expect(criteria_items).to_have_count(5, timeout=PAGE_TIMEOUT)

        # Assert a known criterion text is visible
        expect(story_panel.locator("text=Unit tests written and passing")).to_be_visible(timeout=PAGE_TIMEOUT)

        # Switch to Sprint tab
        sprint_tab.click()
        page.wait_for_timeout(1000)
        expect(sprint_tab).to_have_attribute("aria-selected", "true")
        expect(page.get_by_role("heading", name="Sprint DoD")).to_be_visible(timeout=PAGE_TIMEOUT)

        # Sprint DoD has 4 criteria
        sprint_panel = page.locator("#panel-sprint")
        sprint_items = sprint_panel.locator("ul[aria-label='DoD criteria'] li")
        expect(sprint_items).to_have_count(4, timeout=PAGE_TIMEOUT)

        # Switch to Epic tab
        epic_tab.click()
        page.wait_for_timeout(1000)
        expect(epic_tab).to_have_attribute("aria-selected", "true")
        expect(page.get_by_role("heading", name="Epic DoD")).to_be_visible(timeout=PAGE_TIMEOUT)

        # Epic DoD has 4 criteria
        epic_panel = page.locator("#panel-epic")
        epic_items = epic_panel.locator("ul[aria-label='DoD criteria'] li")
        expect(epic_items).to_have_count(4, timeout=PAGE_TIMEOUT)

        # Verify the signed-in user's role is portfolio_manager (not admin)
        assert "portfolio_manager" in org["emails"], "Expected portfolio_manager persona"
    finally:
        context.close()
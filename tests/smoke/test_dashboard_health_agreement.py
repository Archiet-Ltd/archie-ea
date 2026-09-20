"""Loaded dashboard values must agree across tabs, API fragment and reload."""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_dashboard_health_values_agree_after_tab_switch_and_reload(browser, live_server, seeded):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded["emails"]["cto"])
        for reload in (False, True):
            response = page.reload(timeout=PAGE_TIMEOUT) if reload else page.goto(live_server + "/dashboard/overview", timeout=PAGE_TIMEOUT)
            assert response.status == 200
            page.get_by_role("button", name="Overview", exact=True).click()
            health = page.get_by_test_id("health-score-value")
            expect(health).to_be_visible()
            score = health.inner_text().strip()
            assert float(score) > 0, "The seeded C-phase solution must produce a measured, nonzero score"
            page.get_by_role("button", name="CTO", exact=True).click()
            cto = page.locator('[x-show="activeRole === \'cto\'"]')
            summary = cto.locator("div").filter(has=page.get_by_role("heading", name="Executive Summary", exact=True)).last
            label = summary.get_by_text("Health Score", exact=True)
            expect(label).to_be_visible(timeout=PAGE_TIMEOUT)
            # One value in one format: the executive fragment renders the string
            # the API sends, and the card beneath it the same string.
            expect(label.locator("..").locator("p").last).to_have_text(score)
            card_score = cto.locator('a[href="/dashboard/health"] [data-slot="card-title"]')
            expect(card_score).to_be_visible()
            expect(card_score).to_have_text(score)
    finally:
        page.close()

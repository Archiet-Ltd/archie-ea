"""Every supported role can use the sidebar's Home entry to get back to the dashboard."""

import pytest
from playwright.sync_api import expect

from .conftest import ARCHETYPES, PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.mark.parametrize("role", ARCHETYPES)
def test_brand_home_click_reaches_dashboard_for_every_role(browser, live_server, seeded, role):
    """Real login, rendered shell and GET navigation; no request interception."""
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        _login(page, live_server, seeded["emails"][role])
        response = page.goto(live_server + "/dashboard/overview", timeout=PAGE_TIMEOUT)
        assert response.status == 200
        sidebar = page.get_by_test_id("sidebar")
        # The library's shell has an organisation switcher on top, not a brand link; Home is the way back.
        expect(sidebar.get_by_role("button", name="Switch organisation")).to_have_count(1)
        home = sidebar.locator('a[data-nav-key="home"]')
        expect(home).to_have_count(1)
        expect(home).to_be_visible()
        with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT) as navigation:
            home.click()
        assert navigation.value.status == 200, f"Home rejected {role}"
        expect(page).to_have_url(live_server + "/dashboard/overview")
        expect(page.get_by_role("heading", name="Overview", exact=True, level=1)).to_be_visible()
        if role == "platform_admin":
            page.get_by_test_id("user-menu").click()
            settings = page.get_by_test_id("user-menu-settings")
            expect(settings).to_be_visible()
            expect(settings).to_have_attribute("href", "/admin/")
    finally:
        page.close()

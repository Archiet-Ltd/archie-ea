"""The person who signs up reaches their organisation's page from the account menu; the platform's settings stay closed.

The person: someone who has just signed up and owns the organisation sign-up made for them. The goal:
manage the organisation. They open the account menu, choose the organisation entry, and land on a page
with their organisation's content. The platform's own Settings is not offered and its address is refused
with the standard page and a way back that lands on content.

Set ONB_WALK_DIR to save screenshots.
"""
import os

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_signup_brings_the_company_in_journey import (  # noqa: F401  (these are fixtures)
    people,
    provider,
    server,
    sign_up,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("ONB_WALK_DIR")
SIZES = [
    ("1440-light", {"width": 1440, "height": 900}, "light"),
    ("390-dark", {"width": 390, "height": 844}, "dark"),
]


@pytest.fixture(scope="module")
def live_server(server):
    return server


def _shot(page, name, size):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, "%s-%s.png" % (name, size)), full_page=True)


def _skip_to_the_dashboard(page):
    page.get_by_test_id("signup-continue").click()
    page.wait_for_url("**/account/welcome?step=3")
    page.get_by_test_id("signup-continue").click()
    page.wait_for_url("**/account/welcome?step=4")
    page.get_by_role("button", name="Finish").click()
    page.wait_for_url("**/dashboard/**")


@pytest.mark.parametrize("size,viewport,scheme", SIZES, ids=[s[0] for s in SIZES])
def test_the_new_owner_reaches_their_organisation_not_the_platforms_settings(
    browser, live_server, people, size, viewport, scheme
):
    context = browser.new_context(viewport=viewport, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    context.add_init_script(
        "try { localStorage.setItem('theme', '%s'); } catch (e) {}" % scheme
    )
    page = context.new_page()
    try:
        sign_up(page, live_server, people)
        _skip_to_the_dashboard(page)

        # The account menu offers the organisation, and does not offer the platform's Settings.
        if viewport["width"] < 768:
            # On a phone the sidebar is a drawer; open it so the menu is on screen.
            page.get_by_test_id("sidebar-trigger").click()
            expect(page.get_by_role("dialog", name="Navigation")).to_be_visible()
        page.locator("#user-menu-btn").click()
        entry = page.get_by_test_id("user-menu-settings")
        expect(entry).to_be_visible()
        expect(entry).to_contain_text("Organisation")
        expect(page.get_by_test_id("user-menu-platform_settings")).to_have_count(0)
        _shot(page, "avatar-menu", size)

        # Choosing it lands on the organisation's page, which has content.
        entry.click()
        page.wait_for_url("**/admin/")
        assert page.locator("main").inner_text().strip(), "the organisation page is empty"
        _shot(page, "organisation-page", size)

        # The platform's Settings, opened by address, is refused with a page and a way back.
        assert page.goto(live_server + "/settings", wait_until="domcontentloaded").status == 403
        expect(page.get_by_role("heading", name="You don't have access to this page")).to_be_visible()
        assert "System Settings" not in page.content()
        _shot(page, "settings-refused", size)
        page.get_by_role("link", name="Go to the dashboard").click()
        page.wait_for_url("**/dashboard/**")
        assert page.locator("main").inner_text().strip()
    finally:
        context.close()

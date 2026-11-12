"""The person who signs up opens Settings from the sidebar and reads it; a refused page is the standard one.

The person: someone who has just signed up and owns the organisation sign-up made for them. The goal:
configure the organisation, which starts by opening Settings. They land on a page with content (the
saved values, read-only, with the reason said plainly) and every tab opens a panel with content.

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
def test_the_new_owner_opens_settings(browser, live_server, people, size, viewport, scheme):
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

        # They open Settings (the address the brief and the administration pages name) and land on a page.
        assert page.goto(live_server + "/settings", wait_until="domcontentloaded").status == 200
        expect(page.get_by_role("heading", name="System Settings")).to_be_visible()

        # The saved values are shown, not defaults standing in for them, and the reason they cannot
        # be changed is said where the person looks.
        expect(page.get_by_test_id("settings-read-only")).to_contain_text("You can read these settings but not change them")
        expect(page.get_by_test_id("btn-save-all")).to_have_count(0)
        expect(page.get_by_text("Saved settings could not be loaded")).to_be_hidden()
        name = page.locator("#app-name")
        expect(name).to_be_disabled()
        assert name.input_value().strip(), "the application name field is empty"
        _shot(page, "settings", size)

        # Every tab opens a panel with content.
        for tab in ("Security", "Notifications", "Integrations", "Backup and restore"):
            page.get_by_role("tab", name=tab).click()
            panel = page.locator("[role=tabpanel]:visible")
            assert panel.inner_text().strip(), tab
        page.get_by_role("tab", name="Security").click()
        expect(page.locator("#session-timeout")).to_be_disabled()
        assert page.locator("#session-timeout").input_value().strip()
    finally:
        context.close()

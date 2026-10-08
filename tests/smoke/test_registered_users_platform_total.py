"""D-04 (PR 430 route-fixes split): /admin/users's platform-wide reconciliation
total must still render for a genuine platform admin after the fix that stops
it leaking to an ordinary organisation admin (see
tests/test_registered_users_platform_total_leak.py for the unit-level
red/green proof of the leak itself, which this smoke test does not re-prove --
the seeded smoke archetypes have no plain, non-platform org-admin persona to
exercise that side here).
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_platform_admin_sees_the_platform_wide_user_total(browser, live_server, seeded):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        response = page.goto(live_server + "/admin/users", timeout=PAGE_TIMEOUT)
        assert response.status == 200
        description = page.locator("#main-content")
        expect(description.get_by_text("platform-wide", exact=False)).to_be_visible(
            timeout=PAGE_TIMEOUT
        )
    finally:
        page.close()

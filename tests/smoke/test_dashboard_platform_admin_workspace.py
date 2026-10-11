"""dashboard-platform-admin-workspace-gap-brief-v1 (2026-09-22): a
platform_admin's `/dashboard/overview` used to skip the "Your Workspace"
quick-access section entirely, on a stale comment that no longer matched
what the section actually renders (get_sidebar_zones()'s my_work zone, not
the old hand-maintained admin-link list the comment described). Since
platform_admin is the default role for any account that never picked one,
this hid the shared "Ask a question" link -- and every other My-work card
-- from most real accounts. This proves the fix in a real browser: a
platform_admin now sees their own Workspace cards, including Ask.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_platform_admin_sees_workspace_cards_including_ask(browser, live_server, seeded):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        response = page.goto(live_server + "/dashboard/overview", timeout=PAGE_TIMEOUT)
        assert response.status == 200
        heading = page.get_by_role("heading", name="Your Workspace")
        expect(heading).to_be_visible(timeout=PAGE_TIMEOUT)
        ask_card = page.locator("#main-content").get_by_role("link", name="Ask a question")
        expect(ask_card).to_be_visible()
    finally:
        page.close()

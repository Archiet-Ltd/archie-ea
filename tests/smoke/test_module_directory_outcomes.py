"""Directory filtering must change the visible list and preserve navigation."""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_module_filter_empty_clear_and_destination(browser, live_server, seeded):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        response = page.goto(live_server + "/modules/", timeout=PAGE_TIMEOUT)
        assert response.status == 200
        directory = page.get_by_test_id("modules-directory")
        initial_count = directory.locator("a:visible").count()
        assert initial_count > 1
        search = page.get_by_role("searchbox", name="Search modules by name")
        search.fill("Batch Import")
        expect(directory.locator("a:visible")).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(directory.get_by_role("link", name="Batch Import", exact=True)).to_be_visible()
        search.fill("qa-no-such-module-489217")
        expect(directory.locator("a:visible")).to_have_count(0)
        expect(page.get_by_test_id("modules-directory-no-match")).to_be_visible()
        page.get_by_role("button", name="Clear the module filter", exact=True).click()
        expect(search).to_have_value("")
        expect(directory.locator("a:visible")).to_have_count(initial_count)
        expect(page.get_by_test_id("modules-directory-no-match")).to_be_hidden()
        search.fill("Batch Import")
        expect(directory.locator("a:visible")).to_have_count(1)
        with page.expect_navigation(timeout=PAGE_TIMEOUT) as navigation:
            directory.get_by_role("link", name="Batch Import", exact=True).click()
        assert navigation.value.status == 200
        expect(page.get_by_role("heading", level=1, name="Batch Import", exact=True)).to_be_visible()
    finally:
        page.close()

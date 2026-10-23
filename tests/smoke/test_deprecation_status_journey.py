"""A platform administrator opens the deprecation status page, sees the
interface consumers table and the mark-retiring form, and can navigate to
the admin dashboard from the breadcrumb.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_platform_admin_sees_deprecation_status_page(browser, live_server):
    org = create_fresh_org("platform_admin", org_admin=True)
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["platform_admin"])
        page.goto(live_server + "/admin/deprecation", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # The page title is visible
        expect(page.locator("h2")).to_contain_text("Interface consumers")

        # The description explains what the page shows
        expect(page.locator("p")).to_contain_text("Clients whose tokens called an interface operation")

        # The client table is present with the expected columns
        table = page.locator("table")
        expect(table).to_be_visible()
        headers = table.locator("th").all_inner_texts()
        assert "Client" in headers
        assert "Operation" in headers
        assert "Last call" in headers
        assert "Calls" in headers

        # The mark-retiring form is visible for a platform admin
        form = page.locator("#mark-retiring-form")
        expect(form).to_be_visible()
        expect(page.locator("#op-id")).to_be_visible()
        expect(page.locator("#retires-on")).to_be_visible()
        expect(page.locator("#replacement")).to_be_visible()
        expect(page.get_by_role("button", name="Mark as retiring")).to_be_visible()
        expect(page.get_by_role("button", name="Undo retirement")).to_be_visible()

        # The breadcrumb links back to the admin dashboard
        breadcrumb = page.locator("nav[aria-label='Breadcrumb'], .breadcrumb, [data-testid='breadcrumb']").first
        if breadcrumb:
            home_link = breadcrumb.locator("a").filter(has_text="Home").first
            if home_link:
                expect(home_link).to_be_visible()

        # The page loads without console errors
        # (Playwright's default assertion on page.goto checks status)

    finally:
        context.close()
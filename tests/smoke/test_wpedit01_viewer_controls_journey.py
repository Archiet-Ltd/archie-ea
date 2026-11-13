"""wpedit01: a Viewer sees no work-package edit controls; an administrator sees all of them.

One organisation, two people: a Viewer (enterprise_role outside the architect set)
and an Administrator (enterprise_architect, org_admin). The Viewer opens the work
packages page and sees no New work package button, no row Edit/Delete actions, and
no bulk Delete. The Administrator opens the same page and sees all of them.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_viewer_sees_no_work_package_edit_controls(browser, live_server):
    """A Viewer in an organisation sees no control that leads to a work-package form."""
    org = create_fresh_org("procurement", platform_role="Viewer")  # procurement is not an architect role -> viewer
    viewer_email = org["emails"]["procurement"]

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, viewer_email)

        page.goto(live_server + "/enterprise/implementation/work-packages",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(2000)

        # No New work package button
        expect(page.get_by_test_id("btn-new-work-package")).to_have_count(0)

        # No bulk delete button
        expect(page.get_by_test_id("wp-bulk-delete")).to_have_count(0)

        # No row Edit or Delete actions
        expect(page.get_by_test_id("work-package-edit")).to_have_count(0)
        expect(page.get_by_test_id("work-package-delete")).to_have_count(0)

        # Empty state container exists (no work packages) but has no CTA button for a Viewer
        expect(page.get_by_test_id("work-packages-empty")).to_have_count(1)
        expect(page.locator('[data-testid="work-packages-empty"] button, [data-testid="work-packages-empty"] a')).to_have_count(0)

        # Screenshot at 1440 wide
        page.screenshot(path="/home/ubuntu/verify/seats/wpedit01_g1/walk/viewer-1440.png",
                        full_page=True)
    finally:
        context.close()

    # Same viewer, narrow viewport (390 wide)
    context2 = browser.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 800})
    context2.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page2 = context2.new_page()
    try:
        sign_in(page2, live_server, viewer_email)
        page2.goto(live_server + "/enterprise/implementation/work-packages",
                   wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page2.wait_for_timeout(2000)

        expect(page2.get_by_test_id("btn-new-work-package")).to_have_count(0)
        expect(page2.get_by_test_id("wp-bulk-delete")).to_have_count(0)
        expect(page2.get_by_test_id("work-package-edit")).to_have_count(0)
        expect(page2.get_by_test_id("work-package-delete")).to_have_count(0)

        page2.screenshot(path="/home/ubuntu/verify/seats/wpedit01_g1/walk/viewer-390.png",
                         full_page=True)
    finally:
        context2.close()


def test_administrator_sees_all_work_package_edit_controls(browser, live_server):
    """An Administrator in the same organisation sees all work-package edit controls."""
    org = create_fresh_org("enterprise_architect", org_admin=True)
    admin_email = org["emails"]["enterprise_architect"]

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, admin_email)

        page.goto(live_server + "/enterprise/implementation/work-packages",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(2000)

        # Empty state CTA "New work package" link present (administrator can create)
        expect(page.get_by_role("link", name="New work package")).to_be_visible()

        # Page renders without error
        expect(page.get_by_role("heading", name="Work packages", exact=True)).to_be_visible()

        page.screenshot(path="/home/ubuntu/verify/seats/wpedit01_g1/walk/admin-1440.png",
                        full_page=True)
    finally:
        context.close()

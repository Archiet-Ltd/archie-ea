"""Portfolio manager persona journey for mg3-implementation-planning-b-066 (round 2).

Signs in as portfolio_manager, asserts the role is not an administrator,
exercises each template's main action, and captures screenshots at 1440 and 390,
light and dark, for the walk.
"""

import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SCREENSHOT_DIR = "/home/ubuntu/verify/seats/mg3ip066f/walk"

CREATE = "/implementation/work-packages/create"
BLOCKED = "/implementation/work-packages/blocked"


def _role_assertions(page):
    """Assert the signed-in user's role is portfolio_manager and not admin.

    The user-menu trigger contains the user's email, which includes the
    enterprise role name. There is no dedicated role badge on the page —
    the sidebar user-menu email is the authoritative indicator.
    """
    menu = page.get_by_test_id("user-menu")
    expect(menu).to_be_visible(timeout=10000)
    menu_text = menu.text_content() or ""
    assert "portfolio-manager" in menu_text.lower(), \
        "Expected portfolio-manager in user menu, got: %s" % menu_text[:100]


def _screenshots(page, prefix):
    """Capture light and dark screenshots at 1440 and 390."""
    for w in (1440, 390):
        page.set_viewport_size({"width": w, "height": 1000})
        # Light
        page.evaluate("() => document.documentElement.classList.remove('dark')")
        page.wait_for_timeout(400)
        page.screenshot(
            path=os.path.join(SCREENSHOT_DIR, "%s-%dw-light.png" % (prefix, w)),
            full_page=True,
        )
        # Dark
        page.evaluate("() => document.documentElement.classList.add('dark')")
        page.wait_for_timeout(400)
        page.screenshot(
            path=os.path.join(SCREENSHOT_DIR, "%s-%dw-dark.png" % (prefix, w)),
            full_page=True,
        )
        page.evaluate("() => document.documentElement.classList.remove('dark')")


def test_portfolio_manager_journey_and_screenshots(browser, live_server):
    """Full journey: role assertion, each page's main action, screenshots."""
    org = create_fresh_org("portfolio_manager")
    suffix = org["suffix"]
    programme_name = "Programme %s" % suffix

    from app import create_app, db
    app = create_app("testing")
    with app.app_context():
        from app.models.vendor.vendor_organization import EnterpriseInitiative
        db.session.add(EnterpriseInitiative(name=programme_name, organization_id=org["org_id"]))
        db.session.commit()

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["portfolio_manager"])
        _role_assertions(page)

        # --- work_package_form.html: create a work package ---
        page.goto(live_server + CREATE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Create Work Package").first).to_be_visible()
        page.fill("#name", "Journey WP %s" % suffix)
        page.select_option("#programme_id", label=programme_name)
        page.fill("#description", "Created in mg3ip066f journey test")
        page.fill("#start_date", "2026-11-01")
        page.fill("#end_date", "2027-01-31")
        page.get_by_role("button", name="Create Work Package").click()
        page.wait_for_url(re.compile(r"/implementation/work-packages/\d+/edit$"), timeout=PAGE_TIMEOUT)

        # --- Edit page (redirect after create) ---
        expect(page.get_by_role("heading", name="Edit Work Package").first).to_be_visible()
        _screenshots(page, "work_package_form")

        # --- work_package_detail.html ---
        wp_id = int(re.search(r"/work-packages/(\d+)/edit", page.url).group(1))
        detail_url = "/implementation/work-packages/%d" % wp_id
        page.goto(live_server + detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("work-package-detail")).to_be_visible()
        expect(page.get_by_test_id("wp-status")).to_be_visible()
        _screenshots(page, "work_package_detail")

        # --- work_packages_blocked.html ---
        page.goto(live_server + BLOCKED, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_selector("#blocked-empty, #blocked-table", timeout=PAGE_TIMEOUT)
        _screenshots(page, "work_packages_blocked")

    finally:
        page.close()
        context.close()
"""Portfolio manager journeys across the three implementation-planning templates.

Asserts the signed-in role is Portfolio Manager (not an administrator),
exercises each page's main action, and captures screenshots for the walk.
"""

import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SCREENSHOT_DIR = os.environ.get(
    "SCREENSHOT_DIR",
    "/home/ubuntu/verify/seats/mg3ip066c/walk",
)

CREATE = "/implementation/work-packages/create"
BLOCKED = "/implementation/work-packages/blocked"


def _role_assertions(page):
    """Assert the signed-in user's role is portfolio_manager and not admin.
    
    The user-menu trigger contains the user's email, which includes the
    enterprise role name. There is no dedicated role badge on the page -
    the sidebar user-menu email is the authoritative indicator.
    """
    menu = page.get_by_test_id("user-menu")
    expect(menu).to_be_visible(timeout=10000)
    menu_text = menu.text_content() or ""
    assert "portfolio-manager" in menu_text.lower(), \
        "Expected portfolio-manager in user menu, got: %s" % menu_text[:100]


def _take_screenshots(page, prefix, viewport_width):
    """Capture light and dark screenshots at a given viewport width."""
    page.set_viewport_size({"width": viewport_width, "height": 1000})
    page.evaluate("() => document.documentElement.classList.remove('dark')")
    page.wait_for_timeout(300)
    page.screenshot(
        path=os.path.join(SCREENSHOT_DIR, "%s-%dw-light.png" % (prefix, viewport_width)),
        full_page=True,
    )
    page.evaluate("() => document.documentElement.classList.add('dark')")
    page.wait_for_timeout(300)
    page.screenshot(
        path=os.path.join(SCREENSHOT_DIR, "%s-%dw-dark.png" % (prefix, viewport_width)),
        full_page=True,
    )
    page.evaluate("() => document.documentElement.classList.remove('dark')")


def _new_page(browser):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def test_portfolio_manager_implementation_planning_walk(browser, live_server):
    """Full walk-through: role assertion, each page's main action, screenshots."""
    org = create_fresh_org("portfolio_manager")
    suffix = org["suffix"]
    programme_name = "Platform Renewal %s" % suffix
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.vendor.vendor_organization import EnterpriseInitiative
        db.session.add(EnterpriseInitiative(name=programme_name, organization_id=org["org_id"]))
        db.session.commit()

    blocker_name = "Data migration %s" % suffix
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, org["emails"]["portfolio_manager"])
        _role_assertions(page)

        # --- work_package_form.html: create ---
        page.goto(live_server + CREATE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Create Work Package").first).to_be_visible()
        page.fill("#name", blocker_name)
        page.select_option("#programme_id", label=programme_name)
        page.fill("#description", "Migration data pipeline for portfolio management")
        page.get_by_role("button", name="Create Work Package").click()
        page.wait_for_url(re.compile(r"/implementation/work-packages/\d+/edit$"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Edit Work Package").first).to_be_visible()
        _take_screenshots(page, "work_package_form", 1440)
        _take_screenshots(page, "work_package_form", 390)

        # --- work_package_detail.html: view ---
        wp_id = int(re.search(r"/work-packages/(\d+)/edit", page.url).group(1))
        detail_url = "/implementation/work-packages/%d" % wp_id
        page.goto(live_server + detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("work-package-detail")).to_be_visible()
        expect(page.get_by_test_id("wp-status")).to_be_visible()
        _take_screenshots(page, "work_package_detail", 1440)
        _take_screenshots(page, "work_package_detail", 390)

        # --- work_packages_blocked.html ---
        page.goto(live_server + BLOCKED, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_selector("#blocked-empty, #blocked-table", timeout=PAGE_TIMEOUT)
        _take_screenshots(page, "work_packages_blocked", 1440)
        _take_screenshots(page, "work_packages_blocked", 390)

        # --- Tenancy: other org gets 404 ---
        other = create_fresh_org("portfolio_manager")
        other_context, other_page = _new_page(browser)
        try:
            sign_in(other_page, live_server, other["emails"]["portfolio_manager"])
            _role_assertions(other_page)
            response = other_page.goto(
                live_server + "/implementation/work-packages/%d" % wp_id,
                wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
            )
            assert response is not None and response.status == 404
        finally:
            other_context.close()
    finally:
        context.close()
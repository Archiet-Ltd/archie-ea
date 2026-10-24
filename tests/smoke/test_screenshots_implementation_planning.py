"""
Take screenshots of implementation planning templates as portfolio_manager.
Reuses the live_server fixture from conftest.
Creates a work package through the UI so the detail page renders real content.
"""

import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke]

SCREENSHOT_DIR = os.environ.get("SCREENSHOT_DIR", "/home/ubuntu/verify/seats/mg3ip066d/walk")
CREATE = "/implementation/work-packages/create"


def _make_programme(org_id, suffix):
    from app import create_app, db
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    app = create_app("testing")
    name = "Programme %s" % suffix
    with app.app_context():
        e = EnterpriseInitiative(name=name, organization_id=org_id)
        db.session.add(e)
        db.session.commit()
        eid = e.id
        db.session.remove()
    return name, eid


def _create_work_package(page, live_server, name, programme_id, description):
    """Fill the create form and submit, returning the new WP id."""
    page.goto(live_server + CREATE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#name", name)
    page.select_option("#programme_id", label=programme_id)
    page.fill("#description", description)
    with page.expect_response(
        lambda r: r.url.endswith(CREATE) and r.request.method == "POST", timeout=PAGE_TIMEOUT
    ):
        page.get_by_role("button", name="Create Work Package").click()
    page.wait_for_url(re.compile(r"/implementation/work-packages/\d+/edit$"), timeout=PAGE_TIMEOUT)
    return int(re.search(r"/work-packages/(\d+)/edit", page.url).group(1))


def test_screenshots(browser, live_server):
    org = create_fresh_org("portfolio_manager")
    suffix = org["suffix"]

    # Seed one programme so the form's programme selector has an option.
    programme_name, programme_id = _make_programme(org["org_id"], suffix)

    # Sign in once and create the work package.
    context_light_1440 = browser.new_context(
        ignore_https_errors=True,
        viewport={"width": 1440, "height": 1000},
        color_scheme="light",
    )
    context_light_1440.add_init_script(
        "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
    )
    page = context_light_1440.new_page()
    sign_in(page, live_server, org["emails"]["portfolio_manager"])

    # Create one work package so the detail page has content.
    wp_name = "Screenshot WP %s" % suffix
    wp_id = _create_work_package(page, live_server, wp_name, programme_name,
                                  "Created for screenshot coverage")

    # The detail URL uses the real ID.
    detail_path = "/implementation/work-packages/%d" % wp_id
    blocked_path = "/implementation/work-packages/blocked"
    form_path = "/implementation/work-packages/create"

    # Re-use the signed-in page for light-1440 screenshots.
    for name, path in [("work_package_form", form_path),
                       ("work_package_detail", detail_path),
                       ("work_packages_blocked", blocked_path)]:
        page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(500)
        filename = "%s-1440w-light.png" % name
        filepath = os.path.join(SCREENSHOT_DIR, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        page.screenshot(path=filepath, full_page=True)
        print("[screenshot] %s" % filepath)
    context_light_1440.close()

    # Dark-1440
    context_dark_1440 = browser.new_context(
        ignore_https_errors=True,
        viewport={"width": 1440, "height": 1000},
        color_scheme="dark",
    )
    context_dark_1440.add_init_script(
        "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
    )
    page_dark = context_dark_1440.new_page()
    sign_in(page_dark, live_server, org["emails"]["portfolio_manager"])
    for name, path in [("work_package_form", form_path),
                       ("work_package_detail", detail_path),
                       ("work_packages_blocked", blocked_path)]:
        page_dark.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page_dark.wait_for_timeout(500)
        filename = "%s-1440w-dark.png" % name
        filepath = os.path.join(SCREENSHOT_DIR, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        page_dark.screenshot(path=filepath, full_page=True)
        print("[screenshot] %s" % filepath)
    context_dark_1440.close()

    # Light-390
    context_light_390 = browser.new_context(
        ignore_https_errors=True,
        viewport={"width": 390, "height": 844},
        color_scheme="light",
    )
    context_light_390.add_init_script(
        "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
    )
    page_l390 = context_light_390.new_page()
    sign_in(page_l390, live_server, org["emails"]["portfolio_manager"])
    for name, path in [("work_package_form", form_path),
                       ("work_package_detail", detail_path),
                       ("work_packages_blocked", blocked_path)]:
        page_l390.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page_l390.wait_for_timeout(500)
        filename = "%s-390w-light.png" % name
        filepath = os.path.join(SCREENSHOT_DIR, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        page_l390.screenshot(path=filepath, full_page=True)
        print("[screenshot] %s" % filepath)
    context_light_390.close()

    # Dark-390
    context_dark_390 = browser.new_context(
        ignore_https_errors=True,
        viewport={"width": 390, "height": 844},
        color_scheme="dark",
    )
    context_dark_390.add_init_script(
        "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
    )
    page_d390 = context_dark_390.new_page()
    sign_in(page_d390, live_server, org["emails"]["portfolio_manager"])
    for name, path in [("work_package_form", form_path),
                       ("work_package_detail", detail_path),
                       ("work_packages_blocked", blocked_path)]:
        page_d390.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page_d390.wait_for_timeout(500)
        filename = "%s-390w-dark.png" % name
        filepath = os.path.join(SCREENSHOT_DIR, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        page_d390.screenshot(path=filepath, full_page=True)
        print("[screenshot] %s" % filepath)
    context_dark_390.close()
"""A Platform Admin accesses the Framework Config page and gets redirected.

/framework-config/'s primary endpoint now redirects to /capability-frameworks/
as part of the consolidation effort.

This replaces /framework-config/'s entry in test_uncovered_modules_boot.py's
MODULES dict.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    page.wait_for_timeout(800)
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def test_framework_config_redirects_to_capability_frameworks(browser, live_server, seeded):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1920, "height": 1080})
    page = context.new_page()
    page.on("pageerror", lambda e: print("PAGEERROR:", e))
    page.on("console", lambda m: print("CONSOLE[%s]: %s" % (m.type, m.text)) if m.type == "error" else None)

    email = seeded["emails"]["platform_admin"]

    _login(page, live_server, email)
    
    # Navigate to the folded Framework Config page
    page.goto(live_server + "/framework-config/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1000)
    
    # Should be redirected to Capability Frameworks
    expect(page).to_have_url(live_server + "/capability-frameworks/", timeout=PAGE_TIMEOUT)

    context.close()

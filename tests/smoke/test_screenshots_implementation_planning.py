"""
Take screenshots of implementation planning templates as portfolio_manager.
Reuses the live_server fixture from conftest.
"""

import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke]

SCREENSHOT_DIR = os.environ.get("SCREENSHOT_DIR", "/home/ubuntu/verify/seats/mg3ip066b/walk")
TEMPLATES = {
    "work_package_form": "/implementation/work-packages/create",
    "work_package_detail": "/implementation/work-packages/1",
    "work_packages_blocked": "/implementation/work-packages/blocked",
}


def test_screenshots(browser, live_server):
    org = create_fresh_org("portfolio_manager")

    for theme in ("light", "dark"):
        for width, narrow in [(1440, ""), (390, "-narrow")]:
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": width, "height": 1000},
                color_scheme=theme,
            )
            context.add_init_script(
                "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
            )
            page = context.new_page()

            sign_in(page, live_server, org["emails"]["portfolio_manager"])

            for name, path in TEMPLATES.items():
                page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                page.wait_for_timeout(500)
                filename = "%s-%s%s.png" % (name, theme, narrow)
                filepath = os.path.join(SCREENSHOT_DIR, filename)
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                page.screenshot(path=filepath, full_page=True)
                print("[screenshot] %s" % filepath)

            context.close()
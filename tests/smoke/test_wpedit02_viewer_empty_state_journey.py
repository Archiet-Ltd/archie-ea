"""A Viewer with no work packages opens the work packages page and is told why it is empty.

The page shows the empty-state sentence, offers no create action, and never prints
the word "None". Run at 1440 wide in light and at 390 wide in dark.
"""

import os
import tempfile
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOT_DIR = Path(os.environ.get("SMOKE_SCREENSHOT_DIR") or tempfile.gettempdir())


@pytest.mark.parametrize("width,height,scheme", [(1440, 1000, "light"), (390, 844, "dark")])
def test_viewer_without_work_packages_sees_the_empty_state(browser, live_server, width, height, scheme):
    org = create_fresh_org("procurement", platform_role="Viewer")
    context = browser.new_context(
        ignore_https_errors=True, viewport={"width": width, "height": height}, color_scheme=scheme
    )
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["procurement"])
        page.goto(live_server + "/enterprise/implementation/work-packages",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        empty = page.get_by_test_id("work-packages-empty")
        expect(empty).to_have_count(1)
        expect(empty).to_contain_text("No work packages yet")
        expect(empty).to_contain_text("A work package is a piece of work that closes one or more gaps")
        expect(empty.locator("a, button")).to_have_count(0)
        expect(page.get_by_test_id("btn-new-work-package")).to_have_count(0)
        assert "None" not in empty.inner_text()

        page.screenshot(path=str(SHOT_DIR / f"wpedit02-viewer-empty-{width}-{scheme}.png"), full_page=True)
    finally:
        context.close()

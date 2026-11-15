"""A platform administrator presses Discover Apps before saving any Power Platform credentials. The page says, in
words, that the credentials are missing and to save them first, and the browser console stays quiet."""
import os

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000
SENTENCE = "Power Platform credentials not configured. Save credentials first."


@pytest.mark.smoke
@pytest.mark.journey
@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}])
def test_discover_without_credentials_says_so_on_the_page(browser, live_server, seeded, viewport):
    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    page = context.new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#email", seeded["emails"]["platform_admin"])
        page.fill("#password", PASSWORD)
        page.locator("#submit").click()
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
        page.goto(live_server + "/admin/integrations/power-platform", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.locator("#btn-discover").click()
        notice = page.locator("#pp-alert")
        expect(notice).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(notice).to_contain_text(SENTENCE)
        expect(notice).to_be_in_viewport()
        _frames = os.environ.get("SMALL_FIX_FRAMES_DIR")
        if _frames:
            os.makedirs(_frames, exist_ok=True)
            page.screenshot(path="%s/%s-%d.png" % (_frames, "power-platform", page.viewport_size["width"]), full_page=True)
        form_box = page.locator("#tenant_id").bounding_box()
        notice_box = notice.bounding_box()
        assert notice_box["y"] < form_box["y"], "the sentence is not next to the credentials form"
        assert not [e for e in errors if "400" in e or "credentials" in e.lower()], errors
    finally:
        context.close()

"""A person who still has the old gap-discovery address bookmarked lands on the dashboard, with
real content, no error, and no menu entry offering the retired page."""

import os

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("SMOKE_WALK_DIR")


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
def test_old_gap_discovery_address_lands_on_the_dashboard(width, height, browser, live_server, seeded):
    ctx = browser.new_context(viewport={"width": width, "height": height})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#email", seeded["emails"]["enterprise_architect"])
        page.fill("#password", PASSWORD)
        page.click("#submit", no_wait_after=True)
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)

        response = page.goto(live_server + "/agentic-gaps", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1200)
        if WALK:
            page.screenshot(path=os.path.join(WALK, "old-address-%d.png" % width), full_page=True)

        body = page.inner_text("body")
        assert response.status == 200
        assert "/agentic-gaps" not in page.url
        assert len(body.strip()) > 200
        assert "Gap Discovery" not in body
        assert page.locator("a[href='/agentic-gaps']").count() == 0
    finally:
        ctx.close()

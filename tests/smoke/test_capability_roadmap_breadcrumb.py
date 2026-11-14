"""The capability roadmap renders exactly one breadcrumb trail at 1440 light and 390 dark.

Before the fix, /capability-roadmap carried both a {% block breadcrumb %} and a
shell-rendered breadcrumb from page_trail(), so the page showed two crumb trails
stacked on top of each other. The block breadcrumb was removed; page_shell now
provides the single trail.

Screenshots are written to BCRUMB_WALK_DIR (overridable) and copied to the seat
walk folder by the CI step that runs this journey.

No database writes — read-only journey against the seeded organisation.
"""
import os

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PATH = "/capability-roadmap"
WALK_DIR = os.environ.get("BCRUMB_WALK_DIR")


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", "SmokeJourney!2026")
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _shot(page, name):
    if WALK_DIR:
        os.makedirs(WALK_DIR, exist_ok=True)
        page.screenshot(path=os.path.join(WALK_DIR, name + ".png"), full_page=True)


def _breadcrumb_count(page):
    return page.get_by_role("navigation", name="Breadcrumb", exact=True).count()


@pytest.mark.parametrize("width,theme", [
    (1440, "light"),
    (390, "dark"),
])
def test_capability_roadmap_single_breadcrumb(browser, live_server, seeded, width, theme):
    """Exactly one breadcrumb trail on /capability-roadmap at each width/theme."""
    context = browser.new_context(
        ignore_https_errors=True,
        viewport={"width": width, "height": 1000},
        color_scheme=theme,
    )
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["business_architect"])
        response = page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 200
        expect(page.locator("h1")).to_contain_text("Enterprise Capability Roadmap")
        assert _breadcrumb_count(page) == 1, "expected exactly one breadcrumb trail, got %d" % _breadcrumb_count(page)
        _shot(page, "capability-roadmap-%d-%s" % (width, theme))
    finally:
        context.close()

"""Journey: a person presses the AI Guide button in the header and the guide
drawer opens with its content; Escape closes it and focus returns to the button.

Needs AI_PAGE_GUIDE_ENABLED=true before the live server boots (the button is
not rendered without it). A missing flag fails, it never skips.
"""
import os

import pytest

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PAGES = ["/dashboard/overview", "/capability-map/"]


@pytest.fixture(scope="module", autouse=True)
def guide_enabled():
    if os.environ.get("AI_PAGE_GUIDE_ENABLED", "").strip().lower() not in {"true", "1", "yes", "on"}:
        pytest.fail("page guide journey requires AI_PAGE_GUIDE_ENABLED=true before live-server boot")


@pytest.mark.parametrize("width", [1440, 390])
def test_guide_button_opens_drawer_and_returns_focus(browser, live_server, seeded, width):
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        for path in PAGES:
            page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.wait_for_timeout(1500)
            page.eval_on_selector_all("[x-show='showOnboarding']", "els => els.forEach(e => e.remove())")
            button = page.locator("#page-guide-trigger")
            assert button.is_visible(), "%s must offer the page guide" % path
            button.click()
            drawer = page.locator("#drawer-page-guide [role=dialog]")
            drawer.wait_for(state="visible", timeout=5000)
            assert drawer.get_by_role("heading", name="AI Guide").is_visible()
            page.wait_for_timeout(800)
            entries = page.locator("#page-guide-starters button, #page-guide-actions > *, #page-guide-glossary > *")
            assert entries.count() >= 1, "%s: the guide drawer opened with no guide entry" % path
            page.keyboard.press("Escape")
            drawer.wait_for(state="hidden", timeout=5000)
            focused = page.evaluate("document.activeElement && document.activeElement.id")
            assert focused == "page-guide-trigger", "%s: focus went to %r, not the button" % (path, focused)
    finally:
        ctx.close()

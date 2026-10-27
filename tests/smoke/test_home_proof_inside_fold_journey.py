"""A visitor who opens the public home sees the real product without scrolling.

The visitor is signed out. While the first wave is live the proof under the headline is a real capture of
the Overview of the sample company. The headline and both buttons sit in the first screen, the image starts
inside it with enough of it showing to read (the whole image is taller than a screen, as the product is), and
there is no empty band between the navigation bar and the headline. Run at 1440 by 900 and 375 by 812, in the
light and the dark theme.
"""
import pytest

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SCREENS = {
    "1440": {"width": 1440, "height": 900},
    "375": {"width": 375, "height": 812},
}
MIN_SHOWING = 150  # pixels of the Overview that must show in the first screen
MAX_BAND = 64  # pixels between the bottom of the navigation bar and the first thing in the hero (the label above the headline)


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("screen", sorted(SCREENS))
def test_overview_starts_inside_the_fold(browser, live_server, screen, theme):
    viewport = SCREENS[screen]
    context = browser.new_context(viewport=viewport)
    context.set_default_timeout(PAGE_TIMEOUT)
    if theme == "dark":
        context.add_init_script("document.documentElement.classList.add('dark');")
    page = context.new_page()
    try:
        page.goto(live_server + "/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        proof = page.locator('[data-testid="home-hero-proof"]')
        assert proof.evaluate("e => e.complete && e.naturalWidth > 0")
        # the image is the Overview: its description names it, and the caption carries the capture date
        alt = proof.get_attribute("alt")
        assert "overview" in alt.lower() and "who owns them" in alt.lower() and "chat" not in alt.lower()
        assert "captured on" in page.locator('[data-testid="home-hero-figure"] figcaption').inner_text()
        top, bottom = proof.evaluate("e => { const r = e.getBoundingClientRect(); return [r.top, r.bottom]; }")
        showing = min(bottom, viewport["height"]) - max(top, 0)
        assert showing >= MIN_SHOWING, "only %spx of the Overview shows in the first screen" % showing
        # no empty band under the navigation bar
        nav_bottom = page.locator("header[data-site-nav]").evaluate("e => e.getBoundingClientRect().bottom")
        first_top = page.locator('[data-testid="home-hero"] > div > *').first.evaluate("e => e.getBoundingClientRect().top")
        assert first_top - nav_bottom <= MAX_BAND, "%spx of empty space under the navigation bar" % (first_top - nav_bottom)
        # the headline and both buttons are in the first screen with it
        for selector in ('[data-testid="home-hero"] h1', '[data-testid="home-hero-start"]', '[data-testid="home-hero-pricing"]'):
            assert page.locator(selector).evaluate("e => e.getBoundingClientRect().bottom") <= viewport["height"]
    finally:
        context.close()

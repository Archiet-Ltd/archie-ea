"""A visitor who opens the public home sees the cited answer without scrolling.

The visitor is signed out. The proof under the headline is the answer card (the sentence, what
depends on the system, who owns each). On a laptop screen (1440 by 900) the whole image, down to the
source it cites, ends above the bottom edge of the screen; the same on a phone (375 by 812), where the headline,
both buttons and the whole image are inside the screen.
Both in the light and the dark theme, with no empty band between the navigation bar and the headline.
"""
import pytest

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SCREENS = {
    "1440": {"width": 1440, "height": 900},
    "375": {"width": 375, "height": 812},
}
MAX_BAND = 64  # pixels between the bottom of the navigation bar and the top of the headline


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("screen", sorted(SCREENS))
def test_cited_answer_is_inside_the_fold(browser, live_server, screen, theme):
    viewport = SCREENS[screen]
    context = browser.new_context(viewport=viewport)
    context.set_default_timeout(PAGE_TIMEOUT)
    if theme == "dark":
        context.add_init_script("localStorage.setItem('theme', 'dark')")
    page = context.new_page()
    try:
        page.goto(live_server + "/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        # the page really is in the theme the test names (the stored choice is what the page reads)
        assert page.evaluate("document.documentElement.classList.contains('dark')") == (theme == "dark")
        proof = page.locator('[data-testid="home-hero-proof"]')
        assert proof.evaluate("e => e.complete && e.naturalWidth > 0")
        # the image is the answer card: its description names the sentence, the dependents and the owners
        alt = proof.get_attribute("alt")
        assert "what breaks if" in alt and "owner" in alt
        box = proof.evaluate("e => { const b = e.getBoundingClientRect(); return {top: b.top, bottom: b.bottom}; }")
        assert box["bottom"] <= viewport["height"], "answer card ends at %spx, below the %spx fold" % (box["bottom"], viewport["height"])
        # no empty band under the navigation bar
        nav_bottom = page.locator("header[data-site-nav]").evaluate("e => e.getBoundingClientRect().bottom")
        h1_top = page.locator('[data-testid="home-hero"] h1').evaluate("e => e.getBoundingClientRect().top")
        assert h1_top - nav_bottom <= MAX_BAND, "%spx of empty space under the navigation bar" % (h1_top - nav_bottom)
        # the headline and both buttons are still in the first screen with it
        for selector in ('[data-testid="home-hero"] h1', '[data-testid="home-hero-start"]', '[data-testid="home-hero-pricing"]'):
            assert page.locator(selector).evaluate("e => e.getBoundingClientRect().bottom") <= viewport["height"]
    finally:
        context.close()

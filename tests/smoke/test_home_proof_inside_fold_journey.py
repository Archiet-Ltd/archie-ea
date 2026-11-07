"""A visitor who opens the public home sees the cited answer without scrolling.

The visitor is signed out. The proof under the headline is the answer card (the sentence, what
depends on the system, who owns each). The headline, both buttons and the top of the answer card must
sit inside a laptop screen (1440 by 900) and a phone (375 by 812), in the light and the dark theme,
with no empty band between the navigation bar and the headline. The founder's description above the
buttons is longer than the one sentence it replaced, so the whole card no longer ends inside the first
screen; what must hold is that the card has begun and a person can see it is there.
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
        context.add_init_script("document.documentElement.classList.add('dark');")
    page = context.new_page()
    try:
        page.goto(live_server + "/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        proof = page.locator('[data-testid="home-hero-proof"]')
        assert proof.evaluate("e => e.complete && e.naturalWidth > 0")
        # the image is the answer card: its description names the sentence, the dependents and the owners
        alt = proof.get_attribute("alt")
        assert "what breaks if" in alt and "owner" in alt
        top = proof.evaluate("e => e.getBoundingClientRect().top")
        assert top < viewport["height"], "answer card starts at %spx, below the %spx fold" % (top, viewport["height"])
        # no empty band under the navigation bar
        nav_bottom = page.locator("header[data-site-nav]").evaluate("e => e.getBoundingClientRect().bottom")
        h1_top = page.locator('[data-testid="home-hero"] h1').evaluate("e => e.getBoundingClientRect().top")
        assert h1_top - nav_bottom <= MAX_BAND, "%spx of empty space under the navigation bar" % (h1_top - nav_bottom)
        # the headline and both buttons are still in the first screen with it
        for selector in ('[data-testid="home-hero"] h1', '[data-testid="home-hero-start"]', '[data-testid="home-hero-pricing"]'):
            assert page.locator(selector).evaluate("e => e.getBoundingClientRect().bottom") <= viewport["height"]
    finally:
        context.close()

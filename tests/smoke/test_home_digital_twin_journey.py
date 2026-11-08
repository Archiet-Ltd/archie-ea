"""A visitor opens the public home and learns Entelim is the company brain, a digital twin and intelligence.

The visitor is signed out. The goal: in the first screen understand that Entelim is the company brain, the
living digital twin of the business and the intelligence to ask it anything, see who it is for, and start
free to a sign-up page that has content. "What breaks if a system fails" is one example question lower
down, not the promise the page opens with.
"""
import os
import re

import pytest

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SCREENS = {
    "1440-light": ({"width": 1440, "height": 900}, "light", 2),
    "1440-dark": ({"width": 1440, "height": 900}, "dark", 2),
    "390-light": ({"width": 390, "height": 844}, "light", 3),
    "390-dark": ({"width": 390, "height": 844}, "dark", 3),
}


def _shot(page, name):
    folder = os.environ.get("SMOKE_SCREENSHOT_DIR")
    if folder:
        os.makedirs(folder, exist_ok=True)
        page.screenshot(path=os.path.join(folder, name + ".png"))


def _lines(page, selector):
    return page.locator(selector).evaluate(
        "e => Math.round(e.getBoundingClientRect().height / parseFloat(getComputedStyle(e).lineHeight))"
    )


def _assert_theme(page, theme):
    """The page really rendered in the theme the case names, not just a context that asked for it."""
    is_dark = page.evaluate(
        "() => document.documentElement.classList.contains('dark')"
        " || getComputedStyle(document.body).backgroundColor !== 'rgb(255, 255, 255)'"
    )
    assert is_dark == (theme == "dark"), (theme, is_dark)


@pytest.mark.parametrize("screen", sorted(SCREENS))
def test_visitor_learns_company_brain_digital_twin_and_intelligence(browser, live_server, screen):
    viewport, theme, max_h1_lines = SCREENS[screen]
    context = browser.new_context(viewport=viewport, color_scheme=theme)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        page.goto(live_server + "/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        _assert_theme(page, theme)

        title = page.title().lower()
        assert "company brain" in title and "digital twin" in title, title
        for selector in ('meta[name="description"]', 'meta[property="og:description"]', 'meta[name="twitter:description"]'):
            content = page.locator(selector).get_attribute("content").lower()
            for phrase in ("company brain", "digital twin", "intelligence"):
                assert phrase in content, (selector, content)

        hero = page.locator('[data-testid="home-hero"]')
        h1 = hero.locator("h1").inner_text().strip()
        assert "company brain" in h1.lower(), h1
        _shot(page, "home-first-screen-" + screen)
        assert _lines(page, '[data-testid="home-hero"] h1') <= max_h1_lines

        lead = " ".join(hero.locator("p.home-lead").inner_text().split())
        for phrase in ("company brain", "digital twin", "intelligence", "founders", "Start free"):
            assert phrase.lower() in lead.lower(), (phrase, lead)
        assert len(lead.split()) < 60, len(lead.split())

        # what breaks is an example further down, not the promise the page opens with
        hero_text = " ".join(hero.inner_text().split()).lower()
        assert "what breaks if a system fails" not in hero_text, hero_text
        assert "what breaks if a system fails" not in lead.lower()

        # the first screen's own button lands on a sign-up page with content
        hero.locator('[data-testid="home-hero-start"]').click()
        page.wait_for_url(re.compile(r"/register"), timeout=PAGE_TIMEOUT)
        assert page.locator("h1", has_text="Create an account").count() == 1
        assert page.locator("form input").count() >= 2
    finally:
        context.close()

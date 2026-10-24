"""Test the home page first screen speaks to a startup CTO and shows the real product.

UC-S2-01, UC-S2-04: the home page's first screen at 1440x900 holds one startup headline,
one sentence, two buttons and the real product screenshot visible within the viewport.

The journey: a signed-out startup CTO sees the headline "See what breaks before you change it.",
one sentence about GitHub connection and cited answers, Start free and See pricing buttons,
the AGPL line below the buttons, and the hero image starting within the first screen. Clicking
Start free lands on the sign-up form; See pricing lands on pricing cards. At 390 the H1,
sentence and primary button fit the first screen with no horizontal scroll.
"""

from __future__ import annotations

import pytest

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_home_first_screen_startup_cto_at_1440x900(browser, live_server):
    """As a signed-out startup CTO at 1440x900, the home page's first screen shows
    the headline, one sentence, two buttons, the AGPL line below buttons, and the
    hero image top within the viewport."""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        response = page.goto(f"{live_server}/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 200, f"Home page returned {response.status}"
        page.wait_for_timeout(500)

        # H1 reads the startup-focused copy
        h1 = page.locator("h1.home-display")
        assert h1.count() == 1, "Expected exactly one H1 with class home-display"
        h1_text = h1.text_content().strip()
        assert h1_text == "See what breaks before you change it.", (
            f"H1 text should be startup-focused, got: {h1_text}"
        )

        # The first sentence under it reads the GitHub connection copy
        lead = page.locator("p.home-lead").first
        lead_text = " ".join(lead.text_content().split())  # Normalize whitespace
        expected_lead = (
            "Connect GitHub and Entelim maps your services, owners and dependencies, "
            "then answers in plain words, citing the file and commit behind every fact."
        )
        assert lead_text == expected_lead, f"Lead paragraph should describe GitHub connection, got: {lead_text}"

        # "Start free" and "See pricing" are visible in the hero section
        hero = page.locator('[data-testid="home-hero"]')
        start_free = hero.locator('a.public-btn--primary', has_text="Start free")
        assert start_free.count() == 1, "Expected exactly one 'Start free' button in hero"
        assert start_free.is_visible(), "'Start free' button should be visible"

        see_pricing = hero.locator('a.public-btn--outline', has_text="See pricing")
        assert see_pricing.count() == 1, "Expected exactly one 'See pricing' button in hero"
        assert see_pricing.is_visible(), "'See pricing' button should be visible"

        # The AGPL line sits below the buttons (y-position comparison)
        buttons_box = start_free.bounding_box()
        agpl_line = hero.locator("text=Open source under AGPL-3.0")
        assert agpl_line.is_visible(), "AGPL line should be visible"
        agpl_box = agpl_line.bounding_box()
        assert agpl_box["y"] > buttons_box["y"], (
            f"AGPL line should be below buttons: AGPL y={agpl_box['y']}, button y={buttons_box['y']}"
        )

        # The hero image's top is within the viewport (above 900px from top)
        hero_figure = page.locator('[data-testid="home-hero-figure"]')
        assert hero_figure.count() == 1, "Expected exactly one hero figure"
        figure_box = hero_figure.bounding_box()
        assert figure_box is not None, "Hero figure should have bounding box"
        assert figure_box["y"] < 900, (
            f"Hero figure top should be within viewport (< 900px), got y={figure_box['y']}"
        )

    finally:
        ctx.close()


def test_home_first_screen_buttons_navigate_correctly(browser, live_server):
    """Clicking 'Start free' lands on the sign-up form; 'See pricing' lands on pricing cards."""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(f"{live_server}/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(300)

        # Click "Start free" in the hero section and verify it lands on sign-up form
        hero = page.locator('[data-testid="home-hero"]')
        hero.locator('a.public-btn--primary', has_text="Start free").click()
        page.wait_for_url(lambda url: "register" in url, timeout=PAGE_TIMEOUT)
        assert page.locator("h1", has_text="Create an account").count() == 1, (
            "Start free should land on sign-up form with 'Create an account' heading"
        )

        # Go back and click "See pricing"
        page.goto(f"{live_server}/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(300)

        hero = page.locator('[data-testid="home-hero"]')
        hero.locator('a.public-btn--outline', has_text="See pricing").click()
        page.wait_for_url(lambda url: url.rstrip("/").endswith("/pricing"), timeout=PAGE_TIMEOUT)
        assert page.locator("h1", has_text="Pricing").count() == 1, (
            "See pricing should land on pricing page with 'Pricing' heading"
        )
        # Verify pricing cards are present
        pricing_cards = page.locator(".public-card")
        assert pricing_cards.count() >= 1, "Pricing page should show pricing cards"

    finally:
        ctx.close()


def test_home_first_screen_at_390_no_overflow(browser, live_server):
    """At 390px width: H1, sentence and 'Start free' button are within the first screen
    (844px height), and the page has no horizontal scroll."""
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    try:
        response = page.goto(f"{live_server}/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 200
        page.wait_for_timeout(500)

        # No horizontal scroll
        scroll_width = page.evaluate("() => document.documentElement.scrollWidth")
        viewport_width = page.evaluate("() => window.innerWidth")
        assert scroll_width <= viewport_width, (
            f"Home page at 390px should have no horizontal scroll "
            f"(scrollWidth={scroll_width}, innerWidth={viewport_width})"
        )

        # H1 is within the first screen
        h1 = page.locator("h1.home-display")
        h1_box = h1.bounding_box()
        assert h1_box is not None, "H1 should be visible"
        assert h1_box["y"] + h1_box["height"] < 844, (
            f"H1 should be within first screen at 390px, bottom at {h1_box['y'] + h1_box['height']}"
        )

        # Sentence is within the first screen
        lead = page.locator("p.home-lead").first
        lead_box = lead.bounding_box()
        assert lead_box is not None, "Lead paragraph should be visible"
        assert lead_box["y"] + lead_box["height"] < 844, (
            f"Lead paragraph should be within first screen at 390px, bottom at {lead_box['y'] + lead_box['height']}"
        )

        # Primary button ("Start free") in hero is within the first screen
        hero = page.locator('[data-testid="home-hero"]')
        start_free = hero.locator('a.public-btn--primary', has_text="Start free")
        button_box = start_free.bounding_box()
        assert button_box is not None, "Start free button should be visible"
        assert button_box["y"] + button_box["height"] < 844, (
            f"Start free button should be within first screen at 390px, bottom at {button_box['y'] + button_box['height']}"
        )

    finally:
        ctx.close()


def test_home_h1_fits_three_lines_at_390(browser, live_server):
    """At 390px width, the H1 is at most three lines tall (acceptance criterion)."""
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    try:
        page.goto(f"{live_server}/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(500)

        h1 = page.locator("h1.home-display")
        h1_box = h1.bounding_box()
        assert h1_box is not None, "H1 should be visible"

        # Get line height to estimate line count
        line_height = page.evaluate(
            """() => {
                const h1 = document.querySelector('h1.home-display');
                return parseFloat(window.getComputedStyle(h1).lineHeight);
            }"""
        )

        # Height should be <= 3 lines worth
        max_height = line_height * 3.5  # Allow some tolerance
        assert h1_box["height"] <= max_height, (
            f"H1 should be at most 3 lines at 390px (height {h1_box['height']}px, "
            f"line-height {line_height}px, max {max_height}px)"
        )

    finally:
        ctx.close()


def test_home_lead_paragraph_fits_four_lines_at_390(browser, live_server):
    """At 390px width, the lead paragraph is at most four lines tall (acceptance criterion)."""
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    try:
        page.goto(f"{live_server}/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(500)

        lead = page.locator("p.home-lead").first
        lead_box = lead.bounding_box()
        assert lead_box is not None, "Lead paragraph should be visible"

        # Get line height to estimate line count
        line_height = page.evaluate(
            """() => {
                const lead = document.querySelector('p.home-lead');
                return parseFloat(window.getComputedStyle(lead).lineHeight);
            }"""
        )

        # Height should be <= 4 lines worth
        max_height = line_height * 4.5  # Allow some tolerance
        assert lead_box["height"] <= max_height, (
            f"Lead paragraph should be at most 4 lines at 390px (height {lead_box['height']}px, "
            f"line-height {line_height}px, max {max_height}px)"
        )

    finally:
        ctx.close()

"""Behavioural tests for the header search trigger and dark-theme toggle.

These drive a real browser against a live server because string-matching
the rendered HTML cannot prove the event object is not passed as the search
query, nor that the theme toggle actually applies the ``dark`` class.
"""

import pytest

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login, _visit, page  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_dark_theme_toggle_applies_and_removes_dark_class(page, live_server, seeded):
    """Clicking the Dark theme menuitemcheckbox once adds the ``dark`` class
    to ``<html>``; clicking it again removes it.  The switch must not
    double-toggle (D-1)."""
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    _visit(page, live_server, "/dashboard/overview")

    # Open the user menu
    user_btn = page.locator("#user-menu-btn")
    user_btn.click()
    page.wait_for_timeout(400)

    # The dark-theme item is a <button role="menuitemcheckbox">
    dark_item = page.locator('button[role="menuitemcheckbox"]')
    assert dark_item.count() == 1, "expected exactly one menuitemcheckbox in the user menu"

    # Click once — dark class must appear
    dark_item.click()
    page.wait_for_timeout(400)
    has_dark = page.evaluate("() => document.documentElement.classList.contains('dark')")
    assert has_dark is True, "expected <html> to have class 'dark' after one click"

    # Click again — dark class must be removed
    # Re-open the menu (it may have closed)
    if not page.locator('button[role="menuitemcheckbox"]').is_visible():
        page.locator("#user-menu-btn").click()
        page.wait_for_timeout(400)
    dark_item = page.locator('button[role="menuitemcheckbox"]')
    dark_item.click()
    page.wait_for_timeout(400)
    has_dark = page.evaluate("() => document.documentElement.classList.contains('dark')")
    assert has_dark is False, "expected <html> to NOT have class 'dark' after second click"


def test_search_trigger_does_not_pass_event_as_query(page, live_server, seeded):
    """Clicking the desktop search trigger must open the search modal with an
    empty input, not ``[object PointerEvent]``."""
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    _visit(page, live_server, "/dashboard/overview")

    # Click the desktop search trigger
    search_trigger = page.locator("#search-modal-trigger")
    search_trigger.click()
    page.wait_for_timeout(600)

    # The search modal should be visible and its input empty
    search_input = page.locator("#search-modal-input")
    assert search_input.is_visible(), "search modal input not visible after clicking trigger"
    value = search_input.input_value()
    assert value == "", (
        f"search input value must be empty after trigger click, got {value!r}"
    )


def test_dark_theme_toggle_via_enter_key_toggles_exactly_once(page, live_server, seeded):
    """Pressing Enter once on the focused Dark theme menuitemcheckbox must
    toggle the ``dark`` class exactly once.  A redundant @keydown handler
    would cause a double-toggle in some browsers (D-5)."""
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    _visit(page, live_server, "/dashboard/overview")

    # Open the user menu
    user_btn = page.locator("#user-menu-btn")
    user_btn.click()
    page.wait_for_timeout(400)

    # Focus the dark-theme menuitemcheckbox
    dark_item = page.locator('button[role="menuitemcheckbox"]')
    assert dark_item.count() == 1
    dark_item.focus()
    page.wait_for_timeout(100)

    # Press Enter once
    page.keyboard.press("Enter")
    page.wait_for_timeout(400)

    # Verify dark class was applied exactly once
    has_dark = page.evaluate("() => document.documentElement.classList.contains('dark')")
    assert has_dark is True, (
        "expected <html> to have class 'dark' after pressing Enter on the "
        "dark theme menuitemcheckbox — the toggle may have double-fired and "
        "returned to the original state"
    )


def _relative_luminance(r, g, b):
    """Compute WCAG relative luminance from sRGB 0-255 channel values."""
    def _linearise(c):
        s = c / 255.0
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4
    return 0.2126 * _linearise(r) + 0.7152 * _linearise(g) + 0.0722 * _linearise(b)


def _contrast_ratio(l1, l2):
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def test_user_menu_profile_contrast_in_dark_mode(page, live_server, seeded):
    """In dark mode, the Profile menu item text must contrast at least 4.5:1
    against the popover background."""
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    _visit(page, live_server, "/dashboard/overview")

    # Enable dark mode
    page.evaluate("() => document.documentElement.classList.add('dark')")
    page.wait_for_timeout(300)

    # Open the user menu
    user_btn = page.locator("#user-menu-btn")
    user_btn.click()
    page.wait_for_timeout(400)

    # Get the Profile link — the first <a role="menuitem"> in the dropdown
    profile_link = page.locator('#user-dropdown a[role="menuitem"]').first
    assert profile_link.is_visible(), "Profile link not visible in user menu"

    # Get the user dropdown (popover) element
    dropdown = page.locator("#user-dropdown")

    # Read computed colours
    text_color = profile_link.evaluate("el => window.getComputedStyle(el).color")
    bg_color = dropdown.evaluate("el => window.getComputedStyle(el).backgroundColor")

    # Parse rgb(r, g, b) or rgba(r, g, b, a) strings
    text_rgb = _parse_rgb(text_color)
    bg_rgb = _parse_rgb(bg_color)

    # Assert colours differ
    assert text_rgb != bg_rgb, (
        f"Profile text colour {text_rgb} must differ from popover background {bg_rgb}"
    )

    # Compute contrast ratio
    text_lum = _relative_luminance(*text_rgb)
    bg_lum = _relative_luminance(*bg_rgb)
    ratio = _contrast_ratio(text_lum, bg_lum)

    assert ratio >= 4.5, (
        f"Profile text ({text_color}) on popover background ({bg_color}) "
        f"contrast ratio {ratio:.2f}:1 is below WCAG AA minimum 4.5:1"
    )


def _parse_rgb(css_color):
    """Parse an rgb(r, g, b) or rgba(r, g, b, a) string into (r, g, b)."""
    import re
    m = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", css_color)
    if not m:
        raise ValueError(f"Could not parse colour: {css_color!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _switch_track_knob_contrast(page, theme_label):
    """Return (track_bg, knob_bg, ratio) for the unchecked switch in the user menu."""
    # Ensure the user menu is open
    user_btn = page.locator("#user-menu-btn")
    dropdown = page.locator("#user-dropdown")
    if not dropdown.is_visible():
        user_btn.click()
        page.wait_for_timeout(400)
    assert dropdown.is_visible(), f"user dropdown not visible in {theme_label}"

    # The switch track is the span.peer inside the menuitemcheckbox button
    track = page.locator('button[role="menuitemcheckbox"] > span.peer')
    assert track.is_visible(), f"switch track not visible in {theme_label}"

    # The knob is the inner span
    knob = track.locator("> span").first
    assert knob.is_visible(), f"switch knob not visible in {theme_label}"

    track_bg = track.evaluate("el => window.getComputedStyle(el).backgroundColor")
    knob_bg = knob.evaluate("el => window.getComputedStyle(el).backgroundColor")

    track_rgb = _parse_rgb(track_bg)
    knob_rgb = _parse_rgb(knob_bg)

    track_lum = _relative_luminance(*track_rgb)
    knob_lum = _relative_luminance(*knob_rgb)
    ratio = _contrast_ratio(track_lum, knob_lum)

    return track_bg, knob_bg, ratio


def test_switch_unchecked_track_knob_contrast(page, live_server, seeded):
    """The unchecked switch track must have a non-transparent background and at
    least 3:1 contrast against the knob in both light and dark themes."""
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    _visit(page, live_server, "/dashboard/overview")

    # --- Light theme ---
    page.evaluate("() => document.documentElement.classList.remove('dark')")
    page.wait_for_timeout(300)

    track_bg_light, knob_bg_light, ratio_light = _switch_track_knob_contrast(page, "light")

    # Track must not be transparent
    assert track_bg_light != "rgba(0, 0, 0, 0)", (
        f"switch track background is transparent in light theme: {track_bg_light}"
    )
    assert "rgba(0, 0, 0, 0)" not in track_bg_light, (
        f"switch track background is transparent in light theme: {track_bg_light}"
    )

    assert ratio_light >= 3.0, (
        f"switch track ({track_bg_light}) vs knob ({knob_bg_light}) "
        f"contrast ratio {ratio_light:.2f}:1 in light theme is below 3:1 minimum"
    )

    # Close the menu before switching themes
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    # --- Dark theme ---
    page.evaluate("() => document.documentElement.classList.add('dark')")
    page.wait_for_timeout(300)

    track_bg_dark, knob_bg_dark, ratio_dark = _switch_track_knob_contrast(page, "dark")

    # Track must not be transparent
    assert track_bg_dark != "rgba(0, 0, 0, 0)", (
        f"switch track background is transparent in dark theme: {track_bg_dark}"
    )
    assert "rgba(0, 0, 0, 0)" not in track_bg_dark, (
        f"switch track background is transparent in dark theme: {track_bg_dark}"
    )

    assert ratio_dark >= 3.0, (
        f"switch track ({track_bg_dark}) vs knob ({knob_bg_dark}) "
        f"contrast ratio {ratio_dark:.2f}:1 in dark theme is below 3:1 minimum"
    )
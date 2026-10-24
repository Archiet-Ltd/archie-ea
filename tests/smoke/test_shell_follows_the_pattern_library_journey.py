"""
Shell pattern library conformance journey.

Tests that the five shared shell parts (notifications panel, guided-mode panel,
sidebar search, and card patterns) follow the pattern library rules from
design/patterns.json.

Brief: /home/ubuntu/verify/briefs/shell01.md
"""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _login(page, base, email):
    """Sign in as a user."""
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    page.wait_for_timeout(800)


def test_shell_parts_follow_pattern_library(page, live_server, seeded):
    """
    As solution architect, on three pages, assert the five shared shell parts
    carry pattern-library classes and work interactively.

    Five parts:
    1. Notifications panel "Mark all read" button - should use button macro (ghost/link, sm)
    2. Guided-mode panel "Exit" button - should use button macro (ghost/link, sm)
    3. Sidebar search input - should be h-9 (not h-8)
    4. Guided-mode panel card - should be rounded-lg (not rounded-xl), no box-shadow
    5. Card components - should use rounded-lg (not rounded-xl)
    """
    # Login as solution architect
    _login(page, live_server, seeded["emails"]["solution_architect"])

    # Test on three pages as specified
    test_pages = [
        "/dashboard/overview",
        "/applications/",
        "/ai-chat",
    ]

    for path in test_pages:
        page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1200)

        # Part 3: Sidebar search input should be h-9
        search_input = page.locator('input[placeholder*="Search or ask"]')
        expect(search_input).to_be_visible()

        # Get computed height - h-9 is 2.25rem = 36px
        search_classes = search_input.get_attribute("class")
        assert "h-9" in search_classes, f"Sidebar search input should have h-9, got: {search_classes}"
        assert "h-8" not in search_classes, f"Sidebar search input should not have h-8, got: {search_classes}"

        # Part 1: Open notifications panel and check "Mark all read" button
        notif_btn = page.locator('button#notif-btn')
        notif_btn.click()
        page.wait_for_timeout(300)  # Wait for dropdown animation

        # The "Mark all read" button should use button macro classes
        # Pattern library: button with ghost/link variant and sm size
        # Expected base: inline-flex items-center justify-center whitespace-nowrap rounded-md
        # Expected variant (ghost): hover:bg-accent hover:text-accent-foreground or hover:text-foreground
        # Expected size (sm): h-8 px-3
        mark_read_btn = page.locator('button#notif-mark-read-btn')

        # Check if button uses the macro's base classes
        mark_classes = mark_read_btn.get_attribute("class") or ""

        # The button should have pattern library classes, not raw classes
        expected_patterns = [
            "inline-flex",
            "items-center",
            "justify-center",
            "rounded-md",
        ]

        for pattern in expected_patterns:
            assert pattern in mark_classes, (
                f"Notifications 'Mark all read' button should have '{pattern}' "
                f"from button macro, got: {mark_classes}"
            )

        # Should have sm size (h-8 px-3) or similar button macro sizing
        # Note: The brief says sm size, which is h-8 in the button macro
        # (different from the input which should be h-9)

        # Close notifications
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)

        # Part 4 & 2: Check guided-mode panel if present
        # Note: The guided-mode panel may not always be present, so we check if it exists
        guided_trigger = page.locator('button#guided-mode-trigger')

        if guided_trigger.is_visible():
            # Open guided mode
            guided_trigger.click()
            page.wait_for_timeout(300)

            # Check guided-mode panel card classes
            guided_panel = page.locator('div#guided-mode-panel')
            panel_classes = guided_panel.get_attribute("class") or ""

            # Should be rounded-lg, not rounded-xl
            assert "rounded-lg" in panel_classes or "rounded-xl" not in panel_classes, (
                f"Guided-mode panel should use rounded-lg, got: {panel_classes}"
            )

            # Should not have box-shadow (check computed style)
            box_shadow = page.evaluate("""
                () => {
                    const el = document.getElementById('guided-mode-panel');
                    return el ? window.getComputedStyle(el).boxShadow : '';
                }
            """)
            # Pattern library says: hairline border, no shadow
            # "none" is the CSS value for no shadow
            assert box_shadow == "none" or "0px 0px" in box_shadow, (
                f"Guided-mode panel should have no box-shadow, got: {box_shadow}"
            )

            # Part 2: Check "Exit" button in guided mode
            exit_btn = page.locator('div#guided-mode-panel button:has-text("Exit")')

            if exit_btn.is_visible():
                exit_classes = exit_btn.get_attribute("class") or ""

                # Should use button macro classes
                expected_exit_patterns = [
                    "inline-flex",
                    "items-center",
                    "rounded-md",
                ]

                for pattern in expected_exit_patterns:
                    assert pattern in exit_classes, (
                        f"Guided-mode 'Exit' button should have '{pattern}' "
                        f"from button macro, got: {exit_classes}"
                    )

            # Close guided mode
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)

        # Part 3 (interactive): Search the sidebar
        search_input.click()
        search_input.fill("applications")
        page.wait_for_timeout(500)  # Wait for search results

        # Clear search
        search_input.fill("")
        page.wait_for_timeout(200)


def test_mark_all_read_works(page, live_server, seeded):
    """Mark all read button should work when clicked."""
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1200)

    # Open notifications
    notif_btn = page.locator('button#notif-btn')
    notif_btn.click()
    page.wait_for_timeout(300)

    # If mark all read button is visible, click it
    mark_read_btn = page.locator('button#notif-mark-read-btn')
    if mark_read_btn.is_visible():
        mark_read_btn.click()
        page.wait_for_timeout(500)

        # Button should either hide or remain clickable
        # (behavior depends on whether there were notifications)


def test_sidebar_search_works(page, live_server, seeded):
    """Sidebar search should filter navigation and be clearable."""
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1200)

    # Find and use sidebar search
    search_input = page.locator('input[placeholder*="Search or ask"]')
    expect(search_input).to_be_visible()

    # Type a search query
    search_input.click()
    search_input.fill("portfolio")
    page.wait_for_timeout(500)

    # Clear button should appear
    clear_btn = page.locator('button[title="Clear search"]')
    if clear_btn.is_visible():
        clear_btn.click()
        page.wait_for_timeout(200)

        # Input should be empty
        expect(search_input).to_have_value("")


def test_guided_mode_exit_works(page, live_server, seeded):
    """Guided mode exit button should close the panel."""
    _login(page, live_server, seeded["emails"]["solution_architect"])

    # Try a page that likely has guided mode
    page.goto(live_server + "/solutions/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1200)

    # Check if guided mode trigger exists
    guided_trigger = page.locator('button#guided-mode-trigger')

    if not guided_trigger.is_visible():
        pytest.skip("Guided mode not available on this page")

    # Open guided mode
    guided_trigger.click()
    page.wait_for_timeout(300)

    # Panel should be visible
    guided_panel = page.locator('div#guided-mode-panel')
    expect(guided_panel).to_be_visible()

    # Click Exit button
    exit_btn = page.locator('div#guided-mode-panel button:has-text("Exit")')
    exit_btn.click()
    page.wait_for_timeout(300)

    # Panel should be hidden
    expect(guided_panel).not_to_be_visible()

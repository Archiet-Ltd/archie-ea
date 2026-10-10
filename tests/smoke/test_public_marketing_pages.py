"""Capture the public marketing pages as evidence, and prove the redirects
resolve in a real browser, not just through the test client.

Follows tests/smoke/test_canvas_pages_screenshots.py's convention: a real
browser against the real server, both required viewports, screenshots
written for review. Anonymous throughout - none of these pages needs a
session, and asserting that stays true is part of the point.

Set SMOKE_SCREENSHOT_DIR to control the destination.
"""

from __future__ import annotations

import os
import pathlib

import pytest

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = {"1440x900": (1440, 900), "390x844": (390, 844)}

CONTENT_PAGES = [
    "pricing",
    "about",
    "security",
    "privacy",
    "terms",
    "contact",
    "features",
    "docs",
]


def _shot_dir():
    target = os.environ.get(
        "SMOKE_SCREENSHOT_DIR",
        str(pathlib.Path(__file__).resolve().parents[2] / "screenshots" / "public-pages"),
    )
    path = pathlib.Path(target)
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.mark.parametrize("slug", CONTENT_PAGES)
@pytest.mark.parametrize("viewport_name", sorted(VIEWPORTS))
def test_capture_public_page_no_overflow(browser, live_server, viewport_name, slug):
    """Every marketing/legal page renders, anonymously, with no horizontal
    overflow at either required width."""
    width, height = VIEWPORTS[viewport_name]
    ctx = browser.new_context(viewport={"width": width, "height": height})
    page = ctx.new_page()
    try:
        response = page.goto(f"{live_server}/{slug}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 200, f"/{slug} returned {response.status}"
        page.wait_for_timeout(500)

        scroll_width = page.evaluate("() => document.documentElement.scrollWidth")
        viewport_width = page.evaluate("() => window.innerWidth")
        assert scroll_width <= viewport_width, (
            f"/{slug} at {viewport_name}: horizontal scroll "
            f"(scrollWidth={scroll_width}, innerWidth={viewport_width})"
        )

        out = _shot_dir() / f"{slug}-{viewport_name}.png"
        page.screenshot(path=str(out), full_page=True)
        assert out.exists() and out.stat().st_size > 0
        print(f"[screenshot] {out}")
    finally:
        ctx.close()


@pytest.mark.parametrize("path", ["/signup", "/register"])
def test_redirect_reaches_the_real_sign_up_form(browser, live_server, path):
    """/signup and /register land on the real sign-up form in a real browser,
    anonymously - not a second form."""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        response = page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 200, f"{path} did not resolve, got {response.status}"
        assert page.url.rstrip("/").endswith("/account/register"), (
            f"{path} landed on {page.url}, not /account/register"
        )
        assert page.locator("h1", has_text="Create an account").count() == 1
    finally:
        ctx.close()


def test_home_page_footer_and_navbar_links_are_clickable(browser, live_server):
    """The links the footer and navbar add actually navigate, not just render."""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(300)

        page.locator('a[href="/pricing"]').first.click()
        page.wait_for_url(lambda url: url.rstrip("/").endswith("/pricing"), timeout=PAGE_TIMEOUT)
        assert page.locator("h1", has_text="Priced per company").count() == 1

        page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(300)
        page.locator('footer a[href="/about"]').first.click()
        page.wait_for_url(lambda url: url.rstrip("/").endswith("/about"), timeout=PAGE_TIMEOUT)
        assert page.locator("h1", has_text="We are building Enterprise Intelligence Management").count() == 1
    finally:
        ctx.close()

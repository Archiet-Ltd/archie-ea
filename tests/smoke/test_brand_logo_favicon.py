"""The real Entelim logo and favicon set, and the login page's brand copy.

Follows tests/smoke/test_public_marketing_pages.py's convention (real browser
against the real server, no DB seeding needed for the public surfaces) for the
visual checks, and tests/smoke/test_login_page_offers_no_saml_link.py's
convention (plain `requests` against `live_server`) for the text/meta checks
that don't need a rendered page.

Covers the T-entelim-logo-and-favicon brief: the placeholder favicon and
plain-text wordmark are replaced by the real logo everywhere it renders, the
favicon set referenced from partials/_head.html actually resolves, the login
page's brand panel (desktop and mobile) carries the real mark and the
"Enterprise Intelligence Management" kicker instead of the old "Enterprise
Architecture Platform" wording, and the homepage no longer claims a
"enter your website address" feature that does not exist.
"""

from __future__ import annotations

import os
import pathlib

import pytest
import requests

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

FAVICON_FILES = [
    "favicon.svg",
    "favicon.ico",
    "favicon-16x16.png",
    "favicon-32x32.png",
    "apple-touch-icon.png",
    "android-chrome-192x192.png",
    "android-chrome-512x512.png",
    "site.webmanifest",
]

BRAND_LOGO_FILES = [
    "brand/entelim-logo.svg",
    "brand/entelim-share.png",
]


def _shot_dir():
    target = os.environ.get(
        "SMOKE_SCREENSHOT_DIR",
        str(pathlib.Path(__file__).resolve().parents[2] / "screenshots" / "brand"),
    )
    path = pathlib.Path(target)
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.mark.parametrize("filename", FAVICON_FILES)
def test_favicon_set_asset_resolves(live_server, filename):
    response = requests.get(f"{live_server}/static/{filename}", timeout=30)
    assert response.status_code == 200, f"/static/{filename} returned {response.status_code}"
    assert len(response.content) > 0


@pytest.mark.parametrize("filename", BRAND_LOGO_FILES)
def test_brand_logo_asset_resolves(live_server, filename):
    response = requests.get(f"{live_server}/static/{filename}", timeout=30)
    assert response.status_code == 200, f"/static/{filename} returned {response.status_code}"
    assert len(response.content) > 0


def test_homepage_head_references_full_favicon_set(live_server):
    response = requests.get(live_server + "/", timeout=30)
    assert response.status_code == 200
    text = response.text
    # static URLs carry a '?v=<build-id>' cache-busting suffix (app/_bootstrap/
    # assets.py) -- match the href prefix rather than the whole attribute.
    assert 'rel="icon" type="image/svg+xml" href="/static/favicon.svg' in text
    assert 'rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32x32.png' in text
    assert 'rel="icon" type="image/png" sizes="16x16" href="/static/favicon-16x16.png' in text
    assert 'rel="shortcut icon" href="/static/favicon.ico' in text
    assert 'rel="apple-touch-icon" sizes="180x180" href="/static/apple-touch-icon.png' in text
    assert 'rel="manifest" href="/static/site.webmanifest' in text


def test_homepage_meta_description_has_no_website_address_claim(live_server):
    """The 'enter your website address' claim was banned by the lead's 6 Oct
    ruling -- the product has no such feature. Fetches the live-built page,
    not just the template source, per the brief's acceptance criterion."""
    response = requests.get(live_server + "/", timeout=30)
    assert response.status_code == 200
    assert "enter your website address" not in response.text


def test_homepage_has_og_and_twitter_share_image(live_server):
    response = requests.get(live_server + "/", timeout=30)
    assert response.status_code == 200
    text = response.text
    assert 'property="og:image"' in text
    assert "brand/entelim-share.png" in text
    assert 'name="twitter:card" content="summary_large_image"' in text
    assert 'name="twitter:image"' in text


def test_login_page_kicker_reads_enterprise_intelligence_management(live_server):
    response = requests.get(live_server + "/account/login", timeout=30)
    assert response.status_code == 200
    assert "Enterprise Intelligence Management" in response.text
    assert "Enterprise Architecture Platform" not in response.text


def test_login_page_brand_marks_use_the_real_logo_not_the_placeholder(live_server):
    response = requests.get(live_server + "/account/login", timeout=30)
    assert response.status_code == 200
    text = response.text
    assert 'data-lucide="layout-grid"' not in text
    assert text.count("brand/entelim-logo.svg") == 2, (
        "expected both the desktop brand panel and the mobile fallback to "
        "reference the real logo"
    )


@pytest.mark.parametrize("viewport_name,width,height", [("desktop", 1440, 900), ("mobile", 390, 844)])
def test_capture_login_page_brand_panel(browser, live_server, viewport_name, width, height):
    """Visual evidence for the login page's brand panel (desktop) and the
    mobile fallback view, per the brief's screenshot requirement."""
    ctx = browser.new_context(viewport={"width": width, "height": height})
    page = ctx.new_page()
    try:
        response = page.goto(live_server + "/account/login", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert response.status == 200
        page.wait_for_timeout(500)
        page.screenshot(path=str(_shot_dir() / f"login-{viewport_name}.png"), full_page=False)
    finally:
        ctx.close()


def test_capture_public_navbar_and_footer(browser, live_server):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        response = page.goto(live_server + "/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert response.status == 200
        page.wait_for_timeout(500)
        navbar = page.locator("header").first
        navbar.screenshot(path=str(_shot_dir() / "navbar.png"))
        footer = page.locator("footer").first
        footer.scroll_into_view_if_needed()
        footer.screenshot(path=str(_shot_dir() / "footer.png"))
    finally:
        ctx.close()


def test_capture_admin_page_with_real_logo(browser, live_server, seeded):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        response = page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert response.status == 200
        page.wait_for_timeout(500)
        sidebar = page.get_by_test_id("sidebar")
        sidebar.screenshot(path=str(_shot_dir() / "admin-sidebar.png"))
        page.screenshot(path=str(_shot_dir() / "admin-dashboard.png"), full_page=False)
    finally:
        page.close()

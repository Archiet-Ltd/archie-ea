"""A visitor's pages, the sitemap and the link previews all name the site's configured address.

The goal: someone shares the home page, a role page or a recording; the address on the preview card, the canonical
link and the sitemap entry is the site's own address, not an old one and not the private address of the machine that
answered. The server here is configured with an address of its own, so only a page that reads the configured address
can pass. Every internal link the visitor clicks lands on a page with content.
"""

import re
import tempfile
from pathlib import Path

import pytest

from app.services.origin import canonical_origin
from app.services.public_pages import MODULE_RECORDINGS

from .conftest import PAGE_TIMEOUT, boot_live_server

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

CONFIGURED = "https://configured-address.example"
SLUG = sorted(MODULE_RECORDINGS)[0]
VIEWPORTS = {"1440": {"width": 1440, "height": 900}, "390": {"width": 390, "height": 844}}


@pytest.fixture(scope="module")
def configured_server(request, app):
    return boot_live_server(request, None, app, extra_env={"PUBLIC_BASE_URL": CONFIGURED})


def _head(page, selector, attribute):
    return page.locator(selector).first.get_attribute(attribute)


def test_the_helper_reads_the_configured_address(app):
    previous = app.config.get("PUBLIC_BASE_URL")
    app.config["PUBLIC_BASE_URL"] = CONFIGURED
    try:
        with app.app_context():
            assert canonical_origin() == CONFIGURED
    finally:
        app.config["PUBLIC_BASE_URL"] = previous


@pytest.mark.parametrize("size", sorted(VIEWPORTS))
def test_pages_and_previews_name_the_configured_address(browser, configured_server, size, tmp_path):
    base = str(configured_server)
    context = browser.new_context(viewport=VIEWPORTS[size])
    page = context.new_page()
    try:
        for path in ("/", "/roles", "/see/%s" % SLUG):
            response = page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert response.status == 200, path
            expected = CONFIGURED + (path if path != "/" else "/")
            assert _head(page, "link[rel=canonical]", "href").rstrip("/") == expected.rstrip("/"), path
            og_url = _head(page, "meta[property='og:url']", "content")
            assert og_url.rstrip("/") == expected.rstrip("/"), path
            for tag in ("og:image", "og:video"):
                found = page.locator("meta[property='%s']" % tag)
                if found.count():
                    assert found.first.get_attribute("content").startswith(CONFIGURED + "/"), (path, tag)
            assert "entelim.org" not in page.content().split("<body", 1)[0], path
        shots = Path(tempfile.gettempdir()) / "configured-address-shots"
        shots.mkdir(exist_ok=True)
        page.screenshot(path=str(shots / ("share-%s.png" % size)), full_page=True)

        index = page.goto(base + "/sitemap.xml", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT).text()
        listed = re.findall(r"<loc>([^<]+)</loc>", index)
        assert listed and all(loc.startswith(CONFIGURED + "/") for loc in listed), listed
        sitemap = page.goto(base + listed[0][len(CONFIGURED):], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT).text()
        locs = re.findall(r"<loc>([^<]+)</loc>", sitemap)
        assert len(locs) > 10
        assert all(loc.startswith(CONFIGURED + "/") or loc == CONFIGURED for loc in locs), [
            loc for loc in locs if not loc.startswith(CONFIGURED)][:3]
        for path in ("/roles", "/see/%s" % SLUG):
            assert CONFIGURED + path in locs or CONFIGURED + path + "/" in locs, path
        robots = page.goto(base + "/robots.txt", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT).text()
        assert "Sitemap: %s/sitemap.xml" % CONFIGURED in robots

        # a role card on the roles page opens a page with content
        page.goto(base + "/roles", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.locator("[data-role-card]").first.click()
        page.wait_for_url("**/roles/*", timeout=PAGE_TIMEOUT)
        assert page.locator("h1").first.inner_text().strip()
        assert len(page.locator("main").inner_text().strip()) > 200

        # the share page's way back and its call to action open pages with content
        page.goto(base + "/see/%s" % SLUG, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("share-module-link").click()
        page.wait_for_url("**/modules/%s" % SLUG, timeout=PAGE_TIMEOUT)
        assert page.locator("h1").first.inner_text().strip()
        page.goto(base + "/see/%s" % SLUG, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("share-cta").click()
        page.wait_for_load_state("domcontentloaded")
        assert page.locator("h1").first.inner_text().strip()
        assert len(page.locator("body").inner_text().strip()) > 100
    finally:
        context.close()

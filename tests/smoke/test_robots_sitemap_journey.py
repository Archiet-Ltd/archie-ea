"""A search engine can read the early-access pages: robots.txt allows them and the sitemap lists them.

Journey: signed out, someone checking why the site is not found in search opens /robots.txt and reads that
everything is allowed except the signed-in areas, opens /sitemap.xml and finds the early-access pages listed,
applies the rules to three of those pages and to the home page with a tracking query string, then opens each
page and reads it (its name, the waiting-list form, nothing telling a search engine to skip it), at 1440 and 390
wide. A signed-in area is read as disallowed.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from test_public_content_pages import _parse_robots_rules, _robots_allows

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SIZES = {"1440": {"width": 1440, "height": 900}, "390": {"width": 390, "height": 844}}


def _shot(page, name):
    folder = os.environ.get("SMOKE_SCREENSHOT_DIR")
    if folder:
        os.makedirs(folder, exist_ok=True)
        page.screenshot(path=os.path.join(folder, name + ".png"))


def _text(page, url):
    response = page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response.status == 200, url
    return response, response.text()


@pytest.mark.parametrize("size", sorted(SIZES))
def test_search_engines_may_read_the_early_access_pages(browser, live_server, size):
    context = browser.new_context(viewport=SIZES[size])
    page = context.new_page()
    try:
        _response, robots = _text(page, live_server + "/robots.txt")
        _shot(page, f"robots-{size}")
        assert "Allow: /" in robots and f"Sitemap: {live_server}/sitemap.xml" in robots
        assert "Disallow: /\n" not in robots + "\n"
        assert "Disallow: /dashboard/" in robots and "Disallow: /api/" in robots

        _response, sitemap = _text(page, live_server + "/sitemap.xml")
        listed = re.findall(re.escape(live_server) + r"(/early-access[^\s<]*)", sitemap)
        pages = [p for p in listed if p != "/early-access"]
        assert "/early-access" in listed and len(pages) >= 3, listed
        assert len(listed) == len(set(listed)), "an early-access page is listed twice"

        for agent in ("*", "Googlebot"):
            rules = _parse_robots_rules(robots, agent)
            for path in ["/early-access"] + pages[:3] + ["/?utm_source=newsletter&utm_medium=email"]:
                assert _robots_allows(rules, path), f"{path} is blocked for {agent}"
            assert not _robots_allows(rules, "/dashboard/overview")
            assert not _robots_allows(rules, "/applications/")

        for path in pages[:3]:
            response, body = _text(page, live_server + path)
            title = page.locator("h1").first
            expect(title).to_be_visible()
            assert title.inner_text().strip(), path
            expect(page.get_by_test_id("early-access-page")).to_be_visible()
            expect(page.get_by_role("heading", name="Join the waiting list")).to_be_visible()
            assert "noindex" not in (response.headers.get("x-robots-tag") or ""), path
            assert page.locator('meta[name="robots"][content="noindex"]').count() == 0, path
            _shot(page, f"early-access-{size}-{path.rsplit('/', 1)[1][:30]}")
    finally:
        context.close()


@pytest.mark.parametrize("size", sorted(SIZES))
def test_a_visitor_finds_the_early_access_pages_from_the_home_page_footer(browser, live_server, size):
    """From the home page: the footer's Early access link opens the hub, the hub's first page opens with its form,
    and a crawler reading robots.txt may follow every one of those links, an old address and the same page with a
    query string, while the API documentation stays closed."""
    context = browser.new_context(viewport=SIZES[size])
    page = context.new_page()
    try:
        page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        link = page.locator("footer a", has_text=re.compile(r"^\s*Early access\s*$")).first
        link.scroll_into_view_if_needed()
        _shot(page, f"footer-{size}")
        link.click()
        page.wait_for_url(re.compile(r"/early-access$"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("early-access-hub")).to_be_visible()
        first = page.locator('[data-testid="early-access-hub"] li a').first
        name, target = first.inner_text().strip(), first.get_attribute("href")
        _shot(page, f"hub-{size}")
        first.click()
        page.wait_for_url(re.compile(r"/early-access/[^/]+$"), timeout=PAGE_TIMEOUT)
        assert page.locator("h1").first.inner_text().strip() == name
        expect(page.get_by_role("heading", name="Join the waiting list")).to_be_visible()
        _shot(page, f"page-{size}")

        _response, robots = _text(page, live_server + "/robots.txt")
        for agent in ("*", "Googlebot"):
            rules = _parse_robots_rules(robots, agent)
            for path in ("/early-access", target, target + "?x=1", "/vs/avolution", "/vs/orbus", "/features"):
                assert _robots_allows(rules, path), f"{path} is blocked for {agent}"
            for path in ("/settings", "/settings?x=1", "/account/manage?x=1", "/apidocs/", "/flasgger_static/swagger-ui.css"):
                assert not _robots_allows(rules, path), f"{path} is open to {agent}"
    finally:
        context.close()

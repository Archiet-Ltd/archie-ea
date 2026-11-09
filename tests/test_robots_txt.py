"""robots.txt lets search engines read every public page and keeps every signed-in area out.

The file is generated from the routes and the public-site registry, so a public page added later is crawlable
without a line being written for it. These tests apply Google's rules (the longest matching rule wins, ``*`` and
``$`` are honoured) to what a crawler would actually meet: every sitemap address and every link in the public
header, footer, /features and /early-access.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import pytest

from test_public_content_pages import _parse_robots_rules, _robots_allows

AGENTS = ["*", "Googlebot", "Bingbot", "GPTBot", "ClaudeBot", "PerplexityBot"]

SIGNED_IN_PATHS = [
    "/dashboard/overview", "/applications/", "/applications/vendors", "/admin/", "/admin/connectors/github",
    "/api/v1/capabilities/", "/t/some-organisation/", "/account/manage", "/account/welcome", "/arb/",
    "/capability-map/", "/ai-chat/", "/settings/", "/portfolio/", "/risks/", "/roadmaps/", "/vendors/",
    "/procurement/", "/compliance/", "/archimate/diagrams", "/solutions/", "/frameworks/", "/modules",
]

PAGES_WITH_LINKS = ["/", "/features", "/early-access"]


@pytest.fixture(scope="module")
def client(app):
    return app.test_client()


@pytest.fixture(scope="module")
def robots_text(client):
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    return response.get_data(as_text=True)


@pytest.fixture(scope="module")
def sitemap_paths(client):
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    return re.findall(r"<loc>https://entelim\.org([^<]*)</loc>", response.get_data(as_text=True))


def _hrefs(html: str) -> set[str]:
    found = set()
    for href in re.findall(r'href="([^"]+)"', html):
        if href.startswith("https://entelim.org"):
            href = href[len("https://entelim.org"):] or "/"
        if href.startswith("/") and not href.startswith("//"):
            found.add(urlsplit(href).path)
    return found


@pytest.fixture(scope="module")
def linked_paths(client):
    paths = set()
    for page in PAGES_WITH_LINKS:
        response = client.get(page)
        assert response.status_code == 200, page
        paths |= _hrefs(response.get_data(as_text=True))
    return paths


def test_robots_txt_names_the_sitemap_and_allows_everything_by_default(robots_text):
    assert "Sitemap: https://entelim.org/sitemap.xml" in robots_text
    assert "Allow: /\n" in robots_text
    assert "Disallow: /\n" not in robots_text


@pytest.mark.parametrize("agent", AGENTS)
def test_every_sitemap_address_and_public_link_is_allowed(robots_text, sitemap_paths, linked_paths, agent):
    rules = _parse_robots_rules(robots_text, agent)
    assert sitemap_paths, "the sitemap is empty"
    blocked = sorted(p for p in set(sitemap_paths) | linked_paths if not _robots_allows(rules, p))
    assert not blocked, f"robots.txt blocks public pages for {agent}: {blocked}"


@pytest.mark.parametrize("agent", AGENTS)
def test_crawler_files_stay_reachable(robots_text, agent):
    rules = _parse_robots_rules(robots_text, agent)
    for path in ("/robots.txt", "/sitemap.xml", "/llms.txt", "/llms-full.txt", "/favicon.ico", "/static/css/site.css"):
        assert _robots_allows(rules, path), path


@pytest.mark.parametrize("agent", AGENTS)
def test_signed_in_areas_are_disallowed(robots_text, agent):
    rules = _parse_robots_rules(robots_text, agent)
    allowed = [p for p in SIGNED_IN_PATHS if _robots_allows(rules, p)]
    assert not allowed, f"signed-in paths a crawler may read for {agent}: {allowed}"


def test_every_route_that_sends_a_visitor_to_sign_in_is_disallowed(app, client, robots_text):
    """Measured, not listed: a route that answers a signed-out visitor with a redirect to sign-in is a signed-in
    page, so robots.txt must block it."""
    rules = _parse_robots_rules(robots_text, "Googlebot")
    leaks = []
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.arguments or rule.rule.startswith("/static"):
            continue
        response = client.get(rule.rule)
        location = response.headers.get("Location", "")
        if response.status_code in (301, 302, 303, 307) and "/account/login" in location and rule.rule != "/account/login":
            if _robots_allows(rules, rule.rule):
                leaks.append(rule.rule)
    assert not leaks, f"signed-in pages a crawler may read: {sorted(leaks)}"


@pytest.mark.parametrize("agent", AGENTS)
@pytest.mark.parametrize("path", ["/?utm_source=newsletter", "/?ref=a&b=c", "/?"])
def test_home_page_with_a_query_string_is_allowed(robots_text, agent, path):
    assert _robots_allows(_parse_robots_rules(robots_text, agent), path)


def test_every_early_access_page_is_in_the_sitemap_once_and_can_be_indexed(client, sitemap_paths):
    hub = client.get("/early-access")
    pages = sorted(p for p in _hrefs(hub.get_data(as_text=True)) if p.startswith("/early-access/"))
    assert len(pages) >= 3, "the early-access hub lists no pages"
    assert "/early-access" in sitemap_paths
    for page in pages:
        assert sitemap_paths.count(page) == 1, f"{page} is not in the sitemap exactly once"
        response = client.get(page)
        html = response.get_data(as_text=True)
        assert response.status_code == 200 and "Join the waiting list" in html, page
        assert "noindex" not in (response.headers.get("X-Robots-Tag") or ""), f"{page} tells crawlers not to index it"
        assert 'content="noindex"' not in html, f"{page} carries a noindex tag"
    assert 'content="noindex"' not in hub.get_data(as_text=True)
    assert len(sitemap_paths) == len(set(sitemap_paths)), "the sitemap lists an address twice"


def test_the_same_page_shown_in_place_of_a_closed_address_is_not_indexed(app, client):
    from app.release_waves.routes import early_access_page

    hub = client.get("/early-access").get_data(as_text=True)
    slug = sorted(p for p in _hrefs(hub) if p.startswith("/early-access/"))[0].rsplit("/", 1)[1]
    with app.test_request_context("/modules/some-closed-address"):
        response = early_access_page(slug)
        assert response.headers.get("X-Robots-Tag") == "noindex"
        assert 'content="noindex"' in response.get_data(as_text=True)


def test_pages_that_redirect_to_sign_in_are_not_in_the_sitemap(sitemap_paths):
    for path in ("/solutions/", "/solutions", "/frameworks/", "/frameworks", "/modules", "/modules/"):
        assert path not in sitemap_paths, path

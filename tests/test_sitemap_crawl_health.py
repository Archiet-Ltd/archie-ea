"""CI crawl-health gate: every sitemap.xml URL is reachable and correctly
tagged, so a regression here fails CI instead of surfacing on the next
manual SEO pass.

Against the Flask test client -- not a live network crawl -- same pattern
as every other public-page test in this suite (see
tests/test_public_content_pages.py, tests/test_public_jsonld.py).

Checks, for every URL currently listed in /sitemap.xml:
  1. it returns 200 (no 404s);
  2. it has a non-empty <meta name="description">;
  3. it has a <link rel="canonical"> pointing at its own entelim.org URL --
     every public page is self-canonical, with no exception, including a
     comparison page that also carries an archiet.ai external_url for
     cross-linking only (see app/services/public_pages.py::self_canonical_url
     and test_comparison_external_url in test_public_content_pages.py).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from app.services.public_pages import SITE_URL, load_all_pages, load_feed_pages

_LOC_RE = re.compile(r"<loc>([^<]+)</loc>")
_DESCRIPTION_RE = re.compile(r'<meta\s+name="description"\s+content="([^"]*)"')
_CANONICAL_RE = re.compile(r'<link\s+rel="canonical"\s+href="([^"]*)"')

# Views that render a public page but are not backed by a PublicPage from
# load_all_pages(): the home page and the two hub indexes. Each is
# self-referencing by construction -- see main/index.html, public/vs_hub.html
# and public/use_cases_index.html.
_NON_CONTENT_PAGE_PATHS = {"/", "/vs", "/use-cases"}


def _sitemap_paths(client) -> list[str]:
    rv = client.get("/sitemap.xml")
    assert rv.status_code == 200
    xml = rv.data.decode()
    locs = _LOC_RE.findall(xml)
    assert locs, "sitemap.xml returned no <loc> entries"
    return [urlparse(loc).path for loc in locs]


def _expected_canonical(path: str, pages_by_url: dict) -> str:
    # Every public page is self-canonical -- see self_canonical_url -- so
    # the expected value is always this page's own URL on this domain,
    # whether or not a PublicPage backs this path (the home page and the
    # two hub indexes do not).
    return SITE_URL + path


def test_every_sitemap_url_is_crawlable_and_tagged(app):
    """No sitemap URL 404s; every one has a description and its canonical."""
    # load_feed_pages(), not load_all_pages(): the sitemap itself is built
    # from the feed set, which leaves out any page withdrawn from discovery
    # (state: not_planned) -- see app/services/public_pages.py::load_feed_pages.
    pages_by_url = {p.url: p for p in load_feed_pages()}

    with app.test_client() as client:
        paths = _sitemap_paths(client)
        # Same shape as test_sitemap_xml_lists_the_homepage_once_with_top_priority:
        # +3 non-content URLs (home, /vs, /use-cases) over load_feed_pages().
        assert len(paths) == len(pages_by_url) + len(_NON_CONTENT_PAGE_PATHS)

        for path in paths:
            rv = client.get(path)
            assert rv.status_code == 200, f"{path} returned {rv.status_code}"
            html = rv.data.decode()

            description_match = _DESCRIPTION_RE.search(html)
            assert description_match is not None, f"{path}: no meta description"
            assert description_match.group(1).strip(), f"{path}: empty meta description"

            canonical_match = _CANONICAL_RE.search(html)
            assert canonical_match is not None, f"{path}: no canonical link"
            expected = _expected_canonical(path, pages_by_url)
            assert canonical_match.group(1) == expected, (
                f"{path}: canonical is {canonical_match.group(1)!r}, "
                f"expected {expected!r}"
            )


def test_every_page_canonical_is_self_referencing(app):
    """Every public page -- including a comparison page that also carries
    an archiet.ai external_url for cross-linking only -- is canonical to
    its own entelim.org URL, with no exception."""
    for page in load_all_pages():
        assert page.self_canonical_url == SITE_URL + page.url, (
            f"{page.url}: canonical is not self-referencing"
        )


# ── IndexNow: the same feed-set exclusion as the sitemap ──────────────────


def test_ping_indexnow_excludes_a_withdrawn_page(app, monkeypatch):
    """The `ping-indexnow` CLI command (app/commands/indexnow_commands.py)
    builds its URL list from public_pages.feed_page_paths(), the same path
    list the sitemap is built from -- a page withdrawn from discovery
    (state: not_planned) must not be submitted to IndexNow either.

    Exercises the real command (not a re-implementation of its URL-building
    logic) with the network call swapped out, so a regression in the
    command's own loader choice is caught here.
    """
    withdrawn = [
        p for p in load_all_pages()
        if p.front_matter.get("state") == "not_planned"
    ]
    assert withdrawn, (
        "expected at least one withdrawn page (state: not_planned) to exist "
        "on this branch to exercise the exclusion"
    )

    captured = {}

    def _fake_ping_indexnow(app, urls, base_url=None):
        captured["urls"] = list(urls)
        return {"status_code": 200, "body": "ok"}

    monkeypatch.setattr(
        "app.services.indexnow_service.ping_indexnow", _fake_ping_indexnow
    )

    runner = app.test_cli_runner()
    result = runner.invoke(args=["ping-indexnow"])
    assert result.exit_code == 0, result.output

    submitted = captured.get("urls")
    assert submitted is not None, "ping-indexnow did not call ping_indexnow (is INDEXNOW_API_KEY unset?)"

    for page in withdrawn:
        assert not any(url.endswith(page.url) for url in submitted), (
            f"ping-indexnow submitted withdrawn page {page.url}: {submitted}"
        )

    feed_pages = load_feed_pages()
    # +3: the site root "/" plus the /vs and /use-cases hub views, which
    # are not PublicPage content and so are not in feed_pages -- see
    # public_pages.feed_page_paths(), the one path list this command and
    # /sitemap.xml are both built from (D-26: a manual ping used to miss
    # the two hub pages the sitemap always listed).
    assert len(submitted) == len(feed_pages) + 3
    assert any(url.endswith("/vs") for url in submitted)
    assert any(url.endswith("/use-cases") for url in submitted)
    for page in feed_pages:
        assert any(url.endswith(page.url) for url in submitted), (
            f"ping-indexnow did not submit live page {page.url}"
        )

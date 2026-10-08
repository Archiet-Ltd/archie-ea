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
     every public page is self-canonical, with no exception, including
     comparison pages that also carry a cross-link to archiet.ai (SEO/GEO
     audit item 1/2 -- see app/services/public_pages.py::PublicPage.self_canonical_url
     and test_every_public_page_has_a_self_canonical_link in
     test_public_content_pages.py, which already covers that case).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from app.services.public_pages import HELD_PAGE_URLS, MERGED_PAGES, SITE_URL, load_all_pages

_LOC_RE = re.compile(r"<loc>([^<]+)</loc>")
_DESCRIPTION_RE = re.compile(r'<meta\s+name="description"\s+content="([^"]*)"')
_CANONICAL_RE = re.compile(r'<link\s+rel="canonical"\s+href="([^"]*)"')

# Views that render a public page but are not backed by a PublicPage from
# load_all_pages(): the home page and the two hub indexes. Each is
# self-referencing by construction -- see main/index.html, public/vs_hub.html
# and public/use_cases_index.html.
_NON_CONTENT_PAGE_PATHS = {"/", "/vs", "/use-cases"}


def _indexable(pages):
    """Pages the sitemap carries -- excludes MERGE-verdict (301s elsewhere)
    and HOLD-verdict (reachable but noindexed) pages, same filter as
    test_public_content_pages.py::_indexable."""
    return [p for p in pages if p.url not in MERGED_PAGES and p.url not in HELD_PAGE_URLS]


def _sitemap_paths(client) -> list[str]:
    rv = client.get("/sitemap.xml")
    assert rv.status_code == 200
    xml = rv.data.decode()
    locs = _LOC_RE.findall(xml)
    assert locs, "sitemap.xml returned no <loc> entries"
    return [urlparse(loc).path for loc in locs]


def test_every_sitemap_url_is_crawlable_and_tagged(app):
    """No sitemap URL 404s; every one has a description and its canonical."""
    pages_by_url = {p.url: p for p in _indexable(load_all_pages())}

    with app.test_client() as client:
        paths = _sitemap_paths(client)
        # Same shape as test_sitemap_xml_lists_the_homepage_once_with_top_priority:
        # +3 non-content URLs (home, /vs, /use-cases) over the indexable pages.
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
            expected = SITE_URL + path
            assert canonical_match.group(1) == expected, (
                f"{path}: canonical is {canonical_match.group(1)!r}, "
                f"expected {expected!r}"
            )


def test_every_page_canonical_is_self_referencing(app):
    """Every public page's canonical is its own URL, with no exception --
    including comparison pages, which used to canonicalize to archiet.ai
    (SEO/GEO audit item 1/2)."""
    for page in load_all_pages():
        assert page.self_canonical_url == SITE_URL + page.url, (
            f"{page.url}: canonical is not self-referencing"
        )

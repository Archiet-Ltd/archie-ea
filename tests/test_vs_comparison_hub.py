"""Tests for the /vs comparison hub and its new comparison pages.

Covers the hub page (GET /vs) linking to every real comparison page URL,
the 6 new competitor comparison pages added alongside it (200 + one h1,
FAQ JSON-LD, pricing link, sourced front matter, canonical archiet.ai
link, no unflagged invented price), and the hub's entry in sitemap.xml.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

from app.services.public_pages import (
    CONTENT_ROOT,
    load_all_pages,
    load_page,
)

# ── /vs comparison hub (SEO: new competitor comparison pages) ─────────────

NEW_VS_SLUGS = [
    "avolution-abacus",
    "orbus-iserver",
    "sparx-enterprise-architect",
    "servicenow-apm",
    "archi",
    "boc-adoit",
]

ALL_VS_SLUGS = ["leanix", "ardoq", "bizzdesign-hopex", *NEW_VS_SLUGS]


def test_vs_hub_returns_200_and_lists_all_comparison_pages(app):
    """GET /vs returns 200 and links to every comparison page at its real URL."""
    comparison_pages = [p for p in load_all_pages() if p.family == "comparison"]
    assert len(comparison_pages) == 9, (
        f"expected 9 comparison pages (3 existing + 6 new), found {len(comparison_pages)}"
    )
    with app.test_client() as client:
        rv = client.get("/vs")
        assert rv.status_code == 200
        html = rv.data.decode()
        for page in comparison_pages:
            real_url = page.external_url or f"https://entelim.org{page.url}"
            assert real_url in html, (
                f"/vs hub missing link to {real_url} ({page.slug})"
            )


def test_vs_hub_has_exactly_one_h1(app):
    """The /vs hub page has exactly one h1."""
    import re

    with app.test_client() as client:
        html = client.get("/vs").data.decode()
        assert len(re.findall(r"<h1[^>]*>", html)) == 1


def test_all_new_vs_slugs_covered_by_this_test_module():
    """Guard against the new-page list drifting from what's really on disk."""
    on_disk = {
        p.stem for p in (CONTENT_ROOT / "vs").glob("*.md")
    }
    assert on_disk == set(ALL_VS_SLUGS), (
        f"content/pages/vs/ has {sorted(on_disk)}, test expects {sorted(ALL_VS_SLUGS)}"
    )


def test_new_vs_pages_return_200_with_one_h1(app):
    """Each of the 6 new comparison pages returns 200 with exactly one h1."""
    import re

    with app.test_client() as client:
        for slug in NEW_VS_SLUGS:
            rv = client.get(f"/vs/{slug}")
            assert rv.status_code == 200, f"/vs/{slug} returned {rv.status_code}"
            html = rv.data.decode()
            h1s = re.findall(r"<h1[^>]*>", html)
            assert len(h1s) == 1, f"/vs/{slug}: expected exactly one h1, found {len(h1s)}"


def _faq_node(ld: dict) -> dict | None:
    """The FAQPage node in one page's JSON-LD, whether or not it's wrapped
    in an @graph alongside a BreadcrumbList (every comparison page gets
    one now -- see app/services/public_pages.py build_jsonld)."""
    nodes = ld["@graph"] if "@graph" in ld else [ld]
    return next((n for n in nodes if n.get("@type") == "FAQPage"), None)


def test_new_vs_pages_have_faq_jsonld_with_entries(app):
    """Each new comparison page has valid FAQPage JSON-LD with at least one entry."""
    import re

    with app.test_client() as client:
        for slug in NEW_VS_SLUGS:
            html = client.get(f"/vs/{slug}").data.decode()
            ld_match = re.search(
                r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                html,
                re.DOTALL,
            )
            assert ld_match is not None, f"/vs/{slug}: no JSON-LD script found"
            ld = json.loads(ld_match.group(1))
            faq = _faq_node(ld)
            assert faq is not None, f"/vs/{slug}: expected a FAQPage node"
            assert len(faq.get("mainEntity", [])) > 0, f"/vs/{slug}: FAQPage mainEntity is empty"
            for item in faq["mainEntity"]:
                assert item["@type"] == "Question"
                assert len(item["name"]) > 0
                assert len(item["acceptedAnswer"]["text"]) > 0


def test_new_vs_pages_link_to_pricing(app):
    """Each new comparison page links to /pricing."""
    with app.test_client() as client:
        for slug in NEW_VS_SLUGS:
            html = client.get(f"/vs/{slug}").data.decode()
            assert 'href="/pricing"' in html, f"/vs/{slug}: no link to /pricing"


def test_new_vs_pages_have_sourced_front_matter(app):
    """Each new comparison page's front matter lists at least one source with url and read_date."""
    for slug in NEW_VS_SLUGS:
        page = load_page("comparison", slug=slug)
        assert page is not None, f"comparison page {slug} not found"
        sources = page.front_matter.get("sources")
        assert sources and len(sources) >= 1, f"{slug}: no sources in front matter"
        for source in sources:
            assert source.get("url"), f"{slug}: a source is missing url"
            assert source.get("read_date"), f"{slug}: a source is missing read_date"


def test_new_vs_pages_have_archiet_ai_external_url_for_cross_linking(app):
    """Each new comparison page carries its archiet.ai address as
    external_url (for the /vs hub's cross-link), but its own <link
    rel="canonical"> in <head> is self -- entelim.org, not archiet.ai
    (SEO/GEO audit item 1/2: these are two different products' content
    now, not one page with two addresses)."""
    with app.test_client() as client:
        for slug in NEW_VS_SLUGS:
            page = load_page("comparison", slug=slug)
            assert page.external_url == f"https://archiet.ai/vs/{slug}", (
                f"{slug}: expected external_url https://archiet.ai/vs/{slug}, got {page.external_url}"
            )
            html = client.get(f"/vs/{slug}").data.decode()
            assert f'rel="canonical" href="https://entelim.org/vs/{slug}"' in html
            assert f'rel="canonical" href="{page.external_url}"' not in html


def test_new_vs_pages_no_invented_price_without_a_source_marker(app):
    """Pages with a specific price figure flag it as vendor-published or third-party-reported."""
    # Only orbus-iserver and sparx-enterprise-architect carry a specific price figure;
    # both must flag it as third-party-reported, never presented as vendor-confirmed.
    priced_slugs = ["orbus-iserver", "sparx-enterprise-architect"]
    with app.test_client() as client:
        for slug in priced_slugs:
            html = client.get(f"/vs/{slug}").data.decode()
            assert "third-party" in html.lower(), (
                f"/vs/{slug}: a price figure is present but not flagged as third-party-reported"
            )


def test_sitemap_includes_vs_hub(app):
    """/sitemap.xml includes the /vs hub."""
    with app.test_client() as client:
        xml = client.get("/sitemap.xml").data.decode()
        assert "<loc>https://entelim.org/vs</loc>" in xml


# ── /vs/avolution and /vs/orbus: merged duplicate pages 301 to the survivor ─

OLD_VS_SLUG_REDIRECTS = {
    "avolution": "/vs/avolution-abacus",
    "orbus": "/vs/orbus-iserver",
}


def test_old_vs_slugs_301_to_merged_survivor(app):
    """/vs/avolution and /vs/orbus were separate pages covering the same
    competitor as /vs/avolution-abacus and /vs/orbus-iserver respectively.
    The old URLs must 301 to the survivor, not 404, since they may already
    be indexed."""
    with app.test_client() as client:
        for old_slug, new_path in OLD_VS_SLUG_REDIRECTS.items():
            rv = client.get(f"/vs/{old_slug}", follow_redirects=False)
            assert rv.status_code == 301, (
                f"/vs/{old_slug}: expected 301, got {rv.status_code}"
            )
            assert urlparse(rv.location).path == new_path, (
                f"/vs/{old_slug}: redirected to {rv.location!r}, expected {new_path!r}"
            )
            # And the survivor it redirects to actually renders.
            rv2 = client.get(new_path)
            assert rv2.status_code == 200, f"{new_path}: expected 200, got {rv2.status_code}"

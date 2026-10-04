"""Sitewide JSON-LD structured-data regression guard (T-SITE-1).

Loops over load_all_pages() plus the homepage instead of a fixed slug
list, so it checks valid JSON, required schema.org properties per
@type, and URL self-consistency on every real page automatically,
including any comparison page added later.
"""

from __future__ import annotations

import json

import pytest

from app.services.public_pages import load_all_pages

_JSONLD_SCRIPT_RE_SOURCE = (
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>'
)

SITE_URL = "https://entelim.org"


def _iter_jsonld_blocks(html: str) -> list[str]:
    """Return the raw text of every ``application/ld+json`` script block in html."""
    import re

    return re.findall(_JSONLD_SCRIPT_RE_SOURCE, html, re.DOTALL)


def _iter_offers(node):
    """Recursively yield every dict with ``@type: "Offer"`` inside a JSON-LD tree.

    ``_jsonld_software_app`` puts its offers in a top-level list; ``_jsonld_webpage``
    nests a single offer under ``about``. Walking the whole tree rather than one
    fixed shape means this keeps working if either shape changes.
    """
    if isinstance(node, dict):
        if node.get("@type") == "Offer":
            yield node
        for value in node.values():
            yield from _iter_offers(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_offers(item)


def test_sitewide_jsonld_schema_and_url_consistency(app):
    """Every page's JSON-LD -- every real page via ``load_all_pages()``, plus the
    homepage's own hardcoded block -- is valid JSON, carries the schema.org
    properties required for its ``@type``, and its ``url`` field matches that
    exact page.

    This loops over ``load_all_pages()`` instead of a fixed slug list, so it is
    a permanent regression guard: a future page is covered automatically. It is
    written to fail loudly, not just check a key exists, if a comparison page's
    ``FAQPage.mainEntity`` comes back empty -- the known silent-failure mode of
    ``_jsonld_faq``, whose HTML-regex extraction can match nothing and return an
    empty list with no error.
    """
    pages = load_all_pages()
    assert len(pages) > 0, "No pages loaded from content/pages/"

    with app.test_client() as client:
        # The homepage first: main/index.html hardcodes its own Organization
        # block; it is not produced by build_jsonld/load_all_pages.
        rv = client.get("/")
        assert rv.status_code == 200, f"/: returned {rv.status_code}"
        home_blocks = _iter_jsonld_blocks(rv.data.decode())
        assert len(home_blocks) >= 1, "/: no JSON-LD script found on homepage"
        for raw in home_blocks:
            try:
                ld = json.loads(raw)
            except json.JSONDecodeError as e:
                pytest.fail(f"/: invalid JSON-LD: {e}")
            assert ld.get("@type") == "Organization", (
                f"/: expected Organization JSON-LD, got {ld.get('@type')}"
            )
            assert ld.get("name"), "/: Organization JSON-LD missing name"
            assert ld.get("url"), "/: Organization JSON-LD missing url"

        for page in pages:
            rv = client.get(page.url)
            assert rv.status_code == 200, f"{page.url}: returned {rv.status_code}"
            html = rv.data.decode()
            blocks = _iter_jsonld_blocks(html)
            assert len(blocks) >= 1, f"{page.url}: no JSON-LD script found"

            for raw in blocks:
                try:
                    ld = json.loads(raw)
                except json.JSONDecodeError as e:
                    pytest.fail(f"{page.url}: invalid JSON-LD: {e}")

                assert "@context" in ld, f"{page.url}: JSON-LD missing @context"
                ld_type = ld.get("@type")
                assert ld_type, f"{page.url}: JSON-LD missing @type"

                # URL self-consistency: catches a page rendering with another
                # page's (or a stale/hardcoded) URL in its own structured data.
                expected_url = f"{SITE_URL}{page.url}"
                assert ld.get("url") == expected_url, (
                    f"{page.url}: JSON-LD url is {ld.get('url')!r}, "
                    f"expected {expected_url!r}"
                )

                if ld_type == "WebPage":
                    assert ld.get("name"), f"{page.url}: WebPage missing name"

                elif ld_type == "SoftwareApplication":
                    assert ld.get("name"), (
                        f"{page.url}: SoftwareApplication missing name"
                    )
                    assert ld.get("applicationCategory"), (
                        f"{page.url}: SoftwareApplication missing applicationCategory"
                    )
                    assert ld.get("offers"), (
                        f"{page.url}: SoftwareApplication missing offers"
                    )

                elif ld_type == "FAQPage":
                    assert "mainEntity" in ld, (
                        f"{page.url}: FAQPage missing mainEntity"
                    )
                    if page.family == "comparison":
                        # The silent-failure mode this test exists to catch:
                        # _jsonld_faq's two regex sub-patterns can both fail to
                        # match a page's real FAQ markup and return [] with no
                        # error, leaving the page serving an empty, useless
                        # FAQPage block. Every comparison page must have at
                        # least one real FAQ entry.
                        assert len(ld["mainEntity"]) > 0, (
                            f"{page.url}: FAQPage.mainEntity is empty -- "
                            f"_jsonld_faq's regex extraction found no FAQ "
                            f"entries on this comparison page"
                        )
                    for item in ld["mainEntity"]:
                        assert item.get("@type") == "Question", (
                            f"{page.url}: mainEntity entry is not a Question: "
                            f"{item}"
                        )
                        assert item.get("name"), (
                            f"{page.url}: Question missing non-empty name: "
                            f"{item}"
                        )
                        answer = item.get("acceptedAnswer") or {}
                        assert answer.get("@type") == "Answer", (
                            f"{page.url}: acceptedAnswer is not an Answer: "
                            f"{item}"
                        )
                        assert answer.get("text"), (
                            f"{page.url}: Answer missing non-empty text: {item}"
                        )

                # Every Offer anywhere in the tree -- top-level list for
                # SoftwareApplication, or nested under about.offers for
                # WebPage -- needs a price and a currency.
                for offer in _iter_offers(ld):
                    assert offer.get("price") not in (None, ""), (
                        f"{page.url}: Offer missing price: {offer}"
                    )
                    assert offer.get("priceCurrency"), (
                        f"{page.url}: Offer missing priceCurrency: {offer}"
                    )

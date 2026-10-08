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


def _iter_by_type(node, type_name):
    """Recursively yield every dict with ``@type: <type_name>`` inside a JSON-LD
    tree. More general than ``_iter_offers``: used for ``AggregateOffer`` (the
    not-purchasable-online, from-price Enterprise node) and
    ``UnitPriceSpecification`` (Team's per-editor price), neither of which is
    an ``Offer`` itself.
    """
    if isinstance(node, dict):
        if node.get("@type") == type_name:
            yield node
        for value in node.values():
            yield from _iter_by_type(value, type_name)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_by_type(item, type_name)


# ── Hosted-tier pricing: every page's JSON-LD vs. the one catalogue ────────
#
# build_jsonld used to emit only a single self-hosted-AGPL $0 Offer on every
# page, including the pricing page itself -- never Entelim's real hosted
# tiers (Startup $49/month, Team $29/editor/month, Enterprise from
# $24,000/year). The tests below pin every page's structured data, and the
# pricing page's and home page's own prose, to app.services.billing_plans's
# display-price fields -- the one place those dollar figures now live -- so
# none of the three can silently drift from the others again.


def test_jsonld_offers_match_billing_plans_catalogue(app):
    """Every non-comparison page's JSON-LD Offer / AggregateOffer /
    UnitPriceSpecification price is one of billing_plans.PLANS's
    display-price figures, and every display-price figure the catalogue
    defines shows up on at least one page -- so a figure edited in one place
    and not the other fails loudly instead of drifting silently.

    Comparison pages are excluded: their FAQPage JSON-LD carries no Offer at
    all, by design, and is out of scope here (see
    test_comparison_pages_have_faq_jsonld).
    """
    from app.services.billing_plans import PLANS

    expected_flat_or_unit_prices = {
        str(amount)
        for plan in PLANS
        for amount in (plan.display_price_monthly, plan.display_price_annual)
        if amount is not None
    }
    expected_floor_prices = {
        str(plan.display_price_floor_annual)
        for plan in PLANS
        if plan.display_price_floor_annual is not None
    }
    expected_unit_prices = {
        str(amount)
        for plan in PLANS
        if plan.display_price_per_unit
        for amount in (plan.display_price_monthly, plan.display_price_annual)
        if amount is not None
    }
    assert expected_flat_or_unit_prices, "billing_plans.PLANS has no display prices to check against"
    assert expected_floor_prices, "billing_plans.PLANS has no Enterprise floor price to check against"
    assert expected_unit_prices, "billing_plans.PLANS has no Team per-unit price to check against"

    pages = [p for p in load_all_pages() if p.page_family != "comparison"]
    assert pages, "no non-comparison pages loaded"

    seen_offer_prices: set[str] = set()
    seen_floor_prices: set[str] = set()
    seen_unit_prices: set[str] = set()

    with app.test_client() as client:
        for page in pages:
            html = client.get(page.url).data.decode()
            for raw in _iter_jsonld_blocks(html):
                ld = json.loads(raw)
                for offer in _iter_offers(ld):
                    assert offer.get("price") not in (None, ""), (
                        f"{page.url}: Offer missing price: {offer}"
                    )
                    seen_offer_prices.add(offer["price"])
                for agg in _iter_by_type(ld, "AggregateOffer"):
                    assert agg.get("lowPrice") not in (None, ""), (
                        f"{page.url}: AggregateOffer missing lowPrice: {agg}"
                    )
                    seen_floor_prices.add(agg["lowPrice"])
                for spec in _iter_by_type(ld, "UnitPriceSpecification"):
                    assert spec.get("price") not in (None, ""), (
                        f"{page.url}: UnitPriceSpecification missing price: {spec}"
                    )
                    seen_unit_prices.add(spec["price"])

    assert seen_offer_prices == expected_flat_or_unit_prices, (
        f"JSON-LD Offer prices {seen_offer_prices} do not match "
        f"billing_plans's display prices {expected_flat_or_unit_prices}"
    )
    assert seen_floor_prices == expected_floor_prices, (
        f"JSON-LD AggregateOffer.lowPrice values {seen_floor_prices} do not "
        f"match billing_plans's Enterprise floor price {expected_floor_prices}"
    )
    assert seen_unit_prices == expected_unit_prices, (
        f"JSON-LD UnitPriceSpecification.price values {seen_unit_prices} do "
        f"not match billing_plans's Team per-editor price {expected_unit_prices}"
    )


def test_jsonld_never_asserts_enterprise_as_a_flat_purchasable_offer(app):
    """Enterprise's from-price never appears as a plain ``Offer`` with a flat
    ``price`` -- it is a contract floor, not a fixed, online-purchasable
    price, and must only appear as an ``AggregateOffer.lowPrice``.
    """
    from app.services.billing_plans import get_plan

    enterprise = get_plan("enterprise")
    floor = str(enterprise.display_price_floor_annual)

    pages = [p for p in load_all_pages() if p.page_family != "comparison"]
    with app.test_client() as client:
        for page in pages:
            html = client.get(page.url).data.decode()
            for raw in _iter_jsonld_blocks(html):
                ld = json.loads(raw)
                for offer in _iter_offers(ld):
                    assert offer.get("price") != floor, (
                        f"{page.url}: Enterprise's from-price {floor} appears "
                        f"as a flat, purchasable Offer.price: {offer}"
                    )


def test_pricing_page_prose_matches_billing_plans_catalogue(app):
    """content/pages/site/pricing.md's rendered dollar figures match
    billing_plans.PLANS's display-price fields exactly. Read-only: this does
    not rewrite pricing.md, it only asserts the two cannot drift unnoticed.
    """
    from app.services.billing_plans import get_plan

    startup = get_plan("startup")
    team = get_plan("team")
    enterprise = get_plan("enterprise")

    with app.test_client() as client:
        html = client.get("/pricing").data.decode()

    assert f"${startup.display_price_monthly}/month" in html, (
        "pricing page: Startup monthly price does not match billing_plans"
    )
    assert f"${startup.display_price_annual}/year" in html, (
        "pricing page: Startup annual price does not match billing_plans"
    )
    assert f"${team.display_price_monthly}/{team.display_price_unit}/month" in html, (
        "pricing page: Team per-editor monthly price does not match billing_plans"
    )
    assert f"${team.display_price_annual}/{team.display_price_unit}/year" in html, (
        "pricing page: Team per-editor annual price does not match billing_plans"
    )
    assert f"${enterprise.display_price_floor_annual:,}/year" in html, (
        "pricing page: Enterprise floor price does not match billing_plans"
    )


def test_homepage_prose_matches_billing_plans_catalogue(app):
    """app/templates/main/index.html's rendered pricing-summary dollar figures
    match billing_plans.PLANS's display-price fields exactly. Read-only: this
    does not rewrite index.html, it only asserts the two cannot drift
    unnoticed.
    """
    from app.services.billing_plans import get_plan

    startup = get_plan("startup")
    team = get_plan("team")
    enterprise = get_plan("enterprise")

    with app.test_client() as client:
        html = client.get("/").data.decode()

    assert (
        f'>${startup.display_price_monthly}<span class="text-sm font-normal '
        'text-muted-foreground">/month</span>' in html
    ), "home page: Startup monthly price does not match billing_plans"
    assert f"${startup.display_price_annual}/year" in html, (
        "home page: Startup annual price does not match billing_plans"
    )
    assert (
        f'>${team.display_price_monthly}<span class="text-sm font-normal '
        f'text-muted-foreground">/{team.display_price_unit}/month</span>' in html
    ), "home page: Team per-editor monthly price does not match billing_plans"
    assert f"${team.display_price_annual}/{team.display_price_unit}/year" in html, (
        "home page: Team per-editor annual price does not match billing_plans"
    )
    assert (
        f'>From ${enterprise.display_price_floor_annual:,}<span class="text-sm '
        'font-normal text-muted-foreground">/year</span>' in html
    ), "home page: Enterprise floor price does not match billing_plans"


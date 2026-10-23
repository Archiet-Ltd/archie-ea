"""Journey: filter the work packages list by SAFe kind across every page.

An enterprise architect opens the Implementation layer repository. The list
spans three pages (55 work packages at 25 rows per page), twelve of them
marked Epic and spread over all three pages. The architect opens the advanced
filters, picks the Epic kind and sees exactly those twelve work packages with
the correct total and page count — not just the Epics that happened to sit on
the current page, and not the unfiltered total. The Kind options come from the
profile registry. Opening a work package shows its kind, and the full detail
page lands with content. None of organisation B's work packages ever appear,
even though B stores the same kinds.

PR 903 merged the SAFe kind profile but applied the Kind filter after
pagination and never recalculated totals: filtering to Epic returned only the
Epics on page 1 and reported the unfiltered total and page count.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# Org A (Alpha): 55 work packages, Epics spread across all three pages.
EPIC_ALPHA = [5, 10, 15, 20, 25, 27, 30, 35, 40, 45, 50, 55]
# Org B (Beta): 30 work packages, two Epics (one on page 2 at 25 rows).
EPIC_BETA = [2, 27]


def _seed_kind_org(org, count, epic_indices, prefix, with_plateau=False):
    """Seed ``count`` work packages (mirrored ArchiMate elements) with kinds
    written through the canonical profile writer, plus linked data: adjacent
    work packages are associated with each other, and when ``with_plateau`` a
    plateau element realises the layer (used for organisation B, whose totals
    the journey does not assert)."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.implementation_migration import WorkPackage
    from app.services.element_profile_service import set_kind

    app = create_app("testing")
    with app.app_context():
        epic = set(epic_indices)
        ae_by_index = {}
        for i in range(1, count + 1):
            name = "%s WP %02d" % (prefix, i)
            ae = ArchiMateElement(
                name=name,
                type="WorkPackage",
                layer="Implementation",
                organization_id=org["org_id"],
                description="Seeded %s work package %02d" % (prefix, i),
            )
            db.session.add(ae)
            db.session.flush()
            db.session.add(
                WorkPackage(
                    name=name,
                    organization_id=org["org_id"],
                    element_type="WorkPackage",
                    archimate_element_id=ae.id,
                )
            )
            set_kind(ae, "Epic" if i in epic else "Feature")
            ae_by_index[i] = ae

        # Linked data: a few work packages are associated with each other, so
        # their rows carry a relationship count and their detail panel shows
        # an outgoing link to a named element.
        for i, j in ((5, 10), (17, 33)):
            if i > count or j > count:
                continue
            db.session.add(
                ArchiMateRelationship(
                    type="association",
                    source_id=ae_by_index[i].id,
                    target_id=ae_by_index[j].id,
                    organization_id=org["org_id"],
                )
            )

        if with_plateau:
            plateau = ArchiMateElement(
                name="%s plateau" % prefix,
                type="Plateau",
                layer="Implementation",
                organization_id=org["org_id"],
            )
            db.session.add(plateau)
            db.session.flush()
            db.session.add(
                ArchiMateRelationship(
                    type="realization",
                    source_id=ae_by_index[min(5, count)].id,
                    target_id=plateau.id,
                    organization_id=org["org_id"],
                )
            )

        db.session.commit()
        db.session.remove()


def _row_names(page):
    """The names currently rendered in the architecture elements table."""
    return page.locator(
        '[data-testid="architecture-elements-table"] [data-field="element-name"] button'
    ).all_inner_texts()


def _expect_pagination(page, start_end, page_of):
    pagination = page.get_by_test_id("architecture-pagination")
    expect(pagination.get_by_text(start_end)).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(pagination.get_by_text(page_of)).to_be_visible(timeout=PAGE_TIMEOUT)


def test_safe_kind_filter_journey(browser, live_server):
    """The whole journey at desktop width: registry options, cross-page kind
    filter with correct totals, quick view showing the kind, and the full
    detail page landing with content."""
    org_a = create_fresh_org("enterprise_architect")
    org_b = create_fresh_org("enterprise_architect")
    _seed_kind_org(org_a, 55, EPIC_ALPHA, "Alpha")
    _seed_kind_org(org_b, 30, EPIC_BETA, "Beta")
    epic_names = {"Alpha WP %02d" % i for i in EPIC_ALPHA}

    context = browser.new_context(
        ignore_https_errors=True,
        viewport={"width": 1440, "height": 1000},
    )
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()

    try:
        sign_in(page, live_server, org_a["emails"]["enterprise_architect"])
        page.goto(
            live_server + "/architecture/dashboard?layer=implementation",
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT,
        )

        # 1. The full list spans three pages at the default 25 rows.
        _expect_pagination(page, "1–25 of 55", "Page 1 of 3")

        # 2. The Kind options come from the profile registry: ask the product
        #    for the layer's kind options and compare them to the rendered ones.
        status, data = api(page, "GET", "/architecture/api/layer/implementation/elements")
        assert status == 200, data
        assert data["kind_options"] == ["Epic", "Feature", "Capability (SAFe)", "Enabler"], (
            "kind_options must come from the profile registry: %s" % data["kind_options"]
        )
        page.get_by_test_id("advanced-toggle").click()
        kind_select = page.locator("#architecture-kind")
        rendered = kind_select.locator("option").all_inner_texts()
        assert rendered == ["Any kind"] + data["kind_options"], (
            "the Kind dropdown must render the registry options, got %s" % rendered
        )

        # 3. Filter to Epic: all twelve, wherever they sit, with the filtered
        #    total and page count — not the current page's Epics and not 55.
        kind_select.select_option("Epic")
        _expect_pagination(page, "1–12 of 12", "Page 1 of 1")
        names = _row_names(page)
        assert len(names) == 12, "expected 12 Epics, got %d: %s" % (len(names), names)
        assert set(names) == epic_names, "missing Epics: %s" % (epic_names - set(names))
        assert not any(n.startswith("Beta") for n in names), (
            "organisation B's work packages appeared in org A's Epic filter"
        )

        # 4. Filter to Feature: two pages, every row a Feature.
        kind_select.select_option("Feature")
        _expect_pagination(page, "1–25 of 43", "Page 1 of 2")
        names = _row_names(page)
        assert not (set(names) & epic_names), "Epics shown in the Feature filter"
        page.get_by_role("button", name="Next architecture elements page").click()
        _expect_pagination(page, "26–43 of 43", "Page 2 of 2")
        names = _row_names(page)
        assert not (set(names) & epic_names), "Epics shown on the Feature filter's page 2"

        # 5. Clearing the filters returns to the full three-page list.
        page.get_by_role("button", name="Clear architecture filters").click()
        _expect_pagination(page, "1–25 of 55", "Page 1 of 3")

        # 6. An Epic's quick view shows its kind, framework and WSJF score.
        kind_select.select_option("Epic")
        _expect_pagination(page, "1–12 of 12", "Page 1 of 1")
        page.get_by_role("button", name="Open details for Alpha WP 05").click()
        kind_panel = page.get_by_test_id("kind-details-panel")
        expect(kind_panel).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(kind_panel.get_by_text("Epic", exact=True)).to_be_visible()
        expect(page.get_by_role("heading", name="Alpha WP 05", exact=True)).to_be_visible()

        # 7. The full detail page for an Epic lands with content: a work
        #    package heading, its ArchiMate identity block, and the stored
        #    kind property — never an error page.
        page.get_by_role("link", name="View full detail").click()
        page.wait_for_url("**/architecture/implementation/WorkPackage/*", timeout=PAGE_TIMEOUT)
        heading = page.get_by_role("heading", name="Alpha WP 05")
        expect(heading).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("ArchiMate Identity", exact=True)).to_be_visible()
        expect(page.get_by_text("entelim:kind", exact=False)).to_be_visible()
    finally:
        context.close()


def test_safe_kind_filter_journey_mobile(browser, live_server):
    """The same filter on a 390px-wide screen: the mobile cards show the twelve
    Epics with the same totals, and a card's full-detail link lands with
    content."""
    org_a = create_fresh_org("enterprise_architect")
    _seed_kind_org(org_a, 55, EPIC_ALPHA, "Alpha")

    context = browser.new_context(
        ignore_https_errors=True,
        viewport={"width": 390, "height": 844},
    )
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()

    try:
        sign_in(page, live_server, org_a["emails"]["enterprise_architect"])
        page.goto(
            live_server + "/architecture/dashboard?layer=implementation",
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT,
        )

        page.get_by_test_id("advanced-toggle").click()
        kind_select = page.locator("#architecture-kind")
        kind_select.select_option("Epic")
        _expect_pagination(page, "1–12 of 12", "Page 1 of 1")

        cards = page.get_by_test_id("architecture-mobile-cards")
        expect(cards).to_be_visible(timeout=PAGE_TIMEOUT)
        names = cards.locator('[data-field="element-name"] h3').all_inner_texts()
        assert len(names) == 12, "expected 12 Epics on mobile, got %d: %s" % (len(names), names)
        assert {"Alpha WP %02d" % i for i in EPIC_ALPHA} == set(names)
        assert not any(n.startswith("Beta") for n in names)

        # A card's Full Detail link lands on the work package page with content.
        page.get_by_role("link", name="Open full detail for Alpha WP 05").click()
        page.wait_for_url("**/architecture/implementation/WorkPackage/*", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Alpha WP 05")).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()
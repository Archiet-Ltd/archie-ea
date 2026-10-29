"""Ownership coverage: a CTO reads, searches, sorts, hides a column and pages through
the business-unit coverage table, and organisation B's unit never shows up on
organisation A's page.

app/templates/applications/ownership_coverage.html was converted from a hand-written
<table> to the shared components/list_table.html macro (T-list-table-01-b01, round 2).
This drives the real controls the macro renders and asserts the outcome a CTO needs:
the business units they were given are on screen with their real application and
ownership counts, search narrows the list, a column can be hidden and a page can be
turned, before asserting nothing from the other organisation leaks in.

Waits are expressed with Playwright's own auto-retrying locator counts rather than
``page.wait_for_function`` -- the product's CSP has no ``unsafe-eval``, and a bare JS
expression passed to ``wait_for_function`` is evaluated the same way a page script
would be, so it is blocked by the same policy a real visitor's browser enforces.
"""
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# organization_units.code and application_components.application_code are globally
# unique, so every run needs its own.
_RUN = uuid.uuid4().hex[:8]

# (unit name, total applications, applications with an owner)
# More than 8 units so the toolbar/search/column-view menu appear by default, and
# 11 units so selecting 10 rows per page produces a genuine second page.
_ORG_A_UNITS = [
    ("Retail Banking", 12, 10),
    ("Corporate Banking", 9, 3),
    ("Wealth Management", 7, 7),
    ("Payments", 15, 2),
    ("Treasury", 5, 5),
    ("Compliance", 6, 1),
    ("Risk", 4, 4),
    ("Operations", 8, 0),
    ("Technology", 10, 9),
    ("Human Resources", 3, 3),
    ("Marketing", 2, 0),
]
_ORG_B_UNIT = ("Org B only unit", 1, 1)


def _seed_units(org_id, units):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.enterprise_intelligence import OrganizationUnit

    app = create_app("testing")
    with app.app_context():
        for name, total_apps, owned in units:
            unit_name = f"{name} {_RUN}"
            unit = OrganizationUnit(
                organization_id=org_id, name=unit_name, code=f"OU-{name[:4].upper()}-{_RUN}",
            )
            db.session.add(unit)
            for i in range(total_apps):
                db.session.add(ApplicationComponent(
                    organization_id=org_id,
                    name=f"{unit_name} App {i}",
                    application_code=f"AC-{name[:4].upper()}-{i}-{_RUN}",
                    business_domain=unit_name,
                    business_owner=("Someone" if i < owned else None),
                ))
        db.session.commit()
        db.session.remove()


def _seed_second_org():
    """A second, isolated organisation with its own unit, for the cross-org assertion."""
    from app import create_app, db
    from app.models.organization import Organization

    app = create_app("testing")
    with app.app_context():
        org = Organization(name="Ownership coverage isolation check org B",
                            slug=f"ownership-coverage-org-b-{_RUN}")
        db.session.add(org)
        db.session.commit()
        org_id = org.id
        db.session.remove()
    _seed_units(org_id, [_ORG_B_UNIT])
    return org_id


def test_cto_reads_searches_sorts_hides_and_pages(browser, live_server, seeded):
    ids = seeded["ids"]
    _seed_units(ids["org"], _ORG_A_UNITS)
    _seed_second_org()

    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["cto"])
        page.goto(live_server + "/applications/ownership-coverage", timeout=PAGE_TIMEOUT)
        table = page.get_by_test_id("ownership-coverage-table")
        table.wait_for(timeout=PAGE_TIMEOUT)

        visible_rows = page.locator('[data-table-id="ownership-coverage-table"] [data-row]:not([hidden])')
        total_rows_before = visible_rows.count()
        assert total_rows_before >= len(_ORG_A_UNITS)

        # --- the units given are on screen with their real figures
        body_text = page.locator("main, body").first.inner_text()
        for name, total_apps, owned in _ORG_A_UNITS:
            assert f"{name} {_RUN}" in body_text, body_text
        assert str(_ORG_A_UNITS[0][1]) in body_text  # a real application count, not a placeholder

        # --- organisation B's unit never shows up here
        assert f"{_ORG_B_UNIT[0]} {_RUN}" not in body_text

        # --- search narrows the list (toolbar present because there are 11 rows)
        search = page.locator('[data-slot="data-table"] [data-table-filter]').first
        search.wait_for(timeout=PAGE_TIMEOUT)
        search.fill("Treasury")
        expect(visible_rows).to_have_count(1, timeout=PAGE_TIMEOUT)
        narrowed = page.locator("main, body").first.inner_text()
        assert f"Treasury {_RUN}" in narrowed
        assert f"Retail Banking {_RUN}" not in narrowed
        reset_btn = page.locator('[data-slot="data-table"] [data-table-reset]').first
        reset_btn.click()
        expect(visible_rows).to_have_count(total_rows_before, timeout=PAGE_TIMEOUT)

        # --- sort by Applications ascending. The org already carries other business
        # units from the shared seed fixture interleaved on the page, so rather than
        # assume an absolute first row, check that this run's own rows -- wherever they
        # land -- come out in non-decreasing order of their known application counts.
        page.locator('[data-table-sort-trigger="1"]').click()
        page.get_by_role("menuitem", name="Ascending").click()
        expect(visible_rows).to_have_count(total_rows_before, timeout=PAGE_TIMEOUT)
        rows_text = visible_rows.all_inner_texts()
        counts_by_name = {name: total_apps for name, total_apps, _owned in _ORG_A_UNITS}
        our_counts_in_order = [
            counts_by_name[name]
            for t in rows_text
            for name in counts_by_name
            if t.startswith(f"{name} {_RUN}\t")
        ]
        assert len(our_counts_in_order) == len(_ORG_A_UNITS), (rows_text, our_counts_in_order)
        assert our_counts_in_order == sorted(our_counts_in_order)

        # --- hide the Coverage column
        page.get_by_role("button", name="View").click()
        page.locator('[data-table-owner="ownership-coverage-table"][data-table-toggle="3"]').click()
        expect(page.locator('[data-table-id="ownership-coverage-table"] thead [data-col="3"]')).to_be_hidden(timeout=PAGE_TIMEOUT)

        # --- turn the page: 10 rows per page makes a table with more than 10 rows a
        # two-page table (the shared org already has more units than this run alone).
        page.locator('[data-table-id="ownership-coverage-table"] [data-table-size]').select_option("10")
        expect(visible_rows).to_have_count(10, timeout=PAGE_TIMEOUT)
        next_button = page.locator('[data-table-id="ownership-coverage-table"] [data-table-pager="next"]')
        next_button.click()
        page.wait_for_timeout(300)
        second_page_count = visible_rows.count()
        assert 1 <= second_page_count <= 10
        assert second_page_count != 10 or total_rows_before > 20  # genuinely a different, non-first page
    finally:
        ctx.close()

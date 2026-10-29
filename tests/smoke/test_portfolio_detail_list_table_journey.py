"""Initiative detail: a portfolio manager reads, searches, sorts, hides a column and
pages through an initiative's benefits and assumptions, and organisation B's initiative
(and its benefits) never shows up on organisation A's page.

app/templates/portfolio/detail.html's two genuine record-list tables (Benefits,
Assumptions) were converted from hand-written <table>s to the shared
components/list_table.html macro (T-list-table-01-b01, round 2); the page's small
key-value money tiles above them are untouched, per the parent brief's ruling that a
detail page's small facts stay on their own pattern and only a genuine record list
converts. This drives the real controls the macro renders for the Benefits table and
asserts the outcome a portfolio manager needs: the benefits recorded against this
initiative are on screen with their real figures, search narrows the list, a column can
be hidden and a page can be turned, before asserting an initiative from another
organisation is unreachable (404) rather than visible.

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

_RUN = uuid.uuid4().hex[:8]

# (name, measure, baseline, target, actual) -- more than 8 benefits so the
# toolbar/search/column-view menu appear by default, and 11 so selecting 10 rows per
# page produces a genuine second page.
_BENEFITS = [
    ("Retire duplicate CRM licences", "Annual licence spend", 500_000, 350_000, 340_000),
    ("Reduce order cycle time", "Order cycle time (days)", 12, 8, 9),
    ("Cut support ticket volume", "Tickets per month", 900, 600, 650),
    ("Lower cloud hosting spend", "Annual hosting spend", 1_200_000, 900_000, 950_000),
    ("Reduce manual reconciliation effort", "Hours per month", 400, 150, 200),
    ("Consolidate vendor contracts", "Active vendor count", 40, 25, 28),
    ("Shrink release cycle time", "Days per release", 30, 10, 12),
    ("Reduce onboarding time", "Days to onboard", 20, 10, 11),
    ("Cut data duplication", "Duplicate records", 5000, 1000, 1200),
    ("Lower incident response time", "Minutes to respond", 60, 20, 25),
    ("Zzz smallest baseline benefit", "Smallest baseline", 10, 5, 5),
]


def _seed_initiative_and_benefits(org_id, name, benefits):
    from app import create_app, db
    from app.models.benefit import Benefit
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    app = create_app("testing")
    with app.app_context():
        initiative = EnterpriseInitiative(
            organization_id=org_id, name=name, code=f"DET-{_RUN}-{abs(hash((name, org_id))) % 100000}",
            status="active",
        )
        db.session.add(initiative)
        db.session.commit()
        initiative_id = initiative.id
        for bname, measure, baseline, target, actual in benefits:
            db.session.add(Benefit(
                organization_id=org_id,
                legacy_enterprise_initiative_id=initiative_id,
                name=f"{bname} {_RUN}",
                measure=measure,
                baseline_value=baseline,
                target_value=target,
                actual_value=actual,
                benefit_type="cost_saving",
            ))
        db.session.commit()
        db.session.remove()
    return initiative_id


def test_portfolio_manager_reads_searches_sorts_hides_and_pages(browser, live_server, seeded):
    ids = seeded["ids"]
    initiative_id = _seed_initiative_and_benefits(ids["org"], f"Detail journey initiative {_RUN}", _BENEFITS)

    from app import create_app, db
    from app.models.organization import Organization

    app = create_app("testing")
    with app.app_context():
        org_b = Organization(name="Portfolio detail isolation check org B",
                              slug=f"portfolio-detail-org-b-{_RUN}")
        db.session.add(org_b)
        db.session.commit()
        org_b_id = org_b.id
        db.session.remove()
    org_b_initiative_id = _seed_initiative_and_benefits(
        org_b_id, f"Org B only initiative {_RUN}", [("Org B only benefit", "n/a", 1, 1, 1)]
    )

    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["portfolio_manager"])
        page.goto(live_server + f"/portfolio/{initiative_id}", timeout=PAGE_TIMEOUT)
        table = page.get_by_test_id("initiative-benefits-table")
        table.wait_for(timeout=PAGE_TIMEOUT)

        visible_rows = page.locator('[data-table-id="initiative-benefits-table"] [data-row]:not([hidden])')
        total_rows_before = visible_rows.count()
        assert total_rows_before == len(_BENEFITS)  # this initiative is freshly created; no other benefits

        # --- the benefits given are on screen with their real figures
        body_text = page.locator("main, body").first.inner_text()
        for bname, *_rest in _BENEFITS:
            assert f"{bname} {_RUN}" in body_text, body_text
        assert "500,000" in body_text  # a real baseline, not a placeholder

        # --- organisation B's initiative is unreachable from organisation A's session
        resp = page.request.get(live_server + f"/portfolio/{org_b_initiative_id}")
        assert resp.status == 404

        # --- search narrows the list (toolbar present because there are 11 rows)
        search = page.locator('[data-slot="data-table"] [data-table-filter]').first
        search.wait_for(timeout=PAGE_TIMEOUT)
        search.fill("cloud hosting")
        expect(visible_rows).to_have_count(1, timeout=PAGE_TIMEOUT)
        # Scoped to the table itself: the page also has a separate "Record a
        # measurement" list below repeating every benefit name, unaffected by the
        # table's own search, so a whole-page text check would see "Retire duplicate
        # CRM licences" there regardless of whether the table narrowed correctly.
        narrowed = table.inner_text()
        assert "Lower cloud hosting spend" in narrowed
        assert "Retire duplicate CRM licences" not in narrowed
        reset_btn = page.locator('[data-slot="data-table"] [data-table-reset]').first
        reset_btn.click()
        expect(visible_rows).to_have_count(total_rows_before, timeout=PAGE_TIMEOUT)

        # --- sort by Baseline ascending. `data-row="N"` is a static id assigned at
        # initial render, never rewritten by the client sort (which only reorders the
        # DOM nodes and toggles `hidden`) -- so the first VISUALLY visible row after
        # sorting is the first element in DOM order among non-hidden rows, not the
        # element whose fixed data-row attribute happens to read "0".
        page.locator('[data-table-sort-trigger="2"]').click()
        page.get_by_role("menuitem", name="Ascending").click()
        first_row = visible_rows.first
        expect(first_row).to_contain_text("Zzz smallest baseline benefit", timeout=PAGE_TIMEOUT)  # smallest baseline (10)

        # --- hide the Owner column
        page.get_by_role("button", name="View").click()
        page.locator('[data-table-owner="initiative-benefits-table"][data-table-toggle="6"]').click()
        expect(page.locator('[data-table-id="initiative-benefits-table"] thead [data-col="6"]')).to_be_hidden(timeout=PAGE_TIMEOUT)

        # --- turn the page: 10 rows per page makes an 11-row table a two-page table
        page.locator('[data-table-id="initiative-benefits-table"] [data-table-size]').select_option("10")
        expect(visible_rows).to_have_count(10, timeout=PAGE_TIMEOUT)
        next_button = page.locator('[data-table-id="initiative-benefits-table"] [data-table-pager="next"]')
        next_button.click()
        expect(visible_rows).to_have_count(1, timeout=PAGE_TIMEOUT)
    finally:
        ctx.close()

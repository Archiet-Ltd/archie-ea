"""Demand decisions: a portfolio manager reads, searches, sorts and pages through the
decided demands, and organisation B's demand never shows up on organisation A's page.

app/templates/portfolio/demands.html's "Decided" table was converted from a
hand-written <table> to the shared components/list_table.html macro
(T-list-table-01-b01, round 2). This drives the real controls the macro renders and
asserts the outcome a portfolio manager needs: the demand decisions they were given
are on screen with their real route and rationale, search narrows the list, a column
can be hidden and a page can be turned, before asserting nothing from the other
organisation leaks in. The deferred row's own row action (the Approve/Decline form) is
asserted present, since a deferred demand is not yet a closed record.

Waits are expressed with Playwright's own auto-retrying locator counts rather than
``page.wait_for_function`` -- the product's CSP has no ``unsafe-eval``, and a bare JS
expression passed to ``wait_for_function`` is evaluated the same way a page script
would be, so it is blocked by the same policy a real visitor's browser enforces.
"""
import datetime
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

_RUN = uuid.uuid4().hex[:8]

# (title, status, days-before-today for decision_date, triage_route, rationale)
# More than 8 decided demands so the toolbar/search/column-view menu appear by
# default, and 11 so selecting 10 rows per page produces a genuine second page.
_ORG_A_DECIDED = [
    ("Replace branch teller app", "approved", 100, "build", "No existing coverage"),
    ("Retire legacy fax gateway", "declined", 95, "reuse", "Volume too low to justify"),
    ("Buy fraud scoring service", "approved", 90, "buy", "Vendor matches requirement"),
    ("Consolidate CRM instances", "deferred", 85, "reuse", "Waiting on budget cycle"),
    ("Mobile deposit limits change", "approved", 80, "build", "Regulatory deadline"),
    ("Archive old loan system", "declined", 75, "reuse", "Still in active use"),
    ("Upgrade treasury middleware", "approved", 70, "buy", "Vendor upgrade path exists"),
    ("New vendor risk portal", "approved", 65, "build", "No market fit found"),
    ("Sunset internal chat tool", "declined", 60, "reuse", "Users migrated already"),
    ("Branch kiosk refresh", "approved", 55, "build", "Hardware end of life"),
    ("Smallest and oldest decision", "approved", 200, "reuse", "Earliest decision date"),
]
_ORG_B_DECIDED = ("Org B only demand", "approved", 50, "build", "Org B's own reasoning")


def _seed_decided(org_id, rows):
    from app import create_app, db
    from app.models.demand import Demand

    app = create_app("testing")
    with app.app_context():
        for title, status, days_ago, route, rationale in rows:
            db.session.add(Demand(
                organization_id=org_id,
                title=f"{title} {_RUN}",
                status=status,
                source="business",
                decision_date=datetime.date.today() - datetime.timedelta(days=days_ago),
                triage_route=route,
                decision_rationale=rationale,
                submitted_date=datetime.date.today() - datetime.timedelta(days=days_ago + 5),
            ))
        db.session.commit()
        db.session.remove()


def _seed_second_org():
    """A second, isolated organisation with its own decided demand, for the cross-org assertion."""
    from app import create_app, db
    from app.models.organization import Organization

    app = create_app("testing")
    with app.app_context():
        org = Organization(name="Demand decisions isolation check org B",
                            slug=f"demand-decisions-org-b-{_RUN}")
        db.session.add(org)
        db.session.commit()
        org_id = org.id
        db.session.remove()
    _seed_decided(org_id, [_ORG_B_DECIDED])
    return org_id


def test_portfolio_manager_reads_searches_sorts_hides_and_pages(browser, live_server, seeded):
    ids = seeded["ids"]
    _seed_decided(ids["org"], _ORG_A_DECIDED)
    _seed_second_org()

    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["portfolio_manager"])
        page.goto(live_server + "/portfolio/demands", timeout=PAGE_TIMEOUT)
        table = page.get_by_test_id("demand-decided-table")
        table.wait_for(timeout=PAGE_TIMEOUT)

        visible_rows = page.locator('[data-table-id="demand-decided-table"] [data-row]:not([hidden])')
        total_rows_before = visible_rows.count()
        assert total_rows_before >= len(_ORG_A_DECIDED)

        # --- the decisions given are on screen with their real figures
        body_text = page.locator("main, body").first.inner_text()
        for title, status, *_rest in _ORG_A_DECIDED:
            assert f"{title} {_RUN}" in body_text, body_text
        assert "No existing coverage" in body_text  # a real rationale, not a placeholder

        # --- organisation B's demand never shows up here
        assert f"{_ORG_B_DECIDED[0]} {_RUN}" not in body_text

        # --- the deferred row's own action: Approve/Decline are present
        deferred_row = page.locator('[data-testid="demand-decided-row"]', has_text="Consolidate CRM instances")
        deferred_row.wait_for(timeout=PAGE_TIMEOUT)
        assert deferred_row.get_by_role("button", name="Approve").count() == 1
        assert deferred_row.get_by_role("button", name="Decline").count() == 1

        # --- search narrows the list (toolbar present because there are 11 rows)
        search = page.locator('[data-slot="data-table"] [data-table-filter]').first
        search.wait_for(timeout=PAGE_TIMEOUT)
        search.fill("fraud scoring")
        expect(visible_rows).to_have_count(1, timeout=PAGE_TIMEOUT)
        narrowed = page.locator("main, body").first.inner_text()
        assert "Buy fraud scoring service" in narrowed
        assert "Replace branch teller app" not in narrowed
        reset_btn = page.locator('[data-slot="data-table"] [data-table-reset]').first
        reset_btn.click()
        expect(visible_rows).to_have_count(total_rows_before, timeout=PAGE_TIMEOUT)

        # --- sort by Date ascending. Other decided demands may already exist in the
        # shared org, so check the relative order of this run's own rows rather than
        # assuming an exact first row.
        page.locator('[data-table-sort-trigger="2"]').click()
        page.get_by_role("menuitem", name="Ascending").click()
        expect(visible_rows).to_have_count(total_rows_before, timeout=PAGE_TIMEOUT)
        rows_text = visible_rows.all_inner_texts()
        days_by_title = {title: days_ago for title, _status, days_ago, *_rest in _ORG_A_DECIDED}
        our_days_in_order = [
            days_by_title[title]
            for t in rows_text
            for title in days_by_title
            if t.startswith(f"{title} {_RUN}\t")
        ]
        assert len(our_days_in_order) == len(_ORG_A_DECIDED), (rows_text, our_days_in_order)
        # Ascending date == descending "days ago" (an older decision has a smaller date).
        assert our_days_in_order == sorted(our_days_in_order, reverse=True)

        # --- hide the Rationale column
        page.get_by_role("button", name="View").click()
        page.locator('[data-table-owner="demand-decided-table"][data-table-toggle="4"]').click()
        expect(page.locator('[data-table-id="demand-decided-table"] thead [data-col="4"]')).to_be_hidden(timeout=PAGE_TIMEOUT)

        # --- turn the page: 10 rows per page makes a table with more than 10 rows a
        # two-page table (the shared org already has at least this run's 11 decisions).
        page.locator('[data-table-id="demand-decided-table"] [data-table-size]').select_option("10")
        expect(visible_rows).to_have_count(10, timeout=PAGE_TIMEOUT)
        next_button = page.locator('[data-table-id="demand-decided-table"] [data-table-pager="next"]')
        next_button.click()
        page.wait_for_timeout(300)
        second_page_count = visible_rows.count()
        assert 1 <= second_page_count <= 10
        assert second_page_count != 10 or total_rows_before > 20  # genuinely a different, non-first page
    finally:
        ctx.close()

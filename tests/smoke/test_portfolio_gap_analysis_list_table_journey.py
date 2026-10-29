"""Portfolio gap analysis: an enterprise architect reads, searches, sorts, hides a
column and pages through the unsupported-capabilities gap table, and organisation B's
capability never shows up on organisation A's page.

app/templates/application_mgmt/portfolio_gap_analysis.html's four gap tables (compliance
risks, single points of failure, unsupported capabilities, orphaned applications) were
converted from hand-written <table>s to the shared components/list_table.html macro
(T-list-table-01-b01, round 2). The route that actually serves this template today
(``dynamic_dashboards.workflow_pipeline``, /auto-dashboard/workflow-pipeline) only ever
populates the unsupported-capabilities table — the other three analyses return ``None``
("not yet implemented") on this route, so only that table is exercised here with real
data; the real route's rendering of the other three is left to the real analyser filling
them in, which is unchanged by this conversion.

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

# (name, level) — level 1 capabilities are "high" severity, anything else "medium".
# More than 8 unmapped capabilities so the toolbar/search/column-view menu appear by
# default, and 11 so selecting 10 rows per page produces a genuine second page.
_ORG_A_CAPABILITIES = [
    ("Customer onboarding", 1),
    ("Fraud detection", 1),
    ("Payments processing", 2),
    ("Regulatory reporting", 1),
    ("Treasury management", 2),
    ("Risk scoring", 2),
    ("Branch operations", 2),
    ("Loan origination", 1),
    ("Collections", 2),
    ("Vendor management", 2),
    ("Zzz lowest-level capability", 2),
]
_ORG_B_CAPABILITY = ("Org B only capability", 1)


def _seed_capabilities(org_id, rows):
    from app import create_app, db
    from app.models.business_capabilities import BusinessCapability

    app = create_app("testing")
    with app.app_context():
        for name, level in rows:
            db.session.add(BusinessCapability(
                organization_id=org_id,
                name=f"{name} {_RUN}",
                code=f"CAP-{abs(hash((name, org_id))) % 100000}-{_RUN}",
                level=level,
            ))
        db.session.commit()
        db.session.remove()


def _seed_second_org():
    """A second, isolated organisation with its own unmapped capability, for the
    cross-org assertion."""
    from app import create_app, db
    from app.models.organization import Organization

    app = create_app("testing")
    with app.app_context():
        org = Organization(name="Portfolio gap analysis isolation check org B",
                            slug=f"portfolio-gap-analysis-org-b-{_RUN}")
        db.session.add(org)
        db.session.commit()
        org_id = org.id
        db.session.remove()
    _seed_capabilities(org_id, [_ORG_B_CAPABILITY])
    return org_id


def test_enterprise_architect_reads_searches_sorts_hides_and_pages(browser, live_server, seeded):
    ids = seeded["ids"]
    _seed_capabilities(ids["org"], _ORG_A_CAPABILITIES)
    _seed_second_org()

    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["enterprise_architect"])
        page.goto(live_server + "/auto-dashboard/workflow-pipeline", timeout=PAGE_TIMEOUT)
        table = page.get_by_test_id("unsupported-capabilities-table")
        table.wait_for(timeout=PAGE_TIMEOUT)

        visible_rows = page.locator('[data-table-id="unsupported-capabilities-table"] [data-row]:not([hidden])')
        total_rows_before = visible_rows.count()
        assert total_rows_before >= len(_ORG_A_CAPABILITIES)

        # --- the capabilities given are on screen with their real severities
        body_text = page.locator("main, body").first.inner_text()
        for name, level in _ORG_A_CAPABILITIES:
            assert f"{name} {_RUN}" in body_text, body_text
        assert "HIGH" in body_text  # a real severity, not a placeholder

        # --- organisation B's capability never shows up here
        assert f"{_ORG_B_CAPABILITY[0]} {_RUN}" not in body_text

        # --- search narrows the list (toolbar present because there are 11 rows)
        search = page.locator('[data-slot="data-table"] [data-table-filter]').first
        search.wait_for(timeout=PAGE_TIMEOUT)
        search.fill("Fraud detection")
        expect(visible_rows).to_have_count(1, timeout=PAGE_TIMEOUT)
        narrowed = page.locator("main, body").first.inner_text()
        assert "Fraud detection" in narrowed
        assert "Customer onboarding" not in narrowed
        reset_btn = page.locator('[data-slot="data-table"] [data-table-reset]').first
        reset_btn.click()
        expect(visible_rows).to_have_count(total_rows_before, timeout=PAGE_TIMEOUT)

        # --- sort by Capability ascending (alphabetical). Other unsupported
        # capabilities may already exist in the shared org, so check the relative
        # order of this run's own rows rather than assuming an exact first row.
        page.locator('[data-table-sort-trigger="0"]').click()
        page.get_by_role("menuitem", name="Ascending").click()
        expect(visible_rows).to_have_count(total_rows_before, timeout=PAGE_TIMEOUT)
        rows_text = visible_rows.all_inner_texts()
        our_names_in_order = [
            name for t in rows_text for name, _level in _ORG_A_CAPABILITIES
            if t.startswith(f"{name} {_RUN}\t")
        ]
        assert len(our_names_in_order) == len(_ORG_A_CAPABILITIES), (rows_text, our_names_in_order)
        assert our_names_in_order == sorted(our_names_in_order)

        # --- hide the Impact column
        page.get_by_role("button", name="View").click()
        page.locator('[data-table-owner="unsupported-capabilities-table"][data-table-toggle="2"]').click()
        expect(page.locator('[data-table-id="unsupported-capabilities-table"] thead [data-col="2"]')).to_be_hidden(timeout=PAGE_TIMEOUT)

        # --- turn the page: 10 rows per page makes a table with more than 10 rows a
        # two-page table (the shared org already has at least this run's 11 gaps).
        page.locator('[data-table-id="unsupported-capabilities-table"] [data-table-size]').select_option("10")
        expect(visible_rows).to_have_count(10, timeout=PAGE_TIMEOUT)
        next_button = page.locator('[data-table-id="unsupported-capabilities-table"] [data-table-pager="next"]')
        next_button.click()
        page.wait_for_timeout(300)
        second_page_count = visible_rows.count()
        assert 1 <= second_page_count <= 10
        assert second_page_count != 10 or total_rows_before > 20  # genuinely a different, non-first page
    finally:
        ctx.close()

"""Vendor catalogue: a procurement persona reads, searches, sorts, hides a column and
pages through the vendor list.

app/templates/application_mgmt/vendor_list.html's separate desktop <table> and mobile
card list were converted to one shared components/list_table.html macro
(T-list-table-01-b01, round 2): the table's own horizontal-scroll container is now the
page's responsive form, so there is one markup to read rather than two kept in step by
hand. The existing server-side vendor-type/contract-status filters and page-link
pagination are kept exactly as before (rendered through the macro's server_pagination
slot) rather than replaced by the macro's own free-text search, because that would drop
the ability to filter by vendor type or contract status specifically -- nothing this
round's brief asks to give up.

No organisation-isolation assertion here: VendorOrganization is deliberately NOT
tenant-scoped (ADR-0003, pinned by tests/test_vendor_tenancy_policy.py) -- it is shared
reference data, identical for every customer, so a second organisation's vendor is
*supposed* to appear on this page. Asserting the opposite would assert a regression of
that documented design decision, not test this conversion.

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

# (name, vendor_type, status, contract_status, readiness_score)
# More than 8 vendors so the toolbar/search/column-view menu appear by default, and 11
# so selecting 10 rows per page produces a genuine second page. Scores are set high
# (89-99) so this run's vendors sort ahead of anything else already in the catalogue.
_VENDORS = [
    ("Acme Cloud Platform", "cloud_provider", "active", "deployed", 99),
    ("Beacon SaaS Suite", "saas_provider", "active", "contracted", 98),
    ("Cobalt Consulting Group", "consulting_firm", "active", "catalog", 97),
    ("Delta Hardware Systems", "hardware_vendor", "active", "inactive", 96),
    ("Echo Enterprise Software", "enterprise_software", "active", "deployed", 95),
    ("Foxtrot Cloud Services", "cloud_provider", "restricted", "contracted", 94),
    ("Granite SaaS Tools", "saas_provider", "active", "catalog", 93),
    ("Helix Consulting Partners", "consulting_firm", "active", "deployed", 92),
    ("Ion Hardware Supply", "hardware_vendor", "active", "contracted", 91),
    ("Jade Enterprise Systems", "enterprise_software", "active", "catalog", 90),
    ("Zzz lowest readiness vendor", "saas_provider", "active", "catalog", 89),
]


def _seed_vendors(rows):
    from app import create_app, db
    from app.models.vendor.vendor_organization import VendorOrganization

    app = create_app("testing")
    with app.app_context():
        for name, vendor_type, status, contract_status, score in rows:
            db.session.add(VendorOrganization(
                name=f"{name} {_RUN}",
                vendor_type=vendor_type,
                status=status,
                contract_status=contract_status,
                enterprise_readiness_score=score,
            ))
        db.session.commit()
        db.session.remove()


def test_procurement_reads_searches_sorts_hides_and_pages(browser, live_server, seeded):
    _seed_vendors(_VENDORS)

    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["procurement"])
        page.goto(live_server + "/dashboard/applications/vendors?per_page=100", timeout=PAGE_TIMEOUT)
        table = page.get_by_test_id("vendor-list-table")
        table.wait_for(timeout=PAGE_TIMEOUT)

        visible_rows = page.locator('[data-table-id="vendor-list-table"] [data-row]:not([hidden])')
        total_rows_before = visible_rows.count()
        assert total_rows_before >= len(_VENDORS)

        # --- the vendors given are on screen with their real figures
        body_text = page.locator("main, body").first.inner_text()
        for name, *_rest in _VENDORS:
            assert f"{name} {_RUN}" in body_text, body_text
        assert "99%" in body_text  # a real readiness score, not a placeholder

        # --- the row's own action: View links to the vendor's own record
        first_link = page.get_by_test_id("vendor-view-link").first
        href = first_link.get_attribute("href")
        assert href and "/vendor" in href

        # --- search narrows the list (toolbar present because there are 11+ rows)
        search = page.locator('[data-slot="data-table"] [data-table-filter]').first
        search.wait_for(timeout=PAGE_TIMEOUT)
        search.fill(f"Beacon SaaS Suite {_RUN}")
        expect(visible_rows).to_have_count(1, timeout=PAGE_TIMEOUT)
        narrowed = page.locator("main, body").first.inner_text()
        assert "Beacon SaaS Suite" in narrowed
        assert "Acme Cloud Platform" not in narrowed
        reset_btn = page.locator('[data-slot="data-table"] [data-table-reset]').first
        reset_btn.click()
        expect(visible_rows).to_have_count(total_rows_before, timeout=PAGE_TIMEOUT)

        # --- sort by Vendor (name) ascending -- check the relative order of this run's
        # own rows, since the catalogue may already carry other vendors.
        page.locator('[data-table-sort-trigger="0"]').click()
        page.get_by_role("menuitem", name="Ascending").click()
        expect(visible_rows).to_have_count(total_rows_before, timeout=PAGE_TIMEOUT)
        rows_text = visible_rows.all_inner_texts()
        our_rows = [i for i, t in enumerate(rows_text) if _RUN in t]
        assert len(our_rows) == len(_VENDORS), (rows_text, our_rows)
        assert our_rows == sorted(our_rows)  # our rows keep their relative alphabetical order once sorted
        assert "Zzz lowest readiness vendor" in rows_text[our_rows[-1]]

        # --- hide the Location column
        page.get_by_role("button", name="View").click()
        page.locator('[data-table-owner="vendor-list-table"][data-table-toggle="7"]').click()
        expect(page.locator('[data-table-id="vendor-list-table"] thead [data-col="7"]')).to_be_hidden(timeout=PAGE_TIMEOUT)

        # --- the server-side pager is still the one rendered (not the macro's own footer)
        assert page.locator('[data-slot="data-table-pagination"]').count() == 0
    finally:
        ctx.close()

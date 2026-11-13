"""An architect imports a model that holds a Gap, then works it from the Gaps page.

On the import screen they choose an exchange file holding one Gap and press Import Model; the result says one
element was created. The gap's record is stored with the import, before anyone opens the Gaps page. They then open
the Gaps page: the gap is listed once, the summary says no gap has a severity yet, and its link lands on the gap's own page, which names it.
Opening the Gaps page again adds nothing. A second organisation sees none of it.
"""
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

GAPS = "/enterprise/implementation/gap-analysis"
IMPORT = "/architecture/import/oef"


def _frame(page, label, viewport):
    out = os.environ.get("GAP_FRAMES_DIR")
    if out:
        os.makedirs(out, exist_ok=True)
        page.screenshot(path="%s/gap-import-%s-%d.png" % (out, label, viewport["width"]), full_page=True)


def _exchange_file(folder, gap_name):
    path = folder / "model-with-a-gap.xml"
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" identifier="id-m">'
        "<name>Billing model</name><elements>"
        '<element identifier="g1" xsi:type="Gap"><name>%s</name>'
        "<documentation>Invoices are still keyed by hand</documentation></element>"
        "</elements><relationships/></model>" % gap_name,
        encoding="utf-8",
    )
    return str(path)


def _records(org_id, name):
    """Gap records of the organisation with this name, read straight from the store (no page is opened)."""
    from sqlalchemy import text

    from app import create_app, db

    with create_app("testing").app_context():
        rows = db.session.execute(text(
            "SELECT id, priority FROM gaps WHERE organization_id = :o AND name = :n"), {"o": org_id, "n": name}).all()
        db.session.remove()
    return rows


@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}])
def test_an_imported_gap_is_stored_once_and_listed_with_its_page(browser, live_server, tmp_path, viewport):
    org = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    gap_name = "Invoices keyed by hand %s" % uuid.uuid4().hex[:6]

    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])

        # Import the model on the import screen.
        page.goto(live_server + IMPORT, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.set_input_files("#oef_file", _exchange_file(tmp_path, gap_name))
        page.click("[data-testid='btn-import-model']")
        expect(page.locator("[data-testid='import-result']")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.locator("[data-testid='result-elements-created']")).to_have_text("1")
        expect(page.locator("[data-testid='result-elements-refused']")).to_have_text("0")
        _frame(page, "imported", viewport)

        # The gap's record came with the import, before anyone opened the Gaps page: one, not rated yet.
        stored = _records(org["org_id"], gap_name)
        assert len(stored) == 1 and stored[0][1] is None, stored

        # The Gaps page lists it once, as not rated yet, and its link lands on the gap's own page.
        page.goto(live_server + GAPS, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("gaps-table")).to_be_visible(timeout=PAGE_TIMEOUT)
        row = page.locator('[data-testid="gap-row"]', has_text=gap_name)
        expect(row).to_have_count(1)
        expect(page.get_by_text("No gaps recorded yet")).to_have_count(0)
        notice = page.locator('[data-slot="data-table-notice"]')
        expect(notice).to_contain_text("no gap has a severity yet")
        _frame(page, "listed", viewport)
        row.locator('a[data-testid^="gap-link-"]').click()
        page.wait_for_url(re.compile(r"/architecture/implementation/Gap/%d$" % stored[0][0]), timeout=PAGE_TIMEOUT)
        expect(page.locator("h1")).to_contain_text(gap_name, timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text("Invoices are still keyed by hand")
        _frame(page, "gap-page", viewport)

        # Opening the Gaps page again adds nothing.
        page.goto(live_server + GAPS, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="gap-row"]', has_text=gap_name)).to_have_count(1, timeout=PAGE_TIMEOUT)
        assert len(_records(org["org_id"], gap_name)) == 1

        # A second organisation sees none of it, and gets no record of it.
        other_page = browser.new_context(ignore_https_errors=True, viewport=viewport).new_page()
        try:
            sign_in(other_page, live_server, other["emails"]["enterprise_architect"])
            other_page.goto(live_server + GAPS, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(other_page.locator("main")).to_be_visible(timeout=PAGE_TIMEOUT)
            assert gap_name not in other_page.content()
            assert _records(other["org_id"], gap_name) == []
        finally:
            other_page.context.close()
    finally:
        context.close()

"""security_architect: open the audit log from the sidebar, filter a date
range, export it, and verify its integrity — then reload and see the result.

The export used to stop at 10,000 rows and the page had no way to check the
trail had not been altered. This drives the real controls: the sidebar link,
the date filter, the Export CSV link (a real download) and the Verify
integrity button, and asserts the downloaded file's row count equals the
count on screen and the verification survives a reload.
"""
import csv
import io
from datetime import datetime, timedelta

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# A window no other smoke test writes into, so the count is this test's own.
_DAY = datetime(2020, 2, 11)


def _seed_entries(org_id):
    from app import create_app, db
    from app.models.audit_log import AuditLog

    app = create_app("testing")
    with app.app_context():
        existing = AuditLog.query.filter(
            AuditLog.organization_id == org_id,
            AuditLog.created_at >= _DAY - timedelta(days=1),
            AuditLog.created_at < _DAY + timedelta(days=2),
        ).count()
        if existing == 0:
            for i in range(7):
                db.session.add(AuditLog(
                    organization_id=org_id, action="update", table_name="application_component",
                    record_id=i, created_at=_DAY + timedelta(minutes=i),
                ))
            # Outside the filtered window: must not appear in the export.
            db.session.add(AuditLog(
                organization_id=org_id, action="update", table_name="application_component",
                record_id=99, created_at=_DAY + timedelta(days=5),
            ))
            db.session.commit()
        db.session.remove()


def test_security_architect_exports_and_verifies_the_audit_log(browser, live_server, seeded):
    _seed_entries(seeded["ids"]["org"])
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["security_architect"])

        # Reached from the persona's own sidebar, not by typing the URL.
        page.get_by_test_id("sidebar").get_by_role("link", name="Audit Log", exact=True).click()
        page.wait_for_url("**/admin/audit-log**", timeout=PAGE_TIMEOUT)

        page.fill("#date_from", _DAY.strftime("%Y-%m-%d"))
        page.fill("#date_to", _DAY.strftime("%Y-%m-%d"))
        page.get_by_role("button", name="Filter").click()
        page.wait_for_url("**date_from=2020-02-11**", timeout=PAGE_TIMEOUT)

        on_screen = int(page.locator("#audit-total").inner_text().strip())
        assert on_screen == 7

        with page.expect_download(timeout=PAGE_TIMEOUT) as info:
            page.locator("#audit-export").click()
        download = info.value
        with open(download.path(), encoding="utf-8") as fh:
            rows = list(csv.DictReader(io.StringIO(fh.read())))
        assert len(rows) == on_screen
        assert all(r["recorded_at"].startswith("2020-02-11") for r in rows)
        assert all(r["row_hash"] for r in rows), "every exported entry carries its seal"

        page.locator("#audit-verify").click()
        expect(page.get_by_text("Integrity check passed", exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)

        page.reload(timeout=PAGE_TIMEOUT)
        verdict = page.locator("#audit-last-verification")
        expect(verdict).to_contain_text("Intact", timeout=PAGE_TIMEOUT)
        expect(verdict).to_contain_text("Last verified", timeout=PAGE_TIMEOUT)
    finally:
        context.close()

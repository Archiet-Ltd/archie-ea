"""A CTO reading the change history sees changes, not hourly rewrites that kept nothing.

The goal: open the overview, read the latest changes, follow "See every change" and find the same real
change there, with no "no field values were kept" line for a solution whose only recorded change was a
refreshed stamp. A second organisation holds the same kind of empty rewrite and a real change of its own;
neither may appear here.
"""

import os
import uuid
from datetime import datetime, timedelta

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("HISTORY_SHOTS_DIR") or os.path.join("test-results", "change-history-empty")


def _seed(org_id):
    from app import create_app, db
    from app.models.audit_log import AuditLog
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        other = Organization(name="Other Org %s" % suffix, slug="other-%s" % suffix)
        db.session.add(other)
        db.session.flush()
        now = datetime.utcnow()
        names = {
            "empty": ["Hourly Solution A %s" % suffix, "Hourly Solution B %s" % suffix],
            "real": "Calibration Ledger %s" % suffix,
            "real_after": "Reads and certifies sensors %s" % suffix,
            "foreign_empty": "Foreign Hourly %s" % suffix,
            "foreign_real": "Foreign Ledger %s" % suffix,
        }

        def entry(owner, table, record, label, old, new, seconds):
            db.session.add(AuditLog(
                organization_id=owner, action="update", table_name=table, record_id=record,
                old_value=old, new_value=new, created_at=now - timedelta(seconds=seconds),
                extra_json={"source": "system", "correlation_id": uuid.uuid4().hex, "label": label},
            ))

        stamp_old = {"blueprint_updated_at": "2026-11-07 10:00:00"}
        stamp_new = {"blueprint_updated_at": "2026-11-07 11:00:00"}
        for number, label in enumerate(names["empty"]):
            entry(org_id, "solution", 910000 + number, label, stamp_old, stamp_new, 1 + number)
        entry(org_id, "archimate_elements", 910010, names["real"],
              {"description": "Reads sensors"}, {"description": names["real_after"]}, 0)
        entry(other.id, "solution", 910020, names["foreign_empty"], stamp_old, stamp_new, 1)
        entry(other.id, "archimate_elements", 910021, names["foreign_real"],
              {"description": "a"}, {"description": "b"}, 1)
        db.session.commit()
        return {**names, "other": other.id}


def _login(browser, base, seeded, size, scheme):
    context = browser.new_context(ignore_https_errors=True, viewport=size, color_scheme=scheme)
    page = context.new_page()
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", seeded["emails"]["cto"])
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    return context, page


@pytest.mark.parametrize(
    "size,scheme",
    [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")],
)
def test_cto_reads_real_changes_and_no_empty_rewrites(browser, live_server, seeded, size, scheme):
    data = _seed(seeded["ids"]["org"])
    base = str(live_server)
    context, page = _login(browser, base, seeded, size, scheme)
    os.makedirs(SHOTS, exist_ok=True)
    tag = "%d-%s" % (size["width"], scheme)
    try:
        page.goto(base + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        recent = page.get_by_test_id("recent-changes")
        expect(recent).to_be_visible()
        expect(recent).to_contain_text(data["real"])
        for label in data["empty"] + [data["foreign_empty"], data["foreign_real"]]:
            assert label not in page.content(), "%s is not a change this organisation should read" % label
        page.screenshot(path=os.path.join(SHOTS, "overview-%s.png" % tag))

        page.get_by_test_id("recent-changes-link").click()
        page.wait_for_url(lambda url: "/history/changes" in url, timeout=PAGE_TIMEOUT)
        rows = page.get_by_test_id("change-card" if size["width"] < 640 else "change-row")
        expect(rows.filter(has_text=data["real"])).to_have_count(1)
        expect(rows.filter(has_text=data["real_after"])).to_have_count(1)
        body = page.content()
        for label in data["empty"] + [data["foreign_empty"], data["foreign_real"]]:
            assert label not in body
        assert "no field values were kept" not in body
        page.screenshot(path=os.path.join(SHOTS, "changes-%s.png" % tag), full_page=True)
    finally:
        context.close()

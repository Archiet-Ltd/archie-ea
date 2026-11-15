"""A work package or plateau with no target date is edited on its own Edit page.

An enterprise architect opens the record, clicks Edit, changes only the description and saves: the
record page shows the new words after a reload and no error appears. A record that has a target date
opens its form with the date as a date input expects it (YYYY-MM-DD) and the record shows it in the
product's date format. Escape on the Edit page returns to the record, as Cancel does.
"""
import os
import re
import uuid
from datetime import date

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")]


def _frame(page, name, viewport, theme):
    out = os.environ.get("EDIT_DATES_FRAMES_DIR")
    if not out:
        return
    os.makedirs(out, exist_ok=True)
    page.screenshot(path="%s/%s-%d-%s.png" % (out, name, viewport["width"], theme), full_page=True)


def _make(org_id, kind, name, target):
    from app import create_app, db
    from app.models.implementation_migration import Plateau, WorkPackage

    with create_app("testing").app_context():
        row = (WorkPackage if kind == "WorkPackage" else Plateau)(
            organization_id=org_id, name=name, description="Old words", target_date=target)
        db.session.add(row)
        db.session.commit()
        row_id = row.id
        db.session.remove()
    return row_id


@pytest.mark.parametrize("kind", ["WorkPackage", "Plateau"])
@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_a_record_without_a_target_date_saves_from_its_edit_page(browser, live_server, kind, viewport, theme):
    org = create_fresh_org("enterprise_architect")
    name = "%s no date %s" % (kind, uuid.uuid4().hex[:6])
    record_id = _make(org["org_id"], kind, name, None)
    dated_id = _make(org["org_id"], kind, name + " dated", date(2030, 3, 5))

    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.evaluate("localStorage.setItem('theme', '%s')" % theme)
        record = "/architecture/implementation/%s/%d" % (kind, record_id)

        page.goto(live_server + record, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name="Edit").first.click()
        form = page.locator("#archimate-form-modal")
        expect(form.get_by_role("textbox", name="Element name")).to_have_value(name, timeout=PAGE_TIMEOUT)
        form.get_by_label("Description").fill("Only the words changed")
        _frame(page, "%s-edit" % kind, viewport, theme)
        form.get_by_role("button", name="Update").click()
        page.wait_for_url(re.compile(r"/%s/%d$" % (kind, record_id)), timeout=PAGE_TIMEOUT)

        page.reload(wait_until="domcontentloaded")
        body = page.locator("body").inner_text()
        assert "Only the words changed" in body
        assert "Invalid request" not in body and "Error updating" not in body

        page.goto(live_server + "/architecture/implementation/%s/%d" % (kind, dated_id),
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name="Edit").first.click()
        form = page.locator("#archimate-form-modal")
        expect(form.get_by_label("Target Date")).to_have_value("2030-03-05", timeout=PAGE_TIMEOUT)
        assert form.get_by_label("Target Date").get_attribute("type") == "date"
        assert "GMT" not in form.inner_text()
        # One way to close the dialog, the dialog's own.
        assert form.get_by_role("button", name="Close").count() == 1
        _frame(page, "%s-dated-edit" % kind, viewport, theme)

        page.keyboard.press("Escape")
        page.wait_for_url(re.compile(r"/%s/%d$" % (kind, dated_id)), timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text(name, timeout=PAGE_TIMEOUT)
        body = page.locator("body").inner_text()
        assert body.count("05 Mar 2030") == 1, body
        assert "2030-03-05" not in body and "00:00" not in body
    finally:
        context.close()

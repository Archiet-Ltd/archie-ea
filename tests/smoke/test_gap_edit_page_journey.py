"""A gap is edited on its own Edit page, and every screen shows the rating that was saved.

An enterprise architect opens a critical gap's record page, clicks Edit, changes only the
description and saves: the gap is still critical on its page, in the gap register and on the
dashboard. Editing again to set the priority to low moves the gap to low in all three places
and the dashboard's critical count falls by one. Another organisation's gap id has no Edit page.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")]


def _frame(page, name, viewport, theme):
    out = os.environ.get("GAP_EDIT_FRAMES_DIR")
    if not out:
        return
    os.makedirs(out, exist_ok=True)
    page.screenshot(path="%s/%s-%d-%s.png" % (out, name, viewport["width"], theme), full_page=True)


def _dashboard_critical(page, live_server, expected):
    page.goto(live_server + "/implementation/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("critical-gap-row")).to_have_count(expected, timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("critical-gaps-total")).to_contain_text("%d critical gap" % expected)


def _register_rating(page, live_server, name):
    page.goto(live_server + "/enterprise/implementation/gap-analysis", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row = page.locator("[data-severity]", has_text=name)
    expect(row).to_have_count(1, timeout=PAGE_TIMEOUT)
    return row.first.get_attribute("data-severity")


def _page_rating(page):
    text = page.locator("body").inner_text()
    found = re.search(r"Severity\s*:?\s*(critical|high|medium|low)", text, re.I)
    assert found, text
    return found.group(1).lower()


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_editing_a_gap_on_its_edit_page_keeps_then_changes_its_rating(browser, live_server, viewport, theme):
    from app import create_app, db
    from app.services.gap_register_service import create_gap

    mine = create_fresh_org("enterprise_architect")
    theirs = create_fresh_org("enterprise_architect")
    name = "Payroll failover %s" % mine["suffix"]
    with create_app("testing").app_context():
        gap, _ = create_gap(mine["org_id"], name, description="Old wording", severity="critical", priority="medium")
        create_gap(mine["org_id"], "Second critical %s" % mine["suffix"], severity="critical", priority="critical")
        foreign, _ = create_gap(theirs["org_id"], "Their critical %s" % theirs["suffix"], severity="critical")
        db.session.commit()
        gap_id, foreign_id = gap.id, foreign.id
        db.session.remove()

    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, mine["emails"]["enterprise_architect"])
        page.evaluate("localStorage.setItem('theme', '%s')" % theme)
        record = "/architecture/implementation/Gap/%d" % gap_id

        page.goto(live_server + record, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert _page_rating(page) == "critical"
        page.get_by_role("link", name="Edit").first.click()
        form = page.locator("#archimate-form-modal")
        expect(form.get_by_role("textbox", name="Element name")).to_have_value(name, timeout=PAGE_TIMEOUT)
        expect(form.get_by_label("Priority")).to_have_value("medium")
        form.get_by_label("Description").fill("Only the words changed")
        _frame(page, "gap-edit-form", viewport, theme)
        form.get_by_role("button", name="Update").click()
        page.wait_for_url(re.compile(r"/Gap/%d$" % gap_id), timeout=PAGE_TIMEOUT)

        page.reload(wait_until="domcontentloaded")
        assert "Only the words changed" in page.locator("body").inner_text()
        assert _page_rating(page) == "critical"
        assert _register_rating(page, live_server, name) == "critical"
        _dashboard_critical(page, live_server, 2)

        page.goto(live_server + record, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name="Edit").first.click()
        form = page.locator("#archimate-form-modal")
        expect(form.get_by_label("Priority")).to_have_value("medium", timeout=PAGE_TIMEOUT)
        form.get_by_label("Priority").select_option("low")
        form.get_by_role("button", name="Update").click()
        page.wait_for_url(re.compile(r"/Gap/%d$" % gap_id), timeout=PAGE_TIMEOUT)

        page.reload(wait_until="domcontentloaded")
        assert _page_rating(page) == "low"
        _frame(page, "gap-page-low", viewport, theme)
        assert _register_rating(page, live_server, name) == "low"
        _dashboard_critical(page, live_server, 1)

        response = page.request.get(live_server + "/architecture/implementation/Gap/%d/edit" % foreign_id)
        assert response.status == 404
    finally:
        context.close()

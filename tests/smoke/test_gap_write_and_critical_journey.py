"""Who may record a gap, and a gap made critical on any path is counted as critical.

  - a reader of the organisation is refused when they try to record a gap, and no gap exists after;
  - an architect who raises an existing gap to Critical finds it on the Implementation Planning
    dashboard and in the gap register, and when they lower it again it leaves both;
  - a gap whose priority alone says critical is listed and rated critical on the register;
  - the dashboard's sentence says "it" for one critical gap and "them" for several;
  - the roadmap's Create Work Package button creates a package that stays after a reload.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

BASE = "/capability-map/api/roadmap"
VIEWPORTS = [{"width": 1440, "height": 900}, {"width": 390, "height": 844}]


def _context(browser, viewport):
    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _frame(page, name, viewport):
    out = os.environ.get("CRITICAL_GAP_FRAMES_DIR")
    if not out:
        return
    os.makedirs(out, exist_ok=True)
    dark = viewport["width"] == 390
    if dark:
        page.evaluate("document.documentElement.classList.add('dark')")
    page.screenshot(path="%s/%s-%d%s.png" % (out, name, viewport["width"], "-dark" if dark else ""), full_page=True)
    if dark:
        page.evaluate("document.documentElement.classList.remove('dark')")


def _gap_count(org_id, name):
    from app import create_app, db
    from app.models.implementation_migration import Gap

    with create_app("testing").app_context():
        count = Gap.query.filter(Gap.organization_id == org_id, Gap.name == name).count()
        db.session.remove()
    return count


def test_a_reader_cannot_record_a_gap(browser, live_server):
    org = create_fresh_org("viewer", platform_role="Viewer", extra_roles=("enterprise_architect",))
    name = "Reader gap %s" % org["suffix"]
    context, page = _context(browser, VIEWPORTS[0])
    try:
        sign_in(page, live_server, org["emails"]["viewer"])
        page.goto(live_server + "/implementation/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        for path in ("/api/roadmap/gaps", BASE + "/gaps"):
            status, body = api(page, "POST", path, {"name": name, "gap_type": "coverage", "priority": "critical"})
            assert status == 403, (path, status, body)
        assert _gap_count(org["org_id"], name) == 0
        expect(page.get_by_text("No critical gaps recorded yet")).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()

    context, page = _context(browser, VIEWPORTS[0])
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/implementation/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        status, body = api(page, "POST", "/api/roadmap/gaps", {"name": name, "gap_type": "coverage", "priority": "critical"})
        assert status == 201, body
        assert _gap_count(org["org_id"], name) == 1
    finally:
        context.close()


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_raising_a_gap_to_critical_and_lowering_it_again(browser, live_server, viewport):
    org = create_fresh_org("enterprise_architect")
    name = "Edited gap %s" % org["suffix"]
    context, page = _context(browser, viewport)
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/capability-roadmap", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        status, gap = api(page, "POST", BASE + "/gaps", {"name": name, "gap_type": "coverage", "priority": "high"})
        assert status in (200, 201), gap
        gap_id = gap["gap"]["id"]

        page.goto(live_server + "/implementation/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("No critical gaps recorded yet")).to_be_visible(timeout=PAGE_TIMEOUT)

        status, body = api(page, "PUT", "%s/gaps/%d" % (BASE, gap_id), {"priority": "critical"})
        assert status == 200, body
        page.goto(live_server + "/implementation/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        rows = page.get_by_test_id("critical-gap-row")
        expect(rows).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(rows.first).to_contain_text(name)
        page.get_by_test_id("critical-gaps-register-link").click()
        page.wait_for_url(re.compile("gap-analysis"), timeout=PAGE_TIMEOUT)
        register = page.locator('[data-severity="critical"]')
        expect(register).to_have_count(1)
        assert name in register.first.inner_text()
        _frame(page, "edited-critical", viewport)

        status, body = api(page, "PUT", "%s/gaps/%d" % (BASE, gap_id), {"priority": "low"})
        assert status == 200, body
        page.goto(live_server + "/implementation/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("No critical gaps recorded yet")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("critical-gap-row")).to_have_count(0)
    finally:
        context.close()


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_a_gap_critical_by_priority_alone_is_listed_and_rated_critical(browser, live_server, viewport):
    from app import create_app, db
    from app.services.gap_register_service import create_gap

    org = create_fresh_org("enterprise_architect")
    name = "Priority only gap %s" % org["suffix"]
    with create_app("testing").app_context():
        create_gap(org["org_id"], name, severity="medium", priority="critical")
        db.session.commit()
        db.session.remove()
    context, page = _context(browser, viewport)
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/implementation/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        rows = page.get_by_test_id("critical-gap-row")
        expect(rows).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(rows.first).to_contain_text(name)
        expect(rows.first).to_contain_text("Severity: critical")
        page.get_by_test_id("critical-gaps-register-link").click()
        page.wait_for_url(re.compile("gap-analysis"), timeout=PAGE_TIMEOUT)
        register = page.locator('[data-severity="critical"]')
        expect(register).to_have_count(1)
        assert name in register.first.inner_text()
        expect(page.get_by_test_id("gap-stat-critical")).to_contain_text("1")
        _frame(page, "priority-only", viewport)
    finally:
        context.close()


def test_the_sentence_says_it_for_one_gap_and_them_for_several(browser, live_server):
    from app import create_app, db
    from app.services.gap_register_service import create_gap

    org = create_fresh_org("enterprise_architect")
    context, page = _context(browser, VIEWPORTS[0])
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        with create_app("testing").app_context():
            create_gap(org["org_id"], "First critical %s" % org["suffix"], severity="critical")
            db.session.commit()
            db.session.remove()
        page.goto(live_server + "/implementation/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        total = page.get_by_test_id("critical-gaps-total")
        expect(total).to_have_text(re.compile(r"1 critical gap\.\s*Open it in the gap register"), timeout=PAGE_TIMEOUT)
        with create_app("testing").app_context():
            create_gap(org["org_id"], "Second critical %s" % org["suffix"], severity="critical")
            db.session.commit()
            db.session.remove()
        page.goto(live_server + "/implementation/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("critical-gaps-total")).to_have_text(
            re.compile(r"2 critical gaps\.\s*Open them in the gap register"), timeout=PAGE_TIMEOUT)
    finally:
        context.close()


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_create_work_package_button_creates_a_package_that_stays(browser, live_server, viewport):
    org = create_fresh_org("enterprise_architect")
    suffix = org["suffix"]
    name = "Button package %s" % suffix
    context, page = _context(browser, viewport)
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/capability-roadmap", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Add Work Package").first.click()
        dialog = page.locator("#rdm-create-wp-modal")
        dialog.get_by_label("Name").first.fill(name)
        dialog.locator("#rdm-wp-start").fill("2026-11-01")
        dialog.locator("#rdm-wp-target").fill("2027-01-31")
        dialog.get_by_role("button", name="Create Work Package").click()
        row = page.locator("tr").filter(has_text=name)
        expect(row).to_have_count(1, timeout=PAGE_TIMEOUT)
        # The row reads like its neighbours straight away, before any reload.
        expect(row.get_by_test_id("roadmap-wp-gaps")).to_contain_text("Closes no recorded gap")
        _frame(page, "work-package-created", viewport)

        page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.locator("tr").filter(has_text=name)
        expect(row).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(row.get_by_test_id("roadmap-wp-gaps")).to_contain_text("Closes no recorded gap")
    finally:
        context.close()

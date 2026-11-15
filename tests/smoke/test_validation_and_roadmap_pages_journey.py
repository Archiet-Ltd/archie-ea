"""Response validation and an application's roadmap show their figures and rows.

An enterprise architect opens an application's roadmap in an organisation with two
work packages on it, filters them, and reads the list. They then open Response
Validation, choose the application, and the four figures add up to the rows of the
comparison table. A second organisation with its own application never sees the
first one's work packages or applications.
"""
import datetime
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")]


def _org_with_application(label, packages):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.implementation_migration import WorkPackage

    org = create_fresh_org("enterprise_architect")
    with create_app("testing").app_context():
        application = ApplicationComponent(
            name="%s Ledger %s" % (label, org["suffix"]), organization_id=org["org_id"],
            lifecycle_status="operational", description="Books of account.")
        db.session.add(application)
        db.session.flush()
        for name, status in packages:
            db.session.add(WorkPackage(
                name="%s %s" % (name, org["suffix"]), status=status, priority="high",
                start_date=datetime.date(2027, 1, 1), target_date=datetime.date(2027, 6, 30),
                application_component_id=application.id, organization_id=org["org_id"]))
        db.session.commit()
        org["application_id"] = application.id
        org["application_name"] = application.name
        db.session.remove()
    return org


def _frame(page, name, viewport, scheme):
    out = os.environ.get("SMOKE_FRAMES_DIR")
    if not out:
        return
    os.makedirs(out, exist_ok=True)
    if scheme == "dark":
        page.evaluate("document.documentElement.classList.add('dark')")
    page.screenshot(path=os.path.join(out, "%s-%s-%d.png" % (name, scheme, viewport["width"])), full_page=True)
    page.evaluate("document.documentElement.classList.remove('dark')")


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS)
def test_roadmap_lists_the_applications_work_packages(browser, live_server, viewport, scheme):
    mine = _org_with_application("Mine", [("Retire batch jobs", "planned"), ("Move to cloud", "in_progress")])
    theirs = _org_with_application("Theirs", [("Their only package", "planned")])

    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    page = context.new_page()
    try:
        sign_in(page, live_server, mine["emails"]["enterprise_architect"])
        page.goto("%s/applications/%d/roadmap" % (live_server, mine["application_id"]),
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("tab", name=re.compile("list", re.I)).click()
        region = page.get_by_role("region", name="Work packages")
        region.get_by_text("Retire batch jobs").first.wait_for(timeout=PAGE_TIMEOUT)
        text = region.inner_text()
        assert "Move to cloud" in text and "Their only package" not in text
        assert region.locator("tbody tr:visible").count() == 2
        assert "1/1/2027 → 6/30/2027" in text
        _frame(page, "roadmap", viewport, scheme)

        page.locator("#roadmap-filter").select_option("completed")
        empty = region.get_by_text("No work packages for this filter")
        empty.wait_for(timeout=PAGE_TIMEOUT)
        assert "add a work package" in empty.inner_text()

        page.goto("%s/applications/%d/roadmap" % (live_server, theirs["application_id"]),
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert mine["application_name"] not in page.locator("main").inner_text()
    finally:
        context.close()


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS)
def test_validation_figures_add_up_to_the_comparison_rows(browser, live_server, viewport, scheme):
    mine = _org_with_application("Mine", [])
    theirs = _org_with_application("Theirs", [])

    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    page = context.new_page()
    try:
        sign_in(page, live_server, mine["emails"]["enterprise_architect"])
        page.goto(live_server + "/dashboard/rationalization/validate", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        figures = {k: page.locator('[data-testid="%s-fields-value"]' % k) for k in
                   ("total", "matched", "conflict", "missing")}
        assert figures["total"].inner_text() == "—"
        options = page.locator("#app-selector option").all_inner_texts()
        assert mine["application_name"] in options
        assert theirs["application_name"] not in options
        _frame(page, "validation-empty", viewport, scheme)

        page.locator("#app-selector").select_option(str(mine["application_id"]))
        expect(figures["total"]).not_to_have_text("—", timeout=PAGE_TIMEOUT)
        n = {k: int(v.inner_text()) for k, v in figures.items()}
        rows = page.locator("table.dense-table tbody tr").count()
        assert n["total"] > 0 and n["total"] == rows
        assert n["matched"] + n["conflict"] + n["missing"] == n["total"]
        assert "Name" in page.locator("table.dense-table").inner_text()
        _frame(page, "validation", viewport, scheme)
    finally:
        context.close()

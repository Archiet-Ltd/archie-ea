"""Three figures that disagreed with the page beside them now agree.

An enterprise architect in an organisation seeded with linked data:
  - the Implementation Planning dashboard lists the critical gaps the gap register
    counts, and a second organisation's critical gap stays out of it;
  - Capability Health's "At Risk Hotspots" tile counts the capabilities its "Top
    At-Risk Capabilities" list shows, under one definition of at risk;
  - a work package that closes no recorded gap says so in words on the roadmap.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [{"width": 1440, "height": 900}, {"width": 390, "height": 844}]


def _context(browser, viewport):
    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _frame(page, name, viewport):
    out = os.environ.get("ACC9GAP_FRAMES_DIR")
    if not out:
        return
    os.makedirs(out, exist_ok=True)
    dark = viewport["width"] == 390
    if dark:
        page.evaluate("document.documentElement.classList.add('dark')")
    page.screenshot(path="%s/%s-%d%s.png" % (out, name, viewport["width"], "-dark" if dark else ""), full_page=True)
    if dark:
        page.evaluate("document.documentElement.classList.remove('dark')")


def _inside_viewport(page, locator):
    box = locator.bounding_box()
    width = page.viewport_size["width"]
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= width + 1, (box, width)


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_dashboard_lists_the_critical_gaps_the_register_counts(browser, live_server, viewport):
    from app import create_app, db
    from app.services.gap_register_service import create_gap

    mine = create_fresh_org("enterprise_architect")
    theirs = create_fresh_org("enterprise_architect")
    with create_app("testing").app_context():
        create_gap(mine["org_id"], "Payroll has no failover %s" % mine["suffix"], severity="critical", priority="high")
        create_gap(mine["org_id"], "Minor wording gap %s" % mine["suffix"], severity="low", priority="critical")
        create_gap(theirs["org_id"], "Their critical gap %s" % theirs["suffix"], severity="critical")
        db.session.commit()
        db.session.remove()

    context, page = _context(browser, viewport)
    try:
        sign_in(page, live_server, mine["emails"]["enterprise_architect"])
        page.goto(live_server + "/enterprise/implementation/gap-analysis?severity=critical",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        register = page.locator('[data-severity="critical"]')
        expect(register).to_have_count(1)
        assert "Payroll has no failover" in register.first.inner_text()

        page.goto(live_server + "/implementation/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        rows = page.get_by_test_id("critical-gap-row")
        expect(rows).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(rows.first).to_contain_text("Payroll has no failover %s" % mine["suffix"])
        body = page.locator("body").inner_text()
        assert "No critical gaps recorded yet" not in body
        assert "Minor wording gap" not in body and "Their critical gap" not in body
        _inside_viewport(page, rows.first)
        _frame(page, "dashboard", viewport)
    finally:
        context.close()


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_dashboard_with_no_critical_gap_still_says_so(browser, live_server, viewport):
    org = create_fresh_org("enterprise_architect")
    context, page = _context(browser, viewport)
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/implementation/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("No critical gaps recorded yet")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("critical-gap-row")).to_have_count(0)
    finally:
        context.close()


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_at_risk_tile_counts_the_capabilities_the_list_shows(browser, live_server, viewport):
    from app import create_app, db
    from app.models.business_capabilities import BusinessCapability

    org = create_fresh_org("enterprise_architect")
    noun = "Risk%s" % org["suffix"]
    # (label, current maturity, target maturity): no application behind any of them, so the
    # score is 100 - 15 per level short - 40: 30 and 45 are below 60, 60 is a warning.
    seeds = [("Settle", 1, 3), ("Reconcile", 2, 3), ("Archive", 1, 1)]
    with create_app("testing").app_context():
        for label, current, target in seeds:
            db.session.add(BusinessCapability(
                name="%s %s" % (noun, label), level=1, organization_id=org["org_id"],
                current_maturity_level=current, target_maturity_level=target))
        db.session.commit()
        db.session.remove()

    context, page = _context(browser, viewport)
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/strategic/capability-health", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Top At-Risk Capabilities")).to_be_visible(timeout=PAGE_TIMEOUT)
        tile = page.locator("[data-slot=card]", has_text="At Risk Hotspots").first
        tile_count = int(re.search(r"At Risk Hotspots\s*(\d+)", tile.inner_text()).group(1))
        listed = [
            row.locator("td").first.inner_text().splitlines()[0].strip()
            for row in page.locator("table.dense-table tbody tr").all()
            if noun in row.inner_text()
        ]
        assert tile_count == 2, tile_count
        assert sorted(listed) == ["%s Reconcile" % noun, "%s Settle" % noun], listed
        statuses = page.locator("table.dense-table tbody tr", has_text=noun).locator("td:last-child").all_inner_texts()
        assert all("Warning" not in s for s in statuses), statuses
        _frame(page, "capability-health", viewport)
    finally:
        context.close()


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_work_package_with_no_gap_says_so_on_the_roadmap(browser, live_server, viewport):
    org = create_fresh_org("enterprise_architect")
    suffix = org["suffix"]
    context, page = _context(browser, viewport)
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/enterprise/implementation/gap-analysis",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        status, plateau = api(page, "POST", "/capability-map/api/roadmap/plateaus",
                              {"name": "Target %s" % suffix, "sequence_order": 1})
        assert status == 201, plateau
        status, gap = api(page, "POST", "/capability-map/api/roadmap/gaps",
                          {"name": "Closed gap %s" % suffix, "gap_type": "coverage", "priority": "high"})
        assert status in (200, 201), gap
        for name, close in (("Procure testers %s" % suffix, False), ("Fix billing %s" % suffix, True)):
            status, wp = api(page, "POST", "/capability-map/api/roadmap/work-packages", {
                "name": name, "plateau_id": plateau["plateau"]["id"],
                "start_date": "2026-11-01", "target_date": "2027-01-31"})
            assert status == 201, wp
            if close:
                status, linked = api(page, "POST", "/implementation/work-packages/%d/link-gap/%d" % (
                    wp["work_package"]["id"], gap["gap"]["id"]))
                if status >= 400:
                    from app import create_app
                    from app.services import work_package_service
                    with create_app("testing").app_context():
                        work_package_service.link_gap(
                            wp["work_package"]["id"], gap["gap"]["id"], organization_id=org["org_id"])
                        from app import db
                        db.session.commit()
                        db.session.remove()

        page.goto(live_server + "/capability-roadmap", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        bare = page.locator("tr").filter(has_text="Procure testers %s" % suffix).get_by_test_id("roadmap-wp-gaps")
        expect(bare).to_contain_text("Closes no recorded gap", timeout=PAGE_TIMEOUT)
        assert bare.inner_text().strip() != "—"
        # The table scrolls sideways at phone width, so the cell is checked against its own
        # box: the sentence is not cut off inside it.
        assert bare.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
        bare.scroll_into_view_if_needed()
        _inside_viewport(page, bare)
        linked = page.locator("tr").filter(has_text="Fix billing %s" % suffix).get_by_test_id("roadmap-wp-gaps")
        expect(linked).to_contain_text("Closed gap %s" % suffix, timeout=PAGE_TIMEOUT)
        expect(linked).not_to_contain_text("Closes no recorded gap")
        _frame(page, "roadmap", viewport)
    finally:
        context.close()

"""Figures that disagreed with the page beside them now agree.

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
        create_gap(mine["org_id"], "Minor wording gap %s" % mine["suffix"], severity="low", priority="low")
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
                status, linked = api(page, "POST", "/capability-map/api/roadmap/gaps/%d/link-work-package/%d" % (
                    gap["gap"]["id"], wp["work_package"]["id"]))
                assert status == 200, linked

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


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_gap_marked_critical_on_the_roadmap_form_reaches_the_dashboard(browser, live_server, viewport):
    """An architect fills the roadmap's Add Gap form with Critical and finds it on the dashboard,
    counted in the same number the gap register shows, with a link that opens that register."""
    org = create_fresh_org("enterprise_architect")
    suffix = org["suffix"]
    name = "Form critical gap %s" % suffix
    context, page = _context(browser, viewport)
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/capability-roadmap", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Add Gap").first.click()
        dialog = page.locator("#rdm-create-gap-modal")
        dialog.get_by_label("Gap Name").fill(name)
        dialog.get_by_label("Priority").select_option("critical")
        dialog.get_by_role("button", name="Create Gap").click()
        expect(page.get_by_test_id("rdm-recorded-gaps")).to_have_text("1", timeout=PAGE_TIMEOUT)

        page.goto(live_server + "/implementation/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        rows = page.get_by_test_id("critical-gap-row")
        expect(rows).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(rows.first).to_contain_text(name)
        assert "No critical gaps recorded yet" not in page.locator("body").inner_text()
        expect(page.get_by_test_id("critical-gaps-total")).to_contain_text("1 critical gap")
        page.get_by_test_id("critical-gaps-register-link").click()
        page.wait_for_url(re.compile("gap-analysis"), timeout=PAGE_TIMEOUT)
        register = page.locator('[data-severity="critical"]')
        expect(register).to_have_count(1)
        assert name in register.first.inner_text()
        _frame(page, "form-gap-register", viewport)
    finally:
        context.close()


def test_dashboard_says_how_many_critical_gaps_it_does_not_list(browser, live_server):
    from app import create_app, db
    from app.services.gap_register_service import create_gap

    org = create_fresh_org("enterprise_architect")
    with create_app("testing").app_context():
        for index in range(7):
            create_gap(org["org_id"], "Critical %d %s" % (index, org["suffix"]), severity="critical")
        db.session.commit()
        db.session.remove()
    context, page = _context(browser, VIEWPORTS[0])
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/implementation/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("critical-gap-row")).to_have_count(5, timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("critical-gaps-total")).to_contain_text("Showing 5 of 7 critical gaps")
        page.get_by_test_id("critical-gaps-register-link").click()
        page.wait_for_url(re.compile("gap-analysis"), timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-severity="critical"]')).to_have_count(7)
    finally:
        context.close()


def test_at_risk_list_says_how_many_it_does_not_show(browser, live_server):
    from app import create_app, db
    from app.models.business_capabilities import BusinessCapability

    org = create_fresh_org("enterprise_architect")
    with create_app("testing").app_context():
        for index in range(12):
            db.session.add(BusinessCapability(
                name="Shortfall %02d %s" % (index, org["suffix"]), level=1, organization_id=org["org_id"],
                current_maturity_level=1, target_maturity_level=3))
        db.session.commit()
        db.session.remove()
    context, page = _context(browser, VIEWPORTS[0])
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/strategic/capability-health", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        tile = page.locator("[data-slot=card]", has_text="At Risk Hotspots").first
        assert int(re.search(r"At Risk Hotspots\s*(\d+)", tile.inner_text()).group(1)) == 12
        expect(page.get_by_test_id("at-risk-list-total")).to_contain_text("Showing the 10 most at risk of 12")
    finally:
        context.close()


def test_roadmap_evidence_line_counts_the_gaps_the_tile_counts(browser, live_server):
    from app import create_app, db
    from app.models.implementation_migration import GAP_KIND_PLATEAU_TRANSITION, Gap, Plateau
    from app.services.gap_register_service import create_gap

    org = create_fresh_org("enterprise_architect")
    with create_app("testing").app_context():
        for index in range(2):
            create_gap(org["org_id"], "Recorded %d %s" % (index, org["suffix"]))
        origin = Plateau(name="From %s" % org["suffix"], organization_id=org["org_id"])
        target = Plateau(name="To %s" % org["suffix"], organization_id=org["org_id"])
        db.session.add_all([origin, target])
        db.session.flush()
        db.session.add(Gap(
            name="Transition %s" % org["suffix"], organization_id=org["org_id"],
            gap_kind=GAP_KIND_PLATEAU_TRANSITION, originating_plateau_id=origin.id,
            target_plateau_id=target.id))
        db.session.commit()
        db.session.remove()
    context, page = _context(browser, VIEWPORTS[0])
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/capability-roadmap", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("rdm-recorded-gaps")).to_have_text("2", timeout=PAGE_TIMEOUT)
        evidence = page.get_by_test_id("roadmap-summary-footer").get_by_test_id("result-footer-evidence")
        expect(evidence).to_contain_text("Gaps: 2")
    finally:
        context.close()


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_architecture_intelligence_counts_and_ranks_a_priority_critical_gap(browser, live_server, viewport):
    """An enterprise architect opens Architecture Intelligence: the gap rated critical by priority alone is
    counted critical, View gaps lists it, the severity filter for medium leaves it out, and the action
    list names it critical."""
    from app import create_app, db
    from app.services.gap_register_service import create_gap

    mine = create_fresh_org("enterprise_architect")
    theirs = create_fresh_org("enterprise_architect")
    name = "Priority only %s" % mine["suffix"]
    with create_app("testing").app_context():
        create_gap(mine["org_id"], name, severity="medium", priority="critical")
        create_gap(mine["org_id"], "Plain high %s" % mine["suffix"], severity="high", priority="high")
        create_gap(theirs["org_id"], "Their critical %s" % theirs["suffix"], severity="critical")
        db.session.commit()
        db.session.remove()

    context, page = _context(browser, viewport)
    try:
        sign_in(page, live_server, mine["emails"]["enterprise_architect"])
        page.goto(live_server + "/enterprise/analysis/ai-architecture", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        tile = page.locator("div.rounded-lg.border", has=page.get_by_test_id("view-gaps-critical")).first
        expect(tile).to_contain_text("Critical Severity")
        expect(tile).to_contain_text("1")
        assert page.get_by_test_id("view-gaps-medium").count() == 0
        row = page.locator("li, tr, article, div", has_text=name).last
        assert "critical" in row.inner_text().lower(), row.inner_text()
        _frame(page, "architecture-intelligence", viewport)

        page.get_by_test_id("view-gaps-critical").click()
        page.wait_for_load_state("domcontentloaded")
        listed = page.locator('[data-severity="critical"]')
        expect(listed).to_have_count(1)
        assert name in listed.first.inner_text()
        assert "Their critical" not in page.locator("body").inner_text()

        page.goto(live_server + "/enterprise/implementation/gap-analysis?severity=medium",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert name not in page.locator("body").inner_text()
    finally:
        context.close()

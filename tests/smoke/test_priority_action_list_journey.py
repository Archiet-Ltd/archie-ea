"""An enterprise architect works from one prioritised action list.

In an organisation with a critical gap, a late work package and an open risk,
the architect opens the impact analysis page, reads the one-sentence ranking
rule and the first rows in order, clicks a gap row to open that gap, comes
back, opens a risk row and sees that risk, then reloads the list and finds the
same order. The Architecture Intelligence page shows the same list.
"""

import re
from datetime import datetime, timedelta

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

LIST_PAGES = ("/strategic/impact-analysis", "/enterprise/analysis/ai-architecture")


def _seed(org_id, suffix):
    from app import create_app, db
    from app.models.implementation_migration import Gap
    from app.models.risk import Risk, RiskStatus
    from app.models.unified_work_package import UnifiedWorkPackage

    app = create_app("testing")
    names = {
        "gap": "Event resilience gap %s" % suffix,
        "work": "Deploy field app %s" % suffix,
        "risk": "Relay single point of failure %s" % suffix,
    }
    with app.app_context():
        db.session.add(Gap(name=names["gap"], severity="critical", organization_id=org_id))
        db.session.add(UnifiedWorkPackage(
            name=names["work"], organization_id=org_id, status="in_progress", priority="critical",
            progress_percentage=58, end_date=datetime.utcnow() - timedelta(days=10),
        ))
        db.session.add(Risk(title=names["risk"], likelihood=4, impact=5, status=RiskStatus.OPEN,
                            organization_id=org_id))
        db.session.commit()
        db.session.remove()
    return names


def _order(page):
    return [t.strip() for t in page.locator('[data-testid="priority-action-link"]').all_inner_texts()]


def test_architect_works_from_the_prioritised_action_list(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    names = _seed(org["org_id"], org["suffix"])
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + LIST_PAGES[0], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        expect(page.get_by_test_id("priority-actions-rule")).to_contain_text("Ranked by severity, critical first")
        expected = [
            "Convert the %s to a work package" % names["gap"],
            "Finish %s: due %s, 58 percent done" % (
                names["work"], (datetime.utcnow() - timedelta(days=10)).strftime("%-d %b")),
            "Reduce the risk: %s" % names["risk"],
        ]
        assert _order(page) == expected

        # The gap row names an action; the page it opens must offer that action on that gap.
        page.get_by_test_id("priority-action-link").first.click()
        page.wait_for_url(lambda url: "/implementation/gap-analysis" in url, timeout=PAGE_TIMEOUT)
        gap_row = page.locator("tr").filter(has_text=names["gap"]).first
        expect(gap_row).to_be_visible()
        expect(gap_row.get_by_role("button", name="Convert this gap to a work package")).to_be_visible()
        expect(gap_row.get_by_role("button", name="Convert this gap to a work package")).to_be_enabled()

        page.go_back(wait_until="domcontentloaded")
        page.get_by_test_id("priority-action-link").nth(2).click()
        page.wait_for_url(lambda url: "/risks/" in url, timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(names["risk"]).first).to_be_visible()

        page.goto(live_server + LIST_PAGES[0], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.reload(wait_until="domcontentloaded")
        assert _order(page) == expected

        page.goto(live_server + LIST_PAGES[1], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert _order(page) == expected
    finally:
        context.close()


def test_a_list_cut_at_ten_has_a_link_to_all_of_it(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    suffix = org["suffix"]
    from app import create_app, db
    from app.models.implementation_migration import Gap

    app = create_app("testing")
    with app.app_context():
        for n in range(13):
            db.session.add(Gap(name="Cut-off gap %02d %s" % (n, suffix), severity="high",
                               organization_id=org["org_id"]))
        db.session.commit()
        db.session.remove()
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        for path in LIST_PAGES:
            page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert len(_order(page)) == 10
            page.get_by_role("link", name="Show all 13 actions").click()
            page.wait_for_url(lambda url: "all_actions=1" in url, timeout=PAGE_TIMEOUT)
            assert len(_order(page)) == 13
            expect(page.get_by_test_id("priority-actions")).to_contain_text("Cut-off gap 12 %s" % suffix)
    finally:
        context.close()


def test_a_critical_row_lands_on_a_critical_highlighted_gap(browser, live_server):
    """The row the list calls Critical is, on the page it opens, Critical and marked."""
    org = create_fresh_org("enterprise_architect")
    suffix = org["suffix"]
    from app import create_app, db
    from app.models.implementation_migration import Gap

    app = create_app("testing")
    with app.app_context():
        # Only severity is set: priority and impact keep their stored default of "medium".
        critical = Gap(name="Event backbone resilience %s" % suffix, severity="critical",
                       organization_id=org["org_id"])
        calm = Gap(name="Minor reporting gap %s" % suffix, severity="low", organization_id=org["org_id"])
        db.session.add_all([critical, calm])
        db.session.commit()
        critical_id, calm_id = critical.id, calm.id
        db.session.remove()
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + LIST_PAGES[0], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.get_by_test_id("priority-action-row").filter(has_text="Event backbone resilience")
        expect(row).to_contain_text("Critical")
        row.get_by_test_id("priority-action-link").click()
        page.wait_for_url(lambda url: url.endswith("/implementation/gap-analysis#gap-%d" % critical_id),
                          timeout=PAGE_TIMEOUT)

        landing = page.locator("#gap-%d" % critical_id)
        expect(landing).to_be_visible()
        expect(landing).to_contain_text("Critical")
        # The page's own summary agrees with the list: one critical gap.
        summary = page.get_by_text("By the severity recorded on each gap").locator(
            "xpath=ancestor::*[@data-slot='card'][1]")
        expect(summary).to_contain_text(re.compile(r"Critical\s+1\b"))

        # The landing row is the browser's target and is marked; the other row is not.
        assert page.evaluate("document.querySelector(':target') && document.querySelector(':target').id")             == "gap-%d" % critical_id
        shade = lambda sel: page.evaluate(  # noqa: E731
            "s => getComputedStyle(document.querySelector(s)).backgroundColor", sel)
        assert shade("#gap-%d" % critical_id) != shade("#gap-%d" % calm_id)
        # ... across the whole row, the pinned first and last cells included.
        for cell in ("td:first-child", "td:last-child"):
            assert shade("#gap-%d %s" % (critical_id, cell)) != shade("#gap-%d %s" % (calm_id, cell)), cell

        # No bare dashes: an empty Type or Work packages cell says why and what to do.
        expect(landing.get_by_test_id("gap-type-unset")).to_contain_text("open the gap to classify it")
        expect(landing.get_by_test_id("gap-no-work-package")).to_contain_text("Convert to Work Package")
        expect(landing.get_by_role("cell", name="—", exact=True)).to_have_count(0)
    finally:
        context.close()

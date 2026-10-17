"""An enterprise architect works from one prioritised action list.

In an organisation with a critical gap, a late work package and an open risk,
the architect opens the impact analysis page, reads the one-sentence ranking
rule and the first rows in order, clicks a gap row to open that gap, comes
back, opens a risk row and sees that risk, then reloads the list and finds the
same order. The Architecture Intelligence page shows the same list.
"""

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
            "Link a work package to the %s" % names["gap"],
            "Finish %s: due %s, 58 percent done" % (
                names["work"], (datetime.utcnow() - timedelta(days=10)).strftime("%-d %b")),
            "Reduce the risk: %s" % names["risk"],
        ]
        assert _order(page) == expected

        page.get_by_test_id("priority-action-link").first.click()
        page.wait_for_url(lambda url: "/architecture/implementation/Gap/" in url, timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text(names["gap"])

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

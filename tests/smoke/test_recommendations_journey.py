"""A portfolio manager reads the recommendations list in a real browser.

Open it from the sidebar, see the most urgent item first with the record it came
from, follow the link to the backlog; reload and see the same list; a second
organisation sees nothing.
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id):
    from app import create_app, db
    from app.datetime_helpers import utcnow
    from app.models.drift_report import DriftReport
    from app.models.implementation_migration import Gap

    app = create_app("testing")
    tag = uuid.uuid4().hex[:6]
    with app.app_context():
        db.session.add(Gap(organization_id=org_id, name="Slow gap %s" % tag, priority="low"))
        finding = {
            "type": "orphaned_element",
            "severity": "high",
            "why": "Nothing connects to it.",
            "elements": [{"archimate_element_id": 1, "name": "Old box %s" % tag, "archimate_type": "Node", "role": "subject"}],
        }
        db.session.add(DriftReport(
            organization_id=org_id,
            report_json={"findings": [finding], "summary": {"total": 1}},
            spec_hash="sha256:x",
            computed_at=utcnow(),
            finding_count=1,
        ))
        db.session.commit()
        db.session.remove()
    return tag


def _page(browser):
    ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    ctx.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return ctx, ctx.new_page()


def test_portfolio_manager_reads_recommendations_in_urgency_order(browser, live_server):
    org = create_fresh_org("portfolio_manager")
    other = create_fresh_org("portfolio_manager")
    tag = _seed(org["org_id"])

    ctx, page = _page(browser)
    sign_in(page, live_server, org["emails"]["portfolio_manager"])
    page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_role("link", name="Recommendations", exact=True).first.click()
    expect(page).to_have_url(re.compile(r"/intelligence/recommendations$"), timeout=PAGE_TIMEOUT)

    for _ in range(2):
        items = page.get_by_test_id("recommendation")
        expect(items).to_have_count(2, timeout=PAGE_TIMEOUT)
        expect(items.nth(0)).to_contain_text("Link or retire “Old box %s”" % tag)
        expect(items.nth(0)).to_contain_text("model-health report")
        expect(items.nth(1)).to_contain_text("Plan a work package for the gap “Slow gap %s”" % tag)
        page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_test_id("recommendation").nth(1).get_by_role("link", name="Open the backlog").click()
    expect(page.locator("[data-backlog-item]")).to_contain_text("Slow gap %s" % tag, timeout=PAGE_TIMEOUT)
    ctx.close()

    ctx, page = _page(browser)
    sign_in(page, live_server, other["emails"]["portfolio_manager"])
    page.goto(live_server + "/intelligence/recommendations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("recommendation")).to_have_count(0)
    expect(page.locator("body")).to_contain_text("Nothing to recommend", timeout=PAGE_TIMEOUT)
    ctx.close()

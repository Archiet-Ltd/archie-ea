"""A portfolio manager opens the transformation backlog from the sidebar in a real browser.

The most urgent unplanned item is first; the answer is the same after a reload;
a second organisation sees an empty backlog.
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
    from app.models.implementation_migration import Gap
    from app.models.unified_work_package import UnifiedWorkPackage

    app = create_app("testing")
    tag = uuid.uuid4().hex[:6]
    with app.app_context():
        db.session.add_all([
            Gap(organization_id=org_id, name="Low gap %s" % tag, priority="low"),
            Gap(organization_id=org_id, name="Critical gap %s" % tag, priority="critical"),
            UnifiedWorkPackage(organization_id=org_id, name="High work %s" % tag, priority="high"),
        ])
        db.session.commit()
        db.session.remove()
    return tag


def _page(browser):
    ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    ctx.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return ctx, ctx.new_page()


def test_portfolio_manager_reads_the_backlog_in_priority_order(browser, live_server):
    org = create_fresh_org("portfolio_manager")
    other = create_fresh_org("portfolio_manager")
    tag = _seed(org["org_id"])

    ctx, page = _page(browser)
    sign_in(page, live_server, org["emails"]["portfolio_manager"])
    page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_role("link", name="Transformation backlog", exact=True).first.click()
    expect(page).to_have_url(re.compile(r"/portfolio/backlog$"), timeout=PAGE_TIMEOUT)

    for _ in range(2):
        items = page.locator("[data-backlog-item]")
        expect(items).to_have_count(3, timeout=PAGE_TIMEOUT)
        expect(items.nth(0)).to_contain_text("Critical gap %s" % tag)
        expect(items.nth(1)).to_contain_text("High work %s" % tag)
        expect(items.nth(2)).to_contain_text("Low gap %s" % tag)
        page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    ctx.close()

    ctx, page = _page(browser)
    sign_in(page, live_server, other["emails"]["portfolio_manager"])
    page.goto(live_server + "/portfolio/backlog", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("[data-backlog-empty]")).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.locator("[data-backlog-item]")).to_have_count(0)
    ctx.close()

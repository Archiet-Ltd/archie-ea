"""A portfolio manager checks a quarter for competing initiatives in a real browser.

Open the conflict page, enter the quarter, see the two initiatives that share a
capability; reload and see the same answer. A second organisation sees none.
"""

import datetime
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id):
    from app import create_app, db
    from app.models.business_capabilities import BusinessCapability
    from app.models.strategic import StrategicInitiative

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        cap = BusinessCapability(name="Order to Cash %s" % suffix, organization_id=org_id)
        one = StrategicInitiative(name="Billing rebuild %s" % suffix, organization_id=org_id,
                                  start_date=datetime.date(2026, 9, 1), target_completion_date=datetime.date(2026, 12, 1),
                                  status="in_progress")
        two = StrategicInitiative(name="Invoice portal %s" % suffix, organization_id=org_id,
                                  start_date=datetime.date(2026, 10, 15), target_completion_date=datetime.date(2027, 2, 1),
                                  status="in_progress")
        db.session.add_all([cap, one, two])
        db.session.flush()
        one.capabilities.append(cap)
        two.capabilities.append(cap)
        db.session.commit()
        out = {"capability": cap.name, "one": one.name, "two": two.name}
        db.session.remove()
    return out


def _page(browser):
    ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    ctx.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return ctx, ctx.new_page()


def test_portfolio_manager_finds_competing_initiatives(browser, live_server):
    org = create_fresh_org("portfolio_manager")
    other = create_fresh_org("portfolio_manager")
    seeded = _seed(org["org_id"])

    ctx, page = _page(browser)
    sign_in(page, live_server, org["emails"]["portfolio_manager"])
    page.goto(live_server + "/portfolio/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("link", name="Check for initiative conflicts next quarter").click()
    page.fill("#conflict-quarter", "2026-Q4")
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        page.get_by_role("button", name="Show conflicts").click()
    listing = page.get_by_test_id("conflict-list")
    expect(listing).to_contain_text("Same capability: " + seeded["capability"])
    expect(listing).to_contain_text(seeded["one"])
    expect(listing).to_contain_text(seeded["two"])
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("conflict-list")).to_contain_text(seeded["two"])
    ctx.close()

    ctx, page = _page(browser)
    sign_in(page, live_server, other["emails"]["portfolio_manager"])
    page.goto(live_server + "/portfolio/conflicts?quarter=2026-Q4", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("conflict-list")).to_contain_text("No conflicts recorded")
    ctx.close()

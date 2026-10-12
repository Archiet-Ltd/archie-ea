"""A portfolio manager submits a demand, sees what already covers it and records a route.

Open the new-demand form, name the capability, submit; the queue shows the
applications already mapped to that capability and a recommendation. Choosing
the route and approving it is still there after a reload, in the decided list.
A second organisation's queue holds none of it.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.unified_capability import UnifiedCapability

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        cap = UnifiedCapability(name="Supplier Onboarding %s" % suffix, organization_id=org_id, level=1)
        application = ApplicationComponent(name="Onboard Hub %s" % suffix, organization_id=org_id)
        db.session.add_all([cap, application])
        db.session.flush()
        if cap.archimate_element_id is None:
            element = ArchiMateElement(name=cap.name, type="Capability", layer="strategy", organization_id=org_id)
            db.session.add(element)
            db.session.flush()
            cap.archimate_element_id = element.id
        db.session.add(UnifiedApplicationCapabilityMapping(
            unified_capability_id=cap.id, application_component_id=application.id,
            support_level="full", coverage_percentage=90, is_active=True))
        db.session.commit()
        out = {"capability": cap.name, "application": application.name, "suffix": suffix}
        db.session.remove()
    return out


def _page(browser):
    ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    ctx.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return ctx, ctx.new_page()


def test_demand_shows_existing_coverage_and_records_the_route(browser, live_server):
    org = create_fresh_org("portfolio_manager")
    other = create_fresh_org("portfolio_manager")
    seeded = _seed(org["org_id"])
    title = "Faster supplier onboarding %s" % seeded["suffix"]

    ctx, page = _page(browser)
    sign_in(page, live_server, org["emails"]["portfolio_manager"])
    page.goto(live_server + "/portfolio/demands/new", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#title", title)
    page.get_by_role("combobox", name="Type a capability name").fill(seeded["capability"])
    page.get_by_role("option", name=seeded["capability"]).click()
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        page.get_by_role("button", name="Submit demand").click()

    row = page.locator("div.px-6.py-4").filter(has_text=title)
    expect(row).to_contain_text(seeded["application"], timeout=PAGE_TIMEOUT)
    expect(row).to_contain_text("Recommended: reuse")

    row.get_by_label("Route").select_option("reuse")
    row.get_by_label("Decision rationale").fill("Use the application we already run")
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        row.get_by_role("button", name="Approve").click()

    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    decided = page.locator("tr").filter(has_text=title)
    expect(decided).to_contain_text("Approved")
    expect(decided).to_contain_text("Reuse")
    ctx.close()

    ctx, page = _page(browser)
    sign_in(page, live_server, other["emails"]["portfolio_manager"])
    page.goto(live_server + "/portfolio/demands", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_text(title)).to_have_count(0)
    ctx.close()

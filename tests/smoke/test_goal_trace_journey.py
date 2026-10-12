"""A business architect traces a goal from its element page; an operations lead finds value streams at risk.

The architect opens the goal's element, presses "Trace from here", sees the
initiative recorded under the goal, and sees the same page after a reload. The
operations lead opens the sidebar and follows "Value Streams at Risk". A second
organisation is refused the first organisation's trace.
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
    from app.models.archimate_core import ArchiMateElement
    from app.models.motivation import Goal
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        goal = Goal(name="Grow Retail %s" % suffix, organization_id=org_id)
        initiative = EnterpriseInitiative(name="Digital Checkout %s" % suffix, organization_id=org_id)
        db.session.add_all([goal, initiative])
        db.session.flush()
        if goal.archimate_element_id is None:
            element = ArchiMateElement(name=goal.name, type="Goal", layer="motivation", organization_id=org_id)
            db.session.add(element)
            db.session.flush()
            goal.archimate_element_id = element.id
        goal.initiatives.append(initiative)
        db.session.commit()
        out = {"goal_id": goal.id, "element_id": goal.archimate_element_id, "initiative": initiative.name}
        db.session.remove()
    return out


def _page(browser):
    ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    ctx.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return ctx, ctx.new_page()


def test_architect_traces_a_goal_from_its_element(browser, live_server):
    org = create_fresh_org("business_architect")
    other = create_fresh_org("business_architect")
    seeded = _seed(org["org_id"])

    ctx, page = _page(browser)
    sign_in(page, live_server, org["emails"]["business_architect"])
    page.goto("%s/architecture/traceability/%d" % (live_server, seeded["element_id"]),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        page.get_by_test_id("trace-from-here").click()
    expect(page.get_by_test_id("trace-initiatives")).to_contain_text(seeded["initiative"])
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("trace-initiatives")).to_contain_text(seeded["initiative"])
    ctx.close()

    ctx, page = _page(browser)
    sign_in(page, live_server, other["emails"]["business_architect"])
    response = page.goto("%s/architecture/goals/%d/trace" % (live_server, seeded["goal_id"]),
                         wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response.status == 404
    ctx.close()


def test_operations_lead_finds_value_streams_at_risk_in_the_sidebar(browser, live_server):
    org = create_fresh_org("operations")
    ctx, page = _page(browser)
    sign_in(page, live_server, org["emails"]["operations"])
    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        page.get_by_role("link", name="Value Streams at Risk").first.click()
    expect(page).to_have_url(re.compile(r"/intelligence/value-streams-at-risk"), timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name="Value streams at risk").first).to_be_visible(timeout=PAGE_TIMEOUT)
    ctx.close()

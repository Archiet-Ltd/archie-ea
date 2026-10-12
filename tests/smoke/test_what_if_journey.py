"""A solution architect compares what stops if two systems are retired, in a real browser.

Open the comparison from the sidebar, pick two systems with the search box, see
each one's effects and what they share; reload and see the same answer; a
second organisation cannot reach the first one's elements.
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
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    app = create_app("testing")
    tag = uuid.uuid4().hex[:6]
    with app.app_context():
        def el(name, type_, layer):
            e = ArchiMateElement(name="%s %s" % (name, tag), type=type_, layer=layer, organization_id=org_id)
            db.session.add(e)
            db.session.flush()
            return e

        billing = el("Billing", "ApplicationComponent", "application")
        crm = el("Crmsys", "ApplicationComponent", "application")
        invoicing = el("Invoicing", "BusinessProcess", "business")
        reporting = el("Reporting", "BusinessProcess", "business")
        for source, target in ((billing, invoicing), (crm, invoicing), (billing, reporting)):
            db.session.add(ArchiMateRelationship(source_id=source.id, target_id=target.id, type="Serving", organization_id=org_id))
        db.session.commit()
        out = {"tag": tag, "billing": billing.name, "crm": crm.name, "billing_id": billing.id}
        db.session.remove()
    return out


def _page(browser):
    ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    ctx.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return ctx, ctx.new_page()


def _pick(page, text, name):
    box = page.get_by_role("combobox")
    box.fill(text)
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_role("option", name=name).click()


def test_solution_architect_compares_two_retirements(browser, live_server):
    org = create_fresh_org("solution_architect")
    other = create_fresh_org("solution_architect")
    seeded = _seed(org["org_id"])

    ctx, page = _page(browser)
    sign_in(page, live_server, org["emails"]["solution_architect"])
    page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_role("link", name="What-if comparison", exact=True).first.click()

    _pick(page, "Billing " + seeded["tag"], seeded["billing"])
    _pick(page, "Crmsys " + seeded["tag"], seeded["crm"])

    for _ in range(2):
        columns = page.get_by_test_id("whatif-scenario")
        expect(columns).to_have_count(2, timeout=PAGE_TIMEOUT)
        expect(columns.nth(0)).to_contain_text("Reporting " + seeded["tag"])
        expect(columns.nth(0).get_by_test_id("whatif-affected")).to_have_text("2")
        expect(columns.nth(1).get_by_test_id("whatif-affected")).to_have_text("1")
        expect(page.get_by_test_id("whatif-shared")).to_contain_text("Invoicing " + seeded["tag"])
        page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    ctx.close()

    ctx, page = _page(browser)
    sign_in(page, live_server, other["emails"]["solution_architect"])
    page.goto(live_server + "/intelligence/what-if?element=%d" % seeded["billing_id"], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("whatif-scenario")).to_contain_text("does not exist in your organisation", timeout=PAGE_TIMEOUT)
    expect(page.locator("body")).not_to_contain_text("Reporting " + seeded["tag"])
    ctx.close()

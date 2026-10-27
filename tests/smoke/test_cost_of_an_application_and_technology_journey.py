"""Journey: an architect finds out what an application and a technology really cost, and the canvas agrees.

A seeded organisation has three applications and two technologies, with costs in pounds and dollars. The architect
opens an application's Cost tab, reads its total and its lines, adds a cost line and sees the total change; opens a
technology it runs on and sees the share that comes from the application, named and linked; opens the business
model canvas and finds the same totals, each line opening the page that holds the figure. A viewer in the same
organisation can read the cost but cannot add to it, and a second organisation sees none of it.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

RATE_DAY = date(2000, 1, 1)
RATE_SOURCE = "cost journey"  # the journey owns the USD to GBP rate on RATE_DAY


def _remove_rate():
    from app import create_app, db
    from app.models.cost_fact import ExchangeRate

    app = create_app("testing")
    with app.app_context():
        ExchangeRate.query.filter_by(from_currency="USD", to_currency="GBP", effective_date=RATE_DAY).delete()
        db.session.commit()
        db.session.remove()


@pytest.fixture
def journey_rate():
    _remove_rate()
    yield
    _remove_rate()


def _seed(org):
    """Three applications (one in dollars), two technologies, Ledger running on both, a viewer and a canvas."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.business_model import BusinessModelCanvas
    from app.models.cost_fact import ExchangeRate
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.services.application_cost_accessor import sync_cost_fact

    app = create_app("testing")
    with app.app_context():
        db.session.get(Organization, org["org_id"]).reporting_currency = "GBP"
        # The rate table is shared by every organisation; the journey's rate is removed when it ends.
        db.session.add(ExchangeRate(from_currency="USD", to_currency="GBP", rate=Decimal("0.8"),
                                    effective_date=RATE_DAY, source=RATE_SOURCE))
        out = {}
        for name in ("Cluster", "Database server"):
            tech = ArchiMateElement(name=name, type="Node", layer="technology", organization_id=org["org_id"])
            db.session.add(tech)
            db.session.flush()
            out[name] = tech.id
        apps = (
            ("Ledger", "GBP", {"total_cost_of_ownership": Decimal("72000"), "license_cost_annual": Decimal("60000"),
                               "infrastructure_cost_monthly": Decimal("1000")}, ("Cluster", "Database server")),
            ("Payments", "USD", {"total_cost_of_ownership": Decimal("10000")}, ()),
            ("Archive", "GBP", {"total_cost_of_ownership": Decimal("5000")}, ()),
        )
        for name, currency, costs, runs_on in apps:
            element = ArchiMateElement(name=name, type="ApplicationComponent", layer="application",
                                       organization_id=org["org_id"])
            db.session.add(element)
            db.session.flush()
            row = ApplicationComponent(name=name, organization_id=org["org_id"], archimate_element_id=element.id,
                                       **costs)
            db.session.add(row)
            db.session.flush()
            for column in costs:
                sync_cost_fact(row, column, currency)
            for tech in runs_on:
                db.session.add(ArchiMateRelationship(type="Serving", source_id=out[tech], target_id=element.id,
                                                     organization_id=org["org_id"]))
            out[name] = row.id
        viewer = User(email="launch.viewer.%s@example.com" % org["suffix"], first_name="Launch", last_name="Viewer",
                      organization_id=org["org_id"], enterprise_role="business_analyst", confirmed=True)
        viewer.role = Role.query.filter_by(name="Viewer").one()
        viewer.password = PASSWORD
        db.session.add(viewer)
        canvas = BusinessModelCanvas(name="How we make money", organization_id=org["org_id"])
        db.session.add(canvas)
        db.session.commit()
        out["viewer"] = viewer.email
        out["canvas"] = canvas.id
        db.session.remove()
    return out


def _total(page):
    return page.get_by_test_id("cost-total").first


def test_the_cost_of_an_application_and_a_technology_agrees_on_every_page(browser, live_server, journey_rate):
    org = create_fresh_org("solution_architect")
    seeded = _seed(org)
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    ledger_cost = live_server + "/applications/%d?tab=cost" % seeded["Ledger"]
    try:
        sign_in(page, live_server, org["emails"]["solution_architect"])

        # 1. The application's total and its lines, each with its source.
        page.goto(ledger_cost, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        panel = page.get_by_test_id("cost-panel").first
        expect(_total(page)).to_have_text("72,000.00 GBP", timeout=PAGE_TIMEOUT)
        lines = panel.get_by_test_id("cost-line")
        expect(lines).to_have_count(2)
        expect(lines.filter(has_text="Licences")).to_contain_text("60,000.00 GBP")
        expect(lines.filter(has_text="Infrastructure")).to_contain_text("12,000.00 GBP")
        expect(lines.first).to_contain_text("Recorded on the application's cost fields")

        # 2. A cost line added in dollars changes the total by its converted amount.
        panel.get_by_test_id("cost-add").click()
        form = page.get_by_test_id("cost-add-form")
        form.locator("select[name=category]").select_option("support")
        form.locator("input[name=amount]").fill("500")
        form.locator("input[name=currency]").fill("USD")
        form.get_by_test_id("cost-add-submit").click()
        page.wait_for_load_state("domcontentloaded")
        expect(_total(page)).to_have_text("72,400.00 GBP", timeout=PAGE_TIMEOUT)
        support = page.get_by_test_id("cost-line").filter(has_text="Support")
        expect(support).to_contain_text("500.00 USD")
        expect(support).to_contain_text("Added by")

        # 3. The technology it runs on carries its share, naming the application, and the link lands on its cost.
        page.goto(live_server + "/architecture/technology/Node/%d?ae=1" % seeded["Cluster"],
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        tech = page.get_by_test_id("technology-cost")
        expect(tech.get_by_test_id("cost-total")).to_have_text("6,000.00 GBP", timeout=PAGE_TIMEOUT)
        share = tech.get_by_test_id("cost-line")
        expect(share).to_have_count(1)
        expect(share).to_contain_text("Infrastructure: share of Ledger")
        expect(share).to_contain_text("split evenly across the 2 technologies")
        share.get_by_test_id("cost-line-source").click()
        page.wait_for_url(re.compile(r".*/applications/%d\?tab=cost$" % seeded["Ledger"]), timeout=PAGE_TIMEOUT)
        expect(_total(page)).to_have_text("72,400.00 GBP", timeout=PAGE_TIMEOUT)

        # 4. The canvas Cost Structure box shows the same totals, and each line opens the page with that figure.
        page.goto(live_server + "/business-model/%d" % seeded["canvas"], wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        box = page.locator("[data-canvas-notes='cost-structure']").first
        expect(page.get_by_test_id("bmc-cost-total")).to_contain_text(
            "Cost Structure total across 3 applications: 85,400.00 GBP a year", timeout=PAGE_TIMEOUT)
        expect(box).to_contain_text("Ledger: 72,400.00 GBP a year")
        expect(box).to_contain_text("Payments: 8,000.00 GBP a year")
        expect(box).to_contain_text("Archive: 5,000.00 GBP a year")
        for name, figure in (("Payments", "8,000.00 GBP"), ("Ledger", "72,400.00 GBP")):
            box.get_by_role("link", name=re.compile("^%s: " % name)).first.click()
            page.wait_for_url(re.compile(r".*/applications/%d\?tab=cost$" % seeded[name]), timeout=PAGE_TIMEOUT)
            expect(_total(page)).to_have_text(figure, timeout=PAGE_TIMEOUT)
            page.go_back(wait_until="domcontentloaded")
    finally:
        context.close()

    # 5. A viewer reads the cost but has no Add cost, and a write is refused.
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, seeded["viewer"])
        page.goto(ledger_cost, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(_total(page)).to_have_text("72,400.00 GBP", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("cost-add")).to_have_count(0)
        status, _ = api(page, "POST", "/applications/%d/costs" % seeded["Ledger"], {"amount": "1"})
        assert status == 403
    finally:
        context.close()

    # 6. A second organisation sees none of it.
    other = create_fresh_org("solution_architect")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, other["emails"]["solution_architect"])
        response = page.goto(ledger_cost, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 404
        expect(page.get_by_text("72,400.00 GBP")).to_have_count(0)
        status, _ = api(page, "POST", "/applications/%d/costs" % seeded["Ledger"], {"amount": "1"})
        assert status in (403, 404)
    finally:
        context.close()

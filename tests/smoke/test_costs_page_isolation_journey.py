"""The Costs page totals one organisation, and its total agrees with the Vendors page.

Two organisations each hold active contracts of distinct amounts. A finance lead of the first
reads only the first's annual total and top vendors on the Costs page, with no name or amount
from the second; the Vendors page states the same annual total for the same organisation; and a
finance lead of the second reads only the second's. The Vendors breadcrumb names the page once.
"""
import os
import re
import uuid
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD, _seed_standard_org
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = os.environ.get("COSTS_WALK_DIR")


@pytest.fixture(scope="module")
def two_costed_orgs(request, ai_protocol_stub, live_server):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.models.vendor.vendor_organization import VendorOrganization
    from app.services.billing_plans import set_contract_plan

    seeded = _seed_standard_org(request, ai_protocol_stub)
    tag = uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        first = seeded["ids"]["org"]
        second = Organization(name=f"Second {tag}", slug=f"second-{tag}")
        db.session.add(second)
        db.session.flush()
        set_contract_plan(second, "enterprise", None)
        other_email = f"smoke.finance.second.{tag}@example.com"
        user = User(email=other_email, first_name="Smoke", last_name="Second", organization_id=second.id,
                    enterprise_role="finance", confirmed=True)
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()

        def contract(org_id, vendor, system_cost, annual_cost, days_left):
            system = ApplicationComponent(name=f"{vendor} system", organization_id=org_id, vendor_name=vendor,
                                          total_cost_of_ownership=system_cost, lifecycle_status="operational")
            db.session.add(system)
            db.session.commit()
            vendor_row = VendorOrganization.query.filter_by(name=vendor).one()
            db.session.add(VendorContract(
                contract_name=f"{vendor} contract", organization_id=org_id, vendor_id=vendor_row.id,
                application_id=system.id, start_date=date.today() - timedelta(days=200),
                end_date=date.today() + timedelta(days=days_left), annual_cost=annual_cost, status="active"))
            db.session.commit()

        # The first organisation's systems cost what their contracts cost, so its two annual
        # totals (systems on Vendors, contracts on Costs) are the same number: 14,000. The second
        # organisation's contracts total 6,000, so on the base the first would read 20,000.
        contract(first, f"Datadog {tag}", 2000, 2000.0, 25)
        contract(first, f"Salesforce {tag}", 12000, 12000.0, 200)
        second_id = second.id
        contract(second_id, f"Globex {tag}", 5000, 5000.0, 40)
        contract(second_id, f"Initech {tag}", 1000, 1000.0, 400)
    return {"seeded": seeded, "tag": tag, "app": app, "first": first, "second": second_id,
            "other_email": other_email}


MONEY = re.compile(r"[£$€]\s?([\d,]+(?:\.\d+)?)\s*([kKmM])?\b")


def _money(text):
    """The first money figure in a text, whichever form the page uses (14,000.00 or 14k)."""
    found = MONEY.search(text)
    assert found, text
    value = float(found.group(1).replace(",", ""))
    return value * {"k": 1e3, "m": 1e6}.get((found.group(2) or "").lower(), 1)


def _text(page):
    return re.sub(r"\s+", " ", page.locator("main, body").first.inner_text())


def _shot(page, name):
    if WALK_DIR:
        os.makedirs(WALK_DIR, exist_ok=True)
        page.screenshot(path=os.path.join(WALK_DIR, name + ".png"), full_page=True)


def _crumbs(page):
    nav = page.locator('nav[aria-label="Breadcrumb"]').first
    return [re.sub(r"\s+", " ", t).strip() for t in nav.locator("li").all_inner_texts()]


def test_finance_reads_only_their_organisations_spend_and_the_vendors_total_agrees(
        browser, live_server, two_costed_orgs):
    tag = two_costed_orgs["tag"]
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, two_costed_orgs["seeded"]["emails"]["finance"])
        assert page.goto(live_server + "/procurement/spend", timeout=PAGE_TIMEOUT).status == 200
        card = page.get_by_test_id("spend-annual-cost")
        expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
        assert _money(card.inner_text()) == 14000.0, card.inner_text()
        rows = page.get_by_test_id("spend-vendor-row")
        expect(rows.filter(has_text="Datadog %s" % tag)).to_contain_text("a year")
        expect(rows.filter(has_text="Salesforce %s" % tag)).to_contain_text("a year")
        assert _money(rows.filter(has_text="Datadog %s" % tag).inner_text().split(tag, 1)[1]) == 2000.0
        assert _money(rows.filter(has_text="Salesforce %s" % tag).inner_text().split(tag, 1)[1]) == 12000.0
        body = _text(page)
        for absent in ("Globex", "Initech", "5,000", "5k", "1,000", "20k", "20,000"):
            assert absent not in body, absent
        _shot(page, "first-costs-1440-light")

        # The Vendors page states the same annual total for the same organisation, and names itself once.
        assert page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT).status == 200
        answer = page.get_by_test_id("vendors-answer")
        expect(answer).to_contain_text("Annual cost across vendors:", timeout=PAGE_TIMEOUT)
        assert _money(answer.inner_text().split("Annual cost across vendors:")[1]) == 14000.0
        assert _crumbs(page) == ["Home", "Spend", "Vendors"], _crumbs(page)
        _shot(page, "first-vendors-1440-light")
    finally:
        page.close()


def test_the_second_organisations_finance_reads_only_theirs(browser, live_server, two_costed_orgs):
    tag = two_costed_orgs["tag"]
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, two_costed_orgs["other_email"])
        assert page.goto(live_server + "/procurement/spend", timeout=PAGE_TIMEOUT).status == 200
        card = page.get_by_test_id("spend-annual-cost")
        expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
        assert _money(card.inner_text()) == 6000.0, card.inner_text()
        rows = page.get_by_test_id("spend-vendor-row")
        expect(rows.filter(has_text="Globex %s" % tag)).to_contain_text("a year")
        expect(rows.filter(has_text="Initech %s" % tag)).to_contain_text("a year")
        body = _text(page)
        for absent in ("Datadog", "Salesforce", "12k", "14k", "2k a year"):
            assert absent not in body, absent
        _shot(page, "second-costs-1440-light")
    finally:
        page.close()


def test_compliance_sees_the_vendors_page_named_once(browser, live_server, two_costed_orgs):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, two_costed_orgs["seeded"]["emails"]["compliance"])
        assert page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT).status == 200
        expect(page.get_by_test_id("vendors-answer")).to_be_visible(timeout=PAGE_TIMEOUT)
        assert _crumbs(page) == ["Home", "Spend", "Vendors"], _crumbs(page)
        _shot(page, "compliance-vendors-crumbs-1440-light")
    finally:
        page.close()


def test_the_costs_page_fits_a_phone(browser, live_server, two_costed_orgs):
    page = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme="dark")
    try:
        _login(page, live_server, two_costed_orgs["seeded"]["emails"]["finance"])
        assert page.goto(live_server + "/procurement/spend", timeout=PAGE_TIMEOUT).status == 200
        card = page.get_by_test_id("spend-annual-cost")
        expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
        assert _money(card.inner_text()) == 14000.0
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        _shot(page, "first-costs-390-dark")
    finally:
        page.close()

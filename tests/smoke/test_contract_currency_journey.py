"""A procurement lead records a contract in euros and an Administrator reads the figures it leaves alone.

The organisation reports in pounds and has a vendor with a £6,000 contract. The procurement lead opens the new
contract form, finds pounds chosen from a list, picks euros, states €1,200 a year and saves. The contract page
shows the euros under their own symbol, the Vendors page keeps its total in pounds and lists the euros apart,
and the Overview monthly figure stays a twelfth of the pounds. A second organisation with a system cost and no
contract reads £0 a month on its Overview. A third organisation's dollars never appear.
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

WALK_DIR = os.environ.get("CONTRACT_CURRENCY_WALK_DIR")


@pytest.fixture(scope="module")
def estate(request, ai_protocol_stub, live_server):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.organization import Organization
    from app.models.user import User
    from app.models.vendor.vendor_organization import VendorOrganization

    seeded = _seed_standard_org(request, ai_protocol_stub)
    tag = uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]
        today = date.today()

        def user(org, kind, role):
            u = User(email=f"cur-{kind}-{tag}@example.com", first_name="Cur", last_name=kind.title(),
                     organization_id=org, enterprise_role=role, confirmed=True)
            u.password = PASSWORD
            db.session.add(u)
            db.session.commit()
            if kind.startswith("admin"):
                u.grant_org_admin()
                db.session.commit()
            return u.email

        emails = {"buyer": user(org_id, "buyer", "procurement"), "admin": user(org_id, "admin", "enterprise_architect")}
        quiet = Organization(name=f"Quiet {tag}", slug=f"quiet-{tag}")
        rival = Organization(name=f"Rival {tag}", slug=f"rival-{tag}")
        db.session.add_all([quiet, rival])
        db.session.flush()
        emails["quiet"] = user(quiet.id, "admin-quiet", "enterprise_architect")
        vendor = VendorOrganization(name=f"Euro {tag}")
        db.session.add(vendor)
        db.session.flush()
        quiet_vendor = VendorOrganization(name=f"Quiet vendor {tag}")
        db.session.add(quiet_vendor)
        db.session.flush()
        db.session.add(ApplicationComponent(name=f"Quiet system {tag}", organization_id=quiet.id,
                                            lifecycle_status="operational", total_cost_of_ownership=24000,
                                            vendor_organization_id=quiet_vendor.id))
        db.session.add(VendorContract(contract_name=f"Pounds {tag}", organization_id=org_id, vendor_id=vendor.id,
                                      annual_cost=6000.0, currency="GBP", status="active",
                                      start_date=today - timedelta(days=30), end_date=today + timedelta(days=300)))
        db.session.add(VendorContract(contract_name=f"Dollars {tag}", organization_id=rival.id, vendor_id=vendor.id,
                                      annual_cost=777000.0, currency="USD", status="active",
                                      start_date=today - timedelta(days=30), end_date=today + timedelta(days=300)))
        db.session.commit()
    return {"tag": tag, **emails}


def _text(page):
    return re.sub(r"\s+", " ", page.locator("main, body").first.inner_text())


def _shot(page, name):
    if WALK_DIR:
        os.makedirs(WALK_DIR, exist_ok=True)
        page.screenshot(path=os.path.join(WALK_DIR, name + ".png"), full_page=True)


def test_a_euro_contract_is_shown_in_euros_and_left_out_of_the_pound_figures(browser, live_server, estate):
    tag = estate["tag"]
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, estate["buyer"])
        assert page.goto(live_server + "/procurement/contracts/new", timeout=PAGE_TIMEOUT).status == 200
        currency = page.locator("select#currency")
        expect(currency).to_have_value("GBP", timeout=PAGE_TIMEOUT)
        _shot(page, "form-1440-light")
        page.fill("#contract_name", f"Euro deal {tag}")
        page.select_option("#vendor_id", label=f"Euro {tag}")
        page.fill("#annual_cost", "1200")
        currency.select_option("EUR")
        page.locator("main form button[type=submit], main form [type=submit]").first.click()
        page.wait_for_url(re.compile(r"/procurement/contracts/\d+$"), timeout=PAGE_TIMEOUT)
        detail = _text(page)
        assert "€1,200" in detail and "£1,200" not in detail and "$1,200" not in detail
        _shot(page, "contract-1440-light")
        page.reload()
        assert "€1,200" in _text(page)
    finally:
        page.close()

    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, estate["admin"])
        assert page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT).status == 200
        vendors = _text(page)
        assert "£6,000" in vendors
        assert re.search(r"Not added: 1 active contract states €1,200(?:\.00)? a year in EUR", vendors), vendors
        assert "777" not in vendors
        _shot(page, "vendors-1440-light")
        page.get_by_test_id("vendor-link").filter(has_text=f"Euro {tag}").click()
        page.wait_for_url(re.compile(r"/vendors?/"), timeout=PAGE_TIMEOUT)
        vendor = _text(page)
        assert f"Euro deal {tag}" in vendor and "Not added: stated in EUR" in vendor
        assert f"Pounds {tag}" in vendor and re.search(r"£6,000(?:\.00)? a year", vendor)
        _shot(page, "vendor-1440-light")

        assert page.goto(live_server + "/dashboard/overview", timeout=PAGE_TIMEOUT).status == 200
        assert page.get_by_test_id("monthly-cost-value").inner_text().strip() == "£500"
    finally:
        page.close()


def test_an_organisation_with_a_system_cost_and_no_contract_reads_zero_a_month(browser, live_server, estate):
    page = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme="dark")
    try:
        _login(page, live_server, estate["quiet"])
        assert page.goto(live_server + "/dashboard/overview", timeout=PAGE_TIMEOUT).status == 200
        card = page.get_by_test_id("monthly-cost-value")
        expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
        assert card.inner_text().strip() == "£0"
        assert "Active contracts, per month" in _text(page)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        _shot(page, "overview-390-dark")
    finally:
        page.close()

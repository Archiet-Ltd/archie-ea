"""An Administrator reads what the estate costs, and every vendor and cost figure agrees across pages.

The Administrator holds the Enterprise Architect persona (not the Platform Administrator persona), in an
organisation with systems, a vendor, active contracts stating annual cost and a decommissioned system, next
to a second organisation whose contracts name the same vendor. They open Vendors and read each vendor's annual
cost, open the Overview and the Applications list and read the same run cost, and open the scenarios page
and read the same coverage sentence as Vendors. Following a vendor link lands on that vendor's page.
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

WALK_DIR = os.environ.get("ESTATE_COST_WALK_DIR")


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
        admin = User(email=f"estate-admin-{tag}@example.com", first_name="Estate", last_name="Admin",
                     organization_id=org_id, enterprise_role="enterprise_architect", confirmed=True)
        admin.password = PASSWORD
        db.session.add(admin)
        db.session.commit()
        admin.grant_org_admin()
        db.session.commit()

        vendor = VendorOrganization(name=f"Meridian {tag}")
        db.session.add(vendor)
        db.session.flush()
        vendor_id = vendor.id

        def system(name, org=org_id, with_vendor=True, status="operational"):
            row = ApplicationComponent(name=name, organization_id=org, lifecycle_status=status,
                                       vendor_organization_id=vendor.id if with_vendor else None)
            db.session.add(row)
            db.session.flush()
            return row

        billing = system(f"Billing {tag}")
        ledger = system(f"Ledger {tag}")
        system(f"Intranet {tag}", with_vendor=False)
        system(f"Old Fax {tag}", with_vendor=False, status="5. Decommissioned")
        for app_row, cost in ((billing, 30000.0), (ledger, 18000.0)):
            db.session.add(VendorContract(
                contract_name=f"{app_row.name} licence", organization_id=org_id, vendor_id=vendor.id,
                application_id=app_row.id, annual_cost=cost, status="active",
                start_date=date.today() - timedelta(days=100), end_date=date.today() + timedelta(days=200)))
        other = Organization(name=f"Rival {tag}", slug=f"rival-{tag}")
        db.session.add(other)
        db.session.flush()
        theirs = system(f"Rival CRM {tag}", org=other.id)
        db.session.add(VendorContract(
            contract_name=f"Rival {tag}", organization_id=other.id, vendor_id=vendor.id,
            application_id=theirs.id, annual_cost=777000.0, status="active",
            start_date=date.today() - timedelta(days=10)))
        db.session.commit()
    return {"email": f"estate-admin-{tag}@example.com", "tag": tag, "vendor_id": vendor_id}


def _text(page):
    return re.sub(r"\s+", " ", page.locator("main, body").first.inner_text())


def _shot(page, name):
    if WALK_DIR:
        os.makedirs(WALK_DIR, exist_ok=True)
        page.screenshot(path=os.path.join(WALK_DIR, name + ".png"), full_page=True)


def test_administrator_reads_vendor_cost_and_every_page_agrees(browser, live_server, estate):
    tag = estate["tag"]
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, estate["email"])

        # Vendors: the Administrator reads the cost, never "Not shown for your role".
        assert page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT).status == 200
        row = page.get_by_test_id("vendor-row").filter(has_text=f"Meridian {tag}")
        expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
        body = _text(page)
        assert "Not shown for your role" not in body
        assert "777" not in body
        assert "48,000" in row.inner_text().replace(".00", "")
        answer = page.get_by_test_id("vendors-answer").inner_text()
        coverage = re.search(r"(\d+ vendors? suppl(?:y|ies) \d+ of your \d+ systems?)", re.sub(r"\s+", " ", answer))
        assert coverage, answer
        assert "Decommissioned systems are not counted (1)" in re.sub(r"\s+", " ", answer)
        _shot(page, "vendors-1440-light")

        # The scenarios page states the same coverage and has the same table, with no "none" beside it.
        assert page.goto(live_server + "/vendors/technical/scenarios", timeout=PAGE_TIMEOUT).status == 200
        scenarios = re.sub(r"\s+", " ", page.get_by_test_id("vendors-answer").inner_text())
        assert coverage.group(1) in scenarios
        assert "None of your" not in _text(page)
        expect(page.get_by_test_id("vendor-row").filter(has_text=f"Meridian {tag}")).to_be_visible()

        # The run cost is the same on the Overview and the Applications list.
        assert page.goto(live_server + "/dashboard/overview", timeout=PAGE_TIMEOUT).status == 200
        overview = _text(page)
        assert re.search(r"48(,000|k|\.0k)", overview), overview[:800]
        assert "appears once a contract states" not in overview
        _shot(page, "overview-1440-light")
        assert page.goto(live_server + "/applications/", timeout=PAGE_TIMEOUT).status == 200
        line = page.get_by_test_id("run-cost-total")
        expect(line).to_be_visible(timeout=PAGE_TIMEOUT)
        assert "48,000" in line.inner_text().replace(".00", "")
        assert "active contracts" in page.get_by_test_id("run-cost-line").inner_text()
        _shot(page, "applications-1440-light")

        # A vendor link lands on that vendor's page with its content.
        assert page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT).status == 200
        page.get_by_test_id("vendor-link").filter(has_text=f"Meridian {tag}").click()
        page.wait_for_url(re.compile(r"/vendors?/"), timeout=PAGE_TIMEOUT)
        assert f"Meridian {tag}" in _text(page)
    finally:
        page.close()


def test_the_estate_views_fit_a_phone(browser, live_server, estate):
    page = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme="dark")
    try:
        _login(page, live_server, estate["email"])
        for path, name in (("/applications/vendors", "vendors"), ("/applications/", "applications"),
                           ("/dashboard/overview", "overview")):
            assert page.goto(live_server + path, timeout=PAGE_TIMEOUT).status == 200
            page.wait_for_timeout(500)
            assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"), path
            _shot(page, f"{name}-390-dark")
    finally:
        page.close()

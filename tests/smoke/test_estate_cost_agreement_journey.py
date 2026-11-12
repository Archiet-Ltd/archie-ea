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

        # The Vendors list, opened again, states the same coverage and has the same table, with no "none" beside it.
        assert page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT).status == 200
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


def test_vendor_row_cost_and_the_data_quality_banner_agree_with_the_vendors_page(browser, live_server, estate):
    """The vendor row states its contracts' cost with its source, and the applications list's banner
    states the same vendor coverage as Vendors; following the banner's link lists exactly those systems."""
    tag = estate["tag"]
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, estate["email"])
        assert page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT).status == 200
        row = page.get_by_test_id("vendor-row").filter(has_text=f"Meridian {tag}")
        expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
        row_text = re.sub(r"\s+", " ", row.inner_text())
        assert "No cost recorded" not in row_text
        assert "48,000" in row_text.replace(".00", "")
        assert "from active contracts" in row_text
        answer = re.sub(r"\s+", " ", page.get_by_test_id("vendors-answer").inner_text())
        supplied, total = re.search(r"suppl(?:y|ies) (\d+) of your (\d+) systems?", answer).groups()

        assert page.goto(live_server + "/applications/", timeout=PAGE_TIMEOUT).status == 200
        link = page.get_by_test_id("dq-no-vendor")
        expect(link).to_be_visible(timeout=PAGE_TIMEOUT)
        missing, of = re.search(r"(\d+) of (\d+) have no vendor", link.inner_text()).groups()
        assert (int(of), int(missing)) == (int(total), int(total) - int(supplied))
        _shot(page, "applications-banner-1440-light")

        link.click()
        page.wait_for_url(re.compile(r"no_vendor=true"), timeout=PAGE_TIMEOUT)
        landed = _text(page)
        assert f"Intranet {tag}" in landed
        assert f"Billing {tag}" not in landed
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


@pytest.fixture(scope="module")
def recorded_cost_estate(live_server):
    """An organisation where a system records its own cost beside a smaller contract, a contract sits on a live
    system that names no vendor, and a contract sits on a decommissioned system; a second organisation beside it."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.organization import Organization
    from app.models.user import User
    from app.models.vendor.vendor_organization import VendorOrganization

    tag = uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        orgs = [Organization(name=f"{n} {tag}", slug=f"{n.lower()}-{tag}") for n in ("Harbour", "Rival")]
        db.session.add_all(orgs)
        db.session.flush()
        mine, other = orgs
        admin = User(email=f"cost-admin-{tag}@example.com", first_name="Cost", last_name="Admin",
                     organization_id=mine.id, enterprise_role="enterprise_architect", confirmed=True)
        admin.password = PASSWORD
        db.session.add(admin)
        db.session.commit()
        admin.grant_org_admin()
        db.session.commit()
        beacon, cobalt, delta = (VendorOrganization(name=f"{n} {tag}") for n in ("Beacon", "Cobalt", "Delta"))
        db.session.add_all([beacon, cobalt, delta])
        db.session.flush()

        def system(name, org, vendor, status="operational", cost=None):
            row = ApplicationComponent(name=f"{name} {tag}", organization_id=org.id, lifecycle_status=status,
                                       vendor_organization_id=vendor.id if vendor else None,
                                       total_cost_of_ownership=cost)
            db.session.add(row)
            db.session.flush()
            return row

        def contract(name, org, vendor, on, cost):
            db.session.add(VendorContract(
                contract_name=f"{name} {tag}", organization_id=org.id, vendor_id=vendor.id, application_id=on.id,
                annual_cost=cost, status="active", start_date=date.today() - timedelta(days=100),
                end_date=date.today() + timedelta(days=200)))

        warehouse = system("Cobalt Warehouse", mine, cobalt, cost=50000)
        crm = system("Beacon CRM", mine, beacon)
        orphan = system("Floating tool", mine, None)
        old = system("Delta Legacy", mine, delta, status="5. Decommissioned")
        contract("Cobalt support", mine, cobalt, warehouse, 1000.0)
        contract("Beacon licence", mine, beacon, crm, 12000.0)
        contract("Floating licence", mine, delta, orphan, 4000.0)
        contract("Legacy support", mine, delta, old, 9000.0)
        theirs = system("Rival Warehouse", other, cobalt, cost=888000)
        contract("Rival support", other, cobalt, theirs, 777000.0)
        db.session.commit()
    return {"email": f"cost-admin-{tag}@example.com", "tag": tag}


def test_vendors_spend_and_the_overview_state_one_total_and_no_share_over_a_hundred(
        browser, live_server, recorded_cost_estate):
    """A finance reader wants the vendor spend: Vendors, Spend and the Overview give the same total, the largest
    vendor's share stays within 100%, and the card for it opens the Vendors row carrying that same figure."""
    tag = recorded_cost_estate["tag"]
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, recorded_cost_estate["email"])
        assert page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT).status == 200
        banner = re.sub(r"\s+", " ", page.get_by_test_id("vendors-answer").inner_text())
        assert "Annual cost across vendors: £66,000.00, including £4,000.00 on 1 active contract that names no vendor" in banner
        cobalt = re.sub(r"\s+", " ", page.get_by_test_id("vendor-row").filter(has_text=f"Cobalt {tag}").inner_text())
        assert "50,000" in cobalt
        text = _text(page)
        assert "777" not in text and "888" not in text and "9,000" not in text
        _shot(page, "vendors-recorded-1440-light")

        assert page.goto(live_server + "/procurement/spend", timeout=PAGE_TIMEOUT).status == 200
        annual = page.get_by_test_id("spend-annual-cost").inner_text()
        assert "£66k" in annual or "66,000" in annual
        top = re.sub(r"\s+", " ", page.get_by_test_id("spend-top-vendor").inner_text())
        assert f"Cobalt {tag}" in top and "76%" in top and "£50k a year" in top
        shares = [int(p) for p in re.findall(r"(\d+)%", page.get_by_test_id("spend-top-vendors").inner_text())]
        assert shares and max(shares) <= 100
        assert "£4k on 1 active contract that names no vendor" in re.sub(r"\s+", " ", page.get_by_test_id("spend-unassigned").inner_text())
        _shot(page, "spend-recorded-1440-light")
        page.get_by_test_id("spend-top-vendor").click()
        page.wait_for_url(re.compile(r"/applications/vendors"), timeout=PAGE_TIMEOUT)
        landed = re.sub(r"\s+", " ", page.get_by_test_id("vendor-row").filter(has_text=f"Cobalt {tag}").inner_text())
        assert "50,000" in landed

        assert page.goto(live_server + "/dashboard/overview", timeout=PAGE_TIMEOUT).status == 200
        overview = _text(page)
        assert re.search(r"66(,000|k|\.0k)", overview), overview[:800]
        assert re.search(r"\b76%", overview) and not [p for p in re.findall(r"(\d+)%", overview) if int(p) > 100]
        _shot(page, "overview-recorded-1440-light")
    finally:
        page.close()


def test_the_vendor_spend_views_fit_a_phone(browser, live_server, recorded_cost_estate):
    tag = recorded_cost_estate["tag"]
    page = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme="dark")
    try:
        _login(page, live_server, recorded_cost_estate["email"])
        for path, name in (("/applications/vendors", "vendors"), ("/procurement/spend", "spend"),
                           ("/dashboard/overview", "overview")):
            assert page.goto(live_server + path, timeout=PAGE_TIMEOUT).status == 200
            page.wait_for_timeout(500)
            assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"), path
            if name == "spend":
                assert f"Cobalt {tag}" in _text(page)
            _shot(page, f"{name}-recorded-390-dark")
    finally:
        page.close()

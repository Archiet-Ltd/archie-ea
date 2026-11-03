"""An Administrator reads what a vendor costs when one contract has run out and one system records its own cost.

The organisation has a vendor whose live system records £20,000 and carries a £500 contract, a £6,000 contract
with time to run, and a £3,000 contract still marked active whose end date has passed. A second organisation
has the same shape with larger figures. Vendors, the vendor page, Spend and the Overview must state £26,000,
the lapsed contract must be explained rather than counted, and the Overview monthly figure must be a twelfth of it.
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

WALK_DIR = os.environ.get("EXPIRED_COST_WALK_DIR")


@pytest.fixture(scope="module")
def lapsed(request, ai_protocol_stub, live_server):
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
        admin = User(email=f"lapse-admin-{tag}@example.com", first_name="Lapse", last_name="Admin",
                     organization_id=org_id, enterprise_role="enterprise_architect", confirmed=True)
        admin.password = PASSWORD
        db.session.add(admin)
        db.session.commit()
        admin.grant_org_admin()
        db.session.commit()
        other = Organization(name=f"Rival {tag}", slug=f"rival-{tag}")
        db.session.add(other)
        db.session.flush()
        today = date.today()
        ids = {}
        for org, scale in ((org_id, 1), (other.id, 40)):
            vendor = VendorOrganization(name=f"Lapse {tag}" if scale == 1 else f"Rival vendor {tag}")
            db.session.add(vendor)
            db.session.flush()
            system = ApplicationComponent(name=f"Ledger {scale} {tag}", organization_id=org,
                                          lifecycle_status="operational", vendor_organization_id=vendor.id,
                                          total_cost_of_ownership=20000 * scale)
            db.session.add(system)
            db.session.flush()
            for name, on, cost, end in (("on-system", system.id, 500.0, today + timedelta(days=300)),
                                        ("lapsed", None, 3000.0, today - timedelta(days=10)),
                                        ("running", None, 6000.0, today + timedelta(days=200))):
                db.session.add(VendorContract(
                    contract_name=f"{name} {scale} {tag}", organization_id=org, vendor_id=vendor.id,
                    application_id=on, annual_cost=cost * scale, status="active",
                    start_date=today - timedelta(days=400), end_date=end))
            if scale == 1:
                ids["vendor"] = vendor.id
        db.session.commit()
    return {"email": f"lapse-admin-{tag}@example.com", "tag": tag, **ids}


def _text(page):
    return re.sub(r"\s+", " ", page.locator("main, body").first.inner_text())


def _shot(page, name):
    if WALK_DIR:
        os.makedirs(WALK_DIR, exist_ok=True)
        page.screenshot(path=os.path.join(WALK_DIR, name + ".png"), full_page=True)


def test_a_lapsed_contract_and_a_system_cost_give_one_answer_on_every_page(browser, live_server, lapsed):
    tag = lapsed["tag"]
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, lapsed["email"])
        assert page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT).status == 200
        row = page.get_by_test_id("vendor-row").filter(has_text=f"Lapse {tag}")
        expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
        assert "26,000" in row.inner_text().replace(".00", "")
        assert "777" not in _text(page) and "Rival vendor" not in _text(page)
        _shot(page, "vendors-1440-light")

        # following the vendor link lands on its page, which explains the lapsed and the system-priced contracts
        page.get_by_test_id("vendor-link").filter(has_text=f"Lapse {tag}").click()
        page.wait_for_url(re.compile(r"/vendors?/"), timeout=PAGE_TIMEOUT)
        vendor = _text(page)
        assert f"lapsed 1 {tag}" in vendor and "Not counted: the contract ended on " in vendor
        assert "Not added: the system it sits on records its own cost" in vendor
        assert f"running 1 {tag}" in vendor
        _shot(page, "vendor-1440-light")

        assert page.goto(live_server + "/procurement/spend", timeout=PAGE_TIMEOUT).status == 200
        spend = _text(page)
        assert "£26k" in spend
        assert "costs recorded on systems plus the annual cost of active contracts" in spend
        assert "Contract cost by type" in spend
        # the standard organisation carries one contract that states no cost; the lapsed one is not the fourth
        assert re.search(r"(Active contracts 3|3 Active contracts)\b", spend), spend
        assert not [p for p in re.findall(r"(\d+)%", spend) if int(p) > 100]
        _shot(page, "spend-1440-light")

        assert page.goto(live_server + "/dashboard/overview", timeout=PAGE_TIMEOUT).status == 200
        overview = _text(page)
        assert "£26k" in overview or "£26.0k" in overview
        monthly = page.get_by_test_id("monthly-cost-value").inner_text()
        assert re.fullmatch(r"£2\.2k|£2,167", monthly.strip()), monthly
        assert "than last month" not in overview
        assert "1 active contract" in overview
        _shot(page, "overview-1440-light")
    finally:
        page.close()


@pytest.mark.skipif(not WALK_DIR, reason="screenshots are only taken for a walk")
def test_the_narrow_dark_screens_hold_the_same_figures(browser, live_server, lapsed):
    page = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme="dark")
    try:
        _login(page, live_server, lapsed["email"])
        for path, name, figure in (("/applications/vendors", "vendors", "26,000"),
                                   ("/procurement/spend", "spend", "£26k"),
                                   ("/dashboard/overview", "overview", "26")):
            assert page.goto(live_server + path, timeout=PAGE_TIMEOUT).status == 200
            assert figure in _text(page)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            _shot(page, name + "-390-dark")
    finally:
        page.close()

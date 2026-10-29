"""A procurement lead opens Vendors, follows a vendor to its systems, and sets a missing vendor.

The organisation is seeded with linked data (systems naming vendors, a contract
near renewal, systems naming none) next to a second organisation that names one
of the same vendors, and the journey asserts what that person needs: the vendors
their systems name, each with its systems and cost, the next renewal first, and
a system set on the Mapping tab moving under its vendor after a reload.
"""
import re
import uuid
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, _seed_standard_org
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def vendor_world(request, ai_protocol_stub, live_server):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.organization import Organization
    from app.models.vendor.vendor_organization import VendorOrganization

    seeded = _seed_standard_org(request, ai_protocol_stub)
    tag = uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]
        existing = db.session.execute(db.text(
            "select count(*) from application_components where organization_id = :o"), {"o": org_id}).scalar()

        def system(name, vendor=None, cost=None, org=org_id):
            row = ApplicationComponent(name=name, organization_id=org, vendor_name=vendor,
                                       total_cost_of_ownership=cost, lifecycle_status="operational")
            db.session.add(row)
            db.session.commit()
            return row.id

        ids = {
            "sf1": system(f"Sales Cloud {tag}", f"Salesforce {tag}", 12000),
            "sf2": system(f"Service Cloud {tag}", f"Salesforce {tag}", 3000),
            "okta": system(f"Workforce Identity {tag}", f"Okta {tag}"),
            "oracle": system(f"Ledger {tag}", f"Oracle {tag}", 5000),
            "dd": system(f"Telemetry {tag}", f"Datadog {tag}", 2000),
            "loose1": system(f"Legacy Billing {tag}"),
            "loose2": system(f"Legacy Rota {tag}"),
        }
        datadog = VendorOrganization.query.filter_by(name=f"Datadog {tag}").one()
        db.session.add(VendorContract(
            contract_name=f"Datadog monitoring {tag}", organization_id=org_id, vendor_id=datadog.id,
            application_id=ids["dd"], start_date=date.today() - timedelta(days=330),
            end_date=date.today() + timedelta(days=25), annual_cost=2000.0, status="active"))
        other = Organization(name=f"Other {tag}", slug=f"other-{tag}")
        db.session.add(other)
        db.session.commit()
        system(f"Their CRM {tag}", f"Okta {tag}", 999, org=other.id)
        system(f"Their Secret {tag}", f"OnlyTheirs {tag}", org=other.id)
    return {"seeded": seeded, "tag": tag, "ids": ids, "existing": existing, "app": app, "org_id": org_id}


def _text(page):
    return re.sub(r"\s+", " ", page.locator("main, body").first.inner_text())


def test_procurement_lead_reads_vendors_then_sets_a_missing_vendor(browser, live_server, vendor_world):
    from app import db
    from app.models.user import User

    tag, email = vendor_world["tag"], vendor_world["seeded"]["emails"]["procurement"]
    with vendor_world["app"].app_context():
        assert User.query.filter_by(email=email).one().enterprise_role == "procurement"
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, email)
        assert page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT).status == 200

        # The answer first: 4 vendors supply 5 of this organisation's systems.
        total = vendor_world["existing"] + 7
        answer = page.get_by_test_id("vendors-answer")
        expect(answer).to_contain_text("4 vendors supply 5 of your %d systems." % total, timeout=PAGE_TIMEOUT)
        expect(answer).to_contain_text("1 renews within 90 days.")
        unmapped = total - 5
        expect(answer).to_contain_text("%d of your systems have no vendor recorded." % unmapped)

        # The table lists exactly the vendors the systems and contract name, the renewal first.
        rows = page.get_by_test_id("vendor-row")
        expect(rows.first).to_contain_text("Datadog %s" % tag, timeout=PAGE_TIMEOUT)
        expect(rows).to_have_count(4)
        assert page.get_by_text("OnlyTheirs %s" % tag).count() == 0
        salesforce = rows.filter(has_text="Salesforce %s" % tag)
        expect(salesforce).to_contain_text("2 systems")
        expect(salesforce).to_contain_text("15,000")
        expect(rows.first).to_contain_text("Due within 90 days")
        expect(rows.filter(has_text="Okta %s" % tag)).to_contain_text("No cost recorded")

        # A vendor opens and leads to its systems, each of which opens on a page with content.
        salesforce.get_by_role("link", name="2 systems").click()
        expect(page).to_have_url(re.compile(r"/applications/vendors/\d+"), timeout=PAGE_TIMEOUT)
        systems = page.locator("#vendor-systems")
        expect(systems).to_contain_text("2 of your systems")
        for name in ("Sales Cloud %s" % tag, "Service Cloud %s" % tag):
            link = systems.get_by_role("link", name=name)
            expect(link).to_be_visible()
        assert "Their CRM" not in systems.inner_text()
        systems.get_by_role("link", name="Sales Cloud %s" % tag).click()
        expect(page.get_by_role("heading", name="Sales Cloud %s" % tag).first).to_be_visible(timeout=PAGE_TIMEOUT)

        # The old mapping address lands on the Mapping tab, with one plain answer and the list.
        page.goto(live_server + "/vendor-archimate-analysis", timeout=PAGE_TIMEOUT)
        assert "tab=mapping" in page.url
        mapping = page.get_by_test_id("mapping-answer")
        expect(mapping).to_contain_text("%d of your systems have no vendor recorded." % unmapped)
        expect(page.get_by_test_id("mapping-list")).to_contain_text("Legacy Billing %s" % tag)

        # Set the vendor on one system; it leaves the list and appears under that vendor, after a reload.
        box = page.get_by_role("combobox", name="Vendor for Legacy Billing %s" % tag)
        box.fill("Okta")
        page.get_by_role("option", name="Okta %s" % tag).click()
        expect(page).to_have_url(re.compile(r"saved=1"), timeout=PAGE_TIMEOUT)
        page.reload(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("mapping-answer")).to_contain_text(
            "%d of your systems have no vendor recorded." % (unmapped - 1))
        assert page.get_by_role("combobox", name="Vendor for Legacy Billing %s" % tag).count() == 0
        page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT)
        okta = page.get_by_test_id("vendor-row").filter(has_text="Okta %s" % tag)
        expect(okta).to_contain_text("2 systems")
        okta.get_by_role("link", name="2 systems").click()
        expect(page.locator("#vendor-systems")).to_contain_text("Legacy Billing %s" % tag)
        expect(page.locator("#vendor-systems")).to_contain_text("Workforce Identity %s" % tag)
    finally:
        page.close()


def test_vendors_page_fits_a_phone(browser, live_server, vendor_world):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    try:
        _login(page, live_server, vendor_world["seeded"]["emails"]["procurement"])
        page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("vendors-answer")).to_be_visible(timeout=PAGE_TIMEOUT)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), \
            "the page scrolls sideways at 390 wide"
    finally:
        page.close()

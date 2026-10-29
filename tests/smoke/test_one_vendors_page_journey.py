"""A CTO finds who their vendors are and narrows the list -- there is one Vendors page.

T-vendor-list-01 retires the legacy vendor list (app/templates/application_mgmt/vendor_list.html,
served at /dashboard/applications/vendors) in favour of the canonical page under Spend
(unified_applications.vendors). The legacy page answered "how big is the shared catalogue" with a
count that was the same for every organisation; the canonical page answers "who supplies my
systems" from this organisation's own vendor register. This file is the eight walk lines of
launch-cut/PM-vendor-list-retirement-decision.md, in a seeded two-organisation fixture, red before
the build and green after it.
"""
import re
import uuid

import pytest
import requests
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, _seed_standard_org
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def two_org_vendor_world(request, ai_protocol_stub, live_server):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.organization import Organization
    from app.models.vendor.vendor_organization import VendorOrganization

    seeded = _seed_standard_org(request, ai_protocol_stub)
    tag = uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]

        def system(name, vendor=None, cost=None, org=org_id):
            row = ApplicationComponent(name=name, organization_id=org, vendor_name=vendor,
                                       total_cost_of_ownership=cost, lifecycle_status="operational")
            db.session.add(row)
            db.session.commit()
            return row.id

        # Org A (the seeded organisation): three vendors, two vendor types and
        # two contract statuses, so the facets have something real to narrow.
        system(f"Sales Cloud {tag}", f"Salesforce {tag}", 12000)
        system(f"Service Cloud {tag}", f"Salesforce {tag}", 3000)
        system(f"Workforce Identity {tag}", f"Okta {tag}")
        system(f"Ledger {tag}", f"Oracle {tag}", 5000)

        salesforce = VendorOrganization.query.filter_by(name=f"Salesforce {tag}").one()
        salesforce.vendor_type = "saas_platform"
        salesforce.contract_status = "contracted"
        okta = VendorOrganization.query.filter_by(name=f"Okta {tag}").one()
        okta.vendor_type = "cloud_provider"
        okta.contract_status = "deployed"
        oracle = VendorOrganization.query.filter_by(name=f"Oracle {tag}").one()
        oracle.vendor_type = "software_vendor"
        oracle.contract_status = "contracted"
        db.session.commit()
        vendor_ids = {"salesforce": salesforce.id, "okta": okta.id, "oracle": oracle.id}

        # Org B: a second organisation naming one of the same vendor names
        # (Okta) plus a vendor of its own, so a cross-org count disagreement
        # would be caught by this fixture, not hidden by it.
        other = Organization(name="Other %s" % tag, slug=f"other-{tag}")
        db.session.add(other)
        db.session.commit()
        other_id = other.id
        system(f"Their CRM {tag}", f"Okta {tag}", 999, org=other_id)
        system(f"Their Payroll {tag}", f"Workday {tag}", 4000, org=other_id)
        workday = VendorOrganization.query.filter_by(name=f"Workday {tag}").one()
        workday.vendor_type = "saas_platform"
        workday.contract_status = "contracted"
        db.session.commit()

    return {"seeded": seeded, "tag": tag, "app": app, "org_id": org_id, "other_id": other_id,
            "vendor_ids": vendor_ids}


def _vendors_url(live_server):
    return live_server + "/applications/vendors"


def test_tenant_administrator_lands_on_the_canonical_page_with_only_its_own_vendors(
        browser, live_server, two_org_vendor_world):
    """Walk lines 1, 2, 3 and 7: the legacy address redirects, counts agree and stay per-organisation."""
    world = two_org_vendor_world
    tag = world["tag"]
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, world["seeded"]["emails"]["procurement"])

        # Line 1: the old address lands on the canonical page, not the legacy list.
        response = page.goto(live_server + "/dashboard/applications/vendors",
                              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 200
        expect(page).to_have_url(re.compile(r"/applications/vendors/?$"), timeout=PAGE_TIMEOUT)
        assert page.get_by_test_id("vendors-table").count() > 0, "the redirect did not land on the vendors table"
        assert "Application Vendors" not in page.inner_text("body")

        # Line 2: three vendors (Salesforce, Okta, Oracle), none of the other
        # organisation's "Workday", and the table total agrees with the rows shown.
        rows = page.get_by_test_id("vendor-row")
        expect(rows).to_have_count(3, timeout=PAGE_TIMEOUT)
        assert page.get_by_text("Workday %s" % tag).count() == 0, "saw the other organisation's vendor"
        answer = page.get_by_test_id("vendors-answer")
        expect(answer).to_contain_text("3 vendors supply", timeout=PAGE_TIMEOUT)

        # Line 3: the create-vendor page's two "Back to Vendors" links land here too.
        page.goto(live_server + "/applications/vendors/create", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        for link in page.get_by_role("link", name="Back to Vendors").all():
            expect(link).to_have_attribute("href", re.compile(r"/applications/vendors/?$"))

        # Line 7: every link on the page opens a page with real content.
        page.goto(live_server + "/applications/vendors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("vendor-link").first.click()
        expect(page).to_have_url(re.compile(r"/applications/vendors/\d+"), timeout=PAGE_TIMEOUT)
        assert len(page.inner_text("body").strip()) > 200
    finally:
        page.close()


def test_the_other_organisation_sees_its_own_vendor_and_its_own_count(browser, live_server, two_org_vendor_world):
    """Walk line 2 (isolation): the second organisation's count differs and never leaks the first's vendors."""
    world = two_org_vendor_world
    tag = world["tag"]
    with world["app"].app_context():
        from app import db
        from app.models.organization import Organization
        from app.models.user import Role, User

        admin_role = Role.query.filter_by(name="Administrator").one()
        email = "other.admin.%s@example.com" % tag
        other = db.session.get(Organization, world["other_id"])
        user = User(email=email, first_name="Other", last_name="Admin", organization_id=other.id,
                    enterprise_role="platform_admin", confirmed=True, role=admin_role,
                    is_platform_admin=True, is_org_admin=True)
        from tests.smoke.conftest import PASSWORD
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()

    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/dashboard/applications/vendors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page).to_have_url(re.compile(r"/applications/vendors/?$"), timeout=PAGE_TIMEOUT)
        rows = page.get_by_test_id("vendor-row")
        expect(rows).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(rows.first).to_contain_text("Workday %s" % tag)
        assert page.get_by_text("Salesforce %s" % tag).count() == 0, "saw the first organisation's vendor"
        assert page.get_by_text("Oracle %s" % tag).count() == 0
        expect(page.get_by_test_id("vendors-answer")).to_contain_text("1 vendor supplies")
    finally:
        page.close()


def test_vendor_type_and_contract_status_narrow_the_table_and_clearing_restores_it(
        browser, live_server, two_org_vendor_world):
    """Walk lines 4 and 5: facets narrow by the organisation's own values, clearing restores the list,
    an empty combination explains itself, and the filters survive in the address."""
    world = two_org_vendor_world
    tag = world["tag"]
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, world["seeded"]["emails"]["procurement"])
        page.goto(live_server + "/applications/vendors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("vendor-row")).to_have_count(3, timeout=PAGE_TIMEOUT)

        # Narrow by vendor type: only Salesforce is a saas_platform in this organisation.
        page.get_by_test_id("vendors-more-filters").click()
        page.get_by_test_id("vendor-type-filter-saas_platform").click()
        expect(page).to_have_url(re.compile(r"vendor_type=saas_platform"), timeout=PAGE_TIMEOUT)
        rows = page.get_by_test_id("vendor-row")
        expect(rows).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(rows.first).to_contain_text("Salesforce %s" % tag)

        # Clearing restores the unfiltered list.
        page.get_by_test_id("vendors-clear-filters").click()
        expect(page.get_by_test_id("vendor-row")).to_have_count(3, timeout=PAGE_TIMEOUT)

        # Line 5: the address itself keeps the filters when typed directly.
        response = page.goto(
            live_server + "/dashboard/applications/vendors?vendor_type=software_vendor&contract_status=contracted",
            wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 200
        expect(page).to_have_url(re.compile(r"vendor_type=software_vendor.*contract_status=contracted|"
                                             r"contract_status=contracted.*vendor_type=software_vendor"),
                                  timeout=PAGE_TIMEOUT)
        rows = page.get_by_test_id("vendor-row")
        expect(rows).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(rows.first).to_contain_text("Oracle %s" % tag)

        # An empty combination (a type that is never also this status here) says why and offers to clear.
        page.goto(live_server + "/applications/vendors?vendor_type=saas_platform&contract_status=deployed",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("vendor-row")).to_have_count(0, timeout=PAGE_TIMEOUT)
        empty_text = page.inner_text("[data-testid='vendors-table-pager']")
        assert "No vendors match" in empty_text
        page.get_by_test_id("vendors-clear-filters").click()
        expect(page.get_by_test_id("vendor-row")).to_have_count(3, timeout=PAGE_TIMEOUT)
    finally:
        page.close()


def test_no_row_shows_edit_and_only_a_platform_administrator_edits_from_the_record(
        browser, live_server, two_org_vendor_world):
    """Walk line 6: no list row offers Edit, for anyone; a platform administrator edits from the
    vendor record and the saved change is visible there after returning; a tenant administrator sees
    no Edit control anywhere."""
    world = two_org_vendor_world
    tag = world["tag"]
    vendor_id = world["vendor_ids"]["oracle"]

    # A tenant administrator (not a platform admin): no Edit on the list, none on the record.
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, world["seeded"]["emails"]["procurement"])
        page.goto(live_server + "/applications/vendors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.get_by_test_id("vendor-edit").count() == 0, "a list row still offers Edit"
        page.goto(live_server + "/applications/vendors/%d" % vendor_id,
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.get_by_test_id("vendor-detail-edit").count() == 0, "a tenant admin can see the catalogue Edit"
    finally:
        page.close()

    # A platform administrator: no Edit on the list either; Edit lives on the record, saves, and
    # the record shows the change after returning.
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, world["seeded"]["emails"]["platform_admin"])
        page.goto(live_server + "/applications/vendors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.get_by_test_id("vendor-edit").count() == 0, "a list row offers Edit even to a platform admin"

        page.goto(live_server + "/applications/vendors/%d" % vendor_id,
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        edit_link = page.get_by_test_id("vendor-detail-edit")
        expect(edit_link).to_be_visible(timeout=PAGE_TIMEOUT)
        edit_link.click()
        expect(page).to_have_url(re.compile(r"/edit$"), timeout=PAGE_TIMEOUT)
        new_name = "Oracle %s renamed" % tag
        page.get_by_label("Vendor Name", exact=False).first.fill(new_name)
        page.get_by_role("button", name=re.compile("Save|Update", re.I)).first.click()
        page.wait_for_url(re.compile(r"/applications/vendors/%d$" % vendor_id), timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text(new_name, timeout=PAGE_TIMEOUT)
    finally:
        page.close()


def test_the_page_fits_a_phone_in_both_themes(browser, live_server, two_org_vendor_world):
    """Walk line 7 (phone width): nothing overflows at 390 wide, light or dark."""
    world = two_org_vendor_world
    for color_scheme in ("light", "dark"):
        page = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme=color_scheme)
        try:
            _login(page, live_server, world["seeded"]["emails"]["procurement"])
            page.goto(live_server + "/applications/vendors?vendor_type=saas_platform",
                      wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(page.get_by_test_id("vendors-answer")).to_be_visible(timeout=PAGE_TIMEOUT)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), \
                "the page scrolls sideways at 390 wide (%s)" % color_scheme
        finally:
            page.close()


def test_no_route_serves_the_retired_legacy_template(live_server, two_org_vendor_world):
    """Walk line 8: the legacy page is gone, not merely unlinked -- the old address itself redirects."""
    import os

    world = two_org_vendor_world
    with world["app"].app_context():
        from flask import url_for
        canonical = url_for("unified_applications.vendors")
    assert canonical.rstrip("/") == "/applications/vendors"

    response = requests.get(live_server + "/dashboard/applications/vendors", allow_redirects=False, timeout=10)
    assert response.status_code == 301, "the legacy address no longer answers as a permanent redirect"
    assert response.headers["Location"].rstrip("/").endswith("/applications/vendors")

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert not os.path.exists(
        os.path.join(repo_root, "app", "templates", "application_mgmt", "vendor_list.html")
    ), "the retired legacy template is still in the tree"

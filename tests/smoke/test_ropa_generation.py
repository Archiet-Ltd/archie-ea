"""The authenticated RoPA page uses the application shell and generates its table."""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_ropa_data(org_id):
    """Seed a data object, two systems, a supplier, and relationships."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.vendor.vendor_organization import VendorOrganization

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    out = {"org": org_id}
    with app.app_context():
        # Data object (processing activity)
        data_obj = ArchiMateElement(
            name="Customer Record %s" % suffix, type="data_object", layer="application",
            organization_id=org_id,
        )
        db.session.add(data_obj)
        db.session.flush()

        # Two systems
        crm = ArchiMateElement(
            name="CRM System %s" % suffix, type="application_component", layer="application",
            organization_id=org_id,
        )
        billing = ArchiMateElement(
            name="Billing System %s" % suffix, type="application_component", layer="application",
            organization_id=org_id,
        )
        db.session.add_all([crm, billing])
        db.session.flush()

        # Access relationships
        db.session.add(ArchiMateRelationship(
            type="access", source_id=crm.id, target_id=data_obj.id,
            access_mode="readwrite", organization_id=org_id,
        ))
        db.session.add(ArchiMateRelationship(
            type="access", source_id=billing.id, target_id=data_obj.id,
            access_mode="read", organization_id=org_id,
        ))
        db.session.flush()

        # ApplicationComponent rows for contracts
        crm_app = ApplicationComponent(
            name="CRM App %s" % suffix, organization_id=org_id,
            archimate_element_id=crm.id,
        )
        billing_app = ApplicationComponent(
            name="Billing App %s" % suffix, organization_id=org_id,
            archimate_element_id=billing.id,
        )
        db.session.add_all([crm_app, billing_app])
        db.session.flush()

        # Supplier
        vendor = VendorOrganization(name="Data Processor %s" % suffix)
        db.session.add(vendor)
        db.session.flush()

        contract = VendorContract(
            contract_name="CRM Support %s" % suffix,
            organization_id=org_id,
            application_id=crm_app.id,
            vendor_id=vendor.id,
            start_date=__import__("datetime").date(2026, 1, 1),
        )
        db.session.add(contract)
        db.session.flush()

        out["data_obj_name"] = data_obj.name
        out["crm_name"] = crm.name
        out["billing_name"] = billing.name
        out["vendor_name"] = vendor.name
        out["crm_app_id"] = crm_app.id
        db.session.commit()
    return out


def test_ropa_generation_from_application_shell(browser, live_server, seeded):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded["emails"]["data_architect"])
        response = page.goto(live_server + "/genome/data/ropa",
                             wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 200
        assert page.get_by_role("main").count() == 1
        assert page.get_by_role("link", name="About", exact=True).count() == 0
        with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT) as navigation:
            page.get_by_role("button", name="Generate RoPA", exact=True).click()
        assert navigation.value.status == 200
        page.get_by_role("heading", name="Record of Processing Activities", exact=True).wait_for(
            state="visible", timeout=PAGE_TIMEOUT
        )
        assert page.get_by_role("main").count() == 1
    finally:
        page.close()


def test_ropa_shows_linked_systems_and_supplier(browser, live_server, seeded):
    """Create a processing activity, link two systems and a supplier,
    reload, see every link; open one system and see the activity listed."""
    ropa = _seed_ropa_data(seeded["ids"]["org"])
    page = browser.new_page()
    try:
        _login(page, live_server, seeded["emails"]["data_architect"])

        # Navigate to RoPA page and generate
        page.goto(live_server + "/genome/data/ropa",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT) as nav:
            page.get_by_role("button", name="Generate RoPA", exact=True).click()
        assert nav.value.status == 200

        # Verify the processing activity name appears
        page.get_by_role("heading", name="Record of Processing Activities", exact=True).wait_for(
            state="visible", timeout=PAGE_TIMEOUT
        )
        page_content = page.content()
        assert ropa["data_obj_name"] in page_content, \
            "Processing activity name not found in RoPA page"
        assert ropa["crm_name"] in page_content, \
            "CRM system not found in RoPA page"
        assert ropa["billing_name"] in page_content, \
            "Billing system not found in RoPA page"
        assert ropa["vendor_name"] in page_content, \
            "Supplier name not found in RoPA page"

        # Navigate to the CRM application detail page and verify the
        # processing activity is listed (linked via access relationship)
        page.goto(live_server + "/applications/%d" % ropa["crm_app_id"],
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        app_content = page.content()
        assert ropa["data_obj_name"] in app_content, \
            "Processing activity not found on system detail page"
    finally:
        page.close()

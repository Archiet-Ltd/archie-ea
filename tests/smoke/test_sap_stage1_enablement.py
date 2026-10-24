"""Browser verification for SAP Stage 1 enablement (Capgemini assessment, 10 Sep 2026).

Two things must be true in the real, rendered UI - not just in a unit test:
  1. The ADM Kanban board shows a SAP Activate stage label next to each ADM
     phase, so a steering-committee viewer sees Activate language without the
     TOGAF phase names being renamed.
  2. The SAP vendor record in the Vendor Catalogue carries a real product
     portfolio a user can find by searching and see listed on the vendor's
     detail page - not the single generic "ERP" placeholder the assessment
     found.
"""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture
def sap_vendor_seeded(seeded):
    """Seed the real SAP product portfolio (see app/commands/seed_sap_products.py).

    VendorOrganization is deliberately shared reference data, not tenant-scoped
    (see the class docstring), so this is global for the test database - it
    does not depend on which organisation the logged-in user belongs to.
    """
    from app import create_app, db
    from app.commands.seed_sap_products import _SAP_ORG, _SAP_PRODUCTS
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

    app = create_app("testing")
    with app.app_context():
        # Match both "SAP" (the pre-existing generic vendor record) and "SAP SE"
        # (the full legal name), same lookup as app/commands/seed_sap_products.py,
        # so this fixture reuses the real vendor instead of creating a second one -
        # a duplicate vendor org was the actual production defect this test exists
        # to catch, and matching on the full name only would recreate it silently.
        org = VendorOrganization.query.filter(
            VendorOrganization.name.in_(["SAP", _SAP_ORG["org_name"]])
        ).first()
        if org is None:
            org = VendorOrganization(
                name=_SAP_ORG["org_name"],
                display_name=_SAP_ORG["org_name"],
                vendor_type=_SAP_ORG["org_type"],
            )
            db.session.add(org)
            db.session.flush()

        for entry in _SAP_PRODUCTS:
            exists = VendorProduct.query.filter_by(
                vendor_organization_id=org.id, name=entry["name"]
            ).first()
            if exists is None:
                db.session.add(VendorProduct(
                    vendor_organization_id=org.id,
                    name=entry["name"],
                    product_family_name=entry["product_family"],
                    deployment_model=entry["deployment_model"],
                    product_type=entry["product_type"],
                ))
        db.session.commit()
        vendor_id = org.id

    return vendor_id


def test_adm_kanban_shows_sap_activate_stage(browser, live_server, seeded):
    """An Architect viewing the ADM Kanban board sees the Activate stage label."""
    page = browser.new_page()
    try:
        _login(page, live_server, seeded['emails']['enterprise_architect'])
        response = page.goto(live_server + '/adm-kanban/v2', timeout=PAGE_TIMEOUT)
        assert response.status == 200
        page.wait_for_timeout(1500)
        # Each ADM phase lane carries its SAP Activate stage in the lane header.
        page.wait_for_selector('[data-testid="adm-lane"]', timeout=PAGE_TIMEOUT)
        activate_labels = page.locator('text=/Activate: (Prepare|Explore|Realize|Deploy|Run)/')
        expect(activate_labels.first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert activate_labels.count() >= 1, (
            'expected at least one ADM phase row to show its SAP Activate stage'
        )
    finally:
        page.close()


def test_vendor_catalogue_lists_seeded_sap_products(browser, live_server, seeded, sap_vendor_seeded):
    """An Architect searching the Vendor Catalogue for SAP finds the real product portfolio."""
    page = browser.new_page()
    try:
        _login(page, live_server, seeded['emails']['enterprise_architect'])
        response = page.goto(live_server + '/applications/vendors', timeout=PAGE_TIMEOUT)
        assert response.status == 200
        page.wait_for_timeout(1000)

        search = page.get_by_placeholder('Search vendors...')
        expect(search).to_be_visible(timeout=PAGE_TIMEOUT)
        search.fill('SAP')
        page.wait_for_timeout(600)

        # The real pre-existing vendor is named "SAP", not "SAP SE" - matching
        # the full legal name here previously matched nothing (or a duplicate
        # vendor created by this same off-by-name-mismatch bug) rather than
        # the actual seeded row.
        sap_row = page.locator('tr', has_text='SAP').first
        expect(sap_row).to_be_visible(timeout=PAGE_TIMEOUT)
        sap_row.get_by_label('View vendor details').click()
        page.wait_for_timeout(1000)

        product_names = [
            'SAP S/4HANA Cloud',
            'SAP S/4HANA On-Premise',
            'SAP Business Technology Platform',
            'SAP Signavio Process Navigator',
        ]
        found = 0
        for name in product_names:
            locator = page.locator(f'text={name}')
            if locator.count() > 0:
                expect(locator.first).to_be_visible(timeout=PAGE_TIMEOUT)
                found += 1
        assert found >= 2, (
            'expected at least 2 seeded SAP products to render on the vendor detail page, found %d'
            % found
        )
    finally:
        page.close()

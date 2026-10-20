"""Test for vendor list own organisation counts (defect 0)."""
import pytest

from app.modules.vendors.routes.unified_vendor_views import _get_vendor_list_context
from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct
from app.models.application_portfolio import ApplicationComponent
from app.models.capability_to_vendor_mapping import ApplicationVendorProductMapping


@pytest.mark.usefixtures("db_session")
def test_vendor_list_own_org_counts(app, db_session, make_org, tenant_ctx):
    # Create two organisations using the fixture
    org_a = make_org("org-a")
    org_b = make_org("org-b")

    # Create a shared vendor organisation and product
    vendor = VendorOrganization(name="Shared Vendor")
    db_session.add(vendor)
    db_session.flush()

    product = VendorProduct(name="Shared Product", vendor_organization_id=vendor.id)
    db_session.add(product)
    db_session.flush()

    # Create two application components for org A and map to product
    app_components_a = []
    for i in range(2):
        app_comp = ApplicationComponent(
            name=f"App A {i}",
            vendor_name="Shared Vendor",
            organization_id=org_a.id,
        )
        db_session.add(app_comp)
        db_session.flush()
        mapping = ApplicationVendorProductMapping(
            application_component_id=app_comp.id,
            vendor_product_id=product.id,
        )
        db_session.add(mapping)
        app_components_a.append(app_comp)

    # Create three application components for org B and map to product
    app_components_b = []
    for i in range(3):
        app_comp = ApplicationComponent(
            name=f"App B {i}",
            vendor_name="Shared Vendor",
            organization_id=org_b.id,
        )
        db_session.add(app_comp)
        db_session.flush()
        mapping = ApplicationVendorProductMapping(
            application_component_id=app_comp.id,
            vendor_product_id=product.id,
        )
        db_session.add(mapping)
        app_components_b.append(app_comp)

    db_session.commit()

    # Helper to get vendor list context for a given org id using tenant_ctx
    def get_vendor_list_for_org(org_id):
        with app.app_context(), tenant_ctx(org_id):
            stats, vendors = _get_vendor_list_context()
            return stats, vendors

    # Get vendor list for org A
    stats_a, vendors_a = get_vendor_list_for_org(org_a.id)
    # Get vendor list for org B
    stats_b, vendors_b = get_vendor_list_for_org(org_b.id)

    # Find the shared vendor in each list
    vendor_a = next((v for v in vendors_a if v["name"] == "Shared Vendor"), None)
    vendor_b = next((v for v in vendors_b if v["name"] == "Shared Vendor"), None)

    assert vendor_a is not None, "Shared vendor not found in org A's vendor list"
    assert vendor_b is not None, "Shared vendor not found in org B's vendor list"

    # Each organisation should see only its own application count
    assert vendor_a["app_count"] == 2, f"Expected app_count 2 for org A, got {vendor_a['app_count']}"
    assert vendor_b["app_count"] == 3, f"Expected app_count 3 for org B, got {vendor_b['app_count']}"

    # Ensure the other organisation's application is not counted
    assert stats_a["total_linked_apps"] == 2
    assert stats_b["total_linked_apps"] == 3

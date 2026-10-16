"""A vendor-deployed application's ArchiMate element is written by the one shared writer.

``deploy_vendor_product_as_application`` used to construct an
``ArchiMateElement`` by itself, outside the flush listener: the deployed
application's element then carried no provenance, and its description/layer
could drift from a backfilled or listener-created element of the same type.
The one-writer doctrine says every element node for the five domain types
comes from ``sync_archimate_element`` (the flush listener's helper); these
tests pin that the vendor onboarding path goes through it too and never
touches another organisation's rows.
"""

import uuid

from app.models.archimate_core import ArchiMateElement
from app.models.application_portfolio import ApplicationComponent


def test_vendor_deployed_application_element_is_written_by_the_shared_writer(
    db_session, make_org, tenant_ctx,
):
    """The deployed app's element carries the shared writer's provenance."""
    from app.modules.vendors.services.vendor_onboarding_service import (
        VendorOnboardingService,
    )
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

    org = make_org("vdep-writer")
    vendor = VendorOrganization(
        name=f"Vendor {uuid.uuid4().hex[:8]}",
        display_name="Seed Vendor",
    )
    db_session.add(vendor)
    db_session.flush()
    product = VendorProduct(
        vendor_organization_id=vendor.id,
        name=f"Product {uuid.uuid4().hex[:8]}",
    )
    db_session.add(product)
    db_session.flush()

    with tenant_ctx(org.id):
        app = VendorOnboardingService.deploy_vendor_product_as_application(
            vendor_product_id=product.id,
            application_name="Deployed App",
            description="Deployed instance description",
            deployment_type="primary_system",
            criticality="business_critical",
            hosting_model="cloud",
        )

        assert app.archimate_element_id is not None, (
            "the deployed application must carry an ArchiMate element"
        )
        element = db_session.get(ArchiMateElement, app.archimate_element_id)
        assert element is not None
        assert element.custom_properties == {"source_model": "ApplicationComponent"}, (
            "the element must be written by the shared writer (flush listener), "
            "which stamps provenance; a directly-constructed element has none"
        )
        assert element.type == "ApplicationComponent"
        assert element.layer == "Application"
        assert element.organization_id == org.id


def test_vendor_deploy_only_touches_the_deploying_organisation(
    db_session, make_org, tenant_ctx,
):
    """Deploying a vendor product in org A leaves org B's rows untouched.

    The vendor catalogue itself is shared reference data; the deployment is
    tenant-owned and must only ever write rows for the deploying org.
    """
    from sqlalchemy import text

    from app.modules.vendors.services.vendor_onboarding_service import (
        VendorOnboardingService,
    )
    from app.models.organization import Organization
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

    org_a = make_org("vdep-iso-a")
    org_b = make_org("vdep-iso-b")
    vendor = VendorOrganization(
        name=f"Vendor {uuid.uuid4().hex[:8]}",
        display_name="Iso Seed Vendor",
    )
    db_session.add(vendor)
    db_session.flush()
    product = VendorProduct(
        vendor_organization_id=vendor.id,
        name=f"Product {uuid.uuid4().hex[:8]}",
    )
    db_session.add(product)
    db_session.flush()

    with tenant_ctx(org_a.id):
        app = VendorOnboardingService.deploy_vendor_product_as_application(
            vendor_product_id=product.id,
            application_name="Isolated Deploy",
            description=None,
        )
        assert app.archimate_element_id is not None

    # The application row, its element and the product link are all org A's.
    b_apps = db_session.query(ApplicationComponent).filter(
        ApplicationComponent.organization_id == org_b.id,
    ).count()
    assert b_apps == 0, "org B must have no application rows from org A's deploy"

    b_elements = db_session.query(ArchiMateElement).filter(
        ArchiMateElement.organization_id == org_b.id,
    ).count()
    assert b_elements == 0, "org B must have no element rows from org A's deploy"

    links = db_session.execute(
        text(
            """
            SELECT count(*) FROM application_vendor_products
            WHERE archimate_element_id = :elem_id
            """
        ),
        {"elem_id": app.archimate_element_id},
    ).scalar()
    assert links == 1, "the deployment link must be written for the deploying org"
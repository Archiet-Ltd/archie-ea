"""DataSharingAgreement (R1-B80, PB-0355): an organisation registers an
agreement governing a data flow to or from a vendor; a flow reaching that
vendor with no linked, unexpired agreement is flagged "unagreed", and
stops being flagged once linked.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.models.data_sharing_agreement import DataSharingAgreement


def _vendor_org(db_session, name="Test Vendor"):
    from app.models.vendor.vendor_organization import VendorOrganization

    vendor = VendorOrganization(name=f"{name} {uuid.uuid4().hex[:6]}")
    db_session.add(vendor)
    db_session.flush()
    return vendor


def test_a_new_agreement_with_no_expiry_is_current(db_session, make_org):
    org = make_org("dsa-no-expiry")
    vendor = _vendor_org(db_session)
    agreement = DataSharingAgreement(
        name="Agreement A", vendor_organization_id=vendor.id, organization_id=org.id,
        status="active", effective_date=date.today(),
    )
    db_session.add(agreement)
    db_session.commit()

    assert agreement.is_current() is True


def test_an_expired_agreement_is_not_current(db_session, make_org):
    org = make_org("dsa-expired")
    vendor = _vendor_org(db_session)
    agreement = DataSharingAgreement(
        name="Agreement B", vendor_organization_id=vendor.id, organization_id=org.id,
        status="active", effective_date=date.today() - timedelta(days=400),
        expiry_date=date.today() - timedelta(days=1),
    )
    db_session.add(agreement)
    db_session.commit()

    assert agreement.is_current() is False


def test_a_terminated_agreement_is_not_current_even_without_an_expiry_date(db_session, make_org):
    org = make_org("dsa-terminated")
    vendor = _vendor_org(db_session)
    agreement = DataSharingAgreement(
        name="Agreement C", vendor_organization_id=vendor.id, organization_id=org.id,
        status="terminated", effective_date=date.today(),
    )
    db_session.add(agreement)
    db_session.commit()

    assert agreement.is_current() is False


def test_an_agreement_can_cover_more_than_one_flow(db_session, make_org):
    from app.models import ArchiMateElement
    from app.models.all_missing_models import DataLineage

    org = make_org("dsa-multi-flow")
    vendor = _vendor_org(db_session)
    a = ArchiMateElement(name="A", type="ApplicationComponent", layer="application", organization_id=org.id)
    b = ArchiMateElement(name="B", type="ApplicationComponent", layer="application", organization_id=org.id)
    db_session.add_all([a, b])
    db_session.flush()
    flow1 = DataLineage(name="flow1", archimate_element_id=a.id, target_archimate_element_id=b.id, organization_id=org.id)
    flow2 = DataLineage(name="flow2", archimate_element_id=b.id, target_archimate_element_id=a.id, organization_id=org.id)
    db_session.add_all([flow1, flow2])
    db_session.flush()

    agreement = DataSharingAgreement(
        name="Agreement D", vendor_organization_id=vendor.id, organization_id=org.id,
        status="active", effective_date=date.today(),
    )
    agreement.flows = [flow1, flow2]
    db_session.add(agreement)
    db_session.commit()

    db_session.expire_all()
    reloaded = DataSharingAgreement.query.filter_by(id=agreement.id).one()
    assert {f.name for f in reloaded.flows} == {"flow1", "flow2"}


def _vendor_bound_app(db_session, org, element_name="Target App"):
    """An ArchiMateElement + ApplicationComponent + VendorProduct, so the
    element is reachable via a DataLineage edge AND is vendor-linked
    (partner-bound)."""
    from app.models import ArchiMateElement
    from app.models.application_portfolio import ApplicationComponent
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

    vendor = _vendor_org(db_session)
    product = VendorProduct(name=f"Product {uuid.uuid4().hex[:6]}", vendor_organization_id=vendor.id)
    db_session.add(product)
    db_session.flush()

    element = ArchiMateElement(
        name=element_name, type="ApplicationComponent", layer="application", organization_id=org.id,
    )
    db_session.add(element)
    db_session.flush()

    component = ApplicationComponent(
        name=element_name, organization_id=org.id, archimate_element_id=element.id,
        vendor_product_id=product.id,
    )
    db_session.add(component)
    db_session.flush()
    return vendor, element


def test_a_flow_reaching_a_vendor_linked_app_with_no_agreement_is_flagged_unagreed(db_session, make_org):
    from app.models import ArchiMateElement
    from app.models.all_missing_models import DataLineage

    org = make_org("dsa-unagreed")
    _vendor, target = _vendor_bound_app(db_session, org)
    source = ArchiMateElement(name="Source", type="DataObject", layer="application", organization_id=org.id)
    db_session.add(source)
    db_session.flush()
    flow = DataLineage(
        name="unagreed-flow", archimate_element_id=source.id,
        target_archimate_element_id=target.id, organization_id=org.id,
    )
    db_session.add(flow)
    db_session.commit()

    assert flow.id in DataSharingAgreement.unagreed_flow_ids(org.id)


def test_linking_a_current_agreement_stops_the_flow_being_flagged(db_session, make_org):
    from app.models import ArchiMateElement
    from app.models.all_missing_models import DataLineage

    org = make_org("dsa-now-agreed")
    vendor, target = _vendor_bound_app(db_session, org)
    source = ArchiMateElement(name="Source", type="DataObject", layer="application", organization_id=org.id)
    db_session.add(source)
    db_session.flush()
    flow = DataLineage(
        name="now-agreed-flow", archimate_element_id=source.id,
        target_archimate_element_id=target.id, organization_id=org.id,
    )
    db_session.add(flow)
    db_session.flush()
    assert flow.id in DataSharingAgreement.unagreed_flow_ids(org.id)

    agreement = DataSharingAgreement(
        name="Covers it now", vendor_organization_id=vendor.id, organization_id=org.id,
        status="active", effective_date=date.today(),
    )
    agreement.flows = [flow]
    db_session.add(agreement)
    db_session.commit()

    assert flow.id not in DataSharingAgreement.unagreed_flow_ids(org.id)


def test_an_expired_agreement_does_not_un_flag_its_flow(db_session, make_org):
    from app.models import ArchiMateElement
    from app.models.all_missing_models import DataLineage

    org = make_org("dsa-expired-still-flagged")
    vendor, target = _vendor_bound_app(db_session, org)
    source = ArchiMateElement(name="Source", type="DataObject", layer="application", organization_id=org.id)
    db_session.add(source)
    db_session.flush()
    flow = DataLineage(
        name="expired-flow", archimate_element_id=source.id,
        target_archimate_element_id=target.id, organization_id=org.id,
    )
    db_session.add(flow)
    db_session.flush()

    agreement = DataSharingAgreement(
        name="Expired", vendor_organization_id=vendor.id, organization_id=org.id,
        status="active", effective_date=date.today() - timedelta(days=400),
        expiry_date=date.today() - timedelta(days=1),
    )
    agreement.flows = [flow]
    db_session.add(agreement)
    db_session.commit()

    assert flow.id in DataSharingAgreement.unagreed_flow_ids(org.id)


def test_two_organisations_agreements_never_cross(db_session, make_org):
    org_a = make_org("dsa-fence-a")
    org_b = make_org("dsa-fence-b")
    vendor = _vendor_org(db_session)
    agreement_a = DataSharingAgreement(
        name="A's agreement", vendor_organization_id=vendor.id, organization_id=org_a.id,
        status="active", effective_date=date.today(),
    )
    db_session.add(agreement_a)
    db_session.commit()

    org_b_agreements = DataSharingAgreement.query.filter_by(organization_id=org_b.id).all()
    assert org_b_agreements == []

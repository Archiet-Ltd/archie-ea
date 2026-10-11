"""R1-B85 (PB-0043): end-of-support deadline alerts.

A technology product's end-of-life date, when it falls inside an
organisation's configured lead time, creates exactly one tracked inbox
item (R1-B07's one approval queue) per organisation that actually depends
on it -- listing dependent applications and owners, never duplicating the
alert on a later scan, and never crossing organisations.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from app.models.ai_chat_crud_approval import AIChatCRUDApproval
from app.models.application_owner import ApplicationOwner
from app.models.application_portfolio import ApplicationComponent
from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct
from app.modules.intelligence.services.deadline_alert_service import (
    lead_months_for,
    scan_organization_for_eol_alerts,
)


def _vendor_product(db_session, *, months_to_eol):
    vendor = VendorOrganization(name=f"Vendor {uuid.uuid4().hex[:6]}")
    db_session.add(vendor)
    db_session.flush()
    product = VendorProduct(
        vendor_organization_id=vendor.id,
        name=f"Product {uuid.uuid4().hex[:6]}",
        end_of_life_date=datetime.utcnow() + timedelta(days=30 * months_to_eol),
    )
    db_session.add(product)
    db_session.flush()
    return product


def _app(db_session, org, vendor_product_id, name="App"):
    app_component = ApplicationComponent(
        name=f"{name} {uuid.uuid4().hex[:6]}",
        organization_id=org.id,
        vendor_product_id=vendor_product_id,
    )
    db_session.add(app_component)
    db_session.flush()
    return app_component


def test_a_product_inside_the_lead_time_with_a_dependent_creates_one_alert(db_session, make_org):
    org = make_org("eol-create")
    product = _vendor_product(db_session, months_to_eol=10)
    _app(db_session, org, product.id)
    db_session.commit()

    result = scan_organization_for_eol_alerts(org.id)

    assert result["created"] == [AIChatCRUDApproval.query.filter_by(
        organization_id=org.id, source_table="vendor_products", source_id=product.id
    ).one().id]
    alert = AIChatCRUDApproval.query.filter_by(organization_id=org.id).one()
    assert alert.operation_type == "end_of_support_alert"
    assert alert.source_id == product.id


def test_a_product_outside_the_lead_time_creates_no_alert(db_session, make_org):
    org = make_org("eol-outside")
    product = _vendor_product(db_session, months_to_eol=36)
    _app(db_session, org, product.id)
    db_session.commit()

    result = scan_organization_for_eol_alerts(org.id)

    assert result["created"] == []
    assert AIChatCRUDApproval.query.filter_by(organization_id=org.id).count() == 0


def test_a_product_with_no_dependent_application_creates_no_alert(db_session, make_org):
    org = make_org("eol-no-dependent")
    _vendor_product(db_session, months_to_eol=5)
    db_session.commit()

    result = scan_organization_for_eol_alerts(org.id)

    assert result["created"] == []


def test_scanning_twice_does_not_duplicate_the_alert(db_session, make_org):
    org = make_org("eol-no-dup")
    product = _vendor_product(db_session, months_to_eol=6)
    _app(db_session, org, product.id)
    db_session.commit()

    first = scan_organization_for_eol_alerts(org.id)
    second = scan_organization_for_eol_alerts(org.id)

    assert len(first["created"]) == 1
    assert second["created"] == []
    assert second["skipped_existing"] == [product.id]
    assert AIChatCRUDApproval.query.filter_by(organization_id=org.id).count() == 1


def test_an_end_of_life_date_exactly_at_the_lead_time_creates_exactly_one_alert(db_session, make_org):
    from app.modules.intelligence.services.deadline_alert_service import _add_months

    org = make_org("eol-exact")
    product = _vendor_product(db_session, months_to_eol=0)
    # Move the cutoff to exactly the lead time by setting end_of_life_date
    # to precisely "now + lead_months" worth of calendar months.
    product.end_of_life_date = _add_months(datetime.utcnow(), lead_months_for(org.id))
    db_session.flush()
    _app(db_session, org, product.id)
    db_session.commit()

    result = scan_organization_for_eol_alerts(org.id)
    assert len(result["created"]) == 1

    # A second scan must not raise a duplicate for the same boundary date.
    result2 = scan_organization_for_eol_alerts(org.id)
    assert result2["created"] == []


def test_the_alert_lists_dependent_applications_and_their_owner(db_session, make_org):
    from app.models.user import User

    org = make_org("eol-owner")
    product = _vendor_product(db_session, months_to_eol=8)
    app_component = _app(db_session, org, product.id, name="Owned App")
    owner = User(
        first_name="Pat", last_name="Owner",
        email=f"pat-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True,
    )
    db_session.add(owner)
    db_session.flush()
    db_session.add(ApplicationOwner(
        application_id=app_component.id, user_id=owner.id, ownership_type="primary",
        organization_id=org.id,
    ))
    db_session.commit()

    scan_organization_for_eol_alerts(org.id)

    alert = AIChatCRUDApproval.query.filter_by(organization_id=org.id).one()
    import json
    payload = json.loads(alert.operation_payload)
    assert any("Pat Owner" in line for line in payload["dependent_applications"])


def test_an_application_with_no_recorded_owner_reads_as_absence_not_a_guess(db_session, make_org):
    org = make_org("eol-no-owner")
    product = _vendor_product(db_session, months_to_eol=4)
    _app(db_session, org, product.id, name="Orphan App")
    db_session.commit()

    scan_organization_for_eol_alerts(org.id)

    alert = AIChatCRUDApproval.query.filter_by(organization_id=org.id).one()
    import json
    payload = json.loads(alert.operation_payload)
    assert any("no owner recorded" in line for line in payload["dependent_applications"])


def test_two_organisations_alerts_never_cross(db_session, make_org):
    org_a = make_org("eol-fence-a")
    org_b = make_org("eol-fence-b")
    product = _vendor_product(db_session, months_to_eol=6)
    _app(db_session, org_a, product.id)
    db_session.commit()

    scan_organization_for_eol_alerts(org_a.id)
    scan_organization_for_eol_alerts(org_b.id)

    assert AIChatCRUDApproval.query.filter_by(organization_id=org_a.id).count() == 1
    assert AIChatCRUDApproval.query.filter_by(organization_id=org_b.id).count() == 0


def test_lead_months_for_defaults_to_eighteen_when_unset(db_session, make_org):
    org = make_org("eol-lead-default")
    assert lead_months_for(org.id) == 18


def test_lead_months_for_honours_a_per_organization_override(db_session, make_org):
    org = make_org("eol-lead-override")
    org.settings = {"alert_eol_lead_months": 6}
    db_session.commit()
    assert lead_months_for(org.id) == 6

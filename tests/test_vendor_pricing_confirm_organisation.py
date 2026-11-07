"""Vendor pricing confirm never writes a literal organisation id.

A new pricing row takes the signed-in user's organisation; an update leaves the
existing row's organisation exactly as it was.
"""

from __future__ import annotations

import uuid

import pytest


def _admin(db_session, org):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        pytest.skip("no Administrator role seeded in this database")
    user = User(email=f"vp-{uuid.uuid4().hex[:6]}@example.test", first_name="Pricing", last_name="Admin",
                organization_id=org.id, confirmed=True, role=role)
    user.password = uuid.uuid4().hex
    user.is_org_admin = True
    user.is_platform_admin = True
    db_session.add(user)
    db_session.flush()
    return user


def _catalogue(db_session):
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

    suffix = uuid.uuid4().hex[:8]
    vendor = VendorOrganization(name=f"Acme {suffix}")
    db_session.add(vendor)
    db_session.flush()
    product = VendorProduct(vendor_organization_id=vendor.id, name=f"Suite {suffix}")
    db_session.add(product)
    db_session.flush()
    return vendor, product


def _confirm(db_session, client, login_as, user, vendor, product, tier, **extra):
    from app.models.user import User

    user_id = user.id
    db_session.commit()
    db_session.expunge_all()
    login_as(client, db_session.get(User, user_id))
    item = {"vendor_name": vendor, "product_name": product, "tier": tier, "annual_cost": 1000, **extra}
    return client.post("/admin/vendor-pricing/confirm", json={"items": [item]})


def test_new_row_takes_the_signed_in_users_organisation(app, db_session, make_org, client, login_as):
    from app.models.vendor.vendor_organization import VendorProductPricing

    org = make_org("pricing-new")
    user = _admin(db_session, org)
    vendor, product = _catalogue(db_session)
    names = (vendor.name, product.name, product.id)
    org_id = org.id

    resp = _confirm(db_session, client, login_as, user, names[0], names[1], "Gold")
    assert resp.status_code == 200 and resp.get_json()["created"] == 1

    row = VendorProductPricing.query.filter_by(product_id=names[2], tier_name="Gold").one()
    assert row.organization_id == org_id


def _price(db_session, product, tier, org_id, **kw):
    from app.models.vendor.vendor_organization import VendorProductPricing

    row = VendorProductPricing(
        product_id=product.id, pricing_model="per_user", tier_name=tier, list_price_annual=1,
        currency="USD", source="seed", data_source_type="seeded", organization_id=org_id,
        typical_discount_percent=5, contract_term_months=24, setup_fee=7, **kw)
    db_session.add(row)
    db_session.flush()
    return row


def _snapshot(row):
    return (row.list_price_annual, row.typical_discount_percent, row.contract_term_months,
            row.setup_fee, row.implementation_fee, row.expiry_date, row.source, row.data_source_type,
            row.organization_id)


def test_update_changes_only_the_signed_in_organisations_own_row(app, db_session, make_org, client, login_as):
    from app.models.vendor.vendor_organization import VendorProductPricing

    org = make_org("pricing-own")
    user = _admin(db_session, org)
    vendor, product = _catalogue(db_session)
    _price(db_session, product, "Silver", org.id)
    names = (vendor.name, product.name, product.id)
    org_id = org.id

    resp = _confirm(db_session, client, login_as, user, names[0], names[1], "Silver", expiry_date="2028-12-31")
    assert resp.status_code == 200 and resp.get_json()["updated"] == 1

    row = VendorProductPricing.query.filter_by(product_id=names[2], tier_name="Silver").one()
    assert row.organization_id == org_id
    assert str(row.expiry_date) == "2028-12-31"


def test_other_organisations_confirm_leaves_the_first_organisations_price_alone(
        app, db_session, make_org, client, login_as):
    from app.models.vendor.vendor_organization import VendorProductPricing

    owner_org = make_org("pricing-owner")
    other_org = make_org("pricing-other")
    user = _admin(db_session, other_org)
    vendor, product = _catalogue(db_session)
    owner_row = _price(db_session, product, "Silver", owner_org.id)
    before = _snapshot(owner_row)
    owner_row_id = owner_row.id
    names = (vendor.name, product.name, product.id)
    owner_id, other_id = owner_org.id, other_org.id

    resp = _confirm(db_session, client, login_as, user, names[0], names[1], "Silver",
                    expiry_date="2028-12-31", discount_percent=40, setup_fee=999)
    body = resp.get_json()
    assert resp.status_code == 200 and body["created"] == 1 and body["updated"] == 0

    db_session.expire_all()
    assert _snapshot(db_session.get(VendorProductPricing, owner_row_id)) == before
    mine = VendorProductPricing.query.filter_by(product_id=names[2], tier_name="Silver",
                                                organization_id=other_id).one()
    assert mine.id != owner_row_id and mine.list_price_annual == 1000
    assert VendorProductPricing.query.filter_by(product_id=names[2], organization_id=owner_id).count() == 1


def test_shared_reference_price_is_never_updated_by_an_organisation_import(
        app, db_session, make_org, client, login_as):
    from app.models.vendor.vendor_organization import VendorProductPricing

    org = make_org("pricing-shared")
    user = _admin(db_session, org)
    vendor, product = _catalogue(db_session)
    shared = _price(db_session, product, "Gold", None)
    before = _snapshot(shared)
    shared_id = shared.id
    names = (vendor.name, product.name, product.id)
    org_id = org.id

    resp = _confirm(db_session, client, login_as, user, names[0], names[1], "Gold", expiry_date="2028-12-31")
    assert resp.status_code == 200 and resp.get_json()["created"] == 1

    db_session.expire_all()
    assert _snapshot(db_session.get(VendorProductPricing, shared_id)) == before
    assert VendorProductPricing.query.filter_by(
        product_id=names[2], tier_name="Gold", organization_id=org_id).count() == 1

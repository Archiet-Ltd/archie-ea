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


def test_update_keeps_the_existing_rows_organisation(app, db_session, make_org, client, login_as):
    from app.models.vendor.vendor_organization import VendorProductPricing

    owner_org = make_org("pricing-owner")
    other_org = make_org("pricing-other")
    user = _admin(db_session, other_org)
    vendor, product = _catalogue(db_session)
    db_session.add(VendorProductPricing(
        product_id=product.id, pricing_model="per_user", tier_name="Silver", list_price_annual=1,
        currency="USD", source="seed", data_source_type="seeded", organization_id=owner_org.id))
    db_session.flush()
    names = (vendor.name, product.name, product.id)
    owner_id = owner_org.id

    resp = _confirm(db_session, client, login_as, user, names[0], names[1], "Silver", expiry_date="2028-12-31")
    assert resp.status_code == 200 and resp.get_json()["updated"] == 1

    row = VendorProductPricing.query.filter_by(product_id=names[2], tier_name="Silver").one()
    assert row.organization_id == owner_id
    assert str(row.expiry_date) == "2028-12-31"

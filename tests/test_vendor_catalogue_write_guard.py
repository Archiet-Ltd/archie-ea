"""The vendor catalogue has no tenant column: one row serves every organisation.

Creating or deleting a catalogue row therefore publishes or removes it for every other
organisation, so those writes are limited to platform administrators. Reading the
catalogue stays open to every signed-in user.
"""

from __future__ import annotations

import uuid

import pytest

CREATES = [
    ("post", "/api/vendors/"),
    ("post", "/vendor-management/create"),
    ("post", "/vendor-management/api/vendors"),
    ("post", "/vendor-management/import"),
    ("post", "/applications/vendors/create"),
]


def _user(db_session, org, *, platform=False):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        pytest.skip("no Administrator role seeded in this database")
    user = User(email=f"vcw-{uuid.uuid4().hex[:6]}@example.test", first_name="Vendor", last_name="Tester",
                organization_id=org.id, confirmed=True, role=role)
    user.password = uuid.uuid4().hex
    user.is_org_admin = True
    user.is_platform_admin = platform
    db_session.add(user)
    db_session.flush()
    return user


def _world(db_session, make_org):
    org = make_org("vendor-write-guard")
    tenant, platform = _user(db_session, org), _user(db_session, org, platform=True)
    db_session.commit()
    return tenant.id, platform.id


def _login(db_session, client, login_as, user_id):
    from app.models.user import User

    db_session.expunge_all()
    login_as(client, db_session.get(User, user_id))


def _vendor(db_session, name):
    from app.models.vendor.vendor_organization import VendorOrganization

    vendor = VendorOrganization(name=name)
    db_session.add(vendor)
    db_session.commit()
    return vendor.id


def _count(db_session, name):
    from app.models.vendor.vendor_organization import VendorOrganization

    return VendorOrganization.query.filter_by(name=name).count()


@pytest.mark.parametrize("method,path", CREATES)
def test_a_tenant_user_cannot_create_a_catalogue_vendor(app, db_session, make_org, client, login_as, method, path):
    tenant_id, _platform = _world(db_session, make_org)
    name = "ZZ-GUARD " + uuid.uuid4().hex[:8]

    _login(db_session, client, login_as, tenant_id)
    response = getattr(client, method)(path, json={"name": name}, headers={"Accept": "application/json"})

    assert response.status_code == 403
    assert _count(db_session, name) == 0


def test_a_tenant_user_cannot_bulk_delete_or_delete_a_catalogue_vendor(app, db_session, make_org, client, login_as):
    from app.models.vendor.vendor_organization import VendorOrganization

    tenant_id, _platform = _world(db_session, make_org)
    vendor_id = _vendor(db_session, "ZZ-GUARD " + uuid.uuid4().hex[:8])

    _login(db_session, client, login_as, tenant_id)
    bulk = client.delete("/api/vendors/bulk", json={"ids": [vendor_id]})
    _login(db_session, client, login_as, tenant_id)
    single = client.delete(f"/api/vendors/{vendor_id}")
    _login(db_session, client, login_as, tenant_id)
    managed = client.delete(f"/vendor-management/api/vendors/{vendor_id}")

    assert (bulk.status_code, single.status_code, managed.status_code) == (403, 403, 403)
    assert db_session.get(VendorOrganization, vendor_id) is not None


def test_a_tenant_user_can_still_read_the_catalogue(app, db_session, make_org, client, login_as):
    tenant_id, _platform = _world(db_session, make_org)

    _login(db_session, client, login_as, tenant_id)
    response = client.get("/api/vendors/list")

    assert response.status_code == 200


def test_a_platform_admin_can_create_and_bulk_delete_catalogue_vendors(app, db_session, make_org, client, login_as):
    from app.models.vendor.vendor_organization import VendorOrganization

    _tenant, platform_id = _world(db_session, make_org)
    name = "ZZ-GUARD " + uuid.uuid4().hex[:8]

    _login(db_session, client, login_as, platform_id)
    created = client.post("/api/vendors/", json={"name": name})
    assert created.status_code == 201, created.get_data(as_text=True)
    vendor_id = VendorOrganization.query.filter_by(name=name).one().id

    _login(db_session, client, login_as, platform_id)
    deleted = client.delete("/api/vendors/bulk", json={"ids": [vendor_id]})

    assert deleted.status_code == 200, deleted.get_data(as_text=True)
    assert db_session.get(VendorOrganization, vendor_id) is None


def test_a_platform_admin_can_use_the_management_create_and_delete_routes(app, db_session, make_org, client, login_as):
    from app.models.vendor.vendor_organization import VendorOrganization

    _tenant, platform_id = _world(db_session, make_org)
    name = "ZZ-GUARD " + uuid.uuid4().hex[:8]

    _login(db_session, client, login_as, platform_id)
    created = client.post("/vendor-management/api/vendors", json={"name": name})
    assert created.status_code in (200, 201), created.get_data(as_text=True)
    vendor_id = VendorOrganization.query.filter_by(name=name).one().id

    _login(db_session, client, login_as, platform_id)
    deleted = client.delete(f"/vendor-management/api/vendors/{vendor_id}")

    assert deleted.status_code in (200, 204), deleted.get_data(as_text=True)

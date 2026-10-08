"""``require_roles("admin")``-gated vendor import/delete routes write the same
shared, platform-wide vendor catalogue as ``app/application_mgmt/vendor_routes.py``
and have the same global-flag problem: ``require_roles`` is satisfied by the Role
named "Administrator" that every self-registered organisation's own admin holds, not
by anything platform-scoped.

Found while fixing the vendor-catalogue findings in the refuter review of PR 428, via
the targeted search the brief asked for (``require_roles`` usages touching vendor
import/delete specifically, outside ``vendor_routes.py`` itself). Each now also
requires ``@platform_admin_required``.

``bulk_delete_vendors`` (``DELETE /api/vendors/bulk``) is the odd one out: it had no
role check of any kind, only ``@login_required`` -- any authenticated user, admin or
not, could bulk-delete vendors. It gets ``@platform_admin_required`` as its sole
added gate (there was no existing role check to stack alongside).
"""

from __future__ import annotations

import uuid

import pytest


def _user(db_session, org, *, platform=False):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        pytest.skip("no Administrator role seeded in this database")
    user = User(
        email=f"vendorroles-{uuid.uuid4().hex[:6]}@example.test",
        first_name="VendorRoles",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        role=role,
    )
    user.password = uuid.uuid4().hex
    user.is_org_admin = True
    user.is_platform_admin = platform
    db_session.add(user)
    db_session.flush()
    return user


def _login(db_session, client, login_as, user_id):
    from app.models.user import User

    db_session.expunge_all()
    login_as(client, db_session.get(User, user_id))


# (method, path) -- vendor_id 1 never needs to exist: the authorisation decorator
# runs, and refuses, before the view function body ever looks a vendor up.
ROUTES = [
    ("delete", "/api/vendors/1"),
    ("delete", "/api/vendors/bulk"),
    ("post", "/vendors/1/delete"),
    ("get", "/vendors/import"),
    ("get", "/vendor-management/import"),
    ("delete", "/vendor-management/api/vendors/1"),
]


@pytest.mark.parametrize("method,path", ROUTES)
def test_a_tenant_administrator_with_the_admin_role_is_refused(
    app, db_session, make_org, client, login_as, method, path
):
    org_a = make_org("vendorroles-a")
    org_b = make_org("vendorroles-b")
    tenant_admin_a = _user(db_session, org_a)
    tenant_admin_b = _user(db_session, org_b)
    db_session.commit()

    for admin_id in (tenant_admin_a.id, tenant_admin_b.id):
        _login(db_session, client, login_as, admin_id)
        response = getattr(client, method)(path)
        assert response.status_code == 403, f"{method.upper()} {path} should refuse a tenant admin"


@pytest.mark.parametrize("method,path", ROUTES)
def test_a_platform_administrator_is_not_refused(
    app, db_session, make_org, client, login_as, method, path
):
    org_a = make_org("vendorroles-platform")
    platform_admin = _user(db_session, org_a, platform=True)
    db_session.commit()

    _login(db_session, client, login_as, platform_admin.id)
    response = getattr(client, method)(path)

    assert response.status_code not in (401, 403)

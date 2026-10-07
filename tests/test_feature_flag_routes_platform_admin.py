"""Feature flags are platform-wide configuration: only a platform admin may read or change them.

``FeatureFlag`` has no tenant column: one row switches a feature for every organisation. The routes
were guarded by ``@admin_required`` (``Permission.ADMINISTER``), which an organisation's own
administrator role holds, so a tenant administrator could disable, edit, create or delete a flag
for every other tenant. The intent is recorded in ``test_admin_nav_offers_only_permitted_links``
("changes platform-wide behaviour, so ... legitimately narrower than 'can see the admin
console'"); the routes did not enforce it. They now use ``@platform_admin_required``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text


def _user(db_session, org, *, role_name="Administrator", platform=False):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        pytest.skip("no %s role seeded in this database" % role_name)
    user = User(email=f"ff-{uuid.uuid4().hex[:6]}@example.test", first_name="Flag", last_name="Tester",
                organization_id=org.id, confirmed=True, role=role)
    user.password = uuid.uuid4().hex
    user.is_org_admin = True
    user.is_platform_admin = platform
    db_session.add(user)
    db_session.flush()
    return user


def _flag(db_session):
    from app.models.feature_flags import FeatureFlag

    flag = FeatureFlag(key=f"probe_{uuid.uuid4().hex[:8]}", name="Probe flag",
                       feature_type="functionality", enabled=True)
    db_session.add(flag)
    db_session.flush()
    return flag


def _enabled(db_session, flag_id):
    return db_session.execute(text("select enabled from feature_flags where id = :i"), {"i": flag_id}).scalar()


def _world(db_session, make_org):
    org = make_org("flags")
    tenant_admin = _user(db_session, org)
    platform_admin = _user(db_session, org, platform=True)
    flag = _flag(db_session)
    db_session.commit()
    return tenant_admin.id, platform_admin.id, flag.id


def _login(db_session, client, login_as, user_id):
    from app.models.user import User

    db_session.expunge_all()
    login_as(client, db_session.get(User, user_id))


def test_a_tenant_administrator_cannot_toggle_a_platform_wide_flag(app, db_session, make_org, client, login_as):
    tenant_admin_id, _platform_id, flag_id = _world(db_session, make_org)

    _login(db_session, client, login_as, tenant_admin_id)
    response = client.post(f"/admin/feature-flags/{flag_id}/toggle")

    assert response.status_code == 403
    assert _enabled(db_session, flag_id) is True


def test_a_tenant_administrator_cannot_delete_a_platform_wide_flag(app, db_session, make_org, client, login_as):
    tenant_admin_id, _platform_id, flag_id = _world(db_session, make_org)

    _login(db_session, client, login_as, tenant_admin_id)
    response = client.post(f"/admin/feature-flags/{flag_id}/delete")

    assert response.status_code == 403
    assert db_session.execute(text("select count(*) from feature_flags where id = :i"), {"i": flag_id}).scalar() == 1


@pytest.mark.parametrize("method,path", [
    ("get", "/admin/feature-flags"),
    ("get", "/admin/feature-flags/new"),
    ("post", "/admin/feature-flags/new"),
    ("get", "/admin/feature-flags/{id}/edit"),
    ("post", "/admin/feature-flags/{id}/edit"),
    ("get", "/admin/feature-flags/discover-sidebar"),
    ("post", "/admin/feature-flags/discover-sidebar/create"),
])
def test_a_tenant_administrator_is_refused_on_every_flag_route(app, db_session, make_org, client, login_as, method, path):
    tenant_admin_id, _platform_id, flag_id = _world(db_session, make_org)

    _login(db_session, client, login_as, tenant_admin_id)
    response = getattr(client, method)(path.format(id=flag_id))

    assert response.status_code == 403


def test_a_platform_administrator_can_still_toggle_a_flag(app, db_session, make_org, client, login_as):
    _tenant_id, platform_admin_id, flag_id = _world(db_session, make_org)

    _login(db_session, client, login_as, platform_admin_id)
    response = client.post(f"/admin/feature-flags/{flag_id}/toggle")

    assert response.status_code == 200
    assert _enabled(db_session, flag_id) is False

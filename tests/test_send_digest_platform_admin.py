"""Triggering the weekly digest is a platform-wide action: only a platform admin may
fire it.

``send_digest`` (``POST /admin/send-digest``) sends weekly digest emails to every
tenant on the platform and reveals platform-wide counts in its response. It was guarded
only by ``@admin_required``, which every self-registered organisation's own
administrator holds, so any tenant admin could trigger a platform-wide mailing
(refuter review of PR 428, finding D-2). It now also requires
``@platform_admin_required``, stacked alongside the existing ``@admin_required``.
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
        email=f"digest-{uuid.uuid4().hex[:6]}@example.test",
        first_name="Digest",
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


def test_a_tenant_administrator_cannot_trigger_the_platform_wide_digest(
    app, db_session, make_org, client, login_as
):
    org_a = make_org("digest-a")
    org_b = make_org("digest-b")
    tenant_admin_a = _user(db_session, org_a)
    tenant_admin_b = _user(db_session, org_b)
    admin_ids = (tenant_admin_a.id, tenant_admin_b.id)
    db_session.commit()

    for admin_id in admin_ids:
        _login(db_session, client, login_as, admin_id)
        response = client.post("/admin/send-digest", data={"type": "both"})
        assert response.status_code == 403


def test_a_platform_administrator_is_not_refused_on_send_digest(
    app, db_session, make_org, client, login_as
):
    """Does not assert 200: the digest helpers iterate every organisation and send
    real mail through _safe_send_email when matching recipients exist, which this
    test does not want to depend on. The authorisation gate is what this fix
    changes, so that is what this test proves -- the platform admin is not turned
    away with 401/403."""
    org_a = make_org("digest-platform")
    platform_admin = _user(db_session, org_a, platform=True)
    db_session.commit()

    _login(db_session, client, login_as, platform_admin.id)
    response = client.post("/admin/send-digest", data={"type": "both"})

    assert response.status_code not in (401, 403)

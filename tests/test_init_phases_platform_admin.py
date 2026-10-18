"""Resetting the shared TOGAF ADM phase catalogue is a platform-wide action: only a
platform admin may trigger it.

``ADMPhase`` (``app/models/adm_kanban.py``) carries no ``organization_id`` --
``create_adm_phases()`` upserts one shared set of phase rows (with their TOGAF
methodology metadata) for every organisation on the platform at once. ``init_phases``
was guarded only by ``@admin_required``, which every self-registered organisation's own
administrator holds, so any tenant admin could reset/overwrite that table for every
other tenant (refuter review of PR 428, finding D-6). It now also requires
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
        email=f"init-phases-{uuid.uuid4().hex[:6]}@example.test",
        first_name="InitPhases",
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


def test_a_tenant_administrator_cannot_reset_the_global_adm_phases(
    app, db_session, make_org, client, login_as
):
    org_a = make_org("init-phases-a")
    org_b = make_org("init-phases-b")
    tenant_admin_a = _user(db_session, org_a)
    tenant_admin_b = _user(db_session, org_b)
    admin_ids = (tenant_admin_a.id, tenant_admin_b.id)
    db_session.commit()

    for admin_id in admin_ids:
        _login(db_session, client, login_as, admin_id)
        response = client.post("/adm-kanban/init-phases")
        assert response.status_code == 403


def test_a_platform_administrator_can_still_initialise_adm_phases(
    app, db_session, make_org, client, login_as
):
    org_a = make_org("init-phases-platform")
    platform_admin = _user(db_session, org_a, platform=True)
    db_session.commit()

    _login(db_session, client, login_as, platform_admin.id)
    response = client.post("/adm-kanban/init-phases")

    assert response.status_code != 403

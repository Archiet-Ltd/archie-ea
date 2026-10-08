"""The AI confidence calibration dashboard aggregates across every tenant: only a
platform admin may view it.

``ai_confidence_calibration`` (``GET /admin/ai-confidence``) queries ``AISuggestion``
with no organisation filter at all -- ``AISuggestion`` carries no ``organization_id`` --
so the counts and acceptance rates it returns are a genuine cross-tenant aggregate. It
was guarded only by ``@admin_required``, which every self-registered organisation's own
administrator holds (refuter review of PR 428, finding D-6). It now also requires
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
        email=f"aiconf-{uuid.uuid4().hex[:6]}@example.test",
        first_name="AIConf",
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


def test_a_tenant_administrator_cannot_view_the_cross_tenant_ai_confidence_dashboard(
    app, db_session, make_org, client, login_as
):
    org_a = make_org("aiconf-a")
    org_b = make_org("aiconf-b")
    tenant_admin_a = _user(db_session, org_a)
    tenant_admin_b = _user(db_session, org_b)
    admin_ids = (tenant_admin_a.id, tenant_admin_b.id)
    db_session.commit()

    for admin_id in admin_ids:
        _login(db_session, client, login_as, admin_id)
        response = client.get("/admin/ai-confidence")
        assert response.status_code == 403


def test_a_platform_administrator_can_still_view_ai_confidence_calibration(
    app, db_session, make_org, client, login_as
):
    org_a = make_org("aiconf-platform")
    platform_admin = _user(db_session, org_a, platform=True)
    db_session.commit()

    _login(db_session, client, login_as, platform_admin.id)
    response = client.get("/admin/ai-confidence")

    assert response.status_code == 200

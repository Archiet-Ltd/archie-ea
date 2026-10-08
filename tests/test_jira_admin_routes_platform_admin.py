"""The Jira connector is platform-wide configuration: only a platform admin may read or change it.

``ExternalSystem`` and ``Job`` carry no tenant column -- one Jira connector config and job queue
serve every organisation (same model pair as Abacus, see the module docstring on
``tests/test_abacus_admin_routes_platform_admin.py``). The Jira settings and push/status routes
were guarded by ``@admin_required`` (``Permission.ADMINISTER``), which any organisation's own
administrator role holds -- the same defect class already fixed for Abacus, feature flags, and
sidebar/editor content (pr312-final-check-v3.md HIGH 1). A tenant administrator could read or
change platform-wide Jira credentials, or trigger pushes/syncs that touch every tenant's data.

Both live module trees carry these routes (``app/modules/admin/v2/routes/admin_routes.py``,
guardrail-enabled, and ``app/modules/admin/routes/admin_routes.py``, the v1 modular tree) -- fixed
in both defensively, since which tree is registered depends on deploy-time flags. The webhook
receiver (``/jira-settings/webhook``) is deliberately out of scope: it has no admin_required at
all, authenticated instead by HMAC signature verification for inbound Jira events, not by the
calling user's role.
"""

from __future__ import annotations

import uuid

import pytest


def _user(db_session, org, *, role_name="Administrator", platform=False):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        pytest.skip("no %s role seeded in this database" % role_name)
    user = User(email=f"jira-{uuid.uuid4().hex[:6]}@example.test", first_name="Jira", last_name="Tester",
                organization_id=org.id, confirmed=True, role=role)
    user.password = uuid.uuid4().hex
    user.is_org_admin = True
    user.is_platform_admin = platform
    db_session.add(user)
    db_session.flush()
    return user


def _world(db_session, make_org):
    # Two organisations: Jira config is platform-wide, so a tenant admin from
    # either organisation must be refused -- proving this is a platform-admin
    # check, not an org-specific one that happens to match org A by coincidence.
    org_a = make_org("jira-a")
    org_b = make_org("jira-b")
    tenant_admin_a = _user(db_session, org_a)
    tenant_admin_b = _user(db_session, org_b)
    platform_admin = _user(db_session, org_a, platform=True)
    db_session.commit()
    return tenant_admin_a.id, tenant_admin_b.id, platform_admin.id


def _login(db_session, client, login_as, user_id):
    from app.models.user import User

    db_session.expunge_all()
    login_as(client, db_session.get(User, user_id))


@pytest.mark.parametrize("method,path", [
    ("get", "/admin/jira-settings"),
    ("post", "/admin/jira-settings"),
    ("post", "/admin/jira-settings/test-connection"),
    ("post", "/admin/jira-settings/save-env-config"),
    ("post", "/admin/jira-settings/trigger-push"),
    ("get", "/admin/jira-settings/push-status"),
    ("get", "/admin/jira-settings/kanban-push-status"),
    ("post", "/admin/jira-settings/trigger-kanban-push"),
    ("post", "/admin/jira-settings/push-epics"),
    ("post", "/admin/jira-settings/push-applications"),
    ("post", "/admin/jira-settings/push-dependencies"),
    ("get", "/admin/jira-settings/field-discovery"),
])
def test_a_tenant_administrator_is_refused_on_every_jira_settings_route(
    app, db_session, make_org, client, login_as, method, path
):
    tenant_admin_a_id, tenant_admin_b_id, _platform_id = _world(db_session, make_org)

    for admin_id in (tenant_admin_a_id, tenant_admin_b_id):
        _login(db_session, client, login_as, admin_id)
        response = getattr(client, method)(path)
        assert response.status_code == 403


def test_a_platform_administrator_can_still_reach_jira_settings(
    app, db_session, make_org, client, login_as
):
    _tenant_a_id, _tenant_b_id, platform_admin_id = _world(db_session, make_org)

    _login(db_session, client, login_as, platform_admin_id)
    response = client.get("/admin/jira-settings")

    assert response.status_code == 200

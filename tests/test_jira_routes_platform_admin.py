"""Jira push integration is platform-wide configuration: only a platform admin may read,
write, or trigger it.

``ExternalSystem`` carries no ``organization_id`` -- one "jira" row (``system_name`` is
globally unique) serves the whole platform, including ``base_url`` and the encrypted
``credentials`` token. The eleven admin routes that manage it were guarded only by
``@admin_required`` (``Permission.ADMINISTER``), which every self-registered user holds
for their own brand-new organisation -- no invitation into anyone else's org needed. A
plain tenant admin could repoint the platform's single shared Jira integration at a host
of their choosing via ``jira_settings``/``save_env_jira_config`` while the previously-saved
credential stayed in place, so the next push or test sent that real credential to an
attacker-controlled endpoint (refuter review of PR 428, finding D-1, CRITICAL).

They now also require ``@platform_admin_required``, stacked alongside the existing
``@admin_required`` rather than replacing it.

Both live module trees carry these eleven routes at the same URLs
(``app/modules/admin/v2/routes/admin_routes.py``, guardrail-enabled, and
``app/modules/admin/routes/admin_routes.py``, the v1 modular tree) -- fixed in both
defensively, since which tree is registered depends on the ``USE_ADMIN_GUARDRAILS``/
``USE_NEW_ADMIN`` flags at deploy time and this test suite cannot assume either is the one
running in production (same reasoning as ``test_abacus_admin_routes_platform_admin.py``).
Because both trees serve the identical URL under the same ``/admin`` prefix, a single
URL-routed test here exercises whichever tree this process has mounted, and passes
because both were fixed.
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
        email=f"jira-{uuid.uuid4().hex[:6]}@example.test",
        first_name="Jira",
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


def _world(db_session, make_org):
    # Two organisations, DEFECT-7 pattern: a tenant admin from EITHER org must be
    # refused, proving this is a platform-admin check and not an org-specific one
    # that happens to match org A by coincidence.
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


# The eleven routes named in D-1, each a GET or POST against a resource the route
# itself does not require to pre-exist to answer 403 -- authorisation runs before
# any of the Jira-specific body logic.
JIRA_ROUTES = [
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
]


@pytest.mark.parametrize("method,path", JIRA_ROUTES)
def test_a_tenant_administrator_is_refused_on_every_jira_route(
    app, db_session, make_org, client, login_as, method, path
):
    tenant_admin_a_id, tenant_admin_b_id, _platform_id = _world(db_session, make_org)

    for admin_id in (tenant_admin_a_id, tenant_admin_b_id):
        _login(db_session, client, login_as, admin_id)
        response = getattr(client, method)(path)
        assert response.status_code == 403, (
            f"{method.upper()} {path} should refuse a plain organisation admin "
            f"(self-registered, no session switch into any other org); got "
            f"{response.status_code}"
        )


def test_a_platform_administrator_can_still_reach_jira_settings(
    app, db_session, make_org, client, login_as
):
    """jira_settings is a pure render (no outbound Jira call) so a platform admin's
    200 here is a clean proof the fix does not also lock out the account it must
    admit."""
    _tenant_a_id, _tenant_b_id, platform_admin_id = _world(db_session, make_org)

    _login(db_session, client, login_as, platform_admin_id)
    response = client.get("/admin/jira-settings")

    assert response.status_code == 200


@pytest.mark.parametrize("method,path", JIRA_ROUTES)
def test_a_platform_administrator_is_never_refused_on_any_jira_route(
    app, db_session, make_org, client, login_as, method, path
):
    """Downstream Jira-service behaviour (no credentials configured in this test
    environment) can legitimately answer 400/500 for several of these routes --
    this test only asserts the authorisation gate itself admits a genuine platform
    admin, not that every downstream call succeeds."""
    _tenant_a_id, _tenant_b_id, platform_admin_id = _world(db_session, make_org)

    _login(db_session, client, login_as, platform_admin_id)
    response = getattr(client, method)(path)

    assert response.status_code not in (401, 403)


def test_tenant_administrator_cannot_repoint_jira_while_keeping_stored_credential(
    db_session, make_org, client, login_as
):
    """Direct proof of the D-1 attack path: a tenant admin POSTing new Jira
    settings (a different base_url, no new api_token) must be refused outright --
    not merely have the POST silently no-op -- while any existing credential stays
    untouched."""
    from app.models.models import ExternalSystem

    org_a = make_org("jira-repoint-a")
    tenant_admin = _user(db_session, org_a)
    existing = ExternalSystem(
        system_name="jira",
        system_type="alm",
        base_url="https://real-company.atlassian.net",
        credentials='{"username": "real-admin@real-company.com", "api_token": "REAL-SECRET-TOKEN"}',
        enabled=True,
    )
    db_session.add(existing)
    db_session.commit()

    _login(db_session, client, login_as, tenant_admin.id)
    response = client.post(
        "/admin/jira-settings",
        data={
            "base_url": "https://attacker-controlled.example.com",
            "username": "attacker@example.com",
            "project_key": "EVIL",
        },
    )

    assert response.status_code == 403

    db_session.expunge_all()
    reloaded = ExternalSystem.query.filter_by(system_name="jira").first()
    assert reloaded.base_url == "https://real-company.atlassian.net"
    assert "REAL-SECRET-TOKEN" in reloaded.credentials

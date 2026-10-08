"""D-4 (admin-rbac-active-org continuation): ``app/security/rbac.py``'s
``RBACManager.check_permission`` granted every permission on every resource
domain via ``user.is_admin()`` -- a global ``Permission.ADMINISTER`` flag,
independent of which organisation is active in the session
(``g.current_org_id``). Since every self-registered user is Administrator of
their own organisation, a user who merely accepted a read-only Viewer
invitation into a victim organisation and switched their session into it was
granted every permission (including DELETE and ADMIN, which the non-admin
baseline never grants) on every domain there too -- the exact bug
``admin_required``/``org_admin_required`` already fix elsewhere in this PR.
``_get_role_permissions`` carried the identical, independently-reachable
escalation (``role.permissions == Permission.ADMINISTER`` / ``role.name ==
"Administrator"``) and is fixed the same way.

No route reachable over HTTP today requires ``Permission.DELETE`` or
``Permission.ADMIN`` via this mechanism (the three real call sites in
app/routes/enterprise_api.py all require READ or WRITE, which the non-admin
baseline already grants to every authenticated user regardless of org
standing -- a separate, broader, pre-existing design question this fix does
not touch), so this is exercised directly against ``rbac_manager`` rather
than through an HTTP request, matching
tests/test_admin_rbac_active_org_enforcement.py's own
``test_admin_required_denies_anonymous_without_crashing`` for the same
reason: a future route requiring a tier only this bypass could grant must
still get it right.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org, *, role, is_platform_admin=False):
    from app.models.user import User

    user = User(
        email=f"srp-{uuid.uuid4().hex[:8]}@example.test",
        first_name="SRP",
        last_name="Sweep",
        organization_id=org.id,
        confirmed=True,
        role=role,
        is_platform_admin=is_platform_admin,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _switch_session(client, org_id):
    switched = client.post(
        "/account/switch-organization",
        data={"organization_id": str(org_id)},
        follow_redirects=True,
    )
    assert switched.status_code == 200, (
        f"fixture setup: switching the active session failed ({switched.status_code})"
    )


def test_switched_org_viewer_is_refused_delete_permission(
    app, db_session, make_org, client, login_as
):
    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation
    from app.models.user import Role
    from app.security.rbac import Permission, ResourceDomain, rbac_manager

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        pytest.skip("no Administrator role seeded in this database")

    home_org = make_org("srp-home")
    victim_org = make_org("srp-victim")

    attacker = _make_user(db_session, home_org, role=admin_role)
    inviter = _make_user(db_session, victim_org, role=admin_role)
    invitation, _created = PendingInvitation.create_for(
        victim_org.id, attacker.id, "viewer", invited_by_id=inviter.id
    )
    db_session.commit()

    login_as(client, attacker)
    accepted = client.post(
        f"/account/invitation/{invitation.id}/accept", follow_redirects=False
    )
    assert accepted.status_code == 302
    assert OrgRole.get_role(victim_org.id, attacker.id) == "viewer"

    _switch_session(client, victim_org.id)

    has_delete = rbac_manager.check_permission(
        attacker, ResourceDomain.ARCHITECTURE, Permission.DELETE
    )
    assert has_delete is False, (
        f"a Viewer of the active organisation ({victim_org.slug}) was granted "
        f"DELETE permission via RBACManager.check_permission"
    )


def test_the_active_orgs_own_admin_has_delete_permission(
    app, db_session, make_org, client, login_as
):
    from app.models.user import Role
    from app.security.rbac import Permission, ResourceDomain, rbac_manager

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        pytest.skip("no Administrator role seeded in this database")

    org = make_org("srp-own-admin")
    admin = _make_user(db_session, org, role=admin_role)
    db_session.commit()

    login_as(client, admin)
    # A real request establishes g.current_org_id via the actual hook.
    client.get("/account/manage")

    has_delete = rbac_manager.check_permission(
        admin, ResourceDomain.ARCHITECTURE, Permission.DELETE
    )
    assert has_delete is True, (
        "an ordinary administrator was refused DELETE permission in their own "
        "organisation via RBACManager.check_permission"
    )


def test_a_platform_admin_has_delete_permission_in_any_org(
    app, db_session, make_org, client, login_as
):
    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation
    from app.models.user import Role
    from app.security.rbac import Permission, ResourceDomain, rbac_manager

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        pytest.skip("no Administrator role seeded in this database")

    home_org = make_org("srp-platform-home")
    victim_org = make_org("srp-platform-victim")

    platform_admin = _make_user(
        db_session, home_org, role=admin_role, is_platform_admin=True
    )
    inviter = _make_user(db_session, victim_org, role=admin_role)
    invitation, _created = PendingInvitation.create_for(
        victim_org.id, platform_admin.id, "viewer", invited_by_id=inviter.id
    )
    db_session.commit()

    login_as(client, platform_admin)
    accepted = client.post(
        f"/account/invitation/{invitation.id}/accept", follow_redirects=False
    )
    assert accepted.status_code == 302
    assert OrgRole.get_role(victim_org.id, platform_admin.id) == "viewer"

    _switch_session(client, victim_org.id)

    has_delete = rbac_manager.check_permission(
        platform_admin, ResourceDomain.ARCHITECTURE, Permission.DELETE
    )
    assert has_delete is True, (
        "a platform admin was refused DELETE permission in a tenant "
        "organisation via RBACManager.check_permission"
    )


def test_an_ordinary_non_admin_still_gets_the_baseline_read_write(
    app, db_session, make_org, client, login_as
):
    """Regression guard: this fix only removes the ADMIN escalation: the
    unconditional READ|WRITE baseline every authenticated user gets on
    ARCHITECTURE (a separate, broader, pre-existing design choice this fix
    does not touch) must be unaffected.
    """
    from app.models.user import Role
    from app.security.rbac import Permission, ResourceDomain, rbac_manager

    architect_role = Role.query.filter_by(name="Architect").first()
    if architect_role is None:
        pytest.skip("no Architect role seeded in this database")

    org = make_org("srp-baseline")
    non_admin = _make_user(db_session, org, role=architect_role)
    db_session.commit()
    assert non_admin.is_admin() is False, "fixture setup must NOT grant admin"

    login_as(client, non_admin)
    client.get("/account/manage")

    assert rbac_manager.check_permission(
        non_admin, ResourceDomain.ARCHITECTURE, Permission.READ
    ) is True
    assert rbac_manager.check_permission(
        non_admin, ResourceDomain.ARCHITECTURE, Permission.WRITE
    ) is True
    assert rbac_manager.check_permission(
        non_admin, ResourceDomain.ARCHITECTURE, Permission.DELETE
    ) is False

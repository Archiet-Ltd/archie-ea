"""D-4 (admin-rbac-active-org continuation): ``app/utils/rbac.py``'s
``_get_user_role`` mapped a user to the ``"org_admin"`` hierarchy level from
``user.is_admin()`` -- a global ``Permission.ADMINISTER`` flag, independent of
which organisation is active in the session. Since every self-registered
user is Administrator of their own organisation, a user who merely accepted
a read-only Viewer invitation into a victim organisation and switched their
session into it was mapped to ``org_admin`` there too, satisfying every
``@require_role(...)`` route gated at or below that level (the four
``app/api/archimate_generation_routes.py`` ArchiMate-generation endpoints,
``_GENERATION_ROLES = ("architect", "org_admin", "super_admin")``) -- the
exact bug ``admin_required``/``org_admin_required`` already fix elsewhere in
this PR.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org, *, role, is_platform_admin=False):
    from app.models.user import User

    user = User(
        email=f"urr-{uuid.uuid4().hex[:8]}@example.test",
        first_name="URR",
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


def test_switched_org_viewer_is_refused_an_org_admin_level_route(
    app, db_session, make_org, client, login_as
):
    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation
    from app.models.user import Role

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        pytest.skip("no Administrator role seeded in this database")

    home_org = make_org("urr-home")
    victim_org = make_org("urr-victim")

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

    response = client.post(
        "/api/archimate/generate/from-vendors", json={"dry_run": True}
    )
    assert response.status_code == 403, (
        f"a Viewer of the active organisation ({victim_org.slug}) reached an "
        f"org_admin-level @require_role route ({response.status_code}), "
        f"expected 403"
    )


def test_the_active_orgs_own_admin_passes_the_role_check(
    app, db_session, make_org, client, login_as
):
    from app.models.user import Role

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        pytest.skip("no Administrator role seeded in this database")

    org = make_org("urr-own-admin")
    admin = _make_user(db_session, org, role=admin_role)
    db_session.commit()

    login_as(client, admin)
    response = client.post(
        "/api/archimate/generate/from-vendors", json={"dry_run": True}
    )
    assert response.status_code != 403, (
        f"an ordinary administrator was refused their own organisation's "
        f"org_admin-level route by the role check ({response.status_code})"
    )


def test_a_platform_admin_passes_the_role_check_in_any_org(
    app, db_session, make_org, client, login_as
):
    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation
    from app.models.user import Role

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        pytest.skip("no Administrator role seeded in this database")

    home_org = make_org("urr-platform-home")
    victim_org = make_org("urr-platform-victim")

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

    response = client.post(
        "/api/archimate/generate/from-vendors", json={"dry_run": True}
    )
    assert response.status_code != 403, (
        f"a platform admin was refused a tenant organisation's org_admin-level "
        f"route by the role check ({response.status_code})"
    )



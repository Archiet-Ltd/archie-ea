"""D-4 (admin-rbac-active-org continuation): ``governance_gate_reader_required``
(app/_decorators_base.py) used to check only ``current_user.can(Permission.
ADMINISTER)`` -- a global Role flag, independent of which organisation is
active in the session -- OR a global ``enterprise_role == "security_architect"``
persona flag, equally independent of the active organisation. Both guard
``/admin/audit-log`` (another organisation's audit trail, including export)
and the governance-gates read endpoints.

Since every self-registered user is Administrator of their own organisation,
and a security-architect persona is just a profile preference with no
per-organisation grant behind it, either flag alone was enough for a user who
merely accepted a read-only Viewer invitation into a victim organisation,
switched their session into it, to read and export that organisation's
audit trail.

Fixed the same shape as ``admin_required``/``org_admin_required``: resolve
authority against ``g.current_org_id`` via
``rbac_service.is_org_admin``/``is_platform_admin``. The security-architect
persona has no per-organisation row to check, so it is scoped to the user's
own home organisation instead -- the one place that flag is actually
anchored -- rather than removed outright.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org, *, role, enterprise_role=None, is_platform_admin=False):
    from app.models.user import User

    user = User(
        email=f"gga-{uuid.uuid4().hex[:8]}@example.test",
        first_name="GGA",
        last_name="Sweep",
        organization_id=org.id,
        confirmed=True,
        role=role,
        enterprise_role=enterprise_role,
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


def test_switched_org_viewer_is_refused_the_victim_orgs_audit_log(
    app, db_session, make_org, client, login_as
):
    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation
    from app.models.user import Role

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        pytest.skip("no Administrator role seeded in this database")

    home_org = make_org("gga-home")
    victim_org = make_org("gga-victim")

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

    for path in ("/admin/audit-log", "/admin/governance-gates", "/admin/api/governance-gates"):
        response = client.get(path)
        assert response.status_code == 403, (
            f"a Viewer of the active organisation ({victim_org.slug}) reached "
            f"{path} ({response.status_code}), expected 403"
        )


def test_the_active_orgs_own_admin_is_admitted(app, db_session, make_org, client, login_as):
    from app.models.user import Role

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        pytest.skip("no Administrator role seeded in this database")

    org = make_org("gga-own-admin")
    admin = _make_user(db_session, org, role=admin_role)
    db_session.commit()

    login_as(client, admin)
    response = client.get("/admin/audit-log")
    assert response.status_code == 200, (
        f"an ordinary administrator was refused their own organisation's "
        f"audit log ({response.status_code})"
    )


def test_a_platform_admin_is_admitted_to_any_orgs_audit_log(
    app, db_session, make_org, client, login_as
):
    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation
    from app.models.user import Role

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        pytest.skip("no Administrator role seeded in this database")

    home_org = make_org("gga-platform-home")
    victim_org = make_org("gga-platform-victim")

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

    response = client.get("/admin/audit-log")
    assert response.status_code == 200, (
        f"a platform admin was refused a tenant organisation's audit log "
        f"({response.status_code})"
    )


def test_security_architect_persona_reads_their_own_orgs_audit_log_without_admin(
    app, db_session, make_org, client, login_as
):
    """The legitimate case this persona bypass exists for: a non-admin
    (Architect role -- GENERAL permission, not ADMINISTER) whose
    ``enterprise_role`` is ``security_architect`` can read their OWN
    organisation's audit log, never having switched organisations.
    """
    from app.models.user import Role

    architect_role = Role.query.filter_by(name="Architect").first()
    if architect_role is None:
        pytest.skip("no Architect role seeded in this database")

    org = make_org("gga-sec-architect-home")
    sec_architect = _make_user(
        db_session, org, role=architect_role, enterprise_role="security_architect"
    )
    db_session.commit()
    assert sec_architect.is_admin() is False, "fixture setup must NOT grant admin"

    login_as(client, sec_architect)
    response = client.get("/admin/audit-log")
    assert response.status_code == 200, (
        f"a non-admin security_architect was refused their own organisation's "
        f"audit log ({response.status_code})"
    )


def test_security_architect_persona_does_not_reach_a_switched_into_orgs_audit_log(
    app, db_session, make_org, client, login_as
):
    """The gap this fix closes: enterprise_role is a global persona flag with
    no per-organisation grant behind it, so without the home-organisation
    restriction a security_architect who merely accepted a Viewer invitation
    into another organisation and switched into it would read that
    organisation's audit log too.
    """
    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation
    from app.models.user import Role

    architect_role = Role.query.filter_by(name="Architect").first()
    admin_role = Role.query.filter_by(name="Administrator").first()
    if architect_role is None or admin_role is None:
        pytest.skip("required roles not seeded in this database")

    home_org = make_org("gga-sec-architect-home2")
    victim_org = make_org("gga-sec-architect-victim")

    sec_architect = _make_user(
        db_session, home_org, role=architect_role, enterprise_role="security_architect"
    )
    inviter = _make_user(db_session, victim_org, role=admin_role)
    invitation, _created = PendingInvitation.create_for(
        victim_org.id, sec_architect.id, "viewer", invited_by_id=inviter.id
    )
    db_session.commit()

    login_as(client, sec_architect)
    accepted = client.post(
        f"/account/invitation/{invitation.id}/accept", follow_redirects=False
    )
    assert accepted.status_code == 302
    assert OrgRole.get_role(victim_org.id, sec_architect.id) == "viewer"

    _switch_session(client, victim_org.id)

    response = client.get("/admin/audit-log")
    assert response.status_code == 403, (
        f"a security_architect persona reached a switched-into organisation's "
        f"audit log ({response.status_code}), expected 403"
    )

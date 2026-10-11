"""D-4 (admin-rbac-active-org continuation): ``role_required``
(app/_decorators_base.py) let any admin bypass its ``enterprise_role`` check
entirely via ``current_user.is_admin()`` -- a global ``Permission.ADMINISTER``
flag, independent of which organisation is active in the session. Since
every self-registered user is Administrator of their own organisation, a
user who merely accepted a read-only Viewer invitation into a victim
organisation and switched their session into it bypassed every
``@role_required(...)`` route there too -- the exact bug
``admin_required``/``org_admin_required`` already fix elsewhere in this PR.

Exercised here against ``GET /applications/ownership-coverage``
(``@role_required(ROLE_CTO, ROLE_PORTFOLIO_MANAGER)``,
app/modules/applications/routes/coverage_routes.py), one of the three real
call sites of this decorator.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org, *, role, enterprise_role=None, is_platform_admin=False):
    from app.models.user import User

    user = User(
        email=f"rr-{uuid.uuid4().hex[:8]}@example.test",
        first_name="RR",
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


def test_switched_org_viewer_is_refused_a_role_required_route(
    app, db_session, make_org, client, login_as
):
    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation
    from app.models.user import Role

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        pytest.skip("no Administrator role seeded in this database")

    home_org = make_org("rr-home")
    victim_org = make_org("rr-victim")

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

    response = client.get("/applications/ownership-coverage")
    assert response.status_code == 403, (
        f"a Viewer of the active organisation ({victim_org.slug}) reached a "
        f"@role_required route via the is_admin() bypass ({response.status_code}), "
        f"expected 403"
    )


def test_the_active_orgs_own_admin_passes_the_bypass(
    app, db_session, make_org, client, login_as
):
    from app.models.user import Role

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        pytest.skip("no Administrator role seeded in this database")

    org = make_org("rr-own-admin")
    admin = _make_user(db_session, org, role=admin_role)
    db_session.commit()

    login_as(client, admin)
    response = client.get("/applications/ownership-coverage")
    assert response.status_code != 403, (
        f"an ordinary administrator was refused their own organisation's "
        f"@role_required route ({response.status_code})"
    )


def test_a_platform_admin_passes_the_bypass_in_any_org(
    app, db_session, make_org, client, login_as
):
    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation
    from app.models.user import Role

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        pytest.skip("no Administrator role seeded in this database")

    home_org = make_org("rr-platform-home")
    victim_org = make_org("rr-platform-victim")

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

    response = client.get("/applications/ownership-coverage")
    assert response.status_code != 403, (
        f"a platform admin was refused a tenant organisation's "
        f"@role_required route ({response.status_code})"
    )


def test_the_legitimate_persona_still_works_without_admin(
    app, db_session, make_org, client, login_as
):
    """Regression guard: a non-admin CTO persona (the route's actual,
    intended audience) must still be admitted -- this fix only tightens the
    admin BYPASS, not the enterprise_role check itself.
    """
    from app.models.user import ROLE_CTO, Role

    architect_role = Role.query.filter_by(name="Architect").first()
    if architect_role is None:
        pytest.skip("no Architect role seeded in this database")

    org = make_org("rr-cto-persona")
    cto = _make_user(db_session, org, role=architect_role, enterprise_role=ROLE_CTO)
    db_session.commit()
    assert cto.is_admin() is False, "fixture setup must NOT grant admin"

    login_as(client, cto)
    response = client.get("/applications/ownership-coverage")
    assert response.status_code != 403, (
        f"a non-admin CTO persona was refused their own organisation's "
        f"@role_required route ({response.status_code})"
    )

"""Two-organisation isolation for the admin team family (blueprint ``team``).

The team page at ``GET /admin/team`` lists the members of the caller's ACTIVE
organisation, resolved from ``session[ACTIVE_ORG_SESSION_KEY]`` and NOT from
``current_user.organization_id`` (see app/middleware/tenant_context.py).

This file proves the boundary the way the app really crosses it: the signed-in
administrator is a NATIVE member of organisation B and also an OrgRole member of
organisation A, and is switched INTO organisation A through the real session
switch (AccountService.switch_active_organization, the POST
/account/switch-organization route). Once switched, organisation B's own
``/admin/team`` route must list organisation A's distinctively named member,
because ``current_org()`` follows the switched session. A bug where
``current_org()`` read ``user.organization_id`` would list organisation B's
members instead, and these tests would FAIL -- which is the point. The
assertions name the specific member (organisation A's distinctive e-mail), never
a bare substring.

Follows tests/test_rationalization_benefits_aggregates_tenant.py and
tests/smoke/test_multi_org_account_switching_journey.py: the shared db_session /
make_org / login_as / client fixtures from tests/conftest.py, two real
organisations with real ``User`` rows, and the production org-switch mechanism.
This is the genuine two-organisation isolation test the ASVS register's
admin-team V4 row cites.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _switch_into(client, org_id):
    """Switch the signed-in client INTO *org_id* via the real session switch."""
    return client.post(
        "/account/switch-organization",
        data={"organization_id": str(org_id)},
        follow_redirects=False,
    )


def _grant_membership(db_session, user, org_id, role="viewer"):
    """Give *user* an OrgRole in *org_id* so they may switch into it."""
    from app.models.org_role import OrgRole

    OrgRole.set_role(org_id, user.id, role)
    db_session.commit()


# --------------------------------------------------------------------- #
# Helpers                                                                #
# --------------------------------------------------------------------- #


def _make_user(db_session, org_id, label, *, is_org_admin=False, email=None):
    from app.models.user import Permission, Role, User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=email or f"{label.lower()}-{suffix}@example.com",
        first_name=label,
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        is_org_admin=is_org_admin,
    )
    db_session.add(user)
    db_session.flush()

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(
            name="Administrator",
            permissions=Permission.ADMINISTER,
            index="main",
            default=False,
        )
        db_session.add(role)
        db_session.flush()
    user.role = role
    db_session.flush()
    return user


@pytest.fixture
def org_a(make_org):
    return make_org("team-a")


@pytest.fixture
def org_b(make_org):
    return make_org("team-b")


@pytest.fixture
def admin_b(db_session, org_b):
    return _make_user(db_session, org_b.id, "AdminB", is_org_admin=True)


@pytest.fixture
def org_a_member(db_session, org_a):
    """Organisation A has a member with a distinctive e-mail that must never
    appear on another organisation's team page."""
    email = f"org-a-secret-member-{uuid.uuid4().hex[:8]}@example.com"
    member = _make_user(db_session, org_a.id, "OrgAMember", email=email)
    db_session.commit()
    return member


@pytest.fixture
def client(app):
    return app.test_client()


# --------------------------------------------------------------------- #
# GET /admin/team -- the member list is tenant-scoped                    #
# --------------------------------------------------------------------- #


class TestTeamPageIsTwoOrgIsolated:
    def test_team_page_follows_the_switched_session_into_org_a(
        self, db_session, org_a, org_b, org_a_member, admin_b, client, login_as
    ):
        """The administrator is a native member of organisation B and an OrgRole
        member of organisation A. After switching INTO organisation A through the
        real session switch, organisation B's own ``/admin/team`` route must list
        organisation A's distinctively named member -- because ``current_org()``
        follows the switched session, not ``user.organization_id``.

        DB-ONLY (CI): this request needs PostgreSQL; it cannot run in the
        sandbox. A bug where ``current_org()`` read ``user.organization_id``
        would list organisation B's members and FAIL this assertion.
        """
        _grant_membership(db_session, admin_b, org_a.id)
        login_as(client, admin_b)

        switch = _switch_into(client, org_a.id)
        assert switch.status_code in (302, 303), (
            "the session switch into organisation A should be accepted"
        )

        resp = client.get("/admin/team")
        assert resp.status_code == 200
        body = resp.data.decode()

        # Named-row assertion: the specific organisation A member must appear,
        # because the active organisation is now organisation A.
        assert org_a_member.email in body, (
            "current_org() did not follow the switched session into "
            "organisation A: organisation A's named member was not listed."
        )

    def test_org_b_team_page_without_switch_lists_its_own_admin(
        self, db_session, org_a, org_b, org_a_member, admin_b, client, login_as
    ):
        """No-regression control: with NO switch, organisation B's administrator
        on organisation B's own ``/admin/team`` route sees organisation B's own
        administrator and never organisation A's named member.

        DB-ONLY (CI): needs PostgreSQL.
        """
        login_as(client, admin_b)
        resp = client.get("/admin/team")

        assert resp.status_code == 200
        body = resp.data.decode()
        assert admin_b.email in body, (
            "organisation B's own administrator should still be listed."
        )
        assert org_a_member.email not in body, (
            "TENANT LEAK: organisation B's team page listed organisation A's "
            "member without any switch."
        )

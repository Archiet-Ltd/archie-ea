"""Two-organisation isolation for the admin team family (blueprint ``team``).

The team page at ``GET /admin/team`` lists the members of
``current_user.organization_id`` -- ``User.query.filter_by(organization_id=
org_id)`` in app/modules/admin/team_routes.py::_render_team. The tenant boundary
is that ``organization_id`` filter. This file proves it by building TWO
organisations, giving organisation A a distinctively named member, and asserting
that organisation B's administrator, driving organisation B's own ``/admin/team``
route, never sees organisation A's member -- while organisation B still sees its
own.

Follows tests/test_rationalization_benefits_aggregates_tenant.py: the shared
db_session / make_org / login_as / client fixtures from tests/conftest.py, two
real organisations with real ``User`` rows. This is the genuine two-organisation
isolation test the ASVS register's admin-team V4 row cites.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


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
    def test_org_b_team_page_never_lists_org_a_member(
        self, db_session, org_a_member, admin_b, client, login_as
    ):
        """Organisation B's administrator, on organisation B's own
        ``/admin/team`` route, must not see organisation A's member."""
        login_as(client, admin_b)
        resp = client.get("/admin/team")

        assert resp.status_code == 200
        body = resp.data.decode()
        assert org_a_member.email not in body, (
            "TENANT LEAK: organisation B's team page listed organisation A's "
            "member."
        )

    def test_org_b_team_page_still_lists_its_own_admin(
        self, db_session, org_a_member, admin_b, client, login_as
    ):
        """No-regression control: organisation B's own administrator still
        appears on organisation B's team page."""
        login_as(client, admin_b)
        resp = client.get("/admin/team")

        assert resp.status_code == 200
        body = resp.data.decode()
        assert admin_b.email in body, (
            "organisation B's own administrator should still be listed."
        )

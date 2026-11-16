"""Whoever administers an organisation opens that organisation's administrator
pages, whatever their account-wide role.

Two real organisations. Four kinds of person are asked for Connections, a page
only administrators open for a persona that is not one of the connecting
architects: an administrator by organisation role whose account-wide role is
Architect, an Administrator of their own organisation, a plain member, and an
administrator of the other organisation.
"""

from __future__ import annotations

import uuid

import pytest

PAGE = "/admin/connectors/github"
FORM = "github-connect-form"


def _person(db_session, home, *, administrator_role=False):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        confirmed=True,
        email="second-org-%s@example.test" % uuid.uuid4().hex[:8],
        first_name="Case",
        last_name="Person",
        organization_id=home.id,
        enterprise_role="portfolio_manager",
        password_hash="scrypt:dummy",
    )
    user.role = Role.query.filter_by(name="Administrator" if administrator_role else "Architect").one()
    db_session.add(user)
    db_session.flush()
    return user


def _make_org_admin(db_session, org, user):
    from app.models.org_role import OrgRole

    OrgRole.set_role(org.id, user.id, "org_admin", granted_by_id=user.id)
    db_session.flush()


def _open(client, login_as, user, org):
    from app.middleware.tenant_context import ACTIVE_ORG_SESSION_KEY

    login_as(client, user)
    with client.session_transaction() as sess:
        sess[ACTIVE_ORG_SESSION_KEY] = org.id
    return client.get(PAGE)


@pytest.fixture
def orgs(db_session, make_org):
    a, b = make_org("second-a"), make_org("second-b")
    # Home in A as an Architect; administrator of B by organisation role only.
    traveller = _person(db_session, a)
    _make_org_admin(db_session, b, traveller)
    # Home in B as an Architect; administrator of B only through B's role row.
    b_admin = _person(db_session, b)
    _make_org_admin(db_session, b, b_admin)
    a_member = _person(db_session, a)
    a_administrator = _person(db_session, a, administrator_role=True)
    return dict(a=a, b=b, traveller=traveller, b_admin=b_admin, a_member=a_member,
                a_administrator=a_administrator)


def test_administrator_by_organisation_role_opens_the_page_in_that_organisation(orgs, client, login_as):
    resp = _open(client, login_as, orgs["traveller"], orgs["b"])
    assert resp.status_code == 200
    assert FORM in resp.get_data(as_text=True)


def test_same_person_is_refused_where_they_are_only_a_member(orgs, client, login_as):
    resp = _open(client, login_as, orgs["traveller"], orgs["a"])
    assert resp.status_code == 403
    assert FORM not in resp.get_data(as_text=True)


def test_administrator_role_opens_the_page_in_their_own_organisation(orgs, client, login_as):
    resp = _open(client, login_as, orgs["a_administrator"], orgs["a"])
    assert resp.status_code == 200
    assert FORM in resp.get_data(as_text=True)


def test_member_is_refused_in_their_own_organisation(orgs, client, login_as):
    resp = _open(client, login_as, orgs["a_member"], orgs["a"])
    assert resp.status_code == 403


def test_administrator_of_one_organisation_is_refused_in_the_other(orgs, client, login_as):
    # b_admin administers B and has no standing in A: asking for A falls back to B.
    resp = _open(client, login_as, orgs["b_admin"], orgs["a"])
    from flask import g

    assert getattr(g, "current_org_id", None) != orgs["a"].id
    assert resp.status_code == 200  # served as B, their own organisation
    # And the Administrator of A, switched to nothing in B, never reads B.
    resp = _open(client, login_as, orgs["a_administrator"], orgs["b"])
    assert getattr(g, "current_org_id", None) == orgs["a"].id

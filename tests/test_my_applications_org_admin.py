"""An organisation administrator can open the four My Applications pages.

The pages were gated on the application manager persona alone, so the person
who assigns application owners got a 403 on all four. They now admit the
administrator of the organisation as well; the pages still list only what the
signed-in user owns, so an administrator who owns nothing sees an empty state
that says what to do next, and never another user's applications.

Two organisations throughout: an application owned in the other organisation
must not appear for this organisation's administrator.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

PAGES = (
    "/my-applications/",
    "/my-applications/list",
    "/my-applications/health",
    "/my-applications/roadmap",
)


def _user(db_session, org, label, enterprise_role, administrator=False):
    from app.models.user import Permission, Role, User

    user = User(
        email=f"my-apps-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name="My",
        last_name=label,
        organization_id=org.id,
        confirmed=True,
        enterprise_role=enterprise_role,
    )
    db_session.add(user)
    db_session.flush()
    if administrator:
        role = Role.query.filter(Role.name == "Administrator").first()
        if role is None:
            role = Role(name="Administrator", permissions=Permission.ADMINISTER)
            db_session.add(role)
            db_session.flush()
        elif (role.permissions & Permission.ADMINISTER) != Permission.ADMINISTER:
            role.permissions = Permission.ADMINISTER
        user.role = role
        db_session.flush()
    return user


def _own_an_application(db_session, org, user):
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent

    component = ApplicationComponent(
        name=f"Owned elsewhere {uuid.uuid4().hex[:8]}",
        organization_id=org.id,
        lifecycle_status="active",
    )
    db_session.add(component)
    db_session.flush()
    db_session.add(
        ApplicationOwner(
            application_id=component.id,
            user_id=user.id,
            organization_id=org.id,
            ownership_type="primary",
        )
    )
    db_session.flush()
    return component


@pytest.mark.parametrize("path", PAGES)
def test_organisation_administrator_reaches_the_page_with_an_empty_state(
    path, db_session, make_org, client, login_as
):
    org_a = make_org("my-apps-admin-a")
    org_b = make_org("my-apps-admin-b")
    admin = _user(db_session, org_a, "admin", "enterprise_architect", administrator=True)
    other_manager = _user(db_session, org_b, "manager", "application_manager")
    foreign = _own_an_application(db_session, org_b, other_manager)

    login_as(client, admin)
    response = client.get(path)

    assert response.status_code == 200, response.get_data(as_text=True)[:1500]
    html = response.get_data(as_text=True)
    assert 'data-testid="my-applications-empty-title"' in html
    next_step = html.split('data-testid="my-applications-empty-next-step"')[1][:400]
    assert "add yourself as an owner" in next_step
    assert 'href="/applications/"' in html
    assert foreign.name not in html


@pytest.mark.parametrize("path", PAGES)
def test_a_role_that_is_neither_owner_nor_administrator_is_still_refused(
    path, db_session, make_org, client, login_as
):
    org = make_org("my-apps-refused")
    cto = _user(db_session, org, "cto", "cto")

    login_as(client, cto)

    assert client.get(path).status_code == 403


def test_an_administrator_of_another_organisation_does_not_gain_access_here(
    db_session, make_org, client, login_as
):
    """Administrator in organisation B is not an administrator of A: the
    check is per organisation, so B's administrator keeps their own access and
    a non-administrator in A keeps being refused."""
    org_a = make_org("my-apps-iso-a")
    org_b = make_org("my-apps-iso-b")
    admin_b = _user(db_session, org_b, "admin-b", "enterprise_architect", administrator=True)
    plain_a = _user(db_session, org_a, "plain-a", "enterprise_architect")

    login_as(client, admin_b)
    assert client.get("/my-applications/").status_code == 200

    login_as(client, plain_a)
    assert client.get("/my-applications/").status_code == 403

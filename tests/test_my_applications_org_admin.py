"""An organisation administrator can use the My Applications pages.

The pages were gated on the application manager persona alone, so the person
who assigns application owners got a 403 on all four. They now admit the
administrator of the organisation as well; the pages still list only what the
signed-in user owns, so an administrator who owns nothing sees an empty state
that says what to do next, and never another user's applications.

An administrator who adds themselves as an owner can go on to open the
application, edit it and ask for the health assessment, as the empty state tells
them to. Admission is the one active-organisation administrator check: a person
switched into another organisation as a viewer is refused there.

Two organisations throughout: an application owned in the other organisation
must not appear for this organisation's administrator, and two administrators who
each own an application each see only their own.
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
    assert "ask an administrator" not in html.lower(), "the empty state tells an administrator to ask one"
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


def _page_text(client, path):
    response = client.get(path)
    assert response.status_code == 200, (path, response.status_code)
    return response.get_data(as_text=True)


def test_an_administrator_who_owns_an_application_can_open_edit_and_assess_it(
    db_session, make_org, client, login_as
):
    """The path the empty state advises: add yourself as an owner, then use the card."""
    org = make_org("my-apps-admin-owner")
    admin = _user(db_session, org, "admin-owner", "enterprise_architect", administrator=True)
    component = _own_an_application(db_session, org, admin)
    db_session.commit()

    login_as(client, admin)
    assert component.name in _page_text(client, "/my-applications/")
    assert component.name in _page_text(client, "/my-applications/list")
    assert component.name in _page_text(client, f"/my-applications/app/{component.id}")
    assert client.get(f"/my-applications/app/{component.id}/edit").status_code == 200
    # The assessment may be unavailable without an AI provider; what matters is
    # that the administrator is not refused.
    assessed = client.post(f"/my-applications/api/app/{component.id}/ai-health-assessment")
    assert assessed.status_code not in (401, 403), assessed.status_code


def test_an_administrator_cannot_open_an_application_they_do_not_own(
    db_session, make_org, client, login_as
):
    org = make_org("my-apps-admin-not-owner")
    admin = _user(db_session, org, "admin-not-owner", "enterprise_architect", administrator=True)
    manager = _user(db_session, org, "owner", "application_manager")
    component = _own_an_application(db_session, org, manager)
    db_session.commit()

    login_as(client, admin)
    assert client.get(f"/my-applications/app/{component.id}").status_code == 403


def test_two_administrators_each_see_only_the_applications_they_own(
    db_session, make_org, client, login_as
):
    org_a = make_org("my-apps-two-a")
    org_b = make_org("my-apps-two-b")
    admin_a = _user(db_session, org_a, "two-admin-a", "enterprise_architect", administrator=True)
    admin_b = _user(db_session, org_b, "two-admin-b", "enterprise_architect", administrator=True)
    app_a = _own_an_application(db_session, org_a, admin_a)
    app_b = _own_an_application(db_session, org_b, admin_b)
    db_session.commit()

    for admin, mine, theirs in ((admin_a, app_a, app_b), (admin_b, app_b, app_a)):
        login_as(client, admin)
        for path in ("/my-applications/", "/my-applications/list", "/my-applications/health"):
            html = _page_text(client, path)
            assert mine.name in html, (path, "own application missing")
            assert theirs.name not in html, (path, "another organisation's application shown")
        assert client.get(f"/my-applications/app/{theirs.id}").status_code in (403, 404)


def test_an_administrator_switched_into_another_organisation_as_a_viewer_is_refused(
    db_session, make_org, client, login_as
):
    """Administrator of their own organisation A, viewer in B: on B's side they
    are not an administrator and must not pass the My Applications gate."""
    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation

    home = make_org("my-apps-switch-home")
    other = make_org("my-apps-switch-other")
    traveller = _user(db_session, home, "traveller", "enterprise_architect", administrator=True)
    inviter = _user(db_session, other, "inviter", "enterprise_architect", administrator=True)
    invitation, _created = PendingInvitation.create_for(
        other.id, traveller.id, "viewer", invited_by_id=inviter.id
    )
    db_session.commit()

    login_as(client, traveller)
    assert client.get("/my-applications/").status_code == 200  # in their own organisation
    accepted = client.post(f"/account/invitation/{invitation.id}/accept", follow_redirects=False)
    assert accepted.status_code == 302
    assert OrgRole.get_role(other.id, traveller.id) == "viewer"
    switched = client.post(
        "/account/switch-organization",
        data={"organization_id": str(other.id)},
        follow_redirects=True,
    )
    assert switched.status_code == 200

    assert client.get("/my-applications/").status_code == 403

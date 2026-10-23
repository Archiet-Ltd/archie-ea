"""My Applications opens for every role via the permission matrix (perm02s2).

The page reads the permission matrix (``read`` on ``application``) instead of a
role gate. Every role keeps read of its own records. No role gains a write.

Tests use two real organisations. User A sees only organisation A's applications
that A owns. A viewer with no owned applications gets the page with its empty
state, not a 403. The enterprise-architect role gets 200. Each test fails before
the change and passes after.
"""

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

PAGES = (
    "/my-applications/",
    "/my-applications/list",
    "/my-applications/health",
    "/my-applications/roadmap",
)


def _user(db_session, org, label, enterprise_role, org_role="viewer"):
    """Create a user with the given enterprise role and organisation role."""
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    role = Role.query.filter_by(name="User").first()
    user = User(
        email=f"perm02s2-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Perm",
        last_name=label,
        organization_id=org.id,
        role=role,
        confirmed=True,
        enterprise_role=enterprise_role,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, org_role)
    db_session.flush()
    return user


def _own_an_application(db_session, org, user, with_sunset=False):
    """Create an application owned by the user in the given organisation."""
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from datetime import date, timedelta

    component = ApplicationComponent(
        name=f"Owned {uuid.uuid4().hex[:8]}",
        organization_id=org.id,
        lifecycle_status="active",
    )
    if with_sunset:
        component.sunset_date = date.today() + timedelta(days=30)
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


def _page_text(client, path):
    response = client.get(path)
    assert response.status_code == 200, (path, response.status_code)
    return response.get_data(as_text=True)


class TestEnterpriseArchitectAccess:
    """Enterprise architect and other roles can open My Applications."""

    @pytest.mark.parametrize("path", PAGES)
    def test_enterprise_architect_reaches_page_with_empty_state(
        self, path, db_session, make_org, client, login_as
    ):
        """Enterprise architect with no owned applications sees empty state, not 403."""
        org = make_org("perm02s2-ea-empty")
        ea = _user(db_session, org, "ea-empty", "enterprise_architect", org_role="architect")

        login_as(client, ea)
        response = client.get(path)

        assert response.status_code == 200, response.get_data(as_text=True)[:1500]
        html = response.get_data(as_text=True)
        assert 'data-testid="my-applications-empty-title"' in html
        next_step = html.split('data-testid="my-applications-empty-next-step"')[1][:400]
        assert "add yourself as an owner" in next_step

    @pytest.mark.parametrize("path", PAGES)
    def test_viewer_reaches_page_with_empty_state(
        self, path, db_session, make_org, client, login_as
    ):
        """Viewer with no owned applications sees empty state, not 403."""
        org = make_org("perm02s2-viewer-empty")
        viewer = _user(db_session, org, "viewer-empty", "viewer", org_role="viewer")

        login_as(client, viewer)
        response = client.get(path)

        assert response.status_code == 200, response.get_data(as_text=True)[:1500]
        html = response.get_data(as_text=True)
        assert 'data-testid="my-applications-empty-title"' in html

    @pytest.mark.parametrize("path", PAGES)
    def test_solution_architect_reaches_page_with_empty_state(
        self, path, db_session, make_org, client, login_as
    ):
        """Solution architect with no owned applications sees empty state, not 403."""
        org = make_org("perm02s2-sa-empty")
        sa = _user(db_session, org, "sa-empty", "solution_architect", org_role="architect")

        login_as(client, sa)
        response = client.get(path)

        assert response.status_code == 200, response.get_data(as_text=True)[:1500]
        html = response.get_data(as_text=True)
        assert 'data-testid="my-applications-empty-title"' in html

    @pytest.mark.parametrize("path", PAGES)
    def test_business_architect_reaches_page_with_empty_state(
        self, path, db_session, make_org, client, login_as
    ):
        """Business architect with no owned applications sees empty state, not 403."""
        org = make_org("perm02s2-ba-empty")
        ba = _user(db_session, org, "ba-empty", "business_architect", org_role="architect")

        login_as(client, ba)
        response = client.get(path)

        assert response.status_code == 200, response.get_data(as_text=True)[:1500]
        html = response.get_data(as_text=True)
        assert 'data-testid="my-applications-empty-title"' in html


class TestTenantIsolation:
    """User A sees only organisation A's applications that A owns."""

    def test_user_sees_only_own_organisations_applications(
        self, db_session, make_org, client, login_as
    ):
        """User in org A sees only their applications in org A, not org B's."""
        org_a = make_org("perm02s2-iso-a")
        org_b = make_org("perm02s2-iso-b")

        # User A in org A owns an application
        user_a = _user(db_session, org_a, "usera", "enterprise_architect", org_role="architect")
        app_a = _own_an_application(db_session, org_a, user_a, with_sunset=True)

        # User B in org B owns an application
        user_b = _user(db_session, org_b, "userb", "enterprise_architect", org_role="architect")
        app_b = _own_an_application(db_session, org_b, user_b, with_sunset=True)

        db_session.commit()

        # User A logs in and should see only their application
        login_as(client, user_a)
        for path in PAGES:
            html = _page_text(client, path)
            assert app_a.name in html, f"{path}: own application missing"
            assert app_b.name not in html, f"{path}: another organisation's application shown"

        # User B logs in and should see only their application
        login_as(client, user_b)
        for path in PAGES:
            html = _page_text(client, path)
            assert app_b.name in html, f"{path}: own application missing"
            assert app_a.name not in html, f"{path}: another organisation's application shown"

    def test_user_cannot_open_application_they_do_not_own(
        self, db_session, make_org, client, login_as
    ):
        """User cannot open detail/edit of an application they don't own."""
        org = make_org("perm02s2-not-owner")
        owner = _user(db_session, org, "owner", "application_manager", org_role="viewer")
        viewer = _user(db_session, org, "viewer", "viewer", org_role="viewer")
        app = _own_an_application(db_session, org, owner)
        db_session.commit()

        login_as(client, viewer)
        # Detail page should be 403 (not owned)
        assert client.get(f"/my-applications/app/{app.id}").status_code == 403
        # Edit page should be 403 (not owned)
        assert client.get(f"/my-applications/app/{app.id}/edit").status_code == 403


class TestNoWriteAccessGranted:
    """No role gains a write permission through this change."""

    def test_viewer_cannot_edit_application(
        self, db_session, make_org, client, login_as
    ):
        """Viewer can read but not edit an application they own."""
        org = make_org("perm02s2-no-write")
        viewer = _user(db_session, org, "viewer-owner", "viewer", org_role="viewer")
        app = _own_an_application(db_session, org, viewer)
        db_session.commit()

        login_as(client, viewer)
        # Can read
        assert client.get(f"/my-applications/app/{app.id}").status_code == 200
        # Cannot edit
        assert client.get(f"/my-applications/app/{app.id}/edit").status_code == 403

    def test_enterprise_architect_cannot_edit_unless_owner(
        self, db_session, make_org, client, login_as
    ):
        """Enterprise architect can read but not edit unless they own it."""
        org = make_org("perm02s2-ea-no-write")
        ea = _user(db_session, org, "ea", "enterprise_architect", org_role="architect")
        manager = _user(db_session, org, "manager", "application_manager", org_role="viewer")
        app = _own_an_application(db_session, org, manager)
        db_session.commit()

        login_as(client, ea)
        # Can read list pages
        for path in PAGES:
            assert client.get(path).status_code == 200
        # Cannot open detail of app they don't own
        assert client.get(f"/my-applications/app/{app.id}").status_code == 403
        # Cannot edit
        assert client.get(f"/my-applications/app/{app.id}/edit").status_code == 403
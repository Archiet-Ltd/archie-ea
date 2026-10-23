"""The enterprise API asks the permission matrix, not the retired RBAC decorator.

Every route in /api/v2/enterprise now reads ``require_permission(action,
object_type)``. Defaults equal what the retired decorator allowed: any signed-in
account reads, only an administrator creates an architecture review workflow.
Grants belong to one organisation. A bulk-delete grant in organisation A never
lets anyone bulk-delete in organisation B.
"""

import uuid

import pytest

ARB = "/api/v2/enterprise/workflows/arb"
READS = (
    "/api/v2/enterprise/capabilities/mapping",
    "/api/v2/enterprise/vendors/products",
    "/api/v2/enterprise/applications/portfolio",
    "/api/v2/enterprise/kg/elements",
)


@pytest.fixture
def client(app):
    return app.test_client()


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"EA {label} {suffix}", slug=f"ea-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org, org_role, platform_role="User"):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    user = User(
        email=f"ea-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Ent",
        last_name=org_role,
        organization_id=org.id,
        role=Role.query.filter_by(name=platform_role).first(),
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, org_role)
    if org_role == "org_admin":
        user.grant_org_admin()
    db_session.flush()
    return user


def _application(db_session, org):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(
        name=f"EA app {uuid.uuid4().hex[:6]}",
        description="enterprise api permission test",
        organization_id=org.id,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _exists(db_session, app_id):
    from app.models.application_portfolio import ApplicationComponent

    db_session.expire_all()
    return db_session.query(ApplicationComponent).filter_by(id=app_id).first() is not None


def _post_arb(client, login_as, user):
    login_as(client, user)
    return client.post(ARB, json={"title": "Q", "reviewers": [1]})


def _stub_arb_service(monkeypatch):
    """Stand in for the service behind the ARB POST.

    The permission gate is what these tests prove. The service itself is fixed on
    the branch fix/arb-create-workflow-works, which replaces this stub with a
    real call asserting 201 and a stored workflow.
    """
    monkeypatch.setattr(
        "app.routes.enterprise_api.arb_service.create_workflow",
        lambda **kwargs: {"id": 1, "title": kwargs.get("title")},
        raising=False,
    )


class TestRoutesMovedToTheMatrix:
    def test_no_route_in_the_module_uses_the_retired_decorator(self):
        import inspect

        import app.routes.enterprise_api as module

        source = inspect.getsource(module)
        assert "app.security.rbac" not in source
        assert "ResourceDomain" not in source

    def test_viewer_is_refused_the_write_route(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer")
        viewer = _user(db_session, org, "viewer")
        db_session.commit()
        assert _post_arb(client, login_as, viewer).status_code == 403

    def test_architect_is_refused_the_write_route_as_before(
        self, app, db_session, login_as, client
    ):
        org = _org(db_session, "architect")
        architect = _user(db_session, org, "architect")
        db_session.commit()
        assert _post_arb(client, login_as, architect).status_code == 403

    def test_administrator_is_admitted_to_the_write_route(
        self, app, db_session, login_as, client, monkeypatch
    ):
        org = _org(db_session, "admin")
        admin = _user(db_session, org, "org_admin", platform_role="Administrator")
        db_session.commit()
        _stub_arb_service(monkeypatch)
        response = _post_arb(client, login_as, admin)
        assert response.status_code not in (401, 403)
        assert response.status_code < 500

    def test_anonymous_is_401_on_the_write_route(self, app, db_session, client):
        assert client.post(ARB, json={"title": "Q", "reviewers": [1]}).status_code == 401

    @pytest.mark.parametrize("path", READS)
    def test_viewer_still_reads(self, app, db_session, login_as, client, path):
        org = _org(db_session, "reader")
        viewer = _user(db_session, org, "viewer")
        db_session.commit()
        login_as(client, viewer)
        assert client.get(path).status_code not in (401, 403)

    @pytest.mark.parametrize("path", READS)
    def test_anonymous_is_401_on_reads(self, app, db_session, client, path):
        assert client.get(path).status_code == 401

    def test_defaults_stay_read_only_below_administrator(
        self, app, db_session, login_as, client
    ):
        from app.core.auth.permissions import can

        org = _org(db_session, "defaults")
        viewer = _user(db_session, org, "viewer")
        architect = _user(db_session, org, "architect")
        db_session.commit()
        for user in (viewer, architect):
            assert can(user, "read", "review_workflow", org.id)
            assert not can(user, "create", "review_workflow", org.id)
            assert can(user, "read", "enterprise_record", org.id)


class TestTwoOrganisations:
    def test_a_grant_in_one_organisation_does_not_open_the_route_in_another(
        self, app, db_session, login_as, client, monkeypatch
    ):
        _stub_arb_service(monkeypatch)
        from app.core.auth.permissions import set_grant

        org_a = _org(db_session, "a")
        org_b = _org(db_session, "b")
        architect_a = _user(db_session, org_a, "architect")
        architect_b = _user(db_session, org_b, "architect")
        set_grant(org_a.id, "role", "architect", "review_workflow", "create", True)
        db_session.commit()

        admitted = _post_arb(client, login_as, architect_a)
        assert admitted.status_code not in (401, 403)
        assert admitted.status_code < 500
        assert _post_arb(client, login_as, architect_b).status_code == 403

    def test_an_administrator_of_one_organisation_is_only_a_viewer_in_another(
        self, app, db_session, login_as, client
    ):
        from app.core.auth.permissions import can
        from app.models.org_role import OrgRole

        org_a = _org(db_session, "own")
        org_b = _org(db_session, "foreign")
        admin_a = _user(db_session, org_a, "org_admin", platform_role="Administrator")
        OrgRole.set_role(org_b.id, admin_a.id, "viewer")
        db_session.commit()

        assert can(admin_a, "create", "review_workflow", org_a.id)
        assert not can(admin_a, "create", "review_workflow", org_b.id)


class TestSwitchedSessionIsJudgedByTheActiveOrganisation:
    """The matrix decorator must read the organisation the session is switched
    INTO, not the account's home organisation.

    ``TestTwoOrganisations`` above calls ``can(user, ..., org_id)`` with an
    explicit organisation id. That proves the matrix answer is per-organisation,
    but it does not prove the HTTP route resolves the organisation the same way
    a browser does: an administrator of their own organisation who accepts a
    Viewer invitation into another organisation and switches the active session
    into it (the real ``/account/switch-organization`` flow) must be refused the
    write route there, because ``require_permission`` reads the ACTIVE
    organisation. If it ever read the account's home organisation instead, this
    account -- a home-org Administrator -- would be admitted to a foreign
    organisation's write route while holding only Viewer there.
    """

    def _switch_into(self, client, login_as, user, org):
        login_as(client, user)
        switched = client.post(
            "/account/switch-organization",
            data={"organization_id": str(org.id)},
            follow_redirects=True,
        )
        assert switched.status_code == 200, (
            f"fixture setup: switching the active session into {org.slug} failed "
            f"({switched.status_code})"
        )

    def test_a_home_org_admin_switched_into_a_foreign_org_is_refused_the_write_route(
        self, app, db_session, login_as, client, monkeypatch
    ):
        from app.models.org_role import OrgRole

        _stub_arb_service(monkeypatch)

        home_org = _org(db_session, "switch-home")
        foreign_org = _org(db_session, "switch-foreign")
        # Administrator of their OWN organisation ...
        user = _user(db_session, home_org, "org_admin", platform_role="Administrator")
        # ... but only a Viewer invited into the foreign organisation.
        OrgRole.set_role(foreign_org.id, user.id, "viewer")
        db_session.commit()

        # Still an administrator at home: the write route admits them there, so
        # the refusal below cannot be explained by a broken login or an
        # unrelated 403.
        self._switch_into(client, login_as, user, home_org)
        admitted = client.post(ARB, json={"title": "Q", "reviewers": [1]})
        assert admitted.status_code not in (401, 403), (
            f"the same administrator, in their own organisation, was refused the "
            f"write route ({admitted.status_code})"
        )
        assert admitted.status_code < 500

        # Switch the active session into the foreign organisation and try the
        # same write: only a Viewer there, so the matrix must refuse.
        self._switch_into(client, login_as, user, foreign_org)
        refused = client.post(ARB, json={"title": "Q", "reviewers": [1]})
        assert refused.status_code == 403, (
            f"a home-org Administrator, Viewer-only in the organisation the "
            f"session is switched into ({foreign_org.slug}), reached the write "
            f"route there with {refused.status_code} instead of 403 -- the matrix "
            f"decorator read the home organisation, not the active one"
        )


class TestBulkDeleteGrantStaysInItsOrganisation:
    def test_a_bulk_delete_grant_in_a_never_bulk_deletes_in_b(
        self, app, db_session, login_as, client
    ):
        from app.core.auth.permissions import can, set_grant

        org_a = _org(db_session, "bd-a")
        org_b = _org(db_session, "bd-b")
        user = _user(db_session, org_b, "architect")
        target_b = _application(db_session, org_b)
        set_grant(org_a.id, "user", user.id, "application", "bulk_delete", True)
        set_grant(org_a.id, "role", "architect", "application", "bulk_delete", True)
        db_session.commit()

        # The grant is live where it was made, so the refusal below is not vacuous.
        assert can(user, "bulk_delete", "application", org_a.id)
        assert not can(user, "bulk_delete", "application", org_b.id)

        login_as(client, user)
        resp = client.post(
            "/applications/bulk-delete", json={"ids": [target_b.id], "confirm": True}
        )
        assert resp.status_code == 403
        assert _exists(db_session, target_b.id)

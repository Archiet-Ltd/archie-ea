"""One permission matrix per organisation, first slice (applications).

The server and the templates read the same matrix. With no grant rows every
role behaves as before for applications, except that the Architect role may
delete one application. Bulk delete stays administrators only by default. An organisation administrator changes the matrix on the Team page.
"""

import uuid

import pytest


@pytest.fixture
def client(app):
    return app.test_client()


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"PM {label} {suffix}", slug=f"pm-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org, org_role, platform_role="User"):
    """A confirmed user whose organisation role is ``org_role``."""
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    role = Role.query.filter_by(name=platform_role).first()
    user = User(
        email=f"pm-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Perm",
        last_name=org_role,
        organization_id=org.id,
        role=role,
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


def _application(db_session, org, name=None):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(
        name=name or f"PM app {uuid.uuid4().hex[:6]}",
        description="permission matrix test",
        organization_id=org.id,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _exists(db_session, app_id):
    from app.models.application_portfolio import ApplicationComponent

    db_session.expire_all()
    return (
        db_session.query(ApplicationComponent)
        .filter(ApplicationComponent.id == app_id)
        .first()
        is not None
    )


def _set_role_cell(client, login_as, admin, role, action, allowed):
    login_as(client, admin)
    return client.post(
        "/admin/team/permissions/role",
        data={
            "role": role,
            "object_type": "application",
            "action": action,
            "allowed": "1" if allowed else "0",
        },
    )


def _list_html(client, login_as, user):
    login_as(client, user)
    resp = client.get("/applications/")
    assert resp.status_code == 200, resp.status_code
    return resp.get_data(as_text=True)


class TestDefaults:
    def test_architect_deletes_by_default_and_sees_the_control(
        self, app, db_session, login_as, client
    ):
        org = _org(db_session, "arch")
        architect = _user(db_session, org, "architect")
        target = _application(db_session, org)
        db_session.commit()

        html = _list_html(client, login_as, architect)
        assert f'data-testid="btn-delete-{target.id}"' in html

        login_as(client, architect)
        resp = client.post(f"/applications/{target.id}/delete")
        assert resp.status_code in (200, 302)
        assert not _exists(db_session, target.id)

    def test_viewer_is_refused_delete_and_sees_no_control(
        self, app, db_session, login_as, client
    ):
        org = _org(db_session, "view")
        viewer = _user(db_session, org, "viewer")
        target = _application(db_session, org)
        db_session.commit()

        html = _list_html(client, login_as, viewer)
        assert f'data-testid="btn-delete-{target.id}"' not in html
        assert f'data-testid="btn-delete-mobile-{target.id}"' not in html
        assert 'data-testid="bulk-btn-delete"' not in html

        login_as(client, viewer)
        resp = client.post(f"/applications/{target.id}/delete")
        assert resp.status_code == 403
        assert _exists(db_session, target.id)

    def test_viewer_cannot_create_or_edit(self, app, db_session, login_as, client):
        org = _org(db_session, "viewce")
        viewer = _user(db_session, org, "viewer")
        target = _application(db_session, org)
        db_session.commit()

        login_as(client, viewer)
        assert client.get("/applications/create").status_code == 403
        login_as(client, viewer)
        assert client.get(f"/applications/{target.id}/edit").status_code == 403

    def test_anonymous_is_401(self, app, db_session, client):
        org = _org(db_session, "anon")
        target = _application(db_session, org)
        db_session.commit()

        resp = client.post(f"/applications/{target.id}/delete")
        assert resp.status_code == 401
        assert _exists(db_session, target.id)


class TestBulkDeleteDefaults:
    def test_architect_is_refused_bulk_delete_and_sees_no_bulk_control(
        self, app, db_session, login_as, client
    ):
        org = _org(db_session, "bulk-arch")
        architect = _user(db_session, org, "architect")
        target = _application(db_session, org)
        db_session.commit()

        html = _list_html(client, login_as, architect)
        assert f'data-testid="btn-delete-{target.id}"' in html
        assert 'data-testid="bulk-btn-delete"' not in html

        login_as(client, architect)
        resp = client.post(
            "/applications/bulk-delete", json={"ids": [target.id], "confirm": True}
        )
        assert resp.status_code == 403
        assert _exists(db_session, target.id)

    def test_administrator_bulk_deletes_by_default(
        self, app, db_session, login_as, client
    ):
        org = _org(db_session, "bulk-admin")
        admin = _user(db_session, org, "org_admin", platform_role="Administrator")
        target = _application(db_session, org)
        db_session.commit()

        html = _list_html(client, login_as, admin)
        assert 'data-testid="bulk-btn-delete"' in html
        login_as(client, admin)
        resp = client.post(
            "/applications/bulk-delete", json={"ids": [target.id], "confirm": True}
        )
        assert resp.status_code not in (401, 403)

    def test_administrator_can_grant_architects_bulk_delete(
        self, app, db_session, login_as, client
    ):
        org = _org(db_session, "bulk-grant")
        admin = _user(db_session, org, "org_admin", platform_role="Administrator")
        architect = _user(db_session, org, "architect")
        target = _application(db_session, org)
        db_session.commit()

        resp = _set_role_cell(client, login_as, admin, "architect", "bulk_delete", True)
        assert resp.status_code in (200, 302)
        html = _list_html(client, login_as, architect)
        assert 'data-testid="bulk-btn-delete"' in html
        login_as(client, architect)
        resp = client.post(
            "/applications/bulk-delete", json={"ids": [target.id], "confirm": True}
        )
        assert resp.status_code not in (401, 403)


class TestMatrixOnTheScreen:
    def test_administrator_flips_architect_delete_off_and_back(
        self, app, db_session, login_as, client
    ):
        org = _org(db_session, "flip")
        admin = _user(db_session, org, "org_admin", platform_role="Administrator")
        architect = _user(db_session, org, "architect")
        target = _application(db_session, org)
        db_session.commit()

        # The Permissions tab draws the cell as a switch that is on.
        login_as(client, admin)
        page = client.get("/admin/team?tab=permissions")
        assert page.status_code == 200
        body = page.get_data(as_text=True)
        assert 'data-testid="perm-cell-architect-delete"' in body

        # Off: the route refuses and the control disappears.
        resp = _set_role_cell(client, login_as, admin, "architect", "delete", False)
        assert resp.status_code in (200, 302)
        login_as(client, architect)
        assert client.post(f"/applications/{target.id}/delete").status_code == 403
        assert _exists(db_session, target.id)
        html = _list_html(client, login_as, architect)
        assert f'data-testid="btn-delete-{target.id}"' not in html

        # The screen reports the cell as off.
        login_as(client, admin)
        body = client.get("/admin/team?tab=permissions").get_data(as_text=True)
        assert 'data-testid="perm-cell-architect-delete" aria-checked="false"' in body

        # Back on: the control returns and the route accepts.
        resp = _set_role_cell(client, login_as, admin, "architect", "delete", True)
        assert resp.status_code in (200, 302)
        html = _list_html(client, login_as, architect)
        assert f'data-testid="btn-delete-{target.id}"' in html
        login_as(client, architect)
        assert client.post(f"/applications/{target.id}/delete").status_code in (200, 302)
        assert not _exists(db_session, target.id)

    def test_only_administrators_open_the_permissions_tab(
        self, app, db_session, login_as, client
    ):
        org = _org(db_session, "gate")
        architect = _user(db_session, org, "architect")
        db_session.commit()

        login_as(client, architect)
        assert client.get("/admin/team?tab=permissions").status_code == 403
        resp = _set_role_cell(client, login_as, architect, "viewer", "delete", True)
        assert resp.status_code == 403


class TestUserOverrides:
    def test_user_override_beats_the_role_row(self, app, db_session, login_as, client):
        org = _org(db_session, "over")
        admin = _user(db_session, org, "org_admin", platform_role="Administrator")
        viewer = _user(db_session, org, "viewer")
        other_viewer = _user(db_session, org, "viewer")
        target = _application(db_session, org)
        db_session.commit()

        login_as(client, admin)
        resp = client.post(
            "/admin/team/permissions/user",
            data={
                "user_id": str(viewer.id),
                "object_type": "application",
                "action": "delete",
                "allowed": "1",
            },
        )
        assert resp.status_code in (200, 302)

        # The overridden viewer deletes; the other viewer is still refused.
        login_as(client, other_viewer)
        assert client.post(f"/applications/{target.id}/delete").status_code == 403
        html = _list_html(client, login_as, viewer)
        assert f'data-testid="btn-delete-{target.id}"' in html
        login_as(client, viewer)
        assert client.post(f"/applications/{target.id}/delete").status_code in (200, 302)

    def test_user_override_can_deny_what_the_role_allows_and_be_removed(
        self, app, db_session, login_as, client
    ):
        org = _org(db_session, "deny")
        admin = _user(db_session, org, "org_admin", platform_role="Administrator")
        architect = _user(db_session, org, "architect")
        target = _application(db_session, org)
        db_session.commit()

        login_as(client, admin)
        client.post(
            "/admin/team/permissions/user",
            data={"user_id": str(architect.id), "object_type": "application",
                  "action": "delete", "allowed": "0"},
        )
        login_as(client, architect)
        assert client.post(f"/applications/{target.id}/delete").status_code == 403

        login_as(client, admin)
        client.post(
            "/admin/team/permissions/user/remove",
            data={"user_id": str(architect.id), "object_type": "application",
                  "action": "delete"},
        )
        login_as(client, architect)
        assert client.post(f"/applications/{target.id}/delete").status_code in (200, 302)


class TestOrganisationIsolation:
    def test_a_grant_in_one_organisation_never_changes_another(
        self, app, db_session, login_as, client
    ):
        org_a = _org(db_session, "isoa")
        org_b = _org(db_session, "isob")
        admin_a = _user(db_session, org_a, "org_admin", platform_role="Administrator")
        architect_a = _user(db_session, org_a, "architect")
        architect_b = _user(db_session, org_b, "architect")
        viewer_b = _user(db_session, org_b, "viewer")
        app_a = _application(db_session, org_a)
        app_b = _application(db_session, org_b)
        db_session.commit()

        # Organisation A switches Architect delete off and gives Viewer delete.
        assert _set_role_cell(client, login_as, admin_a, "architect", "delete", False).status_code in (200, 302)
        assert _set_role_cell(client, login_as, admin_a, "viewer", "delete", True).status_code in (200, 302)

        login_as(client, architect_a)
        assert client.post(f"/applications/{app_a.id}/delete").status_code == 403

        # Organisation B is untouched: its viewer is still refused and its
        # architect still deletes.
        login_as(client, viewer_b)
        assert client.post(f"/applications/{app_b.id}/delete").status_code == 403
        login_as(client, architect_b)
        assert client.post(f"/applications/{app_b.id}/delete").status_code in (200, 302)
        assert not _exists(db_session, app_b.id)

    def test_grant_rows_are_stored_against_the_callers_organisation(
        self, app, db_session, login_as, client
    ):
        from app.models.permission_grant import PermissionGrant

        org_a = _org(db_session, "rowa")
        org_b = _org(db_session, "rowb")
        admin_a = _user(db_session, org_a, "org_admin", platform_role="Administrator")
        db_session.commit()

        _set_role_cell(client, login_as, admin_a, "viewer", "edit", True)
        db_session.expire_all()
        rows = PermissionGrant.query.filter_by(subject_key="viewer", action="edit").all()
        assert [r.organization_id for r in rows] == [org_a.id]
        assert org_b.id not in [r.organization_id for r in rows]


class TestGlobalAdminArchetypeIsNotAdministratorOfEveryOrganisation:
    """A global ``role_archetype == "admin"`` is not administrator authority.

    ``can`` already answers administrator authority first, scoped to the active
    organisation (``_is_administrator`` -> ``rbac_service.is_org_admin``). The
    matrix role key, reached only for an account with no explicit organisation
    role, must not separately read a global ``role_archetype`` of "admin" as the
    matrix "admin" key -- that handed an account administrator-level matrix
    rights in any organisation it merely belonged to without a role row, with no
    active-organisation scoping at all.
    """

    def test_a_global_admin_archetype_user_cannot_create_in_a_foreign_org(
        self, app, db_session
    ):
        from app.core.auth.permissions import can, role_key

        org_a = _org(db_session, "arch-home")
        org_b = _org(db_session, "arch-foreign")
        # A non-administrator account (platform role "User") that carries the
        # global persona label "admin" and has NO organisation role in org B.
        user = _user(db_session, org_a, "viewer", platform_role="User")
        user.role_archetype = "admin"
        db_session.commit()

        # In the foreign organisation it holds no role row, so the matrix falls
        # through to the role key: that key must not be "admin".
        assert role_key(user, org_b.id) != "admin"
        assert not can(user, "create", "application", org_b.id)
        assert not can(user, "delete", "application", org_b.id)
        assert not can(user, "bulk_delete", "application", org_b.id)
        # It still reads, like any signed-in account.
        assert can(user, "read", "application", org_b.id)
        # And a real administrator of their own organisation is unaffected.
        real_admin = _user(db_session, org_a, "org_admin", platform_role="Administrator")
        db_session.commit()
        assert can(real_admin, "create", "application", org_a.id)

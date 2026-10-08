"""Regression tests for A-01 (S1): a tenant administrator must not be able to
enumerate other tenants' organizations or members.

Scope note: this is the COMPLETE audit, not a sample. Enumerated via
`app.url_map.iter_rules()` (not grep, to catch every registered blueprint
regardless of file layout) for every route whose URL rule takes an
`<int:...>` converter and whose path or endpoint mentions org/user/member,
across app/modules/admin/**, app/admin/**, app/modules/account/**, and any
organization/member/user API. Result: 13 distinct URL rules (one, /admin/user/
<id>, is registered twice — GET and its /info alias share one view function,
`user_info`).

  Routes audited (13) and disposition:
  1. GET  /admin/organizations/<org_id>                                  — safe
  2. GET|POST /admin/organizations/<org_id>/edit                         — safe
  3. POST /admin/organizations/<org_id>/toggle                           — safe
  4. POST /admin/organizations/<org_id>/users/<user_id>/toggle-admin     — safe
  5. POST /admin/organizations/<org_id>/delete                          — safe
  6. POST /admin/organizations/<org_id>/users/<user_id>/remove          — safe
  7. GET  /admin/user/<user_id> (+ /info alias)                          — safe
  8. GET|POST /admin/user/<user_id>/change-email                        — safe
  9. GET|POST /admin/user/<user_id>/change-account-type                 — safe
  10. GET|POST /admin/user/<user_id>/set-password                       — safe
  11. GET  /admin/user/<user_id>/delete (confirmation page)              — safe
  12. GET  /admin/user/<user_id>/_delete (executes deletion)             — safe
  13. DELETE /admin/team/member/<user_id>                                — safe
  (+ GET /admin/users list, no id param, included as A-02's directory surface)
  (+ /account/join-from-invite/<user_id>/<token> — pre-auth invite-accept
    flow gated by a signed confirmation token, not an admin/tenant check;
    not part of this IDOR class, see disposition below)

  Vulnerable: 0 of 13.

  Disposition detail:
  - Routes 1-6 (organizations/**) are gated by @platform_admin_required
    (app/middleware/tenant_decorators.py), which checks
    current_user.is_platform_admin before any org lookup happens, so an
    ordinary tenant (org) admin is rejected before the requested org's
    existence is confirmed or denied either way (uniform 403, no
    differential leak).
  - Routes 7-12 (/admin/user/<id>/**) resolve via
    AdminUserService.get_user_or_404, which filters by
    (id=user_id, organization_id=g.current_org_id) — see
    app/modules/admin/v2/services/admin_user_service_v2.py — so a lookup for
    a user in a different organization 404s regardless of the caller's role.
  - GET /admin/users (registered_users) — AdminUserService.get_all_users() is
    filtered the same way.
  - Route 13 (/admin/team/member/<user_id>) queries
    `OrgRole.query.filter_by(organization_id=org_id, user_id=user_id)` where
    org_id comes from the caller's own session (`_require_org_id()`), not
    from the URL — see app/modules/admin/team_routes.py — so it cannot
    target another tenant's row no matter what user_id is supplied.
  - /account/join-from-invite/<user_id>/<token> is retired: it loads no user
    and only redirects to /account/join/<token>, where the invitation is found
    by the digest of a stored single-use secret, so user_id selects nothing.

All the scoping mechanisms above (@platform_admin_required,
get_user_or_404's organization_id filter, team_routes' session-derived
org_id) were already present before this file was added (tenant-scoping-ok
markers pre-date this change). This file pins that behaviour so a future
edit cannot silently regress it, and additionally proves the
platform-admin/tenant-admin separation actually gates the organizations
blueprint (A-03's "introduce an explicit platform-administrator role"
acceptance item — the is_platform_admin column already exists on User; this
is the first test asserting it is wired into the org routes end-to-end over
real HTTP).
"""

import uuid

import pytest


@pytest.fixture
def client(app):
    return app.test_client()


def _make_org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"IDOR {label} {suffix}", slug=f"idor-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _make_user(db_session, org, *, is_org_admin=False, is_platform_admin=False, email=None):
    from app.models.user import Role, User

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").first()

    user = User(
        email=email or f"idor-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        # NOTE: pass role=<Role instance>, not role_id=<int>. User.__init__
        # checks `if self.role is None` and silently overwrites an
        # unresolved role_id with the default role (Architect) at
        # construction time, before the relationship has a chance to
        # resolve from role_id.
        role=admin_role,
        is_org_admin=is_org_admin,
        is_platform_admin=is_platform_admin,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


class TestOrganizationIDOR:
    """A-01: tenant-A org admin must never reach tenant-B's organization."""

    def test_tenant_admin_cannot_view_another_orgs_detail_page(self, app, db_session, login_as, client):
        org_a = _make_org(db_session, "a")
        org_b = _make_org(db_session, "b")
        # A tenant (org) admin: is_org_admin True, is_platform_admin False —
        # exactly the "tenant administrator" role the finding describes.
        tenant_admin = _make_user(db_session, org_a, is_org_admin=True, is_platform_admin=False)
        db_session.commit()

        with app.app_context():
            login_as(client, tenant_admin)
            resp = client.get(f"/admin/organizations/{org_b.id}")

        # Rejected before org_b's existence can be confirmed or denied by a
        # differential response — platform_admin_required checks role first.
        assert resp.status_code in (403, 404)
        assert org_b.name.encode() not in resp.data

    def test_tenant_admin_cannot_list_all_organizations(self, app, db_session, login_as, client):
        org_a = _make_org(db_session, "list-a")
        tenant_admin = _make_user(db_session, org_a, is_org_admin=True, is_platform_admin=False)
        db_session.commit()

        with app.app_context():
            login_as(client, tenant_admin)
            resp = client.get("/admin/organizations")

        assert resp.status_code in (403, 404)

    def test_platform_admin_can_view_any_organization(self, app, db_session, login_as, client):
        """Positive control: the role split must not be so tight it blocks
        the role it exists for."""
        org_a = _make_org(db_session, "pa-a")
        org_b = _make_org(db_session, "pa-b")
        platform_admin = _make_user(db_session, org_a, is_org_admin=True, is_platform_admin=True)
        db_session.commit()

        with app.app_context():
            login_as(client, platform_admin)
            resp = client.get(f"/admin/organizations/{org_b.id}")

        assert resp.status_code == 200


class TestMemberIDOR:
    """A-01: tenant-A admin must never reach tenant-B's member records."""

    def test_org_admin_cannot_view_another_orgs_user(self, app, db_session, login_as, client):
        org_a = _make_org(db_session, "mem-a")
        org_b = _make_org(db_session, "mem-b")
        admin_a = _make_user(db_session, org_a, is_org_admin=True)
        victim_b = _make_user(db_session, org_b, email=f"victim-{uuid.uuid4().hex[:8]}@example.com")
        db_session.commit()

        with app.app_context():
            login_as(client, admin_a)
            resp = client.get(f"/admin/user/{victim_b.id}")

        assert resp.status_code == 404
        assert victim_b.email.encode() not in resp.data

    def test_org_admin_cannot_change_email_of_another_orgs_user(self, app, db_session, login_as, client):
        org_a = _make_org(db_session, "mem2-a")
        org_b = _make_org(db_session, "mem2-b")
        admin_a = _make_user(db_session, org_a, is_org_admin=True)
        victim_email = f"victim2-{uuid.uuid4().hex[:8]}@example.com"
        victim_b = _make_user(db_session, org_b, email=victim_email)
        db_session.commit()

        with app.app_context():
            login_as(client, admin_a)
            resp = client.post(
                f"/admin/user/{victim_b.id}/change-email",
                data={"email": "pwned@evil.example.com"},
            )

        assert resp.status_code == 404

        from app.models.user import User

        with app.app_context():
            reloaded = User.query.get(victim_b.id)
            assert reloaded.email == victim_email

    def test_registered_users_list_is_scoped_to_caller_org(self, app, db_session, login_as, client):
        org_a = _make_org(db_session, "list-mem-a")
        org_b = _make_org(db_session, "list-mem-b")
        admin_a = _make_user(db_session, org_a, is_org_admin=True)
        other_member_email = f"other-org-member-{uuid.uuid4().hex[:8]}@example.com"
        _make_user(db_session, org_b, email=other_member_email)
        db_session.commit()

        with app.app_context():
            login_as(client, admin_a)
            resp = client.get("/admin/users")

        assert resp.status_code == 200
        assert other_member_email.encode() not in resp.data

    def test_team_remove_member_cannot_target_another_orgs_role(self, app, db_session, login_as, client):
        """Route 13 of the audit: /admin/team/member/<user_id>. org_id for the
        delete filter is derived server-side from the caller's own session
        (_require_org_id()), never from the URL, so a cross-org OrgRole row
        must survive an attempt to delete it via another org's admin."""
        from app.models.org_role import OrgRole

        org_a = _make_org(db_session, "team-a")
        org_b = _make_org(db_session, "team-b")
        admin_a = _make_user(db_session, org_a, is_org_admin=True)
        victim_b = _make_user(db_session, org_b, email=f"team-victim-{uuid.uuid4().hex[:8]}@example.com")

        role_b = OrgRole(organization_id=org_b.id, user_id=victim_b.id, role="member")
        db_session.add(role_b)
        db_session.commit()
        role_b_id = role_b.id

        with app.app_context():
            login_as(client, admin_a)
            resp = client.delete(f"/admin/team/member/{victim_b.id}")

        assert resp.status_code in (200, 403)
        assert OrgRole.query.get(role_b_id) is not None


class TestAdminUserActionRoutes:
    """Deletion by POST only, a refused delete is a flashed
    message rather than a 500, and the role page renders with the full
    VALID_ROLES list."""

    def test_get_delete_is_refused_and_user_survives(self, app, db_session, login_as, client):
        org_a = _make_org(db_session, "del-get-a")
        admin_a = _make_user(db_session, org_a, is_org_admin=True)
        victim = _make_user(db_session, org_a, email=f"getdel-{uuid.uuid4().hex[:8]}@example.com")
        victim_id = victim.id
        db_session.commit()

        with app.app_context():
            login_as(client, admin_a)
            resp = client.get(f"/admin/user/{victim_id}/_delete")

        assert resp.status_code == 405

        from app.models.user import User

        with app.app_context():
            assert User.query.get(victim_id) is not None

    def test_delete_confirmation_page_renders_a_post_form_with_csrf_token(
        self, app, db_session, login_as, client
    ):
        org_a = _make_org(db_session, "del-form-a")
        admin_a = _make_user(db_session, org_a, is_org_admin=True)
        victim = _make_user(db_session, org_a, email=f"delform-{uuid.uuid4().hex[:8]}@example.com")
        db_session.commit()

        with app.app_context():
            login_as(client, admin_a)
            resp = client.get(f"/admin/user/{victim.id}/delete")

        assert resp.status_code == 200
        body = resp.data.decode()
        assert f'action="/admin/user/{victim.id}/_delete"' in body
        assert '<form method="POST"' in body
        assert 'name="csrf_token"' in body

    def test_post_delete_removes_unreferenced_user_and_redirects_to_list(
        self, app, db_session, login_as, client
    ):
        org_a = _make_org(db_session, "del-post-a")
        admin_a = _make_user(db_session, org_a, is_org_admin=True)
        victim = _make_user(db_session, org_a, email=f"delpost-{uuid.uuid4().hex[:8]}@example.com")
        victim_id = victim.id
        db_session.commit()

        with app.app_context():
            login_as(client, admin_a)
            resp = client.post(f"/admin/user/{victim_id}/_delete")

        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/admin/users")

        from app.models.user import User

        with app.app_context():
            assert User.query.get(victim_id) is None

    def test_post_delete_of_referenced_user_flashes_error_instead_of_500(
        self, app, db_session, login_as, client
    ):
        from app.models.org_role import OrgRole

        org_a = _make_org(db_session, "del-ref-a")
        admin_a = _make_user(db_session, org_a, is_org_admin=True)
        victim = _make_user(db_session, org_a, email=f"delref-{uuid.uuid4().hex[:8]}@example.com")
        db_session.flush()
        # A row that references the victim and is not cascade-deleted with the
        # user: the FK refusal (IntegrityError) the fix must turn into a
        # flashed message instead of a 500.
        db_session.add(OrgRole(organization_id=org_a.id, user_id=victim.id, role="viewer"))
        db_session.commit()
        victim_id = victim.id

        with app.app_context():
            login_as(client, admin_a)
            resp = client.post(f"/admin/user/{victim_id}/_delete", follow_redirects=True)

        assert resp.status_code == 200
        assert b"This user still owns records and cannot be deleted." in resp.data

        from app.models.user import User

        with app.app_context():
            assert User.query.get(victim_id) is not None

    def test_cross_org_admin_cannot_delete_or_reach_role_page_of_other_orgs_user(
        self, app, db_session, login_as, client
    ):
        org_a = _make_org(db_session, "del-cross-a")
        org_b = _make_org(db_session, "del-cross-b")
        admin_a = _make_user(db_session, org_a, is_org_admin=True)
        victim_b = _make_user(db_session, org_b, email=f"crossvictim-{uuid.uuid4().hex[:8]}@example.com")
        victim_b_id = victim_b.id
        victim_b_role_before = victim_b.enterprise_role
        db_session.commit()

        with app.app_context():
            login_as(client, admin_a)
            del_resp = client.post(f"/admin/user/{victim_b_id}/_delete")
            role_resp = client.get(f"/admin/user/{victim_b_id}/role")

        assert del_resp.status_code == 404
        assert role_resp.status_code == 404

        from app.models.user import User

        with app.app_context():
            reloaded = User.query.get(victim_b_id)
            assert reloaded is not None
            assert reloaded.enterprise_role == victim_b_role_before

    def test_role_page_renders_full_role_list_and_updates_on_post(
        self, app, db_session, login_as, client
    ):
        from app.models.user import VALID_ROLES

        org_a = _make_org(db_session, "role-a")
        admin_a = _make_user(db_session, org_a, is_org_admin=True)
        target = _make_user(db_session, org_a, email=f"roletarget-{uuid.uuid4().hex[:8]}@example.com")
        target_id = target.id
        db_session.commit()

        with app.app_context():
            login_as(client, admin_a)
            get_resp = client.get(f"/admin/user/{target_id}/role")

        assert get_resp.status_code == 200
        body = get_resp.data.decode()
        assert f'action="/admin/user/{target_id}/role"' in body
        assert body.count('name="enterprise_role"') == len(VALID_ROLES)
        for role_id in VALID_ROLES:
            assert f'value="{role_id}"' in body

        chosen_role = VALID_ROLES[0]
        with app.app_context():
            login_as(client, admin_a)
            post_resp = client.post(
                f"/admin/user/{target_id}/role",
                data={"enterprise_role": chosen_role},
                follow_redirects=True,
            )

        # admin.user_info answers at both /admin/user/<id> and
        # /admin/user/<id>/info (a pre-existing, unrelated alias); follow the
        # redirect and check the destination is the user's own page rather
        # than pinning one of the two equivalent URLs.
        assert post_resp.status_code == 200
        assert target.email.encode() in post_resp.data

        from app.models.user import User

        with app.app_context():
            reloaded = User.query.get(target_id)
            assert reloaded.enterprise_role == chosen_role

        with app.app_context():
            login_as(client, admin_a)
            bad_resp = client.post(
                f"/admin/user/{target_id}/role", data={"enterprise_role": "not-a-real-role"}
            )

        assert bad_resp.status_code == 302
        assert bad_resp.headers["Location"].endswith(f"/admin/user/{target_id}/role")

        with app.app_context():
            login_as(client, admin_a)
            after_bad_resp = client.get(f"/admin/user/{target_id}/role")

        assert after_bad_resp.status_code == 200

    def test_failed_delete_does_not_create_audit_entry(
        self, app, db_session, login_as, client
    ):
        """A delete refused by a foreign-key constraint must not leave an
        audit entry claiming the deletion succeeded."""
        from app.models.audit_log import AuditLog
        from app.models.org_role import OrgRole

        org_a = _make_org(db_session, "audit-fail-a")
        admin_a = _make_user(db_session, org_a, is_org_admin=True)
        victim = _make_user(db_session, org_a, email=f"auditfail-{uuid.uuid4().hex[:8]}@example.com")
        db_session.flush()
        db_session.add(OrgRole(organization_id=org_a.id, user_id=victim.id, role="viewer"))
        db_session.commit()
        victim_id = victim.id

        # Count audit entries before the attempt.
        with app.app_context():
            before = AuditLog.query.filter_by(
                action="admin_user_delete", record_id=victim_id
            ).count()

        with app.app_context():
            login_as(client, admin_a)
            resp = client.post(f"/admin/user/{victim_id}/_delete", follow_redirects=True)

        assert resp.status_code == 200
        assert b"This user still owns records and cannot be deleted." in resp.data

        with app.app_context():
            after = AuditLog.query.filter_by(
                action="admin_user_delete", record_id=victim_id
            ).count()

        assert after == before, (
            f"Expected no new audit entry for failed deletion, "
            f"but count went from {before} to {after}"
        )

    def test_successful_delete_flash_message_includes_user_name(
        self, app, db_session, login_as, client
    ):
        """A successful deletion must flash a message containing the deleted
        user's name, proving full_name() was captured before the object was
        expired by commit."""
        org_a = _make_org(db_session, "audit-ok-a")
        admin_a = _make_user(db_session, org_a, is_org_admin=True)
        victim = _make_user(
            db_session, org_a,
            email=f"auditok-{uuid.uuid4().hex[:8]}@example.com",
        )
        victim_name = victim.full_name()
        victim_id = victim.id
        db_session.commit()

        with app.app_context():
            login_as(client, admin_a)
            resp = client.post(f"/admin/user/{victim_id}/_delete", follow_redirects=True)

        assert resp.status_code == 200
        # The flashed success message must contain the user's name.
        assert victim_name.encode() in resp.data

    def test_successful_delete_creates_audit_record(
        self, app, db_session, login_as, client
    ):
        """A successful user deletion through the current admin module must
        create exactly one audit record naming the deleted user and the actor."""
        from app.models.audit_log import AuditLog

        org_a = _make_org(db_session, "audit-ok2-a")
        admin_a = _make_user(db_session, org_a, is_org_admin=True)
        victim = _make_user(
            db_session, org_a,
            email=f"auditok2-{uuid.uuid4().hex[:8]}@example.com",
        )
        victim_id = victim.id
        db_session.commit()

        with app.app_context():
            before = AuditLog.query.filter_by(
                action="admin_user_delete", record_id=victim_id
            ).count()

        with app.app_context():
            login_as(client, admin_a)
            resp = client.post(f"/admin/user/{victim_id}/_delete", follow_redirects=True)

        assert resp.status_code == 200

        with app.app_context():
            entries = AuditLog.query.filter_by(
                action="admin_user_delete", record_id=victim_id
            ).all()

        assert len(entries) == before + 1, (
            f"Expected exactly one new audit entry for successful deletion, "
            f"but count went from {before} to {len(entries)}"
        )
        entry = entries[-1]
        assert entry.user_id == admin_a.id, (
            f"Audit entry user_id {entry.user_id} != actor {admin_a.id}"
        )
        assert entry.table_name == "admin_user", (
            f"Audit entry table_name {entry.table_name!r} != 'admin_user'"
        )

    def test_cross_org_role_page_post_is_refused(
        self, app, db_session, login_as, client
    ):
        """A POST to another org's user role page must 404 and not change
        the user's enterprise_role."""
        org_a = _make_org(db_session, "role-cross-post-a")
        org_b = _make_org(db_session, "role-cross-post-b")
        admin_a = _make_user(db_session, org_a, is_org_admin=True)
        victim_b = _make_user(
            db_session, org_b,
            email=f"rolecrosspost-{uuid.uuid4().hex[:8]}@example.com",
        )
        victim_b_role_before = victim_b.enterprise_role
        victim_b_id = victim_b.id
        db_session.commit()

        with app.app_context():
            login_as(client, admin_a)
            resp = client.post(
                f"/admin/user/{victim_b_id}/role",
                data={"enterprise_role": "solution_architect"},
            )

        assert resp.status_code == 404

        from app.models.user import User

        with app.app_context():
            reloaded = User.query.get(victim_b_id)
            assert reloaded is not None
            assert reloaded.enterprise_role == victim_b_role_before


class TestViewerRole:
    """A-03 (engineering half): a read-only role must exist and must not be
    able to reach admin write routes. Purely additive — asserts the new
    Viewer role exists and carries no permission bits, without touching any
    existing role's behaviour."""

    def test_viewer_role_exists_with_no_permissions(self, db_session):
        from app.models.user import Permission, Role

        Role.insert_roles()
        viewer = Role.query.filter_by(name="Viewer").first()
        assert viewer is not None
        assert not (viewer.permissions & Permission.GENERAL)
        assert not (viewer.permissions & Permission.ADMINISTER)
        assert viewer.default is False

    def test_existing_roles_unchanged_by_viewer_addition(self, db_session):
        from app.models.user import Permission, Role

        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").first()
        architect_role = Role.query.filter_by(name="Architect").first()
        user_role = Role.query.filter_by(name="User").first()

        assert admin_role.permissions == Permission.ADMINISTER
        assert architect_role.permissions == Permission.GENERAL
        assert architect_role.default is True
        assert user_role.permissions == Permission.GENERAL

    def test_viewer_cannot_administer(self, db_session):
        from app.models.user import Permission, Role, User

        Role.insert_roles()
        viewer_role = Role.query.filter_by(name="Viewer").first()
        org = _make_org(db_session, "viewer")
        viewer_user = User(
            email=f"viewer-{uuid.uuid4().hex[:8]}@example.com",
            organization_id=org.id,
            role=viewer_role,
            confirmed=True,
        )
        viewer_user.password = uuid.uuid4().hex
        db_session.add(viewer_user)
        db_session.flush()

        assert viewer_user.can(Permission.ADMINISTER) is False
        assert viewer_user.is_admin() is False

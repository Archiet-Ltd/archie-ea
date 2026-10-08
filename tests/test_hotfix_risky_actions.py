"""Hotfix: four risky actions are refused to anyone who is not a genuine
platform admin (or, for user management, an admin of the ACTIVE organisation).

Groups:
  1. role create / update / delete (global table)
  2. duplicate-detection runs, cleanup and the all-organisations run listing
  3. audit events read
  4. user management and governance-gate create on the live v2 admin blueprint
"""

import uuid

import pytest

ROLE_ROUTES = [
    ("post", "/admin/api/roles", {"name": "HotfixRole", "permissions": 1}),
    ("put", "/admin/api/roles/999999", {"is_admin": True}),
    ("delete", "/admin/api/roles/999999", None),
]

DETECTION_ROUTES = [
    ("post", "/duplicate-detection/simple/run-detection"),
    ("post", "/duplicate-detection/simple/run-hybrid"),
    ("post", "/duplicate-detection/simple/api/run-detection"),
    ("post", "/duplicate-detection/unified/run-detection"),
    ("post", "/duplicate-detection/run-detection"),
    ("post", "/duplicate-detection/simple/cleanup"),
    ("get", "/duplicate-detection/simple/runs"),
]


@pytest.fixture
def client(app):
    return app.test_client()


def _make_org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Hotfix {label} {suffix}", slug=f"hotfix-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _make_user(db_session, org, *, is_org_admin=False, is_platform_admin=False):
    from app.models.user import Role, User

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").first()
    user = User(
        email=f"hotfix-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        role=admin_role,
        is_org_admin=is_org_admin,
        is_platform_admin=is_platform_admin,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def actors(db_session):
    org = _make_org(db_session, "own")
    org_admin = _make_user(db_session, org, is_org_admin=True)
    platform_admin = _make_user(db_session, org, is_org_admin=True, is_platform_admin=True)
    db_session.commit()
    return org_admin, platform_admin


def _call(client, method, url, body=None):
    fn = getattr(client, method)
    if body is not None:
        return fn(url, json=body)
    return fn(url)


class TestGroup1Roles:
    @pytest.mark.parametrize("method,url,body", ROLE_ROUTES)
    def test_org_admin_refused(self, app, login_as, client, actors, method, url, body):
        org_admin, _ = actors
        with app.app_context():
            login_as(client, org_admin)
            assert _call(client, method, url, body).status_code == 403

    @pytest.mark.parametrize("method,url,body", ROLE_ROUTES)
    def test_platform_admin_not_refused(self, app, login_as, client, actors, method, url, body):
        _, platform_admin = actors
        with app.app_context():
            login_as(client, platform_admin)
            assert _call(client, method, url, body).status_code != 403


class TestGroup2DuplicateDetection:
    @pytest.mark.parametrize("method,url", DETECTION_ROUTES)
    def test_org_admin_refused(self, app, login_as, client, actors, method, url):
        org_admin, _ = actors
        with app.app_context():
            login_as(client, org_admin)
            body = {} if method == "post" else None
            assert _call(client, method, url, body).status_code == 403

    @pytest.mark.parametrize("method,url", DETECTION_ROUTES)
    def test_platform_admin_not_refused(self, app, login_as, client, actors, method, url):
        _, platform_admin = actors
        with app.app_context():
            login_as(client, platform_admin)
            body = {} if method == "post" else None
            assert _call(client, method, url, body).status_code != 403


class TestGroup3AuditEvents:
    def test_org_admin_refused(self, app, login_as, client, actors):
        org_admin, _ = actors
        with app.app_context():
            login_as(client, org_admin)
            assert client.get("/api/security/audit/events").status_code == 403

    def test_platform_admin_not_refused(self, app, login_as, client, actors):
        _, platform_admin = actors
        with app.app_context():
            login_as(client, platform_admin)
            assert client.get("/api/security/audit/events").status_code != 403


class TestGroup4ActiveOrgUserManagement:
    @pytest.fixture
    def scene(self, db_session):
        from app.models.org_role import OrgRole

        org_a = _make_org(db_session, "a")
        org_b = _make_org(db_session, "b")
        # Admin of A (home org), only a viewer of B.
        actor = _make_user(db_session, org_a, is_org_admin=True)
        target_a = _make_user(db_session, org_a)
        target_b = _make_user(db_session, org_b)
        OrgRole.set_role(org_b.id, actor.id, "viewer", granted_by_id=actor.id)
        db_session.commit()
        return org_a, org_b, actor, target_a, target_b

    def _switch(self, client, org):
        resp = client.post(
            "/account/switch-organization",
            data={"organization_id": str(org.id)},
            follow_redirects=False,
        )
        assert resp.status_code == 302

    def test_switched_org_viewer_refused_on_every_route(self, app, login_as, client, scene):
        org_a, org_b, actor, target_a, target_b = scene
        pw = {"password": "Passw0rd!Passw0rd", "password2": "Passw0rd!Passw0rd"}
        with app.app_context():
            login_as(client, actor)
            self._switch(client, org_b)
            checks = [
                client.post(f"/admin/user/{target_b.id}/change-email", data={"email": "pwn@example.com"}),
                client.post(f"/admin/user/{target_b.id}/set-password", data=pw),
                client.get(f"/admin/user/{target_b.id}/info"),
                client.get("/admin/users"),
                client.get("/admin/api/users"),
                client.post("/admin/api/governance-gates", json={"gate_name": "hotfix-gate"}),
            ]
            assert [r.status_code for r in checks] == [403] * len(checks)

    def test_switched_org_change_email_leaves_email_unchanged(self, app, login_as, client, scene):
        from app.models.user import User

        org_a, org_b, actor, target_a, target_b = scene
        original = target_b.email
        target_id = target_b.id
        with app.app_context():
            login_as(client, actor)
            self._switch(client, org_b)
            resp = client.post(f"/admin/user/{target_id}/change-email", data={"email": "pwn@example.com"})
            assert resp.status_code == 403
            assert User.query.get(target_id).email == original

    def test_same_user_in_own_org_still_succeeds(self, app, login_as, client, scene):
        org_a, org_b, actor, target_a, target_b = scene
        with app.app_context():
            login_as(client, actor)
            resp = client.post(
                f"/admin/user/{target_a.id}/change-email",
                data={"email": f"ok-{uuid.uuid4().hex[:6]}@example.com"},
            )
            assert resp.status_code in (200, 302)
            resp = client.post(
                f"/admin/user/{target_a.id}/set-password",
                data={"password": "Passw0rd!Passw0rd", "password2": "Passw0rd!Passw0rd"},
            )
            assert resp.status_code in (200, 302)
            assert client.get(f"/admin/user/{target_a.id}/info").status_code == 200
            assert client.get("/admin/users").status_code == 200
            assert client.get("/admin/api/users").status_code == 200

    def test_platform_admin_not_refused(self, app, db_session, login_as, client, scene):
        org_a, org_b, actor, target_a, target_b = scene
        pa = _make_user(db_session, org_a, is_org_admin=False, is_platform_admin=True)
        db_session.commit()
        with app.app_context():
            login_as(client, pa)
            assert client.get("/admin/api/users").status_code == 200
            assert client.get("/admin/users").status_code == 200


# ---------------------------------------------------------------------------
# Round 2 (P437-1 .. P437-5)
# ---------------------------------------------------------------------------

RUNS_LISTINGS = [
    "/duplicate-detection/enterprise/runs",
    "/duplicate-detection/unified/runs",
    "/duplicate-detection/api/detection-runs",
]


class TestRound2PlatformAdminOnly:
    def _plain_user(self, db_session, org):
        from app.models.user import Role

        Role.insert_roles()
        user = _make_user(db_session, org)
        user.role = Role.query.filter_by(name="User").first()
        user.is_org_admin = False
        db_session.commit()
        return user

    def test_rationalization_run_detection_user_role_refused_groups_survive(
        self, app, db_session, login_as, client, actors
    ):
        from app.models.unified_duplicate_detection import UnifiedDuplicateGroup

        org_admin, _ = actors
        group = UnifiedDuplicateGroup(name=f"global-{uuid.uuid4().hex[:6]}", similarity_score=0.9)
        db_session.add(group)
        db_session.commit()
        gid = group.id
        user = self._plain_user(db_session, org_admin.organization)
        with app.app_context():
            for actor in (user, org_admin):
                login_as(client, actor)
                resp = client.post("/applications/rationalization/api/run-detection", json={})
                assert resp.status_code == 403
            assert UnifiedDuplicateGroup.query.get(gid) is not None

    def test_rationalization_run_detection_platform_admin_not_refused(
        self, app, login_as, client, actors
    ):
        _, platform_admin = actors
        with app.app_context():
            login_as(client, platform_admin)
            resp = client.post("/applications/rationalization/api/run-detection", json={"strategy": "bogus"})
            assert resp.status_code == 400

    @pytest.mark.parametrize("url", RUNS_LISTINGS)
    def test_runs_listings_org_admin_refused(self, app, login_as, client, actors, url):
        org_admin, _ = actors
        with app.app_context():
            login_as(client, org_admin)
            assert client.get(url).status_code == 403

    @pytest.mark.parametrize("url", RUNS_LISTINGS)
    def test_runs_listings_platform_admin_not_refused(self, app, login_as, client, actors, url):
        _, platform_admin = actors
        with app.app_context():
            login_as(client, platform_admin)
            assert client.get(url).status_code != 403

    def test_enterprise_run_detection_refused(self, app, login_as, client, actors):
        org_admin, platform_admin = actors
        with app.app_context():
            login_as(client, org_admin)
            assert client.post("/duplicate-detection/enterprise/run-detection", json={}).status_code == 403
            login_as(client, platform_admin)
            assert client.post("/duplicate-detection/enterprise/run-detection", json={}).status_code != 403

    def test_anonymous_gets_401_or_redirect_not_500(self, client):
        for method, url in (
            ("post", "/applications/rationalization/api/run-detection"),
            ("get", "/duplicate-detection/enterprise/runs"),
            ("get", "/admin/api/enterprise-roles/users"),
        ):
            resp = getattr(client, method)(url)
            assert resp.status_code in (401, 302), (url, resp.status_code)


class TestRound2SwitchedOrgViewer(TestGroup4ActiveOrgUserManagement):
    """Reuses the scene/_switch fixtures; the inherited tests run again."""

    def test_round2_routes_refused_for_switched_org_viewer(self, app, login_as, client, scene):
        from app.models.governance_gates import GovernanceGate

        org_a, org_b, actor, target_a, target_b = scene
        gate = GovernanceGate.query.first()
        gate_id = gate.id if gate else 999999
        with app.app_context():
            login_as(client, actor)
            self._switch(client, org_b)
            checks = [
                client.put(f"/admin/api/governance-gates/{gate_id}", json={"gate_name": "x"}),
                client.delete(f"/admin/api/governance-gates/{gate_id}"),
                client.get("/admin/api/enterprise-roles/users"),
                client.get(f"/admin/user/{target_b.id}/delete"),
                client.post(f"/admin/user/{target_b.id}/_delete"),
                client.post(f"/admin/user/{target_b.id}/change-account-type", data={"account_type": 1}),
            ]
            assert [r.status_code for r in checks] == [403] * len(checks)

    def test_switched_org_bulk_delete_and_account_type_refused(self, app, login_as, client, scene):
        from app.models.user import User

        org_a, org_b, actor, target_a, target_b = scene
        tid = target_b.id
        with app.app_context():
            login_as(client, actor)
            self._switch(client, org_b)
            r = client.delete("/admin/api/users/bulk", json={"ids": [tid]})
            assert r.status_code == 403
            assert User.query.get(tid) is not None

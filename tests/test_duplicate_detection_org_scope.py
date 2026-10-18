"""Duplicate detection is available to each organisation's own administrator
and only ever reads, writes and deletes that organisation's own results."""
import uuid

import pytest

RUN_URL = "/duplicate-detection/simple/api/run-detection"

# (method, url) for every run, hybrid, enterprise, unified, cleanup and run-list route.
ADMIN_ROUTES = [
    ("POST", "/duplicate-detection/simple/api/run-detection"),
    ("POST", "/duplicate-detection/simple/run-detection"),
    ("POST", "/duplicate-detection/simple/run-hybrid"),
    ("POST", "/duplicate-detection/simple/cleanup"),
    ("POST", "/duplicate-detection/enterprise/run-detection"),
    ("POST", "/duplicate-detection/unified/run-detection"),
    ("POST", "/duplicate-detection/run-detection"),
    ("GET", "/duplicate-detection/simple/runs"),
    ("GET", "/duplicate-detection/enterprise/runs"),
    ("GET", "/duplicate-detection/unified/runs"),
    ("GET", "/duplicate-detection/api/statistics/summary"),
    ("GET", "/duplicate-detection/api/detection-runs"),
]

LIST_ROUTES = [
    "/duplicate-detection/simple/groups",
    "/duplicate-detection/enterprise/groups",
    "/duplicate-detection/unified/groups",
    "/duplicate-detection/simple/api/groups",
    "/duplicate-detection/api/duplicate-groups",
]

PAGE_ROUTES = [
    "/duplicate-detection/simple",
    "/duplicate-detection/unified",
    "/duplicate-detection/dashboard",
]


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Dedupe {label} {suffix}", slug=f"dedupe-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org, *, admin):
    from app.models.user import Role, User

    role_name = "Administrator" if admin else "User"
    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name=role_name).first()
    user = User(
        email=f"dedupe-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        role=role,
        is_org_admin=admin,
        is_platform_admin=False,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _apps(db_session, org, names):
    from app.models.application_portfolio import ApplicationComponent

    rows = [ApplicationComponent(name=n, organization_id=org.id) for n in names]
    db_session.add_all(rows)
    db_session.flush()
    return rows


def _seed_two_orgs(db_session):
    org_a, org_b = _org(db_session, "a"), _org(db_session, "b")
    admin_a = _user(db_session, org_a, admin=True)
    admin_b = _user(db_session, org_b, admin=True)
    member_a = _user(db_session, org_a, admin=False)
    apps_a = _apps(db_session, org_a, ["Payroll System", "Payroll System"])
    apps_b = _apps(db_session, org_b, ["Billing Engine", "Billing Engine"])
    db_session.commit()
    return org_a, org_b, admin_a, admin_b, member_a, apps_a, apps_b


def _group_names(payload):
    return {g.get("name") or g.get("group_name") for g in payload["groups"]}


class TestOrganisationAdminCanRunDetection:
    def test_fresh_organisation_admin_is_not_refused(self, app, db_session, login_as, client):
        org = _org(db_session, "fresh")
        admin = _user(db_session, org, admin=True)
        db_session.commit()
        login_as(client, admin)
        resp = client.post(RUN_URL, json={}, headers={"Accept": "application/json"})
        assert resp.status_code == 200, resp.get_data(as_text=True)

    @pytest.mark.parametrize("method,url", ADMIN_ROUTES)
    def test_route_admits_own_organisation_admin(self, db_session, login_as, client, method, url):
        org = _org(db_session, "route")
        admin = _user(db_session, org, admin=True)
        db_session.commit()
        login_as(client, admin)
        resp = client.open(url, method=method, json={} if method == "POST" else None,
                           headers={"Accept": "application/json"})
        assert resp.status_code not in (401, 403), (url, resp.status_code)

    @pytest.mark.parametrize("method,url", ADMIN_ROUTES)
    def test_route_refuses_non_admin_member(self, db_session, login_as, client, method, url):
        org = _org(db_session, "member")
        member = _user(db_session, org, admin=False)
        db_session.commit()
        login_as(client, member)
        resp = client.open(url, method=method, json={} if method == "POST" else None,
                           headers={"Accept": "application/json"})
        assert resp.status_code == 403, (url, resp.status_code)

    @pytest.mark.parametrize("url", LIST_ROUTES + PAGE_ROUTES)
    def test_lists_and_dashboards_open_to_member(self, db_session, login_as, client, url):
        org = _org(db_session, "list")
        member = _user(db_session, org, admin=False)
        db_session.commit()
        login_as(client, member)
        resp = client.get(url, headers={"Accept": "application/json"})
        assert resp.status_code not in (401, 403, 404), (url, resp.status_code)


class TestTwoOrganisationIsolation:
    def test_detection_returns_only_callers_groups(self, db_session, login_as, client):
        _, _, admin_a, admin_b, _, apps_a, apps_b = _seed_two_orgs(db_session)

        login_as(client, admin_b)
        assert client.post(RUN_URL, json={}).status_code == 200
        login_as(client, admin_a)
        resp = client.post(RUN_URL, json={})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["applications_analyzed"] == 2

        groups = client.get("/duplicate-detection/simple/groups").get_json()
        assert _group_names(groups) == {"Exact: payroll system"}
        assert "billing" not in str(groups).lower()

    def test_running_as_one_organisation_keeps_the_others_groups(self, app, db_session, login_as, client):
        from app.models.unified_duplicate_detection import UnifiedDetectionRun, UnifiedDuplicateGroup

        org_a, org_b, admin_a, admin_b, _, apps_a, apps_b = _seed_two_orgs(db_session)
        login_as(client, admin_b)
        assert client.post(RUN_URL, json={}).status_code == 200
        login_as(client, admin_a)
        assert client.post(RUN_URL, json={}).status_code == 200
        assert client.post("/duplicate-detection/simple/run-hybrid", json={}).status_code == 200
        assert client.post("/duplicate-detection/simple/cleanup", json={}).status_code == 200

        from sqlalchemy import text

        def count(table, org):
            return db_session.execute(
                text(f"SELECT count(*) FROM {table} WHERE organization_id = :o"), {"o": org.id}
            ).scalar()

        assert count("unified_duplicate_groups", org_b) == 1
        assert count("unified_detection_runs", org_b) == 1
        members_b = db_session.execute(
            text("SELECT count(*) FROM unified_group_members m JOIN unified_duplicate_groups g "
                 "ON g.id = m.group_id WHERE g.organization_id = :o"), {"o": org_b.id}
        ).scalar()
        assert members_b == 2
        assert count("unified_duplicate_groups", org_a) == 0
        assert count("unified_detection_runs", org_a) == 0

        login_as(client, admin_b)
        groups = client.get("/duplicate-detection/simple/groups").get_json()
        assert _group_names(groups) == {"Exact: billing engine"}

    def test_run_history_is_per_organisation(self, db_session, login_as, client):
        _, _, admin_a, admin_b, _, _, _ = _seed_two_orgs(db_session)
        login_as(client, admin_b)
        client.post(RUN_URL, json={})
        login_as(client, admin_a)
        runs = client.get("/duplicate-detection/simple/runs").get_json()["runs"]
        assert runs == []

    def test_other_organisations_group_detail_is_refused(self, db_session, login_as, client):
        from app.models.unified_duplicate_detection import UnifiedDuplicateGroup
        from sqlalchemy import text

        _, org_b, admin_a, admin_b, _, _, _ = _seed_two_orgs(db_session)
        login_as(client, admin_b)
        client.post(RUN_URL, json={})
        gid = db_session.execute(
            text("SELECT id FROM unified_duplicate_groups WHERE organization_id = :o"), {"o": org_b.id}
        ).scalar()
        login_as(client, admin_a)
        resp = client.get(f"/duplicate-detection/simple/group/{gid}")
        assert resp.status_code in (403, 404) or "Billing" not in resp.get_data(as_text=True)
        assert "Billing Engine" not in resp.get_data(as_text=True)

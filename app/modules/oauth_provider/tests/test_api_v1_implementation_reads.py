"""Work package and deliverable reads over the public interface.

Four GET routes under /api/v1 accept a signed-in session or a bearer token
carrying api:read. The organisation always comes from the token: A's token
lists only A's rows, B's id fetched with A's token answers not found, and a
token without an organisation, an api:write-only token, an mcp:* token, or
no token at all is refused. Lists page with page and per_page, ordered by
name then id, and an organisation with nothing returns an empty list. A
deliverable has no organisation column of its own: it is served only when every
work package it names belongs to the caller's organisation.

Requires MCP_ENABLED and PUBLIC_BASE_URL, like the other /api/v1 token
tests (bearer tokens only resolve while the connector is on).
"""

from __future__ import annotations

import os
import secrets

import pytest

from app.modules.oauth_provider.models import OAuthClient, OAuthToken, hash_secret

API_SCOPE = "api:read"
WRITE_SCOPE = "api:write"
MCP_SCOPE = "mcp:read"

_OAUTH_ACTIVE = (
    os.environ.get("MCP_ENABLED", "").lower() in ("1", "true", "yes")
    and bool(os.environ.get("PUBLIC_BASE_URL"))
)
pytestmark = pytest.mark.skipif(
    not _OAUTH_ACTIVE, reason="OAuth routes not registered (MCP_ENABLED off)"
)

LIST_ROUTES = ("/api/v1/work-packages", "/api/v1/deliverables")
GET_ROUTES = ("/api/v1/work-packages/1", "/api/v1/deliverables/1")


def _api_resource(app) -> str:
    base = (app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    assert base, "PUBLIC_BASE_URL must be set to run this module"
    return f"{base}/api/v1"


def _viewer_role():
    from app.models.user import Role

    role = Role.query.filter_by(name="Viewer").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Viewer").first()
    return role


def _make_service_user(db_session, org, email):
    """A non-human account: no sign-in, only scopes."""
    from app.models.user import User

    user = User(
        email=email,
        first_name="Service",
        last_name="User",
        organization_id=org.id,
        role=_viewer_role(),
        confirmed=True,
        is_service_user=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _register_client(db_session, user):
    """A confidential client-credentials client bound to one service user."""
    client = OAuthClient(
        client_id="svc_" + secrets.token_urlsafe(8),
        client_name="Machine Reader",
        client_secret_hash=hash_secret("machine-secret"),
        grant_types="client_credentials",
        scope=API_SCOPE,
        service_user_id=user.id,
        is_active=True,
    )
    db_session.add(client)
    db_session.flush()
    return client


def _mint_token(user, *, organization_id, scope=API_SCOPE, resource=None):
    """Issue an api:* token directly, the same way the grant would."""
    from flask import current_app

    from app.modules.oauth_provider.models import _new_client_id

    raw_access, _raw_refresh, _row = OAuthToken.issue(
        client_id=_new_client_id("at"),
        user_id=user.id,
        scope=scope,
        resource=resource or _api_resource(current_app._get_current_object()),
        grant_type="client_credentials",
        organization_id=organization_id,
    )
    return raw_access


def _clear_cached_identity():
    """db_session holds one app context for the whole test, so a fresh
    bearer-only call must clear flask-login's and the tenant middleware's
    cached identity first."""
    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _get(app, path, token=None):
    """GET a route on a session-free client, with an optional bearer token."""
    _clear_cached_identity()
    client = app.test_client()
    kwargs = {}
    if token is not None:
        kwargs["headers"] = {"Authorization": f"Bearer {token}"}
    return client.get(path, **kwargs)




def _seed(db_session, make_org):
    """Two organisations; A has three work packages and three deliverables
    (one reached only through a unified work package), B has one of each."""
    from datetime import date

    from app.models.implementation_migration import Deliverable, WorkPackage
    from sqlalchemy import text

    from app.models.unified_work_package import UnifiedWorkPackage

    # The model leaves work_package_id nullable (unified_work_package_id is the
    # key readers use); a database built before that change may still carry
    # NOT NULL. The change is transactional and rolls back with the test.
    db_session.execute(
        text("ALTER TABLE deliverables ALTER COLUMN work_package_id DROP NOT NULL")
    )

    org_a = make_org("alpha")
    org_b = make_org("beta")
    packages = {}
    for key, name, org in (
        ("a1", "gamma package", org_a),
        ("a2", "alpha package", org_a),
        ("a3", "zeta package", org_a),
        ("b", "beta secret package", org_b),
    ):
        packages[key] = WorkPackage(
            name=name,
            description="plain description",
            status="planned",
            percent_complete=40,
            start_date=date(2026, 1, 1),
            target_date=date(2026, 6, 30),
            estimated_cost=12345.0,
            organization_id=org.id,
        )
    db_session.add_all(packages.values())
    db_session.flush()
    packages["a2"].parent_id = packages["a1"].id
    unified_a = UnifiedWorkPackage(name="unified a", organization_id=org_a.id)
    unified_b = UnifiedWorkPackage(name="unified b", organization_id=org_b.id)
    db_session.add_all([unified_a, unified_b])
    db_session.flush()
    deliverables = {
        "a1": Deliverable(
            name="gamma deliverable",
            work_package_id=packages["a1"].id,
            delivery_status="planned",
            approval_status="approved",
            target_date=date(2026, 5, 1),
        ),
        "a2": Deliverable(
            name="alpha deliverable",
            work_package_id=packages["a2"].id,
            delivery_status="planned",
            approval_status="pending",
        ),
        "a3": Deliverable(
            name="zeta deliverable",
            unified_work_package_id=unified_a.id,
            delivery_status="planned",
        ),
        "b": Deliverable(
            name="beta secret deliverable",
            work_package_id=packages["b"].id,
            delivery_status="planned",
        ),
        "mixed": Deliverable(
            name="mixed parents deliverable",
            work_package_id=packages["a1"].id,
            unified_work_package_id=unified_b.id,
            delivery_status="planned",
        ),
        "orphan": Deliverable(name="orphan deliverable", delivery_status="planned"),
    }
    db_session.add_all(deliverables.values())
    db_session.flush()
    svc_a = _make_service_user(db_session, org_a, "svc-a@example.com")
    token = _mint_token(svc_a, organization_id=org_a.id)
    return org_a, org_b, packages, deliverables, token


class TestRefusals:
    """Who is refused, on every route."""

    def test_anonymous_is_refused(self, app, db_session, make_org):
        _seed(db_session, make_org)
        for path in LIST_ROUTES + GET_ROUTES:
            response = _get(app, path)
            assert response.status_code == 401, (path, response.status_code)

    def test_api_write_token_is_refused(self, app, db_session, make_org):
        org_a, *_rest = _seed(db_session, make_org)
        svc = _make_service_user(db_session, org_a, "svc-write@example.com")
        token = _mint_token(svc, organization_id=org_a.id, scope=WRITE_SCOPE)
        for path in LIST_ROUTES + GET_ROUTES:
            response = _get(app, path, token)
            assert response.status_code == 403, (path, response.status_code)

    def test_mcp_token_is_refused(self, app, db_session, make_org):
        org_a, *_rest = _seed(db_session, make_org)
        svc = _make_service_user(db_session, org_a, "svc-mcp@example.com")
        token = _mint_token(svc, organization_id=org_a.id, scope=MCP_SCOPE)
        for path in LIST_ROUTES + GET_ROUTES:
            response = _get(app, path, token)
            assert response.status_code == 401, (path, response.status_code)

    def test_token_without_organisation_is_refused(self, app, db_session, make_org):
        org_a, *_rest = _seed(db_session, make_org)
        svc = _make_service_user(db_session, org_a, "svc-noorg@example.com")
        token = _mint_token(svc, organization_id=None)
        for path in LIST_ROUTES + GET_ROUTES:
            response = _get(app, path, token)
            assert response.status_code == 401, (path, response.status_code)


class TestTwoOrganisations:
    """A's token sees only A; B's id with A's token is not found."""

    def test_token_lists_only_its_own_organisation(self, app, db_session, make_org):
        _a, _b, packages, deliverables, token = _seed(db_session, make_org)
        response = _get(app, "/api/v1/work-packages", token)
        assert response.status_code == 200, response.get_data(as_text=True)
        names = [i["name"] for i in response.get_json()["data"]["work_packages"]]
        assert names == ["alpha package", "gamma package", "zeta package"]

        response = _get(app, "/api/v1/deliverables", token)
        assert response.status_code == 200, response.get_data(as_text=True)
        names = [i["name"] for i in response.get_json()["data"]["deliverables"]]
        assert names == ["alpha deliverable", "gamma deliverable", "zeta deliverable"]
        assert response.get_json()["data"]["pagination"]["total"] == 3

    def test_token_fetching_another_organisations_row_is_not_found(
        self, app, db_session, make_org
    ):
        _a, _b, packages, deliverables, token = _seed(db_session, make_org)
        response = _get(app, f"/api/v1/work-packages/{packages['b'].id}", token)
        assert response.status_code == 404, response.get_data(as_text=True)
        assert response.get_json()["error"]["code"] == "NOT_FOUND"
        for key in ("b", "mixed", "orphan"):
            response = _get(app, f"/api/v1/deliverables/{deliverables[key].id}", token)
            assert response.status_code == 404, (key, response.get_data(as_text=True))
            assert response.get_json()["error"]["code"] == "NOT_FOUND"

    def test_token_fetches_its_own_rows(self, app, db_session, make_org):
        _a, _b, packages, deliverables, token = _seed(db_session, make_org)
        response = _get(app, f"/api/v1/work-packages/{packages['a2'].id}", token)
        assert response.status_code == 200, response.get_data(as_text=True)
        data = response.get_json()["data"]
        assert data["name"] == "alpha package"
        assert data["parent_work_package_id"] == packages["a1"].id
        assert data["start_date"] == "2026-01-01"
        assert data["end_date"] == "2026-06-30"
        assert data["percent_complete"] == 40
        assert data["lifecycle_state"] == "planned"

        response = _get(app, f"/api/v1/deliverables/{deliverables['a1'].id}", token)
        assert response.status_code == 200, response.get_data(as_text=True)
        data = response.get_json()["data"]
        assert data["accepted"] is True and data["due_date"] == "2026-05-01"
        response = _get(app, f"/api/v1/deliverables/{deliverables['a3'].id}", token)
        assert response.status_code == 200
        assert response.get_json()["data"]["accepted"] is False


class TestExposure:
    """Only plain facts are exposed."""

    def test_no_cost_or_owner_fields(self, app, db_session, make_org):
        _a, _b, packages, _deliverables, token = _seed(db_session, make_org)
        response = _get(app, f"/api/v1/work-packages/{packages['a1'].id}", token)
        data = response.get_json()["data"]
        for hidden in ("estimated_cost", "actual_cost", "owner_id", "dependencies"):
            assert hidden not in data
        assert "12345" not in response.get_data(as_text=True)


class TestPaginationAndOrder:
    """Lists page with page/per_page, ordered by name then id."""

    def test_pagination_and_order(self, app, db_session, make_org):
        *_rest, token = _seed(db_session, make_org)
        response = _get(app, "/api/v1/work-packages?page=1&per_page=2", token)
        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        assert [i["name"] for i in body["data"]["work_packages"]] == [
            "alpha package",
            "gamma package",
        ]
        assert body["data"]["pagination"] == {
            "page": 1,
            "per_page": 2,
            "total": 3,
            "pages": 2,
        }
        response = _get(app, "/api/v1/work-packages?page=2&per_page=2", token)
        assert [i["name"] for i in response.get_json()["data"]["work_packages"]] == [
            "zeta package"
        ]

        response = _get(app, "/api/v1/deliverables?page=1&per_page=2", token)
        body = response.get_json()
        assert [i["name"] for i in body["data"]["deliverables"]] == [
            "alpha deliverable",
            "gamma deliverable",
        ]
        assert body["data"]["pagination"]["total"] == 3
        assert body["data"]["pagination"]["pages"] == 2

    def test_organisation_with_nothing_returns_an_empty_list(
        self, app, db_session, make_org
    ):
        _seed(db_session, make_org)
        org_c = make_org("gamma")
        svc_c = _make_service_user(db_session, org_c, "svc-c@example.com")
        token = _mint_token(svc_c, organization_id=org_c.id)
        for path, key in (
            ("/api/v1/work-packages", "work_packages"),
            ("/api/v1/deliverables", "deliverables"),
        ):
            response = _get(app, path, token)
            assert response.status_code == 200, response.get_data(as_text=True)
            assert response.get_json()["data"][key] == []
            assert response.get_json()["data"]["pagination"]["total"] == 0


class TestSessionStillWorks:
    """A signed-in session keeps working beside the new token routes."""

    def test_signed_in_session_user_reads_own_organisation(
        self, app, client, db_session, login_as, make_org
    ):
        org_a, _b, packages, deliverables, _token = _seed(db_session, make_org)
        person = _make_service_user(db_session, org_a, "person-a@example.com")
        login_as(client, person)
        response = client.get("/api/v1/work-packages")
        assert response.status_code == 200, response.get_data(as_text=True)
        names = [i["name"] for i in response.get_json()["data"]["work_packages"]]
        assert packages["a1"].name in names
        response = client.get("/api/v1/deliverables")
        assert response.status_code == 200
        names = [i["name"] for i in response.get_json()["data"]["deliverables"]]
        assert deliverables["a1"].name in names

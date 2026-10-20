"""Business reads over the public interface (business actors and business
processes).

Four GET routes under /api/v1 accept a signed-in session or a bearer
token carrying api:read. The organisation always comes from the token:
A's token lists only A's rows, B's id fetched with A's token answers not
found, and a token without an organisation, an api:write-only token, an
mcp:* token, or no token at all is refused. Lists page with page and
per_page, ordered by name then id, and an organisation with nothing
returns an empty list.

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

LIST_ROUTES = ("/api/v1/business-actors", "/api/v1/business-processes")


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
    """Two organisations, each with three actors and three processes."""
    from app.models.business_layer import BusinessActor
    from app.models.process_data import BusinessProcess

    org_a = make_org("alpha")
    org_b = make_org("beta")
    actor_a1 = BusinessActor(
        name="gamma actor", organization_id=org_a.id, actor_type="Department", operational_status="active"
    )
    actor_a2 = BusinessActor(
        name="alpha actor", organization_id=org_a.id, actor_type="Team", operational_status="active"
    )
    actor_a3 = BusinessActor(
        name="zeta actor", organization_id=org_a.id, actor_type="Business Unit", operational_status="active"
    )
    actor_b = BusinessActor(
        name="beta secret", organization_id=org_b.id, actor_type="Department", operational_status="active"
    )
    process_a1 = BusinessProcess(
        name="gamma process", organization_id=org_a.id, process_type="core", status="active"
    )
    process_a2 = BusinessProcess(
        name="alpha process", organization_id=org_a.id, process_type="support", status="active"
    )
    process_a3 = BusinessProcess(
        name="zeta process", organization_id=org_a.id, process_type="management", status="active"
    )
    process_b = BusinessProcess(
        name="beta process", organization_id=org_b.id, process_type="core", status="active"
    )
    db_session.add_all([actor_a1, actor_a2, actor_a3, actor_b, process_a1, process_a2, process_a3, process_b])
    db_session.flush()
    svc_a = _make_service_user(db_session, org_a, "svc-a@example.com")
    token = _mint_token(svc_a, organization_id=org_a.id)
    return org_a, org_b, actor_a1, actor_a2, actor_a3, actor_b, process_a1, process_a2, process_a3, process_b, token


class TestRefusals:
    """Who is refused, on every route.
    A user with no organisation cannot exist because the organisation_id column is non-nullable with a default.
    This case is covered by the explicit guard in the routes and by the token-without-organisation test.
    """

    def test_anonymous_is_refused(self, app, db_session, make_org):
        _seed(db_session, make_org)
        for path in LIST_ROUTES + (
            "/api/v1/business-actors/1",
            "/api/v1/business-processes/1",
        ):
            response = _get(app, path)
            assert response.status_code == 401, (path, response.status_code)

    def test_api_write_token_is_refused(self, app, db_session, make_org):
        org_a, _org_b, _actor_a1, _actor_a2, _actor_a3, _actor_b, _process_a1, _process_a2, _process_a3, _process_b, _token = _seed(
            db_session, make_org
        )
        svc_a = _make_service_user(db_session, org_a, "svc-write@example.com")
        write_token = _mint_token(svc_a, organization_id=org_a.id, scope=WRITE_SCOPE)
        for path in LIST_ROUTES + (
            "/api/v1/business-actors/1",
            "/api/v1/business-processes/1",
        ):
            response = _get(app, path, write_token)
            assert response.status_code == 403, (path, response.status_code)

    def test_mcp_token_is_refused(self, app, db_session, make_org):
        org_a, _org_b, _actor_a1, _actor_a2, _actor_a3, _actor_b, _process_a1, _process_a2, _process_a3, _process_b, _token = _seed(
            db_session, make_org
        )
        svc_a = _make_service_user(db_session, org_a, "svc-mcp@example.com")
        mcp_token = _mint_token(svc_a, organization_id=org_a.id, scope=MCP_SCOPE)
        for path in LIST_ROUTES + (
            "/api/v1/business-actors/1",
            "/api/v1/business-processes/1",
        ):
            response = _get(app, path, mcp_token)
            assert response.status_code == 401, (path, response.status_code)

    def test_token_without_organisation_is_refused(self, app, db_session, make_org):
        _org_a, _org_b, _actor_a1, _actor_a2, _actor_a3, _actor_b, _process_a1, _process_a2, _process_a3, _process_b, _token = _seed(
            db_session, make_org
        )
        svc_a = _make_service_user(db_session, _org_a, "svc-noorg@example.com")
        orgless_token = _mint_token(svc_a, organization_id=None)
        for path in LIST_ROUTES + (
            "/api/v1/business-actors/1",
            "/api/v1/business-processes/1",
        ):
            response = _get(app, path, orgless_token)
            assert response.status_code == 401, (path, response.status_code)


class TestTwoOrganisations:
    """A's token sees only A; B's id with A's token is not found."""

    def test_token_lists_only_its_own_organisation(
        self, app, client, db_session, make_org
    ):
        org_a, org_b, actor_a1, actor_a2, _actor_a3, actor_b, process_a1, process_a2, _process_a3, process_b, token = _seed(
            db_session, make_org
        )

        response = _get(app, "/api/v1/business-actors", token)
        assert response.status_code == 200, response.get_data(as_text=True)
        names = [item["name"] for item in response.get_json()["data"]["business_actors"]]
        assert actor_a1.name in names
        assert actor_a2.name in names
        assert actor_b.name not in names, "another organisation's actor was returned"

        response = _get(app, "/api/v1/business-processes", token)
        assert response.status_code == 200, response.get_data(as_text=True)
        names = [item["name"] for item in response.get_json()["data"]["business_processes"]]
        assert process_a1.name in names
        assert process_a2.name in names
        assert process_b.name not in names, "another organisation's process was returned"

    def test_token_fetching_another_organisations_row_is_not_found(
        self, app, client, db_session, make_org
    ):
        _org_a, _org_b, _actor_a1, _actor_a2, _actor_a3, actor_b, _process_a1, _process_a2, _process_a3, process_b, token = _seed(
            db_session, make_org
        )

        response = _get(app, f"/api/v1/business-actors/{actor_b.id}", token)
        assert response.status_code == 404, response.get_data(as_text=True)
        assert response.get_json()["error"]["code"] == "NOT_FOUND"

        response = _get(app, f"/api/v1/business-processes/{process_b.id}", token)
        assert response.status_code == 404, response.get_data(as_text=True)
        assert response.get_json()["error"]["code"] == "NOT_FOUND"


class TestPaginationAndOrder:
    """Lists page with page/per_page, ordered by name then id."""

    def test_actors_pagination_and_order(
        self, app, client, db_session, make_org
    ):
        org_a, _org_b, actor_a1, actor_a2, actor_a3, _actor_b, _process_a1, _process_a2, _process_a3, _process_b, token = _seed(
            db_session, make_org
        )

        response = _get(app, "/api/v1/business-actors?page=1&per_page=2", token)
        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        names = [item["name"] for item in body["data"]["business_actors"]]
        assert names == ["alpha actor", "gamma actor"]
        assert body["data"]["pagination"] == {
            "page": 1,
            "per_page": 2,
            "total": 3,
            "pages": 2,
        }

        response = _get(app, "/api/v1/business-actors?page=2&per_page=2", token)
        assert response.status_code == 200
        names = [item["name"] for item in response.get_json()["data"]["business_actors"]]
        assert names == ["zeta actor"]

    def test_processes_pagination_and_order(
        self, app, client, db_session, make_org
    ):
        org_a, _org_b, _actor_a1, _actor_a2, _actor_a3, _actor_b, process_a1, process_a2, process_a3, _process_b, token = _seed(
            db_session, make_org
        )

        response = _get(app, "/api/v1/business-processes?page=1&per_page=2", token)
        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        names = [item["name"] for item in body["data"]["business_processes"]]
        assert names == ["alpha process", "gamma process"]
        assert body["data"]["pagination"] == {
            "page": 1,
            "per_page": 2,
            "total": 3,
            "pages": 2,
        }

        response = _get(app, "/api/v1/business-processes?page=2&per_page=2", token)
        assert response.status_code == 200
        names = [item["name"] for item in response.get_json()["data"]["business_processes"]]
        assert names == ["zeta process"]

    def test_organisation_with_nothing_returns_an_empty_list(
        self, app, client, db_session, make_org
    ):
        _seed(db_session, make_org)
        org_c = make_org("gamma")
        svc_c = _make_service_user(db_session, org_c, "svc-c@example.com")
        token = _mint_token(svc_c, organization_id=org_c.id)

        response = _get(app, "/api/v1/business-actors", token)
        assert response.status_code == 200, response.get_data(as_text=True)
        assert response.get_json()["data"]["business_actors"] == []
        assert response.get_json()["data"]["pagination"]["total"] == 0

        response = _get(app, "/api/v1/business-processes", token)
        assert response.status_code == 200
        assert response.get_json()["data"]["business_processes"] == []


class TestSessionStillWorks:
    """A signed-in session keeps working beside the new token routes."""

    def test_signed_in_session_user_reads_own_organisation(
        self, app, client, db_session, login_as, make_org
    ):
        org_a, _org_b, actor_a1, _actor_a2, _actor_a3, _actor_b, process_a1, _process_a2, _process_a3, _process_b, _token = _seed(
            db_session, make_org
        )
        person = _make_service_user(db_session, org_a, "person-a@example.com")
        login_as(client, person)
        response = client.get("/api/v1/business-actors")
        assert response.status_code == 200, response.get_data(as_text=True)
        names = [item["name"] for item in response.get_json()["data"]["business_actors"]]
        assert actor_a1.name in names

        response = client.get("/api/v1/business-processes")
        assert response.status_code == 200
        names = [item["name"] for item in response.get_json()["data"]["business_processes"]]
        assert process_a1.name in names

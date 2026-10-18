"""Strategy reads over the public interface (capabilities and value
streams).

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

LIST_ROUTES = ("/api/v1/capabilities", "/api/v1/value-streams")


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
    """Two organisations, each with a capability and a value stream."""
    from app.models.unified_capability import UnifiedCapability, ValueStream

    org_a = make_org("alpha")
    org_b = make_org("beta")
    cap_a1 = UnifiedCapability(
        name="gamma capability", organization_id=org_a.id, level=1, status="defined"
    )
    cap_a2 = UnifiedCapability(
        name="alpha capability", organization_id=org_a.id, level=2, status="defined"
    )
    cap_a3 = UnifiedCapability(
        name="zeta capability", organization_id=org_a.id, level=3, status="defined"
    )
    cap_b = UnifiedCapability(
        name="beta secret", organization_id=org_b.id, level=1, status="defined"
    )
    vs_a = ValueStream(
        name="gamma stream", organization_id=org_a.id, code="GAM",
        value_stream_type="customer_facing",
    )
    vs_b = ValueStream(
        name="beta stream", organization_id=org_b.id, code="BET",
        value_stream_type="internal",
    )
    db_session.add_all([cap_a1, cap_a2, cap_a3, cap_b, vs_a, vs_b])
    db_session.flush()
    svc_a = _make_service_user(db_session, org_a, "svc-a@example.com")
    token = _mint_token(svc_a, organization_id=org_a.id)
    return org_a, org_b, cap_a1, cap_a2, cap_b, vs_a, vs_b, token


class TestRefusals:
    """Who is refused, on every route."""

    def test_anonymous_is_refused(self, app, db_session, make_org):
        _seed(db_session, make_org)
        for path in LIST_ROUTES + (
            "/api/v1/capabilities/1",
            "/api/v1/value-streams/1",
        ):
            response = _get(app, path)
            assert response.status_code == 401, (path, response.status_code)

    def test_api_write_token_is_refused(self, app, db_session, make_org):
        org_a, _org_b, _cap_a1, _cap_a2, _cap_b, _vs_a, _vs_b, _token = _seed(
            db_session, make_org
        )
        svc_a = _make_service_user(db_session, org_a, "svc-write@example.com")
        write_token = _mint_token(svc_a, organization_id=org_a.id, scope=WRITE_SCOPE)
        for path in LIST_ROUTES + (
            "/api/v1/capabilities/1",
            "/api/v1/value-streams/1",
        ):
            response = _get(app, path, write_token)
            assert response.status_code == 403, (path, response.status_code)

    def test_mcp_token_is_refused(self, app, db_session, make_org):
        org_a, _org_b, _cap_a1, _cap_a2, _cap_b, _vs_a, _vs_b, _token = _seed(
            db_session, make_org
        )
        svc_a = _make_service_user(db_session, org_a, "svc-mcp@example.com")
        mcp_token = _mint_token(svc_a, organization_id=org_a.id, scope=MCP_SCOPE)
        for path in LIST_ROUTES + (
            "/api/v1/capabilities/1",
            "/api/v1/value-streams/1",
        ):
            response = _get(app, path, mcp_token)
            assert response.status_code == 401, (path, response.status_code)

    def test_token_without_organisation_is_refused(self, app, db_session, make_org):
        _org_a, _org_b, _cap_a1, _cap_a2, _cap_b, _vs_a, _vs_b, _token = _seed(
            db_session, make_org
        )
        svc_a = _make_service_user(db_session, _org_a, "svc-noorg@example.com")
        orgless_token = _mint_token(svc_a, organization_id=None)
        for path in LIST_ROUTES + (
            "/api/v1/capabilities/1",
            "/api/v1/value-streams/1",
        ):
            response = _get(app, path, orgless_token)
            assert response.status_code == 401, (path, response.status_code)


class TestTwoOrganisations:
    """A's token sees only A; B's id with A's token is not found."""

    def test_token_lists_only_its_own_organisation(
        self, app, client, db_session, make_org
    ):
        org_a, org_b, cap_a1, cap_a2, cap_b, vs_a, vs_b, token = _seed(
            db_session, make_org
        )

        response = _get(app, "/api/v1/capabilities", token)
        assert response.status_code == 200, response.get_data(as_text=True)
        names = [item["name"] for item in response.get_json()["data"]["capabilities"]]
        assert cap_a1.name in names
        assert cap_a2.name in names
        assert cap_b.name not in names, "another organisation's capability was returned"

        response = _get(app, "/api/v1/value-streams", token)
        assert response.status_code == 200, response.get_data(as_text=True)
        names = [item["name"] for item in response.get_json()["data"]["value_streams"]]
        assert vs_a.name in names
        assert vs_b.name not in names, "another organisation's value stream was returned"

    def test_token_fetching_another_organisations_row_is_not_found(
        self, app, client, db_session, make_org
    ):
        _org_a, _org_b, _cap_a1, _cap_a2, cap_b, _vs_a, vs_b, token = _seed(
            db_session, make_org
        )

        response = _get(app, f"/api/v1/capabilities/{cap_b.id}", token)
        assert response.status_code == 404, response.get_data(as_text=True)
        assert response.get_json()["error"]["code"] == "NOT_FOUND"

        response = _get(app, f"/api/v1/value-streams/{vs_b.id}", token)
        assert response.status_code == 404, response.get_data(as_text=True)
        assert response.get_json()["error"]["code"] == "NOT_FOUND"


class TestPaginationAndOrder:
    """Lists page with page/per_page, ordered by name then id."""

    def test_list_pages_and_orders_by_name_then_id(
        self, app, client, db_session, make_org
    ):
        org_a, _org_b, cap_a1, cap_a2, _cap_b, _vs_a, _vs_b, token = _seed(
            db_session, make_org
        )

        response = _get(app, "/api/v1/capabilities?page=1&per_page=2", token)
        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        names = [item["name"] for item in body["data"]["capabilities"]]
        assert names == ["alpha capability", "gamma capability"]
        assert body["data"]["pagination"] == {
            "page": 1,
            "per_page": 2,
            "total": 3,
            "pages": 2,
        }

        response = _get(app, "/api/v1/capabilities?page=2&per_page=2", token)
        assert response.status_code == 200
        names = [item["name"] for item in response.get_json()["data"]["capabilities"]]
        assert names == ["zeta capability"]

    def test_organisation_with_nothing_returns_an_empty_list(
        self, app, client, db_session, make_org
    ):
        _seed(db_session, make_org)
        org_c = make_org("gamma")
        svc_c = _make_service_user(db_session, org_c, "svc-c@example.com")
        token = _mint_token(svc_c, organization_id=org_c.id)

        response = _get(app, "/api/v1/capabilities", token)
        assert response.status_code == 200, response.get_data(as_text=True)
        assert response.get_json()["data"]["capabilities"] == []
        assert response.get_json()["data"]["pagination"]["total"] == 0

        response = _get(app, "/api/v1/value-streams", token)
        assert response.status_code == 200
        assert response.get_json()["data"]["value_streams"] == []


class TestSessionStillWorks:
    """A signed-in session keeps working beside the new token routes."""

    def test_signed_in_session_user_reads_own_organisation(
        self, app, client, db_session, login_as, make_org
    ):
        org_a, _org_b, cap_a1, _cap_a2, _cap_b, _vs_a, _vs_b, _token = _seed(
            db_session, make_org
        )
        person = _make_service_user(db_session, org_a, "person-a@example.com")
        login_as(client, person)
        response = client.get("/api/v1/capabilities")
        assert response.status_code == 200, response.get_data(as_text=True)
        names = [item["name"] for item in response.get_json()["data"]["capabilities"]]
        assert cap_a1.name in names

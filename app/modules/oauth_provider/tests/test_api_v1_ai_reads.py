"""AI-system reads over the public interface.

Two GET routes under /api/v1 accept a signed-in session or a bearer token
carrying api:read. The organisation always comes from the token: A's token
lists only A's rows, B's id fetched with A's token answers not found, and a
token without an organisation, an api:write-only token, an mcp:* token, or
no token at all is refused. Lists page with page and per_page, ordered by
name then id, and an organisation with nothing returns an empty list. AI
systems are read through the genome's slice builder, so the public read and
the product screen agree.

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

LIST_ROUTES = ("/api/v1/ai-systems",)
GET_ROUTES = ("/api/v1/ai-systems/1",)


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
    """Two organisations; A has three AI systems, B has one."""
    from app.modules.enterprise_genome.services.ai_systems_seed import register_ai_system

    org_a = make_org("alpha")
    org_b = make_org("beta")
    systems = {}
    for key, name, org, risk in (
        ("a1", "gamma system", org_a, "internal"),
        ("a2", "alpha system", org_a, "regulated"),
        ("a3", "zeta system", org_a, "public"),
        ("b", "beta secret system", org_b, "internal"),
    ):
        systems[key] = register_ai_system(
            db_session,
            org.id,
            name=name,
            provider="openai",
            model_id="gpt-4o",
            purpose="plain purpose",
            autonomy_level="assisted",
            data_sensitivity=risk,
            approval_gate=True,
            human_review=True,
        )
    db_session.flush()
    svc_a = _make_service_user(db_session, org_a, "svc-a@example.com")
    token = _mint_token(svc_a, organization_id=org_a.id)
    return org_a, org_b, systems, token


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
        *_rest, token = _seed(db_session, make_org)
        response = _get(app, "/api/v1/ai-systems", token)
        assert response.status_code == 200, response.get_data(as_text=True)
        names = [i["name"] for i in response.get_json()["data"]["ai_systems"]]
        assert names == ["alpha system", "gamma system", "zeta system"]
        assert response.get_json()["data"]["pagination"]["total"] == 3

    def test_token_fetching_another_organisations_row_is_not_found(
        self, app, db_session, make_org
    ):
        _a, _b, systems, token = _seed(db_session, make_org)
        response = _get(app, f"/api/v1/ai-systems/{systems['b'].id}", token)
        assert response.status_code == 404, response.get_data(as_text=True)
        assert response.get_json()["error"]["code"] == "NOT_FOUND"


class TestAISystemAgreesWithProfile:
    """The public read and the product screen show the same values."""

    def test_public_read_matches_the_profile_builder(self, app, db_session, make_org):
        from app.modules.enterprise_genome.services.ai_system_profile import (
            profile_from_element,
        )

        _a, _b, systems, token = _seed(db_session, make_org)
        element = systems["a2"]
        profile = profile_from_element(element)
        response = _get(app, f"/api/v1/ai-systems/{element.id}", token)
        assert response.status_code == 200, response.get_data(as_text=True)
        data = response.get_json()["data"]
        assert data["id"] == profile["archimate_element_id"]
        for field in (
            "name",
            "purpose",
            "provider",
            "model_id",
            "autonomy_level",
            "risk_class",
            "release_state",
            "assessment_accepted",
        ):
            assert data[field] == profile[field], field
        assert data["human_oversight"] == profile["governance"]["human_review"]

        listed = _get(app, "/api/v1/ai-systems", token).get_json()["data"]["ai_systems"]
        assert [i for i in listed if i["id"] == element.id] == [data]


class TestExposure:
    """Only plain facts are exposed."""

    def test_no_owner_in_the_response(self, app, db_session, make_org):
        _a, _b, systems, token = _seed(db_session, make_org)
        response = _get(app, f"/api/v1/ai-systems/{systems['a1'].id}", token)
        assert "owner" not in response.get_json()["data"]


class TestPaginationAndOrder:
    """Lists page with page/per_page, ordered by name then id."""

    def test_pagination_and_order(self, app, db_session, make_org):
        *_rest, token = _seed(db_session, make_org)
        for path, key, names in (
            (
                "/api/v1/ai-systems",
                "ai_systems",
                ["alpha system", "gamma system", "zeta system"],
            ),
        ):
            response = _get(app, f"{path}?page=1&per_page=2", token)
            assert response.status_code == 200, response.get_data(as_text=True)
            body = response.get_json()
            assert [i["name"] for i in body["data"][key]] == names[:2]
            assert body["data"]["pagination"] == {
                "page": 1,
                "per_page": 2,
                "total": 3,
                "pages": 2,
            }
            response = _get(app, f"{path}?page=2&per_page=2", token)
            assert [i["name"] for i in response.get_json()["data"][key]] == names[2:]

    def test_organisation_with_nothing_returns_an_empty_list(
        self, app, db_session, make_org
    ):
        _seed(db_session, make_org)
        org_c = make_org("gamma")
        svc_c = _make_service_user(db_session, org_c, "svc-c@example.com")
        token = _mint_token(svc_c, organization_id=org_c.id)
        for path, key in (("/api/v1/ai-systems", "ai_systems"),):
            response = _get(app, path, token)
            assert response.status_code == 200, response.get_data(as_text=True)
            assert response.get_json()["data"][key] == []
            assert response.get_json()["data"]["pagination"]["total"] == 0


class TestSessionStillWorks:
    """A signed-in session keeps working beside the new token routes."""

    def test_signed_in_session_user_reads_own_organisation(
        self, app, client, db_session, login_as, make_org
    ):
        org_a, _b, systems, _token = _seed(db_session, make_org)
        person = _make_service_user(db_session, org_a, "person-a@example.com")
        login_as(client, person)
        response = client.get("/api/v1/ai-systems")
        assert response.status_code == 200, response.get_data(as_text=True)
        names = [i["name"] for i in response.get_json()["data"]["ai_systems"]]
        assert systems["a1"].name in names

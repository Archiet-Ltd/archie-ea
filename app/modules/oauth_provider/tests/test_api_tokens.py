"""Scoped organisation tokens for the first /api/v1 read route family.

A machine client reads the model through the first family of /api/v1 read
endpoints (the element list and element detail routes) with a scoped,
expiring organisation token. The token is issued by a client-credentials
grant to a service user; connector tokens (mcp:*) never open these routes,
and api:* tokens never open /mcp.

Everything here fails on main, where no /api/v1 route accepts a token and
the token endpoint has no client-credentials grant.

Scope model: a token carries one namespace. ``mcp:*`` scopes belong to the
connector; ``api:*`` scopes belong to the REST API. A token with any scope
from the other family is refused on each. The organisation always comes
from the token, never from the user or the request.

Requires MCP_ENABLED and PUBLIC_BASE_URL (see the module docstring of
test_oauth_flow.py for the run command).
"""

from __future__ import annotations

import secrets

from app.modules.mcp.tests.test_scope_enforcement import _clear_cached_identity, _mcp_call
from app.modules.mcp.tests.test_tools import _make_element
from app.modules.oauth_provider.tests.test_oauth_flow import _make_user, _register_client

API_SCOPE = "api:read"
MCP_SCOPE = "mcp:read"
SERVICE_CLIENT_SECRET = "machine-secret"


def _api_resource_value(app) -> str:
    """The one resource identifier REST tokens are issued for."""
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


def _make_service_user(db_session, org, email, *, password=None):
    """A non-human account: no sign-in, no role powers, only scopes."""
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
    if password is not None:
        user.password = password
    db_session.add(user)
    db_session.flush()
    return user


def _register_service_client(db_session, user, *, scope=API_SCOPE):
    """A confidential client bound to one service user, as the management
    command creates. Only ``scope`` (the scopes it was registered with) may
    ever be issued to it."""
    from app.modules.oauth_provider.models import OAuthClient, hash_secret

    client = OAuthClient(
        client_id=f"svc_{secrets.token_urlsafe(16)}",
        client_name="Machine Reader",
        client_secret_hash=hash_secret(SERVICE_CLIENT_SECRET),
        grant_types="client_credentials",
        token_endpoint_auth_method="client_secret_post",
        scope=scope,
        service_user_id=user.id,
        is_active=True,
    )
    db_session.add(client)
    db_session.flush()
    return client


def _mint_api_token(user, *, organization_id, scope=API_SCOPE, expires_in=3600,
                    resource=None, client_id=None):
    """Issue an api:* token directly, bypassing the grant — the same way the
    existing scope tests construct tokens with a deliberate property."""
    from flask import current_app

    from app.modules.oauth_provider.models import OAuthToken, _new_client_id

    raw_access, _raw_refresh, _row = OAuthToken.issue(
        client_id=client_id or _new_client_id("at"),
        user_id=user.id,
        scope=scope,
        resource=resource or _api_resource_value(current_app._get_current_object()),
        grant_type="client_credentials",
        organization_id=organization_id,
        expires_in=expires_in,
    )
    return raw_access


def _api_get(app, path, token=None):
    """GET a route under the first-family blueprint, with an optional bearer
    header, on a session-free client."""
    _clear_cached_identity()
    bearer_client = app.test_client()
    kwargs = {}
    if token is not None:
        kwargs["headers"] = {"Authorization": f"Bearer {token}"}
    resp = bearer_client.get(path, **kwargs)
    return resp.status_code, resp.get_json()


def _token_exchange(client, payload: dict) -> tuple[int, dict]:
    """POST /oauth/token the way an OAuth client would."""
    _clear_cached_identity()
    resp = client.post("/oauth/token", data=payload)
    return resp.status_code, resp.get_json()


def _api_get_on(client, path, token=None):
    """GET with the shared client (which may carry a session cookie)."""
    _clear_cached_identity()
    kwargs = {}
    if token is not None:
        kwargs["headers"] = {"Authorization": f"Bearer {token}"}
    return client.get(path, **kwargs)


def _element_names(body):
    return [item["name"] for item in (body.get("data") or {}).get("elements", [])]


class TestClientCredentialsGrant:
    """A registered service client gets an api:read token; only the scopes it
    was registered with can be issued; no refresh token exists."""

    def test_registered_service_client_obtains_api_read_token(self, app, client, db_session, make_org):
        org = make_org("cc-grant")
        svc = _make_service_user(db_session, org, "cc-grant@example.com")
        oc = _register_service_client(db_session, svc)

        status, body = _token_exchange(client, {
            "grant_type": "client_credentials",
            "client_id": oc.client_id,
            "client_secret": SERVICE_CLIENT_SECRET,
            "scope": API_SCOPE,
            "resource": _api_resource_value(app),
        })
        assert status == 200, body
        assert body["access_token"]
        assert body["token_type"] == "Bearer"
        assert body["scope"] == API_SCOPE
        assert "refresh_token" not in body, body

        from app.modules.oauth_provider.models import OAuthToken

        row = OAuthToken.find_by_access_token(body["access_token"])
        assert row is not None
        assert row.scope == API_SCOPE
        assert row.organization_id == org.id
        assert row.user_id == svc.id
        assert row.refresh_token is None, "a client-credentials token carries no refresh token"

    def test_scope_the_client_was_not_registered_with_is_refused(self, app, client, db_session, make_org):
        org = make_org("cc-scope")
        svc = _make_service_user(db_session, org, "cc-scope@example.com")
        oc = _register_service_client(db_session, svc, scope=API_SCOPE)

        for bad_scope in ("api:write", "mcp:read", "api:read api:write"):
            status, body = _token_exchange(client, {
                "grant_type": "client_credentials",
                "client_id": oc.client_id,
                "client_secret": SERVICE_CLIENT_SECRET,
                "scope": bad_scope,
                "resource": _api_resource_value(app),
            })
            assert status == 400, (bad_scope, body)
            assert body["error"] == "invalid_scope", (bad_scope, body)

    def test_a_public_client_cannot_use_client_credentials(self, app, client, db_session, make_org):
        org = make_org("cc-public")
        _make_user(db_session, org, "cc-public@example.com")
        oc = _register_client()  # the dynamic-registration client: no secret, no service user

        status, body = _token_exchange(client, {
            "grant_type": "client_credentials",
            "client_id": oc.client_id,
            "client_secret": "anything-at-all",
            "scope": API_SCOPE,
            "resource": _api_resource_value(app),
        })
        # Refused as a client, not as a random resource mismatch.
        assert status == 401, body
        assert body["error"] == "invalid_client", body

    def test_wrong_client_secret_is_refused(self, app, client, db_session, make_org):
        org = make_org("cc-secret")
        svc = _make_service_user(db_session, org, "cc-secret@example.com")
        oc = _register_service_client(db_session, svc)

        status, body = _token_exchange(client, {
            "grant_type": "client_credentials",
            "client_id": oc.client_id,
            "client_secret": "wrong-secret",
            "scope": API_SCOPE,
            "resource": _api_resource_value(app),
        })
        assert status == 401, body
        assert body["error"] == "invalid_client", body

    def test_client_credentials_never_issues_for_another_orgs_service_user(
        self, app, client, db_session, make_org
    ):
        """The organisation always comes from the client's own bound service
        user; nothing a caller sends can point the grant at another
        organisation's service user."""
        org_a = make_org("cc-org-a")
        org_b = make_org("cc-org-b")
        svc_a = _make_service_user(db_session, org_a, "cc-a@example.com")
        svc_b = _make_service_user(db_session, org_b, "cc-b@example.com")
        oc_a = _register_service_client(db_session, svc_a)

        for extra in ({"organization_id": str(org_b.id)}, {"user_id": str(svc_b.id)}):
            payload = {
                "grant_type": "client_credentials",
                "client_id": oc_a.client_id,
                "client_secret": SERVICE_CLIENT_SECRET,
                "scope": API_SCOPE,
                "resource": _api_resource_value(app),
            }
            payload.update(extra)
            status, body = _token_exchange(client, payload)
            assert status == 200, body

            from app.modules.oauth_provider.models import OAuthToken

            row = OAuthToken.find_by_access_token(body["access_token"])
            assert row.user_id == svc_a.id, (extra, "a token was minted for another service user")
            assert row.organization_id == org_a.id, (extra, "a token was minted for another organisation")


class TestFirstFamilyRouteAccess:
    """The element list and detail routes accept a session or an api:read
    token and nothing else; the organisation comes from the token."""

    def _seed(self, db_session, make_org):
        org_a = make_org("api-org-a")
        org_b = make_org("api-org-b")
        el_a = _make_element(db_session, org_a.id, "alpha-known")
        el_b = _make_element(db_session, org_b.id, "bravo-secret")
        svc_a = _make_service_user(db_session, org_a, "svc-a@example.com")
        token = _mint_api_token(svc_a, organization_id=org_a.id)
        return org_a, org_b, el_a, el_b, token

    def test_anonymous_is_refused(self, app, client, db_session, make_org):
        self._seed(db_session, make_org)
        status, body = _api_get(app, "/api/v1/elements")
        assert status == 401, body

    def test_a_signed_in_session_user_reads_own_org_elements(self, app, client, db_session, login_as, make_org):
        org_a, _org_b, el_a, _el_b, _token = self._seed(db_session, make_org)
        person = _make_user(db_session, org_a, "person-a@example.com")
        login_as(client, person)
        resp = _api_get_on(client, "/api/v1/elements")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        names = _element_names(resp.get_json())
        assert "E-alpha-known" in names
        assert "E-bravo-secret" not in names
        resp = _api_get_on(client, f"/api/v1/elements/{el_a.id}")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["data"]["name"] == "E-alpha-known"

    def test_org_a_token_never_lists_org_b_elements(self, app, client, db_session, make_org):
        _org_a, _org_b, _el_a, _el_b, token = self._seed(db_session, make_org)
        status, body = _api_get(app, "/api/v1/elements", token)
        assert status == 200, body
        names = _element_names(body)
        assert "E-alpha-known" in names
        assert "E-bravo-secret" not in names, "another organisation's element was returned"

    def test_org_a_token_detail_of_org_b_element_is_not_found(self, app, client, db_session, make_org):
        _org_a, _org_b, el_a, el_b, token = self._seed(db_session, make_org)
        status, body = _api_get(app, f"/api/v1/elements/{el_b.id}", token)
        assert status == 404, body
        assert body["error"]["code"] == "NOT_FOUND", body
        status, body = _api_get(app, f"/api/v1/elements/{el_a.id}", token)
        assert status == 200, body
        assert body["data"]["id"] == el_a.id

    def test_mcp_read_token_refused_on_rest_route(self, app, client, db_session, make_org):
        _org_a, _org_b, el_a, _el_b, _token = self._seed(db_session, make_org)
        user = _make_user(db_session, _org_a, "mcp-holder@example.com")
        from app.modules.oauth_provider.tests.test_connector_account_security import _issue

        _oc, mcp_access, _refresh, _row = _issue(app, user, _org_a, scope=MCP_SCOPE)
        status, body = _api_get(app, f"/api/v1/elements/{el_a.id}", mcp_access)
        assert status in (401, 403), body

    def test_api_read_token_refused_on_mcp(self, app, client, db_session, make_org):
        """An api:read token — obtained through the real client-credentials
        grant — is refused on /mcp (namespace separation)."""
        org = make_org("api-on-mcp")
        svc = _make_service_user(db_session, org, "api-on-mcp@example.com")
        oc = _register_service_client(db_session, svc)
        status, body = _token_exchange(client, {
            "grant_type": "client_credentials",
            "client_id": oc.client_id,
            "client_secret": SERVICE_CLIENT_SECRET,
            "scope": API_SCOPE,
            "resource": _api_resource_value(app),
        })
        assert status == 200, body
        api_access = body["access_token"]

        mcp_status, mcp_body = _mcp_call(client, api_access, "list_canvases", {})
        assert mcp_status in (401, 403), (mcp_status, mcp_body)

    def test_read_scoped_token_refused_on_session_only_write_route(self, app, client, db_session, make_org):
        """The element GET routes accept an api:read token; an existing
        session-only write route must not — the token grants nothing there."""
        org = make_org("write-route")
        svc = _make_service_user(db_session, org, "write-route@example.com")
        oc = _register_service_client(db_session, svc)
        status, body = _token_exchange(client, {
            "grant_type": "client_credentials",
            "client_id": oc.client_id,
            "client_secret": SERVICE_CLIENT_SECRET,
            "scope": API_SCOPE,
            "resource": _api_resource_value(app),
        })
        assert status == 200, body
        api_access = body["access_token"]

        _clear_cached_identity()
        resp = client.post("/api/v1/applications/", headers={"Authorization": f"Bearer {api_access}"})
        assert resp.status_code in (302, 401, 403), resp.status_code

    def test_expired_token_is_refused(self, app, client, db_session, make_org):
        org_a, _org_b, el_a, _el_b, _t = self._seed(db_session, make_org)
        svc = _make_service_user(db_session, org_a, "expired@example.com")
        expired = _mint_api_token(svc, organization_id=org_a.id, expires_in=-100)
        status, body = _api_get(app, f"/api/v1/elements/{el_a.id}", expired)
        assert status in (401, 403), body

    def test_revoked_token_is_refused(self, app, client, db_session, make_org):
        org_a, _org_b, el_a, _el_b, _t = self._seed(db_session, make_org)
        svc = _make_service_user(db_session, org_a, "revoked@example.com")
        raw = _mint_api_token(svc, organization_id=org_a.id)
        from app.modules.oauth_provider.models import OAuthToken

        OAuthToken.find_by_access_token(raw).revoke()
        status, body = _api_get(app, f"/api/v1/elements/{el_a.id}", raw)
        assert status in (401, 403), body

    def test_token_without_organisation_is_refused(self, app, client, db_session, make_org):
        org_a, _org_b, el_a, _el_b, _t = self._seed(db_session, make_org)
        svc = _make_service_user(db_session, org_a, "no-org@example.com")
        orgless = _mint_api_token(svc, organization_id=None)
        status, body = _api_get(app, f"/api/v1/elements/{el_a.id}", orgless)
        assert status in (401, 403), body

    def test_token_with_both_namespaces_refused_everywhere(self, app, client, db_session, make_org):
        org_a, _org_b, el_a, _el_b, _t = self._seed(db_session, make_org)
        svc = _make_service_user(db_session, org_a, "both@example.com")
        both = _mint_api_token(svc, organization_id=org_a.id, scope="mcp:read api:read")
        status, body = _api_get(app, f"/api/v1/elements/{el_a.id}", both)
        assert status in (401, 403), body

        from app.modules.oauth_provider.tests.test_connector_account_security import _issue

        _oc, mcp_both, _refresh, _row = _issue(app, svc, org_a, scope="api:read mcp:read")
        mcp_status, mcp_body = _mcp_call(client, mcp_both, "list_canvases", {})
        assert mcp_status in (401, 403), (mcp_status, mcp_body)


class TestServiceUser:
    """A service user cannot sign in and holds no role beyond its scopes."""

    def test_service_user_cannot_sign_in_even_with_a_password_set(self, client, db_session, make_org):
        org = make_org("svc-signin")
        svc = _make_service_user(
            db_session, org, "svc-signin@example.com",
            password="an-accidental-password",
        )
        resp = client.post("/account/login", data={"email": svc.email, "password": "an-accidental-password"})
        assert resp.status_code == 200, "sign-in must not succeed"
        assert "Invalid email or password" in resp.get_data(as_text=True)

    def test_service_user_has_no_role_beyond_its_scopes(self, db_session, make_org):
        org = make_org("svc-role")
        svc = _make_service_user(db_session, org, "svc-role@example.com")
        assert svc.is_service_user is True
        assert svc.password_hash is None, "a service user has no password"
        assert svc.is_admin() is False
        assert svc.is_org_admin is False
        assert svc.is_platform_admin is False
        assert svc.role_name == "Viewer"
        assert (svc.role.permissions or 0) == 0


class TestCreateServiceClientCommand:
    """The management command that issues a service identity + client."""

    def test_command_creates_the_service_user_and_client(self, app, db_session, make_org):
        org = make_org("cmd-client")
        runner = app.test_cli_runner()
        result = runner.invoke(args=["create-service-client", "--org-id", str(org.id), "--name", "Model Reader"])
        assert result.exit_code == 0, result.output
        assert "client_id:" in result.output, result.output
        assert "client_secret:" in result.output, result.output

        from app.modules.oauth_provider.models import OAuthClient
        from app.models.user import User

        client_ids = []
        for line in result.output.splitlines():
            if line.strip().startswith("client_id:"):
                client_ids.append(line.split(":", 1)[1].strip())
        assert client_ids, result.output
        client_row = OAuthClient.query.filter_by(client_id=client_ids[0]).first()
        assert client_row is not None
        assert client_row.grant_types == "client_credentials"
        assert client_row.scope == API_SCOPE
        user = db_session.get(User, client_row.service_user_id)
        assert user is not None
        assert user.is_service_user is True
        assert user.organization_id == org.id
        assert user.password_hash is None
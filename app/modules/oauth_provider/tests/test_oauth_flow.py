"""Tests for the OAuth 2.1 authorization server.

Exercises the real authorization-code + PKCE flow end-to-end in-process.
No real network sockets, no external OAuth providers.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import urllib.parse


def _pkce_pair() -> tuple[str, str]:
    """Generate a code_verifier and its S256 code_challenge."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _make_user(db_session, org, email):
    from app.models.user import Role, User

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").first()

    user = User(
        email=email,
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        role=admin_role,
        is_org_admin=True,
        confirmed=True,
    )
    user.password = "test"
    db_session.add(user)
    db_session.flush()
    return user


class TestOAuthAuthorizationCodeFlow:
    """End-to-end authorization-code + PKCE flow."""

    def test_authorize_requires_login(self, client):
        """The authorize endpoint redirects unauthenticated users."""
        resp = client.get("/oauth/authorize")
        assert resp.status_code in (302, 401)

    def test_authorize_rejects_missing_client_id(self, client, db_session, make_org, login_as):
        """Missing client_id returns 400."""
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-missing@example.com")
        login_as(client, user)

        resp = client.get("/oauth/authorize")
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"] == "invalid_request"

    def test_authorize_requires_pkce(self, client, db_session, make_org, login_as):
        """Authorization without code_challenge is rejected."""
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-pkce@example.com")
        login_as(client, user)

        from app.modules.oauth_provider.models import OAuthClient
        oauth_client = OAuthClient.register(
            client_name="Test Client",
            redirect_uris="http://localhost/callback",
        )

        resp = client.get(
            f"/oauth/authorize?client_id={oauth_client.client_id}"
            f"&redirect_uri=http://localhost/callback"
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "code_challenge" in data["error_description"].lower()

    def test_full_authorization_code_flow(self, client, db_session, make_org, login_as):
        """Complete authorization-code + PKCE flow: authorize → token → user resolution."""
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-flow@example.com")
        login_as(client, user)

        from app.modules.oauth_provider.models import OAuthClient
        oauth_client = OAuthClient.register(
            client_name="Test Client",
            redirect_uris="http://localhost/callback",
        )

        # Step 1: GET /oauth/authorize — show consent
        verifier, challenge = _pkce_pair()
        auth_params = {
            "client_id": oauth_client.client_id,
            "redirect_uri": "http://localhost/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "mcp:read",
            "state": "test-state-123",
        }
        resp = client.get(f"/oauth/authorize?{urllib.parse.urlencode(auth_params)}")
        assert resp.status_code == 200
        assert b"Authorize Access" in resp.data

        # Step 2: POST /oauth/authorize — grant consent
        resp = client.post(
            "/oauth/authorize",
            data={
                "client_id": oauth_client.client_id,
                "redirect_uri": "http://localhost/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "mcp:read",
                "state": "test-state-123",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "code=" in location
        assert "state=test-state-123" in location

        # Extract the authorization code
        parsed = urllib.parse.urlparse(location)
        params = urllib.parse.parse_qs(parsed.query)
        auth_code = params["code"][0]

        # Step 3: POST /oauth/token — exchange code for token
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "http://localhost/callback",
                "client_id": oauth_client.client_id,
                "code_verifier": verifier,
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "access_token" in data
        assert data["token_type"] == "Bearer"
        assert "refresh_token" in data
        assert data["scope"] == "mcp:read"

        # Step 4: Verify the token resolves to the correct user
        from app.modules.oauth_provider.models import OAuthToken
        token = OAuthToken.find_by_access_token(data["access_token"])
        assert token is not None
        assert token.user_id == user.id
        assert token.is_active
        assert not token.is_expired

    def test_token_rejects_wrong_code_verifier(self, client, db_session, make_org, login_as):
        """Token exchange with wrong PKCE verifier is rejected."""
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-wrong@example.com")
        login_as(client, user)

        from app.modules.oauth_provider.models import OAuthClient
        oauth_client = OAuthClient.register(
            client_name="Test Client",
            redirect_uris="http://localhost/callback",
        )

        verifier, challenge = _pkce_pair()
        resp = client.post(
            "/oauth/authorize",
            data={
                "client_id": oauth_client.client_id,
                "redirect_uri": "http://localhost/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "mcp:read",
            },
            follow_redirects=False,
        )
        location = resp.headers["Location"]
        parsed = urllib.parse.urlparse(location)
        params = urllib.parse.parse_qs(parsed.query)
        auth_code = params["code"][0]

        # Use a different verifier
        wrong_verifier, _ = _pkce_pair()
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "http://localhost/callback",
                "client_id": oauth_client.client_id,
                "code_verifier": wrong_verifier,
            },
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"] == "invalid_grant"

    def test_token_rejects_expired_code(self, client, db_session, make_org, login_as):
        """An expired authorization code is rejected."""
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-expired@example.com")
        login_as(client, user)

        from app.modules.oauth_provider.models import OAuthClient
        oauth_client = OAuthClient.register(
            client_name="Test Client",
            redirect_uris="http://localhost/callback",
        )

        verifier, challenge = _pkce_pair()
        resp = client.post(
            "/oauth/authorize",
            data={
                "client_id": oauth_client.client_id,
                "redirect_uri": "http://localhost/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "mcp:read",
            },
            follow_redirects=False,
        )
        location = resp.headers["Location"]
        parsed = urllib.parse.urlparse(location)
        params = urllib.parse.parse_qs(parsed.query)
        auth_code = params["code"][0]

        # Expire all codes by manipulating time
        import app.modules.oauth_provider.routes as oauth_routes
        for k in list(oauth_routes._auth_codes.keys()):
            oauth_routes._auth_codes[k]["expires_at"] = 0

        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "http://localhost/callback",
                "client_id": oauth_client.client_id,
                "code_verifier": verifier,
            },
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"] == "invalid_grant"

    def test_token_rejects_reused_code(self, client, db_session, make_org, login_as):
        """An authorization code can only be used once."""
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-reuse@example.com")
        login_as(client, user)

        from app.modules.oauth_provider.models import OAuthClient
        oauth_client = OAuthClient.register(
            client_name="Test Client",
            redirect_uris="http://localhost/callback",
        )

        verifier, challenge = _pkce_pair()
        resp = client.post(
            "/oauth/authorize",
            data={
                "client_id": oauth_client.client_id,
                "redirect_uri": "http://localhost/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "mcp:read",
            },
            follow_redirects=False,
        )
        location = resp.headers["Location"]
        parsed = urllib.parse.urlparse(location)
        params = urllib.parse.parse_qs(parsed.query)
        auth_code = params["code"][0]

        # First use — succeeds
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "http://localhost/callback",
                "client_id": oauth_client.client_id,
                "code_verifier": verifier,
            },
        )
        assert resp.status_code == 200

        # Second use — rejected
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "http://localhost/callback",
                "client_id": oauth_client.client_id,
                "code_verifier": verifier,
            },
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"] == "invalid_grant"

    def test_resource_parameter_validated(self, client, db_session, make_org, login_as, app):
        """Token request with mismatched resource parameter is rejected."""
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-res@example.com")
        login_as(client, user)

        from app.modules.oauth_provider.models import OAuthClient
        oauth_client = OAuthClient.register(
            client_name="Test Client",
            redirect_uris="http://localhost/callback",
        )

        app.config["MCP_ENDPOINT_URL"] = "https://app.entelim.com/mcp"

        verifier, challenge = _pkce_pair()
        resp = client.post(
            "/oauth/authorize",
            data={
                "client_id": oauth_client.client_id,
                "redirect_uri": "http://localhost/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "mcp:read",
                "resource": "https://other-server.com/mcp",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"] == "invalid_resource"

    def test_revoked_token_returns_401(self, client, db_session, make_org, login_as):
        """A revoked token is inactive."""
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-revoked@example.com")
        login_as(client, user)

        from app.modules.oauth_provider.models import OAuthClient, OAuthToken
        oauth_client = OAuthClient.register(
            client_name="Test Client",
            redirect_uris="http://localhost/callback",
        )

        verifier, challenge = _pkce_pair()
        resp = client.post(
            "/oauth/authorize",
            data={
                "client_id": oauth_client.client_id,
                "redirect_uri": "http://localhost/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "mcp:read",
            },
            follow_redirects=False,
        )
        location = resp.headers["Location"]
        parsed = urllib.parse.urlparse(location)
        params = urllib.parse.parse_qs(parsed.query)
        auth_code = params["code"][0]

        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "http://localhost/callback",
                "client_id": oauth_client.client_id,
                "code_verifier": verifier,
            },
        )
        access_token = resp.get_json()["access_token"]

        token = OAuthToken.find_by_access_token(access_token)
        token.revoked = True
        db_session.flush()

        assert not token.is_active

    def test_expired_token_is_inactive(self, client, db_session, make_org, login_as):
        """An expired token reports is_active=False."""
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-exp@example.com")
        login_as(client, user)

        from app.modules.oauth_provider.models import OAuthClient, OAuthToken
        oauth_client = OAuthClient.register(
            client_name="Test Client",
            redirect_uris="http://localhost/callback",
        )

        verifier, challenge = _pkce_pair()
        resp = client.post(
            "/oauth/authorize",
            data={
                "client_id": oauth_client.client_id,
                "redirect_uri": "http://localhost/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "mcp:read",
            },
            follow_redirects=False,
        )
        location = resp.headers["Location"]
        parsed = urllib.parse.urlparse(location)
        params = urllib.parse.parse_qs(parsed.query)
        auth_code = params["code"][0]

        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "http://localhost/callback",
                "client_id": oauth_client.client_id,
                "code_verifier": verifier,
            },
        )
        access_token = resp.get_json()["access_token"]

        token = OAuthToken.find_by_access_token(access_token)
        from datetime import datetime, timezone, timedelta
        token.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db_session.flush()

        assert token.is_expired
        assert not token.is_active


class TestWellKnownEndpoints:
    """RFC 8414 and RFC 9728 metadata endpoints."""

    def test_protected_resource_metadata(self, client):
        """GET /.well-known/oauth-protected-resource returns RFC 9728 metadata."""
        resp = client.get("/.well-known/oauth-protected-resource")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "resource" in data
        assert "authorization_servers" in data
        assert "scopes_supported" in data
        assert "mcp:read" in data["scopes_supported"]

    def test_authorization_server_metadata(self, client):
        """GET /.well-known/oauth-authorization-server returns RFC 8414 metadata."""
        resp = client.get("/.well-known/oauth-authorization-server")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "authorization_endpoint" in data
        assert "token_endpoint" in data
        assert "code_challenge_methods_supported" in data
        assert "S256" in data["code_challenge_methods_supported"]
        assert "authorization_code" in data["grant_types_supported"]


class TestDynamicClientRegistration:
    """RFC 7591 dynamic client registration."""

    def test_register_client(self, db_session):
        """OAuthClient.register() creates a client with unique client_id."""
        from app.modules.oauth_provider.models import OAuthClient

        client = OAuthClient.register(
            client_name="Test App",
            redirect_uris="http://localhost/callback",
        )
        assert client.id is not None
        assert client.client_id.startswith("cl_")
        assert client.client_name == "Test App"
        assert client.is_active

        client2 = OAuthClient.register(client_name="Test App 2")
        assert client2.client_id != client.client_id

    def test_redirect_uri_list(self, db_session):
        """redirect_uri_list splits on whitespace."""
        from app.modules.oauth_provider.models import OAuthClient

        client = OAuthClient.register(
            client_name="Multi URI",
            redirect_uris="http://localhost/callback http://example.com/cb",
        )
        uris = client.redirect_uri_list
        assert len(uris) == 2
        assert "http://localhost/callback" in uris
        assert "http://example.com/cb" in uris
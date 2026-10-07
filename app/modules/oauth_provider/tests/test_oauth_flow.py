"""Tests for the OAuth 2.1 authorization server.

Exercises the real authorization-code + PKCE flow end-to-end in-process.
No real network sockets, no external OAuth providers.

Requires MCP_ENABLED=true and a PUBLIC_BASE_URL to be set in the environment
*before* the session-scoped ``app`` fixture is first created — both
blueprints in this module are registered only when MCP_ENABLED is true
(app/_bootstrap/blueprints.py), so the OAuth/MCP routes do not exist at all
otherwise. Run this module with, e.g.:

    MCP_ENABLED=true PUBLIC_BASE_URL=https://mcp-test.example \\
        TEST_DATABASE_URL=postgresql://... pytest app/modules/oauth_provider/tests/
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import urllib.parse

import pytest


def _pkce_pair() -> tuple[str, str]:
    """Generate a code_verifier and its S256 code_challenge."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _resource(app) -> str:
    base = (app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    assert base, "PUBLIC_BASE_URL must be set to run this module — see module docstring"
    return f"{base}/mcp"


def _make_user(db_session, org, email, role_name="Administrator"):
    from app.models.user import Role, User

    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name=role_name).first()

    user = User(
        email=email,
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        role=role,
        # Only grant org-admin for role_name="Administrator". User.is_org_admin
        # is a setter: passing True unconditionally called grant_org_admin(),
        # which silently overwrites `role` back to Administrator regardless of
        # what role_name asked for -- a role_name="Viewer" caller got an
        # Administrator in disguise. False is a no-op (see
        # app/models/user.py's is_org_admin setter), so this leaves the
        # requested role intact for every other role_name.
        is_org_admin=(role_name == "Administrator"),
        confirmed=True,
    )
    user.password = "test"
    db_session.add(user)
    db_session.flush()
    return user


def _register_client(redirect_uris="http://localhost/callback"):
    from app.modules.oauth_provider.models import OAuthClient

    return OAuthClient.register(client_name="Test Client", redirect_uris=redirect_uris)


@pytest.fixture
def csrf_enabled(app):
    """Temporarily turn CSRF protection on for the app under test.

    The shared ``app`` fixture hard-disables WTF_CSRF_ENABLED (tests/conftest.py)
    so the suite's other 2,000+ tests do not need to carry tokens. Flask-WTF
    reads this flag from current_app.config at request time, not at
    extension-init time, so flipping it for the duration of one test is
    sufficient and does not require a fresh app.
    """
    original = app.config.get("WTF_CSRF_ENABLED")
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        yield app
    finally:
        app.config["WTF_CSRF_ENABLED"] = original


def _get_consent_csrf_token(client, auth_params) -> str:
    """GET the consent screen and pull the csrf_token hidden field out of it."""
    import re

    resp = client.get(f"/oauth/authorize?{urllib.parse.urlencode(auth_params)}")
    assert resp.status_code == 200
    match = re.search(rb'name="csrf_token" value="([^"]+)"', resp.data)
    assert match, "consent screen did not render a csrf_token field"
    return match.group(1).decode("ascii")


class TestOAuthAuthorizationCodeFlow:
    """End-to-end authorization-code + PKCE flow."""

    def test_authorize_requires_login(self, client):
        resp = client.get("/oauth/authorize")
        assert resp.status_code in (302, 401)

    def test_authorize_rejects_missing_client_id(self, client, db_session, make_org, login_as):
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-missing@example.com")
        login_as(client, user)

        resp = client.get("/oauth/authorize")
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_request"

    def test_authorize_requires_pkce(self, client, db_session, make_org, login_as, app):
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-pkce@example.com")
        login_as(client, user)
        oauth_client = _register_client()

        resp = client.get(
            f"/oauth/authorize?client_id={oauth_client.client_id}"
            f"&redirect_uri=http://localhost/callback"
            f"&resource={urllib.parse.quote(_resource(app), safe='')}"
        )
        assert resp.status_code == 400
        assert "code_challenge" in resp.get_json()["error_description"].lower()

    def test_authorize_requires_resource(self, client, db_session, make_org, login_as):
        """resource is now mandatory — omitting it is invalid_target, not a silent pass-through."""
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-noresource@example.com")
        login_as(client, user)
        oauth_client = _register_client()
        _verifier, challenge = _pkce_pair()

        resp = client.get(
            f"/oauth/authorize?client_id={oauth_client.client_id}"
            f"&redirect_uri=http://localhost/callback"
            f"&code_challenge={challenge}&code_challenge_method=S256"
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_target"

    def test_authorize_rejects_wrong_resource(self, client, db_session, make_org, login_as):
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-wrongresource@example.com")
        login_as(client, user)
        oauth_client = _register_client()
        _verifier, challenge = _pkce_pair()

        resp = client.get(
            f"/oauth/authorize?client_id={oauth_client.client_id}"
            f"&redirect_uri=http://localhost/callback"
            f"&code_challenge={challenge}&code_challenge_method=S256"
            f"&resource=https://other-server.example/mcp"
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_target"

    def test_authorize_rejects_unregistered_redirect_uri(self, client, db_session, make_org, login_as, app):
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-badredirect@example.com")
        login_as(client, user)
        oauth_client = _register_client(redirect_uris="http://localhost/callback")
        _verifier, challenge = _pkce_pair()

        resp = client.get(
            "/oauth/authorize?client_id=" + oauth_client.client_id +
            "&redirect_uri=http://evil.example/callback" +
            f"&code_challenge={challenge}&code_challenge_method=S256" +
            f"&resource={urllib.parse.quote(_resource(app), safe='')}"
        )
        assert resp.status_code == 400
        assert "redirect_uri" in resp.get_json()["error_description"]

    def test_authorize_rejects_client_with_no_redirect_uris(self, client, db_session, make_org, login_as, app):
        """A client registered with no redirect URI accepts nothing — there is no implicit any-URI fallback."""
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-noredirects@example.com")
        login_as(client, user)
        oauth_client = _register_client(redirect_uris=None)
        _verifier, challenge = _pkce_pair()

        resp = client.get(
            "/oauth/authorize?client_id=" + oauth_client.client_id +
            "&redirect_uri=http://localhost/callback" +
            f"&code_challenge={challenge}&code_challenge_method=S256" +
            f"&resource={urllib.parse.quote(_resource(app), safe='')}"
        )
        assert resp.status_code == 400

    def test_full_authorization_code_flow(self, client, db_session, make_org, login_as, app):
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-flow@example.com")
        login_as(client, user)
        oauth_client = _register_client()

        verifier, challenge = _pkce_pair()
        auth_params = {
            "client_id": oauth_client.client_id,
            "redirect_uri": "http://localhost/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "mcp:read",
            "state": "test-state-123",
            "resource": _resource(app),
        }
        resp = client.get(f"/oauth/authorize?{urllib.parse.urlencode(auth_params)}")
        assert resp.status_code == 200
        assert b"Authorize access" in resp.data

        resp = client.post(
            "/oauth/authorize",
            data={**auth_params, "decision": "allow"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "code=" in location
        assert "state=test-state-123" in location

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
                "resource": _resource(app),
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "access_token" in data
        assert data["token_type"] == "Bearer"
        assert "refresh_token" in data
        assert data["scope"] == "mcp:read"
        # (The "no Set-Cookie on a bearer-authenticated response" assertion
        # belongs with the /mcp tests — this call's test client
        # already carries a session cookie from the consent step above, so
        # Flask's own session refresh naturally re-sets it here regardless
        # of this endpoint's own behaviour.)

        from app.modules.oauth_provider.models import OAuthToken
        token = OAuthToken.find_by_access_token(data["access_token"])
        assert token is not None
        assert token.user_id == user.id
        assert token.is_active
        assert not token.is_expired
        assert token.organization_id == org.id
        assert token.resource == _resource(app)
        assert token.grant_type == "authorization_code"

    def test_consent_deny_redirects_with_access_denied(self, client, db_session, make_org, login_as, app):
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-deny@example.com")
        login_as(client, user)
        oauth_client = _register_client()
        _verifier, challenge = _pkce_pair()

        auth_params = {
            "client_id": oauth_client.client_id,
            "redirect_uri": "http://localhost/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "mcp:read",
            "state": "deny-state",
            "resource": _resource(app),
        }
        resp = client.post(
            "/oauth/authorize",
            data={**auth_params, "decision": "deny"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "error=access_denied" in location
        assert "state=deny-state" in location
        assert "code=" not in location

    def test_consent_requires_explicit_decision(self, client, db_session, make_org, login_as, app):
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-nodecision@example.com")
        login_as(client, user)
        oauth_client = _register_client()
        _verifier, challenge = _pkce_pair()

        auth_params = {
            "client_id": oauth_client.client_id,
            "redirect_uri": "http://localhost/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "mcp:read",
            "resource": _resource(app),
        }
        resp = client.post("/oauth/authorize", data=auth_params, follow_redirects=False)
        assert resp.status_code == 400

    def test_token_rejects_wrong_code_verifier(self, client, db_session, make_org, login_as, app):
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-wrong@example.com")
        login_as(client, user)
        oauth_client = _register_client()

        verifier, challenge = _pkce_pair()
        resp = client.post(
            "/oauth/authorize",
            data={
                "client_id": oauth_client.client_id,
                "redirect_uri": "http://localhost/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "mcp:read",
                "resource": _resource(app),
                "decision": "allow",
            },
            follow_redirects=False,
        )
        location = resp.headers["Location"]
        auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["code"][0]

        wrong_verifier, _ = _pkce_pair()
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "http://localhost/callback",
                "client_id": oauth_client.client_id,
                "code_verifier": wrong_verifier,
                "resource": _resource(app),
            },
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_grant"

    def test_token_rejects_expired_code(self, client, db_session, make_org, login_as, app):
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-expired@example.com")
        login_as(client, user)
        oauth_client = _register_client()

        verifier, challenge = _pkce_pair()
        resp = client.post(
            "/oauth/authorize",
            data={
                "client_id": oauth_client.client_id,
                "redirect_uri": "http://localhost/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": _resource(app),
                "decision": "allow",
            },
            follow_redirects=False,
        )
        location = resp.headers["Location"]
        auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["code"][0]

        from datetime import datetime, timezone
        from app.modules.oauth_provider.models import OAuthAuthorizationCode
        for ac in OAuthAuthorizationCode.query.all():
            ac.expires_at = datetime.fromtimestamp(0, tz=timezone.utc)
        db_session.flush()

        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "http://localhost/callback",
                "client_id": oauth_client.client_id,
                "code_verifier": verifier,
                "resource": _resource(app),
            },
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_grant"

    def test_token_rejects_reused_code(self, client, db_session, make_org, login_as, app):
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-reuse@example.com")
        login_as(client, user)
        oauth_client = _register_client()

        verifier, challenge = _pkce_pair()
        resp = client.post(
            "/oauth/authorize",
            data={
                "client_id": oauth_client.client_id,
                "redirect_uri": "http://localhost/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": _resource(app),
                "decision": "allow",
            },
            follow_redirects=False,
        )
        location = resp.headers["Location"]
        auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["code"][0]

        token_data = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": "http://localhost/callback",
            "client_id": oauth_client.client_id,
            "code_verifier": verifier,
            "resource": _resource(app),
        }
        resp = client.post("/oauth/token", data=token_data)
        assert resp.status_code == 200

        resp = client.post("/oauth/token", data=token_data)
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_grant"

    def test_token_requires_resource(self, client, db_session, make_org, login_as, app):
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-tokennoresource@example.com")
        login_as(client, user)
        oauth_client = _register_client()
        verifier, challenge = _pkce_pair()
        resp = client.post(
            "/oauth/authorize",
            data={
                "client_id": oauth_client.client_id,
                "redirect_uri": "http://localhost/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": _resource(app),
                "decision": "allow",
            },
            follow_redirects=False,
        )
        location = resp.headers["Location"]
        auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["code"][0]

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
        assert resp.get_json()["error"] == "invalid_target"

    def test_revoked_token_returns_inactive(self, client, db_session, make_org, login_as, app):
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-revoked@example.com")
        login_as(client, user)
        oauth_client = _register_client()

        verifier, challenge = _pkce_pair()
        resp = client.post(
            "/oauth/authorize",
            data={
                "client_id": oauth_client.client_id,
                "redirect_uri": "http://localhost/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": _resource(app),
                "decision": "allow",
            },
            follow_redirects=False,
        )
        location = resp.headers["Location"]
        auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["code"][0]

        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "http://localhost/callback",
                "client_id": oauth_client.client_id,
                "code_verifier": verifier,
                "resource": _resource(app),
            },
        )
        access_token = resp.get_json()["access_token"]

        from app.modules.oauth_provider.models import OAuthToken
        token = OAuthToken.find_by_access_token(access_token)
        token.revoke()
        db_session.flush()
        assert not token.is_active

    def test_expired_token_is_inactive(self, client, db_session, make_org, login_as, app):
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-exp@example.com")
        login_as(client, user)
        oauth_client = _register_client()

        verifier, challenge = _pkce_pair()
        resp = client.post(
            "/oauth/authorize",
            data={
                "client_id": oauth_client.client_id,
                "redirect_uri": "http://localhost/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": _resource(app),
                "decision": "allow",
            },
            follow_redirects=False,
        )
        location = resp.headers["Location"]
        auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["code"][0]

        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "http://localhost/callback",
                "client_id": oauth_client.client_id,
                "code_verifier": verifier,
                "resource": _resource(app),
            },
        )
        access_token = resp.get_json()["access_token"]

        from datetime import datetime, timezone, timedelta
        from app.modules.oauth_provider.models import OAuthToken
        token = OAuthToken.find_by_access_token(access_token)
        token.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db_session.flush()

        assert token.is_expired
        assert not token.is_active


class TestHashedStorage:
    """Nothing in these tables is ever a usable plaintext secret."""

    def _issue(self, client, db_session, make_org, login_as, app):
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-hash@example.com")
        login_as(client, user)
        oauth_client = _register_client()
        verifier, challenge = _pkce_pair()
        resp = client.post(
            "/oauth/authorize",
            data={
                "client_id": oauth_client.client_id,
                "redirect_uri": "http://localhost/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": _resource(app),
                "decision": "allow",
            },
            follow_redirects=False,
        )
        location = resp.headers["Location"]
        auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["code"][0]
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "http://localhost/callback",
                "client_id": oauth_client.client_id,
                "code_verifier": verifier,
                "resource": _resource(app),
            },
        )
        return resp.get_json(), auth_code

    def test_access_and_refresh_tokens_are_hashed_in_the_database(self, client, db_session, make_org, login_as, app):
        tokens, _auth_code = self._issue(client, db_session, make_org, login_as, app)
        from app.modules.oauth_provider.models import OAuthToken

        row = OAuthToken.query.order_by(OAuthToken.id.desc()).first()
        assert row.access_token != tokens["access_token"]
        assert row.refresh_token != tokens["refresh_token"]
        assert len(row.access_token) == 64  # sha256 hex digest
        assert row.access_token == hashlib.sha256(tokens["access_token"].encode()).hexdigest()
        # The stored row cannot be used as a bearer credential directly.
        assert OAuthToken.find_by_access_token(row.access_token) is None

    def test_authorization_code_is_hashed_in_the_database(self, client, db_session, make_org, login_as, app):
        _tokens, auth_code = self._issue(client, db_session, make_org, login_as, app)
        from app.modules.oauth_provider.models import OAuthAuthorizationCode

        # The code was already consumed (single-use) and deleted; issue a
        # second one and inspect it directly instead.
        org = make_org("oauth2")
        user = _make_user(db_session, org, "oauth-hash2@example.com")
        login_as(client, user)
        oauth_client = _register_client()
        verifier, challenge = _pkce_pair()
        raw_code, row = OAuthAuthorizationCode.issue(
            client_id=oauth_client.client_id,
            user_id=user.id,
            redirect_uri="http://localhost/callback",
            code_challenge=challenge,
            resource=_resource(app),
        )
        assert row.code != raw_code
        assert row.code == hashlib.sha256(raw_code.encode()).hexdigest()


class TestScopesAndPermissionGating:
    def test_mcp_propose_requires_permission_and_consent_tick(self, client, db_session, make_org, login_as, app):
        """mcp:propose is dropped for a user without general write permission."""
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-noperm@example.com", role_name="Viewer")
        login_as(client, user)
        oauth_client = _register_client()
        _verifier, challenge = _pkce_pair()

        resp = client.get(
            "/oauth/authorize?" + urllib.parse.urlencode({
                "client_id": oauth_client.client_id,
                "redirect_uri": "http://localhost/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "mcp:read mcp:propose",
                "resource": _resource(app),
            })
        )
        assert resp.status_code == 200
        assert b"mcp:propose" not in resp.data or b"Propose changes" not in resp.data

    def test_unknown_scope_is_dropped(self, client, db_session, make_org, login_as, app):
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-badscope@example.com")
        login_as(client, user)
        oauth_client = _register_client()
        verifier, challenge = _pkce_pair()
        resp = client.post(
            "/oauth/authorize",
            data={
                "client_id": oauth_client.client_id,
                "redirect_uri": "http://localhost/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "mcp:read admin:delete-everything",
                "resource": _resource(app),
                "decision": "allow",
            },
            follow_redirects=False,
        )
        location = resp.headers["Location"]
        auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["code"][0]
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "http://localhost/callback",
                "client_id": oauth_client.client_id,
                "code_verifier": verifier,
                "resource": _resource(app),
            },
        )
        assert resp.get_json()["scope"] == "mcp:read"


class TestRefreshAndRevoke:
    def _issue_token(self, client, db_session, make_org, login_as, app, email):
        org = make_org("oauth")
        user = _make_user(db_session, org, email)
        login_as(client, user)
        oauth_client = _register_client()
        verifier, challenge = _pkce_pair()
        resp = client.post(
            "/oauth/authorize",
            data={
                "client_id": oauth_client.client_id,
                "redirect_uri": "http://localhost/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": _resource(app),
                "decision": "allow",
            },
            follow_redirects=False,
        )
        location = resp.headers["Location"]
        auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["code"][0]
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "http://localhost/callback",
                "client_id": oauth_client.client_id,
                "code_verifier": verifier,
                "resource": _resource(app),
            },
        )
        return oauth_client, resp.get_json()

    def test_refresh_rotates_token(self, client, db_session, make_org, login_as, app):
        oauth_client, tokens = self._issue_token(client, db_session, make_org, login_as, app, "oauth-refresh@example.com")

        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": oauth_client.client_id,
                "resource": _resource(app),
            },
        )
        assert resp.status_code == 200
        new_tokens = resp.get_json()
        assert new_tokens["access_token"] != tokens["access_token"]
        assert new_tokens["refresh_token"] != tokens["refresh_token"]

        # The old refresh token is now revoked — a second use fails.
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": oauth_client.client_id,
                "resource": _resource(app),
            },
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_grant"

    def test_revoke_endpoint_invalidates_access_token(self, client, db_session, make_org, login_as, app):
        _oauth_client, tokens = self._issue_token(client, db_session, make_org, login_as, app, "oauth-revoke-ep@example.com")

        resp = client.post("/oauth/revoke", data={"token": tokens["access_token"]})
        assert resp.status_code == 200

        from app.modules.oauth_provider.models import OAuthToken
        row = OAuthToken.find_by_access_token(tokens["access_token"])
        assert not row.is_active

    def test_revoke_unknown_token_still_returns_200(self, client):
        resp = client.post("/oauth/revoke", data={"token": "not-a-real-token"})
        assert resp.status_code == 200


class TestBearerIdentityScoping:
    """The bearer loader only ever activates for /mcp — nowhere else."""

    def test_bearer_on_non_mcp_route_leaves_user_anonymous(self, client, db_session, make_org, login_as, app):
        _oauth_client, tokens = TestRefreshAndRevoke()._issue_token(
            client, db_session, make_org, login_as, app, "oauth-scoped@example.com"
        )
        # Fresh client — no session cookie at all, only a bearer header, on an
        # ordinary session-authenticated page.
        fresh = app.test_client()
        resp = fresh.get(
            "/dashboard/overview",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert resp.status_code in (302, 401, 403)


class TestCsrfBehaviour:
    def test_token_endpoint_works_without_csrf_token_when_csrf_enabled(
        self, client, db_session, make_org, login_as, app, csrf_enabled
    ):
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-csrf-token@example.com")
        login_as(client, user)
        oauth_client = _register_client()
        verifier, challenge = _pkce_pair()

        auth_params = {
            "client_id": oauth_client.client_id,
            "redirect_uri": "http://localhost/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": _resource(app),
        }
        csrf_token = _get_consent_csrf_token(client, auth_params)
        resp = client.post(
            "/oauth/authorize",
            data={**auth_params, "decision": "allow", "csrf_token": csrf_token},
            follow_redirects=False,
        )
        location = resp.headers["Location"]
        auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["code"][0]

        # No CSRF token on this POST at all — must still succeed, because
        # /oauth/token is exempted (no session is ever read here).
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "http://localhost/callback",
                "client_id": oauth_client.client_id,
                "code_verifier": verifier,
                "resource": _resource(app),
            },
        )
        assert resp.status_code == 200

    def test_consent_post_without_csrf_token_is_refused_when_csrf_enabled(
        self, client, db_session, make_org, login_as, app, csrf_enabled
    ):
        org = make_org("oauth")
        user = _make_user(db_session, org, "oauth-csrf-consent@example.com")
        login_as(client, user)
        oauth_client = _register_client()
        _verifier, challenge = _pkce_pair()

        resp = client.post(
            "/oauth/authorize",
            data={
                "client_id": oauth_client.client_id,
                "redirect_uri": "http://localhost/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": _resource(app),
                "decision": "allow",
                # deliberately no csrf_token field
            },
            follow_redirects=False,
        )
        assert resp.status_code == 400


class TestWellKnownEndpoints:
    """RFC 8414 and RFC 9728 metadata endpoints."""

    def test_protected_resource_metadata(self, client, app):
        resp = client.get("/.well-known/oauth-protected-resource")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["resource"] == _resource(app)
        assert data["authorization_servers"] == [app.config["PUBLIC_BASE_URL"].rstrip("/")]
        assert "mcp:read" in data["scopes_supported"]

    def test_authorization_server_metadata(self, client, app):
        resp = client.get("/.well-known/oauth-authorization-server")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["issuer"] == app.config["PUBLIC_BASE_URL"].rstrip("/")
        assert "authorization_endpoint" in data
        assert "token_endpoint" in data
        assert "S256" in data["code_challenge_methods_supported"]
        assert "authorization_code" in data["grant_types_supported"]
        assert "refresh_token" in data["grant_types_supported"]


class TestClientRegisterModel:
    """RFC 7591 model-level behaviour (route-level tests live alongside the registration endpoint)."""

    def test_register_client(self, db_session):
        from app.modules.oauth_provider.models import OAuthClient

        oauth_client = OAuthClient.register(
            client_name="Test App",
            redirect_uris="http://localhost/callback",
        )
        assert oauth_client.id is not None
        assert oauth_client.client_id.startswith("cl_")
        assert oauth_client.client_name == "Test App"
        assert oauth_client.is_active
        assert oauth_client.token_endpoint_auth_method == "none"

        client2 = OAuthClient.register(client_name="Test App 2")
        assert client2.client_id != oauth_client.client_id

    def test_redirect_uri_list(self, db_session):
        from app.modules.oauth_provider.models import OAuthClient

        oauth_client = OAuthClient.register(
            client_name="Multi URI",
            redirect_uris="http://localhost/callback http://example.com/cb",
        )
        uris = oauth_client.redirect_uri_list
        assert len(uris) == 2
        assert "http://localhost/callback" in uris
        assert "http://example.com/cb" in uris

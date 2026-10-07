"""CSRF scoping for POST /mcp.

Two things are proven here, independently:

1. A genuine bearer-only call (no session cookie, no CSRF token -- the only
   shape a real MCP client ever sends) succeeds even with CSRF genuinely
   enabled. The rest of this test suite runs with WTF_CSRF_ENABLED=False
   (see tests/conftest.py's shared ``app`` fixture), which is why a prior,
   production-shaped repro of a bearer-only call against a server actually
   running with CSRF protection on returned HTTP 400 {"error_type": "csrf"}
   -- the feature did not work in the one configuration that matters. This
   module forces CSRF back on for the duration of each test here, the same
   pattern app/modules/oauth_provider/tests/test_oauth_flow.py's
   ``csrf_enabled`` fixture already uses.

2. The bearer-only exemption is scoped correctly: a request to /mcp that
   carries a valid, authenticated session cookie (simulating a cross-site
   POST riding an authenticated browser tab) never gets the exemption --
   only a request with no session cookie at all and a real bearer token
   does. That request is still rejected by CSRF, exactly as any other
   session-authenticated POST with no token would be.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.parse

import pytest


def _make_user(db_session, org, email):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()

    user = User(
        email=email,
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        role=role,
        is_org_admin=True,
        confirmed=True,
    )
    user.password = "test"
    db_session.add(user)
    db_session.flush()
    return user


def _resource(app) -> str:
    base = (app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    assert base, "PUBLIC_BASE_URL must be set to run this module"
    return f"{base}/mcp"


def _mint_oauth_token(client, app, user, login_as_fn) -> str:
    """Mint a real OAuth access token through the authorization-code + PKCE
    flow. Runs with CSRF still disabled (the ``app`` fixture's default) --
    only the /mcp call itself needs CSRF genuinely on; minting the token
    through the authenticated consent flow is not what is under test here.
    """
    from app.modules.oauth_provider.models import OAuthClient

    oauth_client = OAuthClient.register(
        client_name="CSRF Scoping Test Client",
        redirect_uris="http://localhost/callback",
    )

    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    resource = _resource(app)

    login_as_fn(client, user)

    resp = client.post(
        "/oauth/authorize",
        data={
            "client_id": oauth_client.client_id,
            "redirect_uri": "http://localhost/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "mcp:read",
            "resource": resource,
            "decision": "allow",
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
            "resource": resource,
        },
    )
    return resp.get_json()["access_token"]


def _clear_cached_identity():
    """db_session holds one app context open for the whole test, so g's
    flask-login/tenant caches from the login_as/token-minting calls above
    would otherwise leak into the bearer-only call below. See the matching
    helper in app/modules/mcp/tests/test_tools.py for the full explanation.
    """
    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


@pytest.fixture
def csrf_enabled(app):
    """Temporarily turn CSRF protection on for the duration of one test.

    Same fixture as app/modules/oauth_provider/tests/test_oauth_flow.py's
    ``csrf_enabled`` -- duplicated here rather than imported because that
    one is module-local, not exported through either module's conftest.py.
    """
    original = app.config.get("WTF_CSRF_ENABLED")
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        yield app
    finally:
        app.config["WTF_CSRF_ENABLED"] = original


class TestBearerOnlyCallSucceedsWithCsrfEnabled:
    """The single most important regression test in this fix: a genuine
    bearer-only call must actually work in the one configuration real
    production traffic runs under -- CSRF genuinely on."""

    def test_tools_list_bearer_only_succeeds_with_csrf_enabled(
        self, client, db_session, make_org, login_as, app
    ):
        org = make_org("csrf-bearer-only")
        user = _make_user(db_session, org, "csrf-bearer-only@example.com")
        # Mint with CSRF still at its default (disabled): the consent-form
        # POST (/oauth/authorize) is a session-authenticated form and is NOT
        # CSRF-exempt, so minting under CSRF-enabled would need the consent
        # page's own token too -- a real, separate concern from what this
        # test is proving. Only the final /mcp call below needs CSRF
        # genuinely on.
        token = _mint_oauth_token(client, app, user, login_as)

        _clear_cached_identity()
        bearer_only_client = client.application.test_client()
        session_cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
        assert bearer_only_client.get_cookie(session_cookie_name) is None, (
            "precondition: this client must never have carried a session cookie"
        )

        original_csrf_enabled = app.config.get("WTF_CSRF_ENABLED")
        app.config["WTF_CSRF_ENABLED"] = True
        try:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            resp = bearer_only_client.post(
                "/mcp",
                data=json.dumps(payload),
                content_type="application/json",
                headers={"Authorization": f"Bearer {token}"},
            )
        finally:
            app.config["WTF_CSRF_ENABLED"] = original_csrf_enabled

        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert "error" not in data, data
        assert len(data["result"]["tools"]) == 10


class TestSessionCookieNeverGetsBearerExemption:
    """A request to /mcp carrying a valid session cookie must still be
    rejected by CSRF -- the bearer-only exemption never applies to it, even
    though nothing about this request shape is otherwise invalid."""

    def test_session_cookie_request_to_mcp_rejected_by_csrf(
        self, csrf_enabled, client, db_session, make_org, login_as
    ):
        org = make_org("csrf-session-cookie")
        user = _make_user(db_session, org, "csrf-session-cookie@example.com")

        # login_as leaves `client` carrying a real, valid, non-revoked
        # session cookie for this user -- exactly what a cross-site forgery
        # would be riding if a signed-in browser tab were ever pointed at
        # /mcp. No Authorization header, no CSRF token: the same shape a
        # forged cross-site POST would have.
        login_as(client, user)

        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp = client.post(
            "/mcp",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["error_type"] == "csrf", body

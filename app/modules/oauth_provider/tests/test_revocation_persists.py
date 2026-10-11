"""Regression tests: OAuthToken.revoke() must actually persist.

Before this fix, ``OAuthToken.revoke()`` only set the in-memory
``revoked_at`` attribute and never committed, so the change was discarded the
instant the request that called it ended (Flask's teardown_appcontext hook
does not commit a successful request — see the matching note on
``OAuthToken.issue`` in models.py). A test that inspects the ORM row
directly, in the same process, can be fooled by the still-mutated in-memory
object and pass even when nothing reached the database — so both tests here
deliberately cross a real request boundary instead: the first makes an
actual bearer-authenticated call to another endpoint (``/mcp``) on a brand
new, cookie-less client; the second makes an actual ``POST /oauth/token``
call. Both only succeed if the revocation genuinely reached the database.

Requires MCP_ENABLED=true and PUBLIC_BASE_URL — see test_oauth_flow.py's
module docstring for how to run this module.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.parse


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


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _resource(app) -> str:
    base = (app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    assert base, "PUBLIC_BASE_URL must be set to run this module"
    return f"{base}/mcp"


def _register_client(redirect_uris="http://localhost/callback"):
    from app.modules.oauth_provider.models import OAuthClient

    return OAuthClient.register(client_name="Revocation Test Client", redirect_uris=redirect_uris)


def _issue_token(client, app, user, login_as_fn) -> dict:
    """Mint a real access + refresh token pair through the authorization-code
    + PKCE flow, exactly like test_oauth_flow.py's TestRefreshAndRevoke does."""
    oauth_client = _register_client()
    verifier, challenge = _pkce_pair()
    login_as_fn(client, user)

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
    tokens = resp.get_json()
    tokens["client_id"] = oauth_client.client_id
    return tokens


def _clear_cached_identity():
    """Clear flask-login's and the tenant middleware's per-app-context
    identity cache before a bearer-only call — see the matching helper in
    app/modules/mcp/tests/test_tools.py for the full explanation of why this
    is needed (``db_session`` holds one app context open for the whole
    test)."""
    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


class TestRevokeActuallyPersists:
    """The single most important regression test in this fix: revoking a
    token through the HTTP endpoint must actually invalidate it on a later,
    separate request — not just mutate an in-memory object that is about to
    be discarded."""

    def test_revoked_access_token_is_rejected_by_a_later_bearer_call(
        self, client, db_session, make_org, login_as, app
    ):
        org = make_org("revoke-persists")
        user = _make_user(db_session, org, "revoke-persists-access@example.com")
        tokens = _issue_token(client, app, user, login_as)

        resp = client.post("/oauth/revoke", data={"token": tokens["access_token"]})
        assert resp.status_code == 200

        # A later, separate request: a bearer-only call to /mcp (no session
        # cookie at all, matching a real MCP client) using the just-revoked
        # access token. Before the fix, revoke() never committed, so this
        # call would still succeed.
        _clear_cached_identity()
        bearer_only_client = client.application.test_client()
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp = bearer_only_client.post(
            "/mcp",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert resp.status_code == 401, resp.get_data(as_text=True)

    def test_revoked_refresh_token_is_rejected_by_a_later_refresh_grant(
        self, client, db_session, make_org, login_as, app
    ):
        org = make_org("revoke-persists")
        user = _make_user(db_session, org, "revoke-persists-refresh@example.com")
        tokens = _issue_token(client, app, user, login_as)

        resp = client.post("/oauth/revoke", data={"token": tokens["refresh_token"]})
        assert resp.status_code == 200

        # A later, separate request: redeem the just-revoked refresh token.
        # Before the fix, revoke() never committed, so this would still
        # succeed and mint a new token pair.
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": tokens["client_id"],
                "resource": _resource(app),
            },
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)
        assert resp.get_json()["error"] == "invalid_grant"

    def test_revoking_access_token_also_invalidates_the_paired_refresh_token(
        self, client, db_session, make_org, login_as, app
    ):
        """access_token and refresh_token are two columns on the SAME row —
        revoking via one invalidates both, through the one shared
        revoked_at."""
        org = make_org("revoke-persists")
        user = _make_user(db_session, org, "revoke-persists-paired@example.com")
        tokens = _issue_token(client, app, user, login_as)

        resp = client.post("/oauth/revoke", data={"token": tokens["access_token"]})
        assert resp.status_code == 200

        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": tokens["client_id"],
                "resource": _resource(app),
            },
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)
        assert resp.get_json()["error"] == "invalid_grant"

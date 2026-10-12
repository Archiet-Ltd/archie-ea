"""Regression coverage for the removed internal-bridge marker header.

A prior version of app/modules/oauth_provider/identity.py authenticated a
request against ANY route (not just /mcp) when it carried a hardcoded,
non-secret header (``X-Entelim-MCP-Bridge: internal-bearer-bridge-v1``)
alongside a valid MCP bearer token. Proven live: a bearer token minted for a
user via the real consent flow, with no session cookie, correctly got
redirected to login when calling a session-authenticated page — but the
identical request WITH that one header added was authenticated as that
user there too, full stop. The header and its value were committed in
source, so anyone holding any valid MCP bearer token (even a narrowly
read-only-scoped one) could forge it.

The fix removed the header-based branch of ``_is_mcp_scoped_request``
entirely rather than hardening it — it is now exactly
``request.path == "/mcp"``. These tests prove the removal: the header,
attached to a real, valid bearer token, authenticates nothing outside /mcp
any more — neither a read page nor a write endpoint.

Requires MCP_ENABLED=true and PUBLIC_BASE_URL (see
app/modules/oauth_provider/tests/test_oauth_flow.py's module docstring for
why — this module shares the same session-scoped ``app`` fixture):

    MCP_ENABLED=true PUBLIC_BASE_URL=https://mcp-test.example \\
        TEST_DATABASE_URL=postgresql://... \\
        pytest app/modules/oauth_provider/tests/test_bridge_header_removed.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.parse

# The exact header/value the removed mechanism used. Hardcoded here
# deliberately (not imported from identity.py) -- the whole point of this
# test is that nothing in the running app still recognises this literal
# constant, so it must not come from the module under test.
_BRIDGE_HEADER = "X-Entelim-MCP-Bridge"
_BRIDGE_VALUE = "internal-bearer-bridge-v1"


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
    """Mint a real, valid (narrowly mcp:read-scoped) OAuth access token
    through the actual authorization-code + PKCE flow -- not a forged or
    hand-built token. The attack this covers is specifically "a REAL token,
    plus one forged header", so the token side of the request must be
    genuine.
    """
    from app.modules.oauth_provider.models import OAuthClient

    oauth_client = OAuthClient.register(
        client_name="Bridge Header Removal Test Client",
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
    """See the matching helper's docstring in app/modules/mcp/tests/test_tools.py:
    db_session holds one app context open for the whole test, so g's
    flask-login/tenant caches from the login_as call above would otherwise
    leak into the forged request below."""
    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


class TestBridgeHeaderNoLongerAuthenticatesOutsideMcp:
    """The removed header, with a real bearer token, authenticates nothing
    outside the /mcp path -- not a read page, not a write endpoint."""

    def test_bridge_header_does_not_authenticate_non_mcp_route(
        self, client, db_session, make_org, login_as, app
    ):
        """A real bearer token + the old bridge header against a
        session-authenticated read page (GET /dashboard/overview) gets the
        same unauthenticated redirect a plain, header-less bearer call gets
        -- not 200."""
        org = make_org("bridge-removed")
        user = _make_user(db_session, org, "bridge-removed-read@example.com")
        token = _mint_oauth_token(client, app, user, login_as)

        _clear_cached_identity()
        forged_client = client.application.test_client()
        resp = forged_client.get(
            "/dashboard/overview",
            headers={
                "Authorization": f"Bearer {token}",
                _BRIDGE_HEADER: _BRIDGE_VALUE,
            },
        )
        # Never 200 and never the real dashboard content -- a bearer token
        # scoped to /mcp must not reach a session-authenticated page just
        # because this one extra header is attached.
        assert resp.status_code != 200, (
            "the removed bridge header must not authenticate a non-/mcp "
            f"route; got {resp.status_code}"
        )
        assert resp.status_code in (302, 401, 403), resp.status_code

        # Identical outcome to the exact same request MINUS the header --
        # proving the header contributes nothing, not just that this one
        # call happened to fail for some unrelated reason.
        _clear_cached_identity()
        baseline_client = client.application.test_client()
        baseline_resp = baseline_client.get(
            "/dashboard/overview",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert baseline_resp.status_code == resp.status_code

    def test_bridge_header_does_not_authenticate_write_route(
        self, client, db_session, make_org, login_as, app
    ):
        """A real bearer token + the old bridge header against a real
        write-shaped route (POST /api/v1/applications/, application
        creation) is refused -- not silently allowed to create a row."""
        from app.models.application_portfolio import ApplicationComponent

        org = make_org("bridge-removed-write")
        user = _make_user(db_session, org, "bridge-removed-write@example.com")
        token = _mint_oauth_token(client, app, user, login_as)

        unique_name = f"Bridge-Header-Forged-Create-{secrets.token_hex(8)}"
        before_count = ApplicationComponent.query.filter_by(name=unique_name).count()
        assert before_count == 0

        _clear_cached_identity()
        forged_client = client.application.test_client()
        resp = forged_client.post(
            "/api/v1/applications/",
            data=json.dumps({"name": unique_name}),
            content_type="application/json",
            headers={
                "Authorization": f"Bearer {token}",
                _BRIDGE_HEADER: _BRIDGE_VALUE,
            },
        )

        # Refused -- 401 is what this API blueprint's login_manager.
        # unauthorized_handler returns for an unauthenticated /api/ request;
        # 403/302 would also both be an acceptable "refused", but 200/201
        # (a created application) is exactly the escalation this proves is
        # gone.
        assert resp.status_code in (401, 403, 302), resp.status_code

        after_count = ApplicationComponent.query.filter_by(name=unique_name).count()
        assert after_count == 0, (
            "the forged request must not have created an application row"
        )

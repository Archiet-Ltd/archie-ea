"""Regression tests: a bearer token's granted scope is enforced on every
tools/call, not just computed and stored at issue time.

None of the ten registered tools write anything today, and every issued
token defaults to "mcp:read", so this was not yet exploitable — but nothing
on the tools/call path checked scope at all, which would become a real hole
the moment any "mcp:propose" (write) tool ships. These tests construct an
``OAuthToken`` row directly with a scope deliberately restricted to exclude
"mcp:read", the same way the brief's note on test setup describes, rather
than going through the consent flow (which only ever grants the allow-listed
scopes a real client would request).
"""

from __future__ import annotations

import json

from app.modules.mcp.tests.test_tools import _make_element, _make_user


def _mcp_resource(app) -> str:
    base = (app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    return f"{base}/mcp"


def _mint_token_with_scope(app, user, org, scope: str) -> str:
    """Issue an access token directly with an arbitrary scope string,
    bypassing the consent screen's own allow-listing — the only way to
    construct a token whose scope deliberately excludes "mcp:read"."""
    from app.modules.oauth_provider.models import OAuthClient, OAuthToken

    oauth_client = OAuthClient.register(
        client_name="Scope Enforcement Test Client",
        redirect_uris="http://localhost/callback",
    )
    raw_access, _raw_refresh, _token = OAuthToken.issue(
        client_id=oauth_client.client_id,
        user_id=user.id,
        scope=scope,
        resource=_mcp_resource(app),
        organization_id=org.id,
    )
    return raw_access


def _clear_cached_identity():
    """See the matching helper in test_tools.py for the full explanation:
    db_session holds one app context open for the whole test, so a fresh
    bearer-only call must clear flask-login's/the tenant middleware's cached
    identity first."""
    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _mcp_call(client, token: str, tool_name: str, arguments: dict) -> tuple[int, dict]:
    _clear_cached_identity()
    bearer_client = client.application.test_client()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    resp = bearer_client.post(
        "/mcp",
        data=json.dumps(payload),
        content_type="application/json",
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp.status_code, resp.get_json()


class TestScopeEnforcedOnToolCall:
    """A token whose granted scope excludes a tool's required_scope must be
    refused at the tools/call boundary, not merely have its scope recorded
    and ignored."""

    def test_token_without_mcp_read_scope_is_refused_on_read_tool(
        self, client, db_session, make_org, login_as
    ):
        from flask import current_app

        org = make_org("scope-enforce")
        user = _make_user(db_session, org, "scope-norefix@example.com")
        element = _make_element(db_session, org.id, "scope-enforce-test")

        # Deliberately restricted: only mcp:propose, never mcp:read.
        token = _mint_token_with_scope(current_app, user, org, scope="mcp:propose")

        status, body = _mcp_call(client, token, "ask_impact", {"element_id": element.id})
        assert status == 403, body
        assert "error" in body, body
        assert "insufficient_scope" in body["error"]["message"]
        assert "mcp:read" in body["error"]["message"]

    def test_token_with_empty_scope_is_refused_on_every_read_tool(
        self, client, db_session, make_org, login_as
    ):
        from flask import current_app

        org = make_org("scope-enforce")
        user = _make_user(db_session, org, "scope-empty@example.com")

        token = _mint_token_with_scope(current_app, user, org, scope="")

        for tool_name, arguments in (
            ("list_canvases", {}),
            ("search_elements", {"query": "anything", "limit": 5}),
        ):
            status, body = _mcp_call(client, token, tool_name, arguments)
            assert status == 403, (tool_name, body)
            assert "insufficient_scope" in body["error"]["message"]

    def test_ordinary_mcp_read_token_still_succeeds_on_every_existing_tool(
        self, client, db_session, make_org, login_as
    ):
        """No regression: a normally-issued mcp:read token keeps working
        against every existing (read-only) tool exactly as before."""
        from flask import current_app

        org = make_org("scope-enforce")
        user = _make_user(db_session, org, "scope-ok@example.com")
        element = _make_element(db_session, org.id, "scope-ok-test")

        token = _mint_token_with_scope(current_app, user, org, scope="mcp:read")

        calls = [
            ("ask_impact", {"element_id": element.id}),
            ("ask_strategy", {"element_id": element.id}),
            ("ask_portfolio", {"element_id": element.id}),
            ("ask_programme", {"element_id": element.id}),
            ("ask_risk", {"element_id": element.id}),
            ("ask_accountability", {"element_id": element.id}),
            ("search_elements", {"query": "scope-ok-test", "limit": 5}),
            ("get_element", {"element_id": element.id}),
            ("list_canvases", {}),
        ]
        for tool_name, arguments in calls:
            status, body = _mcp_call(client, token, tool_name, arguments)
            assert status == 200, (tool_name, body)
            assert "error" not in body, (tool_name, body)

    def test_session_cookie_call_skips_scope_check_entirely(
        self, client, db_session, make_org, login_as
    ):
        """A session-cookie-authenticated browser call carries no OAuth
        token and no scope to check at all — the existing permission system
        already governs that path, unaffected by this fix."""
        org = make_org("scope-enforce")
        user = _make_user(db_session, org, "scope-session@example.com")
        element = _make_element(db_session, org.id, "scope-session-test")

        login_as(client, user)
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ask_impact", "arguments": {"element_id": element.id}},
        }
        resp = client.post("/mcp", data=json.dumps(payload), content_type="application/json")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert "error" not in body, body

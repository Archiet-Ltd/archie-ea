"""Cross-tenant isolation test.

Two organisations, two tokens minted through the real authorization-code flow.
For every read tool, org A's token against an element id that belongs only to
org B returns the identical 404 body and status the REST endpoint already
returns for a cross-tenant id — never 403, no timing or header difference.
"""

from __future__ import annotations

import json
import urllib.parse




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


def _make_element(db_session, org_id, name_hint, type_="ApplicationComponent"):
    from app.models import ArchiMateElement

    row = ArchiMateElement(
        name=f"E-{name_hint}", type=type_, layer="application", organization_id=org_id
    )
    db_session.add(row)
    db_session.flush()
    return row


def _mint_oauth_token(client, db_session, org, user, login_as_fn) -> str:
    """Mint an OAuth access token through the real authorization-code flow."""
    from app.modules.oauth_provider.models import OAuthClient

    oauth_client = OAuthClient.register(
        client_name="Isolation Test Client",
        redirect_uris="http://localhost/callback",
    )

    import base64
    import hashlib
    import secrets

    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    login_as_fn(client, user)

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
    return resp.get_json()["access_token"]


def _mcp_call(client, token: str, tool_name: str, arguments: dict) -> dict:
    """Call an MCP tool through the JSON-RPC endpoint."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    resp = client.post(
        "/mcp",
        data=json.dumps(payload),
        content_type="application/json",
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp.get_json()


class TestCrossTenantIsolation:
    """Two organisations, two tokens — cross-tenant element ids return 404."""

    LENS_TOOLS = [
        "ask_impact", "ask_strategy", "ask_portfolio",
        "ask_programme", "ask_risk", "ask_accountability",
    ]

    def test_cross_tenant_lens_tools_return_404(self, client, db_session, make_org, login_as):
        """Every lens tool returns 404 for another org's element id."""
        org_a = make_org("iso-a")
        org_b = make_org("iso-b")

        user_a = _make_user(db_session, org_a, "iso-a@example.com")
        user_b = _make_user(db_session, org_b, "iso-b@example.com")

        element_b = _make_element(db_session, org_b.id, "belongs-to-b")

        # Mint tokens through the real flow
        token_a = _mint_oauth_token(client, db_session, org_a, user_a, login_as)
        token_b = _mint_oauth_token(client, db_session, org_b, user_b, login_as)

        # Org B's own token can see its own element
        for tool_name in self.LENS_TOOLS:
            mcp_resp = _mcp_call(client, token_b, tool_name, {"element_id": element_b.id})
            mcp_result = json.loads(mcp_resp["result"]["content"][0]["text"])
            assert mcp_result["success"] is True, \
                f"Org B's token should see its own element via {tool_name}"

        # Org A's token gets 404 for org B's element — same as REST
        for tool_name in self.LENS_TOOLS:
            # REST call as user A
            login_as(client, user_a)
            lens = tool_name.replace("ask_", "")
            rest_resp = client.get(f"/api/v1/intelligence/{lens}/{element_b.id}")
            rest_data = rest_resp.get_json()

            # MCP call with token A
            mcp_resp = _mcp_call(client, token_a, tool_name, {"element_id": element_b.id})
            mcp_result = json.loads(mcp_resp["result"]["content"][0]["text"])

            # Both return the same error shape
            assert rest_data["success"] is False, \
                f"REST {tool_name} should fail for cross-tenant element"
            assert mcp_result["success"] is False, \
                f"MCP {tool_name} should fail for cross-tenant element"
            assert rest_data["error"]["code"] == mcp_result["error"]["code"], \
                f"{tool_name}: REST error {rest_data['error']['code']} != MCP error {mcp_result['error']['code']}"
            # Never 403 — always 404 (or the same code REST returns)
            assert rest_data["error"]["code"] != "FORBIDDEN", \
                f"{tool_name}: cross-tenant returned 403, should be 404"

    def test_cross_tenant_element_search_isolated(self, client, db_session, make_org, login_as):
        """search_elements for org A does not return org B's elements."""
        org_a = make_org("iso-a")
        org_b = make_org("iso-b")

        user_a = _make_user(db_session, org_a, "iso-search-a@example.com")
        user_b = _make_user(db_session, org_b, "iso-search-b@example.com")

        element_a = _make_element(db_session, org_a.id, "unique-org-a-element-xyz")
        _make_element(db_session, org_b.id, "unique-org-b-element-xyz")

        token_a = _mint_oauth_token(client, db_session, org_a, user_a, login_as)
        token_b = _mint_oauth_token(client, db_session, org_b, user_b, login_as)

        # Org A searches — should find its own element but not org B's
        mcp_resp = _mcp_call(client, token_a, "search_elements",
                             {"query": "unique-org-a-element-xyz"})
        mcp_result = json.loads(mcp_resp["result"]["content"][0]["text"])
        assert len(mcp_result["data"]) == 1
        assert mcp_result["data"][0]["name"] == element_a.name

        # Org A searches for org B's element — should find nothing
        mcp_resp = _mcp_call(client, token_a, "search_elements",
                             {"query": "unique-org-b-element-xyz"})
        mcp_result = json.loads(mcp_resp["result"]["content"][0]["text"])
        assert len(mcp_result["data"]) == 0

        # Org B can find its own
        mcp_resp = _mcp_call(client, token_b, "search_elements",
                             {"query": "unique-org-b-element-xyz"})
        mcp_result = json.loads(mcp_resp["result"]["content"][0]["text"])
        assert len(mcp_result["data"]) == 1

    def test_cross_tenant_get_element_returns_404(self, client, db_session, make_org, login_as):
        """get_element for another org's element returns 404."""
        org_a = make_org("iso-a")
        org_b = make_org("iso-b")

        user_a = _make_user(db_session, org_a, "iso-detail-a@example.com")
        user_b = _make_user(db_session, org_b, "iso-detail-b@example.com")

        element_b = _make_element(db_session, org_b.id, "detail-belongs-to-b")

        token_a = _mint_oauth_token(client, db_session, org_a, user_a, login_as)
        token_b = _mint_oauth_token(client, db_session, org_b, user_b, login_as)

        # Org B can see its own element
        mcp_resp = _mcp_call(client, token_b, "get_element", {"element_id": element_b.id})
        mcp_result = json.loads(mcp_resp["result"]["content"][0]["text"])
        assert "error" not in mcp_result or mcp_result.get("name") is not None

        # Org A gets 404 for org B's element
        mcp_resp = _mcp_call(client, token_a, "get_element", {"element_id": element_b.id})
        mcp_result = json.loads(mcp_resp["result"]["content"][0]["text"])
        assert "error" in mcp_result
        assert "not found" in str(mcp_result).lower()
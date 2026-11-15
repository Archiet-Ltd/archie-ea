"""A client that follows the MCP Streamable HTTP transport never receives HTTP 404 from POST /mcp.

A 404 tells such a client its server is gone, so it drops the connection. Unknown methods are answered as
JSON-RPC errors with HTTP 200, notifications with HTTP 202 and no body, and ping with an empty result.
"""

from __future__ import annotations

import json

import pytest

from app.modules.mcp.tests.test_tools import _clear_cached_identity, _make_user, _mint_oauth_token


def _post(client, token, payload):
    _clear_cached_identity()
    fresh = client.application.test_client()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return fresh.post("/mcp", data=json.dumps(payload), content_type="application/json", headers=headers)


@pytest.fixture
def token(client, db_session, make_org, login_as):
    org = make_org("mcpnever404")
    user = _make_user(db_session, org, "mcp-never404@example.com")
    return _mint_oauth_token(client, db_session, org, user, login_as)


def _released_tool(client, token):
    reply = _post(client, token, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert reply.status_code == 200
    return reply.get_json()["result"]["tools"][0]["name"]


def test_a_claude_handshake_never_meets_a_404(client, token):
    replies = {}

    def send(key, payload):
        replies[key] = _post(client, token, payload)
        return replies[key]

    init = send("initialize", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init.status_code == 200
    assert init.get_json()["result"]["protocolVersion"] == "2025-11-25"

    note = send("initialized", {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert note.status_code == 202 and note.get_data() == b""

    ping = send("ping", {"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert ping.status_code == 200
    assert ping.get_json() == {"jsonrpc": "2.0", "id": 2, "result": {}}

    for key, method, rid in (
        ("resources", "resources/list", 3),
        ("templates", "resources/templates/list", 4),
        ("prompts", "prompts/list", 5),
        ("invented", "invented/method", 6),
    ):
        reply = send(key, {"jsonrpc": "2.0", "id": rid, "method": method})
        body = reply.get_json()
        assert reply.status_code == 200, (method, reply.status_code)
        assert body["id"] == rid and body["error"]["code"] == -32601, body
        assert method in body["error"]["message"]

    other = send("note2", {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}})
    assert other.status_code == 202 and other.get_data() == b""

    listed = send("tools/list", {"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
    assert listed.status_code == 200 and listed.get_json()["result"]["tools"]

    name = _released_tool(client, token)
    call = send("call", {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": name, "arguments": {}}})
    assert call.status_code != 404, call.get_json()

    unknown = send("unknown", {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                               "params": {"name": "no_such_tool", "arguments": {}}})
    body = unknown.get_json()
    assert unknown.status_code == 200
    assert body["id"] == 9 and body["error"]["code"] == -32601
    assert "Tool not found: no_such_tool" in body["error"]["message"]

    assert all(r.status_code != 404 for r in replies.values())


def test_an_early_access_tool_is_a_json_rpc_error_not_a_404(client, token, monkeypatch):
    monkeypatch.setitem(client.application.config, "RELEASE_WAVE", "1")
    reply = _post(client, token, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                  "params": {"name": "list_canvases", "arguments": {}}})
    body = reply.get_json()
    assert reply.status_code == 200
    assert body["error"]["code"] == -32601 and "not available yet" in body["error"]["message"]


def test_the_401_points_the_client_at_the_resource_metadata(client):
    from app.services.origin import canonical_origin

    reply = _post(client, None, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert reply.status_code == 401
    header = reply.headers["WWW-Authenticate"]
    expected = f'resource_metadata="{canonical_origin()}/.well-known/oauth-protected-resource/mcp"'
    assert expected in header and header.startswith("Bearer realm=")

"""The connector offers only the tools that can answer at the released wave.

At wave 1 the business-model and business-case read tools are not listed and a direct call to either answers
a plain "not available yet" error (JSON, never an early-access page). With the switch off the list is unchanged.
"""

from __future__ import annotations

import json

import pytest

from app.modules.mcp.tests.test_tools import _make_user, _mcp_tools_list, _mint_oauth_token

CANVAS_TOOLS = {"list_canvases", "get_canvas"}


def _call(client, token, name, arguments):
    from app.modules.mcp.tests.test_tools import _clear_cached_identity

    _clear_cached_identity()
    fresh = client.application.test_client()
    payload = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    return fresh.post(
        "/mcp", data=json.dumps(payload), content_type="application/json",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.fixture
def token(client, db_session, make_org, login_as):
    org = make_org("mcpwave")
    user = _make_user(db_session, org, "mcp-wave@example.com")
    return _mint_oauth_token(client, db_session, org, user, login_as)


def test_with_the_switch_off_the_canvas_tools_are_listed_and_called(client, token, monkeypatch):
    monkeypatch.setitem(client.application.config, "RELEASE_WAVE", None)
    monkeypatch.delenv("RELEASE_WAVE", raising=False)
    names = {t["name"] for t in _mcp_tools_list(client, token)}
    assert CANVAS_TOOLS <= names
    reply = _call(client, token, "list_canvases", {})
    assert reply.status_code == 200
    assert "result" in reply.get_json()


def test_at_wave_one_the_canvas_tools_are_not_listed_and_a_direct_call_says_not_available(client, token, monkeypatch):
    monkeypatch.setitem(client.application.config, "RELEASE_WAVE", None)
    monkeypatch.delenv("RELEASE_WAVE", raising=False)
    before = {t["name"] for t in _mcp_tools_list(client, token)}
    monkeypatch.setitem(client.application.config, "RELEASE_WAVE", "1")
    after = {t["name"] for t in _mcp_tools_list(client, token)}
    assert before - after == CANVAS_TOOLS, before ^ after
    assert after <= before
    assert "ask_impact" in after and "search_elements" in after and "get_element" in after
    for name, args in (("list_canvases", {}), ("get_canvas", {"canvas_type": "business_model", "canvas_id": 1})):
        reply = _call(client, token, name, args)
        assert reply.status_code == 404
        assert reply.mimetype == "application/json"
        message = reply.get_json()["error"]["message"]
        assert "not available yet" in message and "<" not in message

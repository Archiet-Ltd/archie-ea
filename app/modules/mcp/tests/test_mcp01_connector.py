"""mcp01: what a real assistant connector found. Addresses are absolute; read-only tools say so."""
from __future__ import annotations

import json

from app.modules.mcp.tests.test_tools import (
    _make_element, _make_user, _mcp_call, _mcp_tools_list, _mint_oauth_token,
)


def _text(response):
    body = response if isinstance(response, dict) else response.get_json()
    return json.loads(body["result"]["content"][0]["text"])


def test_every_address_an_answer_returns_is_absolute(client, db_session, make_org, login_as):
    org = make_org("mcp-abs")
    user = _make_user(db_session, org, "mcp-abs@example.com")
    element = _make_element(db_session, org.id, "billing-service")
    token = _mint_oauth_token(client, db_session, org, user, login_as)

    out = _mcp_call(client, token, "ask_impact", {"element_id": element.id})
    text = out["result"]["content"][0]["text"]
    base = client.application.config["PUBLIC_BASE_URL"].rstrip("/")
    assert base
    answer = json.loads(text)
    answer = answer.get("data", answer)
    body = answer.get("result", answer)
    assert body["page_url"] == base + "/intelligence/answers/" + body["address"], body["page_url"]
    assert body["api_url"] == base + "/api/v1/intelligence/answers/" + body["address"]
    assert "open at /" not in text


def test_the_read_only_tools_say_what_they_are(client, db_session, make_org, login_as):
    org = make_org("mcp-ann")
    user = _make_user(db_session, org, "mcp-ann@example.com")
    token = _mint_oauth_token(client, db_session, org, user, login_as)
    tools = {t["name"]: t for t in _mcp_tools_list(client, token)}
    for name in ("ask_impact", "ask_strategy", "ask_portfolio", "ask_programme", "ask_risk",
                 "ask_accountability", "answer", "get_element", "search_elements"):
        ann = tools[name]["annotations"]
        assert ann["readOnlyHint"] is True and ann["destructiveHint"] is False, name
        assert ann["openWorldHint"] is False and ann["title"], name

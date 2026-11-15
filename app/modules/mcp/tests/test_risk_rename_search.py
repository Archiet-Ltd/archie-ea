"""A renamed risk is found by its new title through the MCP search, and not by the old one."""

import json
import uuid

from flask import g

from app.services import risk_service

from .test_tools import _make_user, _mcp_call, _mint_oauth_token


def _search(client, token, query):
    reply = _mcp_call(client, token, "search_elements", {"query": query, "limit": 10})
    return json.loads(reply["result"]["content"][0]["text"])["data"]


def test_a_renamed_risk_is_found_by_its_new_title_only(client, app, db_session, make_org, login_as):
    org = make_org("mcp-risk-rename")
    other = make_org("mcp-risk-rename-other")
    user = _make_user(db_session, org, "mcp-risk-rename@example.com")
    old = "Legacy ledger %s" % uuid.uuid4().hex[:8]
    new = "Payments queue %s" % uuid.uuid4().hex[:8]
    with app.test_request_context("/"):
        g.current_org_id = org.id
        risk = risk_service.create_risk(None, old, None, 3, 3, "Ops", None, organization_id=org.id)
        element_id = risk.archimate_element_id
        assert element_id is not None
        risk_service.update_risk(risk.id, title=new)
    token = _mint_oauth_token(client, db_session, org, user, login_as)

    assert [row["id"] for row in _search(client, token, new)] == [element_id]
    assert _search(client, token, old) == []
    assert other.id != org.id

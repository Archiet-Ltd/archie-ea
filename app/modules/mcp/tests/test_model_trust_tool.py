"""T-ain-04: get_model_trust through the assistant connection reports the same
figures as the resolver, the read API and the answer envelope, for the token's
own organisation only. Runs with the connector switched on (MCP_ENABLED)."""

from __future__ import annotations

import json


def test_the_connection_the_api_and_the_envelope_report_the_same_figures(client, db_session, make_org, login_as):
    from flask import current_app

    from app.modules.intelligence.services.model_trust import model_trust
    from app.modules.intelligence.tests.test_model_trust import _area, _element, _estate, _user
    from app.modules.mcp.tests.test_scope_enforcement import _mcp_call, _mint_token_with_scope

    org_a = make_org("trust-mcp-a")
    org_b = make_org("trust-mcp-b")
    apps, _goal, _owner_user = _estate(db_session, org_a.id)
    _element(db_session, org_b.id, "OnlyInB")
    user_a = _user(db_session, org_a.id)
    user_b = _user(db_session, org_b.id)
    db_session.commit()
    expected = _area(model_trust(org_a.id), "application")

    token_a = _mint_token_with_scope(current_app, user_a, org_a, "mcp:read")
    status, body = _mcp_call(client, token_a, "get_model_trust", {})
    assert status == 200, body
    mcp_all = json.loads(body["result"]["content"][0]["text"])["data"]
    status, body = _mcp_call(client, token_a, "get_model_trust", {"area": "application"})
    assert status == 200, body
    mcp_one = json.loads(body["result"]["content"][0]["text"])["data"]

    login_as(client, user_a)
    api = _area(client.get("/api/v1/intelligence/trust").get_json()["data"], "application")
    envelope = client.post(
        "/api/v1/intelligence/answers", json={"question": "impact", "element_id": apps[0].id}
    ).get_json()["data"]["freshness"]["model_trust"][0]

    assert _area(mcp_all, "application") == expected == api == envelope
    assert mcp_one["areas"] == [expected]
    assert mcp_one["basis"] and mcp_one["computed_at"]

    token_b = _mint_token_with_scope(current_app, user_b, org_b, "mcp:read")
    status, body = _mcp_call(client, token_b, "get_model_trust", {})
    assert status == 200, body
    theirs = _area(json.loads(body["result"]["content"][0]["text"])["data"], "application")
    assert theirs["elements_total"] == 1 and theirs["coverage"]["owner"]["have"] == 0
    assert "App1" not in json.dumps(theirs)


def test_an_unknown_area_is_refused_through_the_connection(client, db_session, make_org):
    from flask import current_app

    from app.modules.intelligence.tests.test_model_trust import _user
    from app.modules.mcp.tests.test_scope_enforcement import _mcp_call, _mint_token_with_scope

    org = make_org("trust-mcp-bad")
    user = _user(db_session, org.id)
    db_session.commit()
    token = _mint_token_with_scope(current_app, user, org, "mcp:read")

    status, body = _mcp_call(client, token, "get_model_trust", {"area": "not-an-area"})

    assert status == 200, body
    assert json.loads(body["result"]["content"][0]["text"])["success"] is False

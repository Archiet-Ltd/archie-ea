"""T-genome-01 through the assistant connector: get_genome_slice for the token's organisation only.

A token minted through the real authorization-code flow for organisation A's user
returns A's slice, with the AI notice, node ids and server-built links the
customer's own assistant can cite. Organisation B (near-identical name and record
names) never appears, and an id of B's reads exactly like a missing id.
"""
from __future__ import annotations

import json
import uuid

from app.modules.mcp.tests.test_isolation import _make_user, _mcp_call, _mint_oauth_token
from tests.genome_cited_seed import seed_cited_model


def _org(db_session, name):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name="%s %s" % (name, suffix), slug="gm-%s" % suffix)
    db_session.add(org)
    db_session.flush()
    return org


def _payload(response):
    assert "result" in response, response
    return json.loads(response["result"]["content"][0]["text"])


def test_the_connector_lists_the_genome_tool(client, db_session, login_as, app):
    org = _org(db_session, "Acme Payments")
    user = _make_user(db_session, org, "gm-list-%s@example.com" % uuid.uuid4().hex[:6])
    token = _mint_oauth_token(client, db_session, org, user, login_as, app)
    from app.modules.mcp.tests.test_isolation import _clear_cached_identity

    _clear_cached_identity()
    resp = client.application.test_client().post(
        "/mcp",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        content_type="application/json",
        headers={"Authorization": "Bearer %s" % token},
    ).get_json()
    tool = next(t for t in resp["result"]["tools"] if t["name"] == "get_genome_slice")
    assert tool["annotations"] == {
        "title": "Get genome slice",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    assert "organization_id" not in json.dumps(tool["inputSchema"])


def test_organisation_a_gets_its_slice_with_the_notice_and_links_and_nothing_of_b(
    client, db_session, login_as, app,
):
    org_a = _org(db_session, "Acme Payments")
    org_b = _org(db_session, "Acme Payment")
    seed_a = seed_cited_model(db_session, org_a.id)
    seed_b = seed_cited_model(db_session, org_b.id, suffix="s")
    user_a = _make_user(db_session, org_a, "gm-a-%s@example.com" % uuid.uuid4().hex[:6])
    user_b = _make_user(db_session, org_b, "gm-b-%s@example.com" % uuid.uuid4().hex[:6])
    token_a = _mint_oauth_token(client, db_session, org_a, user_a, login_as, app)
    _mint_oauth_token(client, db_session, org_b, user_b, login_as, app)

    business = _payload(_mcp_call(client, token_a, "get_genome_slice", {"domain": "business"}))
    roadmap = _payload(_mcp_call(client, token_a, "get_genome_slice", {"domain": "implementation"}))

    for result in (business, roadmap):
        assert result["success"] is True
        body = result["result"]
        assert body["organization_id"] == org_a.id
        assert body["notice"] == "Answer written by AI from your model"
        assert body["as_of"]
        for node in body["nodes"]:
            assert node["url"].endswith(node["link"]) and node["url"].startswith("http")
        text = json.dumps(body)
        for name in seed_b["names"].values():
            assert '"%s"' % name not in text, "organisation B's record reached A's token"
    unsupported = sorted(n["name"] for n in business["result"]["nodes"] if n["kind"] == "cap" and not n["supported"])
    assert unsupported == ["Fraud Screening", "Partner Payouts"]
    wp = next(n for n in roadmap["result"]["nodes"] if n["kind"] == "wp")
    assert wp["capability"] == "business:cap:%d" % seed_a["cap_ids"]["planned"]

    foreign = _mcp_call(client, token_a, "get_genome_slice",
                        {"domain": "business", "focus_ids": ["business:cap:%d" % seed_b["cap_ids"]["planned"]]})
    missing = _mcp_call(client, token_a, "get_genome_slice",
                        {"domain": "business", "focus_ids": ["business:cap:987654321"]})
    assert _payload(foreign) == _payload(missing) == {"success": False, "error": "Not found.", "code": "NOT_FOUND"}

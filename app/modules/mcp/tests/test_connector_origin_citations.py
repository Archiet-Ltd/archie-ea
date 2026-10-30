"""The assistant connector's ask_impact names where each connector-written link came from.

A link the GitHub connector wrote carries its repository, commit and file. The answer a connected
assistant gets for "what breaks if X fails" must carry them, so the answer can be cited; a drawn link
keeps exactly the shape it had.
"""

from __future__ import annotations

import json

from app.modules.mcp.tests.test_tools import _make_element, _make_user, _mint_oauth_token


def _link(db_session, org_id, source, target, **origin):
    from app.models import ArchiMateRelationship

    row = ArchiMateRelationship(type="Serving", source_id=source.id, target_id=target.id, organization_id=org_id, **origin)
    db_session.add(row)
    db_session.flush()
    return row


def _ask(client, token, element_id):
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "ask_impact", "arguments": {"element_id": element_id}}}
    resp = client.post("/mcp", data=json.dumps(payload), content_type="application/json", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert "error" not in body, body
    return json.loads(body["result"]["content"][0]["text"])


def test_ask_impact_carries_the_repository_and_commit_of_a_connector_written_link(client, db_session, make_org, login_as):
    org = make_org("mcp-origin")
    user = _make_user(db_session, org, "mcp-origin@example.com")
    supplier = _make_element(db_session, org.id, "supplier")
    consumer = _make_element(db_session, org.id, "consumer")
    drawn_target = _make_element(db_session, org.id, "drawn")
    commit = "ab" * 20
    written = _link(
        db_session, org.id, supplier, consumer,
        source_kind="connector",
        source_ref=json.dumps({"system": "github", "repository": "acme/consumer", "commit": commit, "file": "go.mod", "detail": "go.mod names github.com/acme/supplier"}),
        source_label="GitHub acme/consumer @ abababa (go.mod)",
    )
    drawn = _link(db_session, org.id, supplier, drawn_target, source_kind="manual", source_label="Ada")
    token = _mint_oauth_token(client, db_session, org, user, login_as)

    out = _ask(client, token, supplier.id)

    body = out.get("data", out)
    body = body.get("result", body)
    rows = {row["element_id"]: row["relation"] for row in body["rows"]}
    origin = rows[consumer.id]["origins"][str(written.id)]
    assert origin["repository"] == "acme/consumer" and origin["commit"] == commit and origin["file"] == "go.mod"
    assert origin["detail"] == "go.mod names github.com/acme/supplier"
    assert "origins" not in rows[drawn_target.id], "a drawn link keeps the shape it had"
    assert str(drawn.id) not in json.dumps(rows[consumer.id].get("origins", {}))

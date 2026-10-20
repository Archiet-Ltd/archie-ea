"""Every answer through the connector carries citations, confidence and an as-of date.

The ``answer`` tool is the one canonical answer service published on the connector;
``ask_impact`` returns the envelope that service stores. The organisation always
comes from the token.
"""

from __future__ import annotations

from app.modules.mcp.tests.test_scope_enforcement import _mcp_call, _mint_token_with_scope
from app.modules.mcp.tests.test_tools import _make_element, _make_user, _mcp_tools_list


def _estate(db_session, org_id, prefix):
    from app.models import ArchiMateRelationship

    root = _make_element(db_session, org_id, f"{prefix}-root")
    leaf = _make_element(db_session, org_id, f"{prefix}-leaf")
    db_session.add(ArchiMateRelationship(
        source_id=root.id, target_id=leaf.id, type="Serving", organization_id=org_id))
    db_session.flush()
    return root, leaf


def _token(user, org):
    from flask import current_app

    return _mint_token_with_scope(current_app, user, org, "mcp:read")


def _payload(body):
    """The tool result inside the JSON-RPC reply, whichever way it is wrapped."""
    import json

    result = body["result"]
    if isinstance(result, dict) and "content" in result:
        return json.loads(result["content"][0]["text"])
    return result


def _envelope(payload):
    return payload.get("result", payload)


def _assert_cited(env, leaf_id):
    assert [r["element_id"] for r in env["rows"]] == [leaf_id]
    assert env["citations"], env
    assert "confidence" in env and "confidence_basis" in env
    assert env["freshness"]["computed_at"]
    assert env["computed_at"]


def test_tools_list_includes_answer(client, db_session, make_org):
    org = make_org("cited-list")
    user = _make_user(db_session, org, "cited-list@example.com")
    names = {t["name"] for t in _mcp_tools_list(client, _token(user, org))}
    assert "answer" in names


def test_mcp_answer_returns_citations_confidence_and_as_of(client, db_session, make_org):
    org = make_org("cited-answer")
    user = _make_user(db_session, org, "cited-answer@example.com")
    root, leaf = _estate(db_session, org.id, "ca")
    db_session.commit()
    status, body = _mcp_call(client, _token(user, org), "answer",
                             {"question": "impact", "element_id": root.id})
    assert status == 200, body
    payload = _payload(body)
    assert payload["success"] is True, payload
    _assert_cited(_envelope(payload), leaf.id)


def test_mcp_ask_impact_returns_citations_confidence_and_as_of(client, db_session, make_org):
    org = make_org("cited-impact")
    user = _make_user(db_session, org, "cited-impact@example.com")
    root, leaf = _estate(db_session, org.id, "ci")
    db_session.commit()
    status, body = _mcp_call(client, _token(user, org), "ask_impact", {"element_id": root.id})
    assert status == 200, body
    payload = _payload(body)
    assert payload["success"] is True, payload
    _assert_cited(_envelope(payload), leaf.id)


def test_mcp_ask_impact_with_no_rows_says_so_plainly(client, db_session, make_org):
    org = make_org("cited-empty")
    user = _make_user(db_session, org, "cited-empty@example.com")
    lone = _make_element(db_session, org.id, "ce-lone")
    db_session.commit()
    status, body = _mcp_call(client, _token(user, org), "ask_impact", {"element_id": lone.id})
    assert status == 200, body
    payload = _payload(body)
    assert payload["success"] is True, payload
    assert _envelope(payload)["rows"] == []
    assert "Nothing is recorded" in payload["message"]


def test_organisation_a_token_never_receives_b_through_answer_or_ask_impact(
    client, db_session, make_org
):
    org_a, org_b = make_org("cited-iso-a"), make_org("cited-iso-b")
    user_a = _make_user(db_session, org_a, "cited-iso-a@example.com")
    root_a, leaf_a = _estate(db_session, org_a.id, "isoA")
    root_b, leaf_b = _estate(db_session, org_b.id, "isoB")
    db_session.commit()
    token = _token(user_a, org_a)

    # B's element asked with A's token: not recorded, and nothing of B comes back.
    for tool, args in (
        ("answer", {"question": "impact", "element_id": root_b.id}),
        ("ask_impact", {"element_id": root_b.id}),
    ):
        _status, body = _mcp_call(client, token, tool, args)
        text = str(body)
        assert "isoB" not in text, (tool, body)
        assert str(leaf_b.id) not in {str(r.get("element_id")) for r in
                                      (_envelope(_payload(body)).get("rows") or [])}, (tool, body)
        assert not (_payload(body).get("success") and _envelope(_payload(body)).get("rows")), (tool, body)

    # A's own element still answers, with only A's rows and citations.
    for tool, args in (
        ("answer", {"question": "impact", "element_id": root_a.id}),
        ("ask_impact", {"element_id": root_a.id}),
    ):
        _status, body = _mcp_call(client, token, tool, args)
        env = _envelope(_payload(body))
        assert [r["element_id"] for r in env["rows"]] == [leaf_a.id], (tool, body)
        cited_ids = {c["id"] for c in env["citations"]}
        assert leaf_b.id not in cited_ids and root_b.id not in cited_ids
        assert "isoB" not in str(body)

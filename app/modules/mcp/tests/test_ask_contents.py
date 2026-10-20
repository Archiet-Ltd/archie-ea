"""ask_contents: what is in a release, plateau or work package.

Two organisations. Organisation A has a WorkPackage "Release 1" with two
applications and three capabilities linked (Realization, Aggregation,
Association, in both directions); organisation B has its own "Release 1" and,
inside organisation A's own relationship table, a stray link to B's element.
The reader and the MCP tool, called as organisation A's user, return exactly
A's five rows and never one of B's.
"""

from __future__ import annotations

import json

from app.modules.mcp.tests.test_tools import (
    _make_user,
    _mcp_call,
    _mcp_tools_list,
    _mint_oauth_token,
)

DOC = "Delivered in the first release"


def _el(db_session, org_id, name, type_, layer):
    from app.models import ArchiMateElement

    row = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def _rel(db_session, org_id, source, target, type_, description=None):
    from app.models import ArchiMateRelationship

    row = ArchiMateRelationship(
        source_id=source.id, target_id=target.id, type=type_,
        organization_id=org_id, description=description,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _seed(db_session, make_org, commit=False):
    org_a, org_b = make_org("contents-a"), make_org("contents-b")
    out = {"org_a": org_a, "org_b": org_b}

    rel1 = _el(db_session, org_a.id, "Release 1", "WorkPackage", "implementation_migration")
    app1 = _el(db_session, org_a.id, "Billing", "ApplicationComponent", "application")
    app2 = _el(db_session, org_a.id, "Ledger", "ApplicationComponent", "application")
    cap1 = _el(db_session, org_a.id, "Invoicing", "Capability", "strategy")
    cap2 = _el(db_session, org_a.id, "Collections", "Capability", "strategy")
    cap3 = _el(db_session, org_a.id, "Reporting", "Capability", "strategy")
    actor = _el(db_session, org_a.id, "Finance team", "BusinessActor", "business")

    _rel(db_session, org_a.id, app1, rel1, "realization", DOC)       # incoming
    _rel(db_session, org_a.id, rel1, app2, "aggregation")            # outgoing
    _rel(db_session, org_a.id, cap1, rel1, "association")            # incoming
    _rel(db_session, org_a.id, rel1, cap2, "Aggregation")            # outgoing, stored capitalised
    _rel(db_session, org_a.id, cap3, rel1, "realization")            # incoming
    _rel(db_session, org_a.id, rel1, actor, "association")           # not a content type
    _rel(db_session, org_a.id, rel1, app1, "flow")                   # not a content relationship

    b_rel1 = _el(db_session, org_b.id, "Release 1", "WorkPackage", "implementation_migration")
    b_app = _el(db_session, org_b.id, "B-Secret-App", "ApplicationComponent", "application")
    b_cap = _el(db_session, org_b.id, "B-Secret-Capability", "Capability", "strategy")
    _rel(db_session, org_b.id, b_app, b_rel1, "realization", "B-Secret-Doc")
    _rel(db_session, org_b.id, b_rel1, b_cap, "aggregation")
    # A relationship row filed under A that points at B's element, and one
    # filed under B that points at A's release: neither may surface.
    _rel(db_session, org_a.id, rel1, b_app, "aggregation")
    _rel(db_session, org_b.id, b_cap, rel1, "association")

    user_a = _make_user(db_session, org_a, "contents-a@example.com")
    if commit:
        db_session.commit()
    out.update(release=rel1, b_release=b_rel1, user_a=user_a, user_b=None)
    return out


EXPECTED = {
    "Billing": ("realization", "incoming", "ApplicationComponent"),
    "Ledger": ("aggregation", "outgoing", "ApplicationComponent"),
    "Invoicing": ("association", "incoming", "Capability"),
    "Collections": ("aggregation", "outgoing", "Capability"),
    "Reporting": ("realization", "incoming", "Capability"),
}


def _check_rows(rows):
    assert {r["name"] for r in rows} == set(EXPECTED), rows
    assert len(rows) == 5
    for r in rows:
        rel_type, direction, type_ = EXPECTED[r["name"]]
        assert r["relationship_type"].lower() == rel_type
        assert r["direction"] == direction
        assert r["type"] == type_
        assert r["layer"]
        assert r["hop"] == 1 and r["derived"] is False
    billing = next(r for r in rows if r["name"] == "Billing")
    assert billing["documentation"] == DOC
    assert "B-Secret" not in json.dumps(rows)


def test_reader_returns_the_five_rows_and_never_the_other_organisation(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    s = _seed(db_session, make_org, commit=True)
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = s["org_a"].id
        result = IntelligenceQueryService.contents_for_element(s["release"].id)
        other = IntelligenceQueryService.contents_for_element(s["b_release"].id)

    assert result["element"]["name"] == "Release 1"
    assert result["reasons"] == []
    _check_rows(result["contents"])
    # B's package is simply not found from A's organisation.
    assert other["contents"] == [] and other["reasons"] == ["element_not_found"]


def test_reader_gives_the_derived_second_hop_through_a_linked_work_package(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    s = _seed(db_session, make_org)
    org = s["org_a"]
    sub = _el(db_session, org.id, "Release 1a", "WorkPackage", "implementation_migration")
    deep = _el(db_session, org.id, "Payments", "ApplicationComponent", "application")
    _rel(db_session, org.id, s["release"], sub, "composition")
    _rel(db_session, org.id, deep, sub, "serving")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        rows = IntelligenceQueryService.contents_for_element(s["release"].id)["contents"]

    hop2 = [r for r in rows if r["hop"] == 2]
    assert [(r["name"], r["via"]["name"], r["derived"]) for r in hop2] == [("Payments", "Release 1a", True)]
    assert any(r["name"] == "Release 1a" and r["hop"] == 1 for r in rows)


def test_a_package_with_nothing_linked_and_a_non_container_give_the_reason_code(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("contents-empty")
    empty = _el(db_session, org.id, "Release 9", "WorkPackage", "implementation_migration")
    plain = _el(db_session, org.id, "Some app", "ApplicationComponent", "application")
    db_session.commit()
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        a = IntelligenceQueryService.contents_for_element(empty.id)
        b = IntelligenceQueryService.contents_for_element(plain.id)
    assert a["contents"] == [] and a["reasons"] == ["no_contents_recorded"]
    assert b["contents"] == [] and b["reasons"] == ["no_contents_recorded"]


def test_mcp_tool_is_published_and_read_only(client, db_session, make_org, login_as):
    s = _seed(db_session, make_org)
    token = _mint_oauth_token(client, db_session, s["org_a"], s["user_a"], login_as)
    tools = {t["name"]: t for t in _mcp_tools_list(client, token)}
    assert "ask_contents" in tools
    assert tools["ask_contents"]["annotations"] == {"readOnlyHint": True}


def test_mcp_tool_by_id_and_by_name_as_organisation_a(client, db_session, make_org, login_as):
    s = _seed(db_session, make_org)
    token = _mint_oauth_token(client, db_session, s["org_a"], s["user_a"], login_as)

    by_id = json.loads(
        _mcp_call(client, token, "ask_contents", {"element_id": s["release"].id})["result"]["content"][0]["text"]
    )
    assert by_id["success"] is True
    _check_rows(by_id["data"]["contents"])

    by_name = json.loads(
        _mcp_call(client, token, "ask_contents", {"name": "release 1"})["result"]["content"][0]["text"]
    )
    assert by_name["success"] is True
    assert by_name["data"]["element"]["id"] == s["release"].id
    _check_rows(by_name["data"]["contents"])


def test_mcp_tool_never_reaches_the_other_organisations_package(client, db_session, make_org, login_as):
    s = _seed(db_session, make_org)
    token = _mint_oauth_token(client, db_session, s["org_a"], s["user_a"], login_as)

    foreign = json.loads(
        _mcp_call(client, token, "ask_contents", {"element_id": s["b_release"].id})["result"]["content"][0]["text"]
    )
    assert foreign["success"] is False
    assert foreign["error"]["code"] == "NOT_FOUND"
    assert "B-Secret" not in json.dumps(foreign)

    missing = json.loads(
        _mcp_call(client, token, "ask_contents", {"name": "B-Secret-App"})["result"]["content"][0]["text"]
    )
    assert missing["success"] is False
    assert "B-Secret-Doc" not in json.dumps(missing)


def test_mcp_tool_empty_package_returns_the_named_reason(client, db_session, make_org, login_as):
    org = make_org("contents-mcp-empty")
    user = _make_user(db_session, org, "contents-empty@example.com")
    empty = _el(db_session, org.id, "Release 9", "WorkPackage", "implementation_migration")
    token = _mint_oauth_token(client, db_session, org, user, login_as)
    body = json.loads(
        _mcp_call(client, token, "ask_contents", {"element_id": empty.id})["result"]["content"][0]["text"]
    )
    assert body["success"] is True
    assert body["data"]["contents"] == []
    assert body["data"]["reasons"] == ["no_contents_recorded"]

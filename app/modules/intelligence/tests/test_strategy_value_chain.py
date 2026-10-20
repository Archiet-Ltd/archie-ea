"""The strategy lens answers a stakeholder question with the chain
Stakeholder -> Value -> Capability -> Application Component, every hop a
recorded relationship that names where it came from and when."""

from __future__ import annotations

from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "oef" / "archiet-model-v1.oef.xml"
FILE_NAME = "archiet-model-v1.oef.xml"
STAKEHOLDER = "Scale-up CTO / VP Engineering"


def _el(db_session, org_id, name, type_):
    from app.models import ArchiMateElement

    row = ArchiMateElement(name=name, type=type_, layer="strategy", organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def _rel(db_session, org_id, source, target, type_, label="seed.oef.xml"):
    import datetime as dt

    from app.models import ArchiMateRelationship

    row = ArchiMateRelationship(
        source_id=source.id, target_id=target.id, type=type_, organization_id=org_id,
        source_kind="import", source_label=label, source_recorded_at=dt.datetime(2026, 10, 1, 9, 30),
    )
    db_session.add(row)
    db_session.flush()
    return row


def _chain(db_session, org_id, prefix=""):
    """CTO -> one Value -> two Capabilities -> three Applications."""
    cto = _el(db_session, org_id, f"{prefix}CTO", "Stakeholder")
    value = _el(db_session, org_id, f"{prefix}Value", "Value")
    cap1 = _el(db_session, org_id, f"{prefix}Cap1", "Capability")
    cap2 = _el(db_session, org_id, f"{prefix}Cap2", "Capability")
    apps = [_el(db_session, org_id, f"{prefix}App{i}", "ApplicationComponent") for i in (1, 2, 3)]
    _rel(db_session, org_id, cto, value, "Association")
    _rel(db_session, org_id, cap1, value, "Realization")
    _rel(db_session, org_id, cap2, value, "Association")  # a modelled realization, stored as association
    _rel(db_session, org_id, apps[0], cap1, "Realization")
    _rel(db_session, org_id, apps[1], cap1, "Realization")
    _rel(db_session, org_id, apps[2], cap2, "Realization")
    return cto


def _ask(app, org_id, element_id):
    from flask import g

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        g.current_org_id = org_id
        return IntelligenceQueryService.strategy_for_element(element_id)


def test_seeded_chain_is_returned_complete_in_order_and_cited(app, db_session, make_org):
    org = make_org("chain-seeded")
    cto = _chain(db_session, org.id)
    db_session.commit()

    result = _ask(app, org.id, cto.id)

    assert result["reasons"] == []
    rows = result["chain"]
    assert [(r["hop"], r["to"]["name"]) for r in rows] == [
        (1, "Value"), (2, "Cap1"), (3, "App1"), (3, "App2"), (2, "Cap2"), (3, "App3"),
    ]
    assert [r["to"]["type"] for r in rows] == [
        "Value", "Capability", "ApplicationComponent", "ApplicationComponent",
        "Capability", "ApplicationComponent",
    ]
    assert [r["from"]["name"] for r in rows] == ["CTO", "Value", "Cap1", "Cap1", "Value", "Cap2"]
    for row in rows:
        assert row["relationship_id"]
        assert row["source_name"] == "seed.oef.xml"
        assert row["source_timestamp"].startswith("2026-10-01T09:30")


def test_stakeholder_with_no_value_returns_the_reason_not_an_empty_list(app, db_session, make_org):
    org = make_org("chain-empty")
    cto = _el(db_session, org.id, "Lonely CTO", "Stakeholder")
    other = _el(db_session, org.id, "Some app", "ApplicationComponent")
    _rel(db_session, org.id, other, cto, "Serving")
    db_session.commit()

    result = _ask(app, org.id, cto.id)

    assert result["chain"] == []
    assert result["reasons"] == ["no_value_linked"]


def test_archiet_model_answers_for_the_scale_up_cto(app, db_session, make_org, tenant_ctx):
    from app.models import ArchiMateElement
    from app.services.archimate_import_service import ArchiMateImportService

    org = make_org("chain-archiet")
    with tenant_ctx(org.id):
        service = ArchiMateImportService()
        parsed = service.parse_oef_xml(FIXTURE.read_text(encoding="utf-8"))
        service.execute_import(parsed, strategy="skip_duplicates", source_ref="batch-a", source_label=FILE_NAME)
        cto = ArchiMateElement.query.filter_by(organization_id=org.id, name=STAKEHOLDER).one()
    db_session.commit()

    result = _ask(app, org.id, cto.id)

    assert result["reasons"] == []
    rows = result["chain"]
    names = {1: set(), 2: set(), 3: set()}
    for row in rows:
        names[row["hop"]].add(row["to"]["name"])
    assert "Know what breaks, who is called and what you pay for twice" in names[1]
    assert any(n.startswith("A trustworthy model of your enterprise") for n in names[1])
    assert {"External AI Assistant Access", "Natural-Language Question Answering",
            "Explanation and Provenance", "Impact and Dependency Analysis",
            "Audit and Assurance"} <= names[2]
    assert "Entelim MCP server" in names[3]
    assert any(n.startswith("Entelim Jira and GitHub connec") for n in names[3])
    assert all(r["source_name"] == FILE_NAME for r in rows)
    assert all(r["source_timestamp"] for r in rows)
    # The apps hang under the capability they realize.
    parent = {r["to"]["name"]: r["from"]["name"] for r in rows if r["hop"] == 3}
    assert parent["Entelim MCP server"] == "External AI Assistant Access"


def test_two_organisations_never_see_each_others_chain(app, db_session, make_org):
    org_a, org_b = make_org("chain-iso-a"), make_org("chain-iso-b")
    cto_a = _chain(db_session, org_a.id, "A-")
    cto_b = _chain(db_session, org_b.id, "B-")
    db_session.commit()

    a_names = {r["to"]["name"] for r in _ask(app, org_a.id, cto_a.id)["chain"]}
    b_names = {r["to"]["name"] for r in _ask(app, org_b.id, cto_b.id)["chain"]}
    assert a_names and b_names
    assert all(n.startswith("A-") for n in a_names)
    assert all(n.startswith("B-") for n in b_names)

    crossed = _ask(app, org_b.id, cto_a.id)
    assert crossed["chain"] == []
    assert crossed["reasons"] == ["element_not_found"]


def test_citations_name_where_each_relationship_came_from(app, db_session, make_org):
    from app.models import ArchiMateRelationship
    from app.modules.intelligence.services.answer_envelope import _citations

    org = make_org("chain-citations")
    a, b, c = (_el(db_session, org.id, n, "ApplicationComponent") for n in "abc")
    with_origin = _rel(db_session, org.id, a, b, "Serving", label="file.oef.xml")
    bare = ArchiMateRelationship(source_id=b.id, target_id=c.id, type="Serving", organization_id=org.id)
    db_session.add(bare)
    db_session.flush()
    db_session.commit()

    from flask import g

    with app.test_request_context("/"):
        g.current_org_id = org.id
        cited = _citations("impact", [
            {"relation": {"chain": [with_origin.id, bare.id], "derived_id": None}},
        ])
    by_id = {c["id"]: c for c in cited}
    assert by_id[with_origin.id]["source_name"] == "file.oef.xml"
    assert by_id[with_origin.id]["source_timestamp"].startswith("2026-10-01T09:30")
    assert by_id[bare.id]["source_name"] == "not recorded"


def test_mcp_ask_strategy_returns_the_cited_chain(client, db_session, make_org):
    from app.modules.mcp.tests.test_cited_answers import _payload, _token
    from app.modules.mcp.tests.test_scope_enforcement import _mcp_call
    from app.modules.mcp.tests.test_tools import _make_user

    org = make_org("chain-mcp")
    user = _make_user(db_session, org, "chain-mcp@example.com")
    cto = _chain(db_session, org.id)
    db_session.commit()

    status, body = _mcp_call(client, _token(user, org), "ask_strategy", {"element_id": cto.id})

    assert status == 200, body
    payload = _payload(body)
    data = payload.get("data", payload)
    assert [r["to"]["name"] for r in data["chain"]] == ["Value", "Cap1", "App1", "App2", "Cap2", "App3"]
    assert all(r["source_name"] == "seed.oef.xml" and r["source_timestamp"] for r in data["chain"])

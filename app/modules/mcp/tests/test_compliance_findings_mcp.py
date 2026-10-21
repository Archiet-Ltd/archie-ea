"""Compliance findings through the connector: the same ``answer`` tool, the compliance question
with no element named. Findings are for the frameworks the token's organisation has adopted and
the elements it owns; another organisation's token never sees them.
"""

from __future__ import annotations

import json

from app.modules.mcp.tests.test_scope_enforcement import _mcp_call, _mint_token_with_scope
from app.modules.mcp.tests.test_tools import _make_user


def _world(db_session, make_org):
    from app.models import ArchiMateElement
    from app.models.compliance_models import RegulatoryFramework
    from app.modules.compliance.services.applicability_service import ApplicabilityService
    from app.services.compliance.regulatory_framework_service import RegulatoryFrameworkService

    RegulatoryFrameworkService.seed_manufacturing_frameworks()
    framework = db_session.query(RegulatoryFramework).filter_by(code="NIST-CSF").one()
    org_a, org_b = make_org("mcp-find-a"), make_org("mcp-find-b")
    ApplicabilityService.adopt_framework(org_a.id, framework.id, None)
    ApplicabilityService.adopt_framework(org_b.id, framework.id, None)
    mine = ArchiMateElement(name="Alpha private host", type="Node", layer="technology", organization_id=org_a.id)
    theirs = ArchiMateElement(name="Beta private host", type="Node", layer="technology", organization_id=org_b.id)
    db_session.add_all([mine, theirs])
    db_session.commit()
    return org_a, org_b, mine, theirs


def test_the_connector_returns_findings_with_framework_control_element_and_citation(client, db_session, make_org):
    from flask import current_app

    org_a, _b, mine, _theirs = _world(db_session, make_org)
    user = _make_user(db_session, org_a, "mcp-find-a@example.com")
    token = _mint_token_with_scope(current_app, user, org_a, "mcp:read")

    status, body = _mcp_call(client, token, "answer", {"question": "compliance"})

    assert status == 200, body
    payload = json.loads(body["result"]["content"][0]["text"])
    assert payload["success"] is True, payload
    rows = payload["result"]["rows"]
    row = next(r for r in rows if r["finding"] == "element_without_control" and r["element_id"] == mine.id)
    assert row["framework_code"] == "NIST-CSF"
    assert any(r["finding"] == "control_without_element" and r["control_id"] for r in rows)
    citations = payload["result"]["citations"]
    assert {"type": "element", "id": mine.id} in citations
    assert any(c["type"] == "regulatory_framework" for c in citations)
    assert any(c["type"] == "compliance_control" for c in citations)


def test_the_connector_never_returns_another_organisations_elements(client, db_session, make_org):
    from flask import current_app

    org_a, _b, _mine, theirs = _world(db_session, make_org)
    user = _make_user(db_session, org_a, "mcp-find-iso@example.com")
    token = _mint_token_with_scope(current_app, user, org_a, "mcp:read")

    status, body = _mcp_call(client, token, "answer", {"question": "compliance", "framework_code": "NIST-CSF"})

    assert status == 200, body
    text = body["result"]["content"][0]["text"]
    assert "Beta private host" not in text
    payload = json.loads(text)
    assert theirs.id not in {r["element_id"] for r in payload["result"]["rows"] if r["element_id"]}


def test_an_organisation_that_has_adopted_nothing_gets_no_findings(client, db_session, make_org):
    from flask import current_app

    _a, _b, _mine, _theirs = _world(db_session, make_org)
    org_c = make_org("mcp-find-c")
    user = _make_user(db_session, org_c, "mcp-find-c@example.com")
    token = _mint_token_with_scope(current_app, user, org_c, "mcp:read")

    status, body = _mcp_call(client, token, "answer", {"question": "compliance"})

    assert status == 200, body
    payload = json.loads(body["result"]["content"][0]["text"])
    assert payload["success"] is True and payload["result"]["rows"] == []

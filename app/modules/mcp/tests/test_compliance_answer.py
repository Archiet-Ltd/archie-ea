"""The compliance answer through the connector: the same ``answer`` tool the assistant uses.

"Which controls protect this element, and which are unmet" reaches the connector with no
second tool: the catalogue entry for ``answer`` gains the connector surface and the
``compliance`` question. Another organisation's token reads an element of ours as absent.
"""

from __future__ import annotations

import json
import uuid

from app.modules.mcp.tests.test_scope_enforcement import _mcp_call, _mint_token_with_scope
from app.modules.mcp.tests.test_tools import _make_user, _mcp_tools_list


def _world(db_session, make_org):
    from app.models import ArchiMateElement
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework
    from app.models.regulatory_framework import FrameworkAdoption
    from app.modules.compliance.services.control_model_service import link_control_to_element

    org_a, org_b = make_org("mcp-comp-a"), make_org("mcp-comp-b")
    node = ArchiMateElement(name="Payments host", type="Node", layer="technology", organization_id=org_a.id)
    framework = RegulatoryFramework(code=f"FW{uuid.uuid4().hex[:8]}", name="Framework", category="security")
    db_session.add_all([node, framework])
    db_session.flush()
    control = ComplianceControl(framework_id=framework.id, control_code="PR.DS-01", title="Data-at-rest protected")
    db_session.add(control)
    db_session.flush()
    db_session.add(FrameworkAdoption(
        organization_id=org_a.id, scope="tenant", framework_id=framework.id, status="active"
    ))
    link_control_to_element(control.id, node.id, org_a.id)
    db_session.commit()
    return org_a, org_b, node, control


def test_answer_is_listed_on_the_connector_and_offers_the_compliance_question(client, db_session, make_org):
    from flask import current_app

    org_a, _b, _node, _control = _world(db_session, make_org)
    user = _make_user(db_session, org_a, "comp-list@example.com")
    token = _mint_token_with_scope(current_app, user, org_a, "mcp:read")

    tools = {t["name"]: t for t in _mcp_tools_list(client, token)}

    assert "answer" in tools
    assert "compliance" in tools["answer"]["inputSchema"]["properties"]["question"]["enum"]


def test_the_connector_answers_which_controls_protect_an_element_and_which_are_unmet(client, db_session, make_org):
    from flask import current_app

    org_a, _b, node, control = _world(db_session, make_org)
    user = _make_user(db_session, org_a, "comp-call@example.com")
    token = _mint_token_with_scope(current_app, user, org_a, "mcp:read")

    status, body = _mcp_call(client, token, "answer", {"question": "compliance", "element_id": node.id})

    assert status == 200, body
    payload = json.loads(body["result"]["content"][0]["text"])
    assert payload["success"] is True, payload
    [row] = payload["result"]["rows"]
    assert row["control_id"] == control.id and row["no_evidence"] is True
    assert {"type": "compliance_control", "id": control.id} in payload["result"]["citations"]
    assert payload["result"]["summary"]["unmet"] == 1


def test_another_organisations_token_reads_our_element_as_absent(client, db_session, make_org):
    from flask import current_app

    _a, org_b, node, _control = _world(db_session, make_org)
    user_b = _make_user(db_session, org_b, "comp-foreign@example.com")
    token = _mint_token_with_scope(current_app, user_b, org_b, "mcp:read")

    status, body = _mcp_call(client, token, "answer", {"question": "compliance", "element_id": node.id})

    assert status == 200, body
    payload = json.loads(body["result"]["content"][0]["text"])
    assert payload["success"] is False and payload["reason"] == "element_not_found"


def test_the_connector_shows_adopted_frameworks_by_default_and_all_on_request(client, db_session, make_org):
    from flask import current_app

    from app.models.compliance_models import ComplianceControl, RegulatoryFramework
    from app.modules.compliance.services.control_model_service import link_control_to_element

    org_a, _b, node, adopted_control = _world(db_session, make_org)
    other = RegulatoryFramework(code=f"FW{uuid.uuid4().hex[:8]}", name="Not adopted", category="security")
    db_session.add(other)
    db_session.flush()
    other_control = ComplianceControl(framework_id=other.id, control_code="X-1", title="Not adopted control")
    db_session.add(other_control)
    db_session.flush()
    link_control_to_element(other_control.id, node.id, org_a.id)
    db_session.commit()
    user = _make_user(db_session, org_a, "comp-adopt@example.com")
    token = _mint_token_with_scope(current_app, user, org_a, "mcp:read")

    _status, body = _mcp_call(client, token, "answer", {"question": "compliance", "element_id": node.id})
    default = json.loads(body["result"]["content"][0]["text"])
    _status, body = _mcp_call(
        client, token, "answer", {"question": "compliance", "element_id": node.id, "all_frameworks": True}
    )
    everything = json.loads(body["result"]["content"][0]["text"])

    assert [r["control_id"] for r in default["result"]["rows"]] == [adopted_control.id]
    assert sorted(r["control_id"] for r in everything["result"]["rows"]) == sorted([adopted_control.id, other_control.id])

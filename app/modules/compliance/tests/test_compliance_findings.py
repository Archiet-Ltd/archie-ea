"""Compliance findings from an organisation's own model (Job 5, launch acceptance item 13).

Two kinds, both computed by the one findings function in the regulatory framework service
and cited through the answer envelope's compliance kind:

* a control with no evidence: an adopted control linked to no element, or whose linked
  element has no evidence recorded;
* an element with no control: an element of a type the framework addresses that no adopted
  control of that framework covers.

Fixtures (app, db_session, make_org, client, login_as) come from app/modules/conftest.py.
"""

from __future__ import annotations

import pytest
from flask import g

from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor
from app.modules.intelligence.services import answer_envelope as envelope

FRAMEWORK = "NIST-CSF"
NO_ELEMENT = "control_without_element"
NO_EVIDENCE = "control_without_evidence"
NO_CONTROL = "element_without_control"


@pytest.fixture
def library(app, db_session):
    from app.services.compliance.regulatory_framework_service import RegulatoryFrameworkService

    RegulatoryFrameworkService.seed_manufacturing_frameworks()
    return RegulatoryFrameworkService


def _framework(db_session):
    from app.models.compliance_models import RegulatoryFramework

    return db_session.query(RegulatoryFramework).filter_by(code=FRAMEWORK).one()


def _controls(db_session, n=3):
    from app.models.compliance_models import ComplianceControl

    framework = _framework(db_session)
    return (
        db_session.query(ComplianceControl)
        .filter_by(framework_id=framework.id)
        .order_by(ComplianceControl.id)
        .limit(n)
        .all()
    )


def _adopt(db_session, org):
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    return ApplicabilityService.adopt_framework(org.id, _framework(db_session).id, None)


def _element(db_session, org, name, type_="Node", layer="technology"):
    from app.models import ArchiMateElement

    element = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org.id)
    db_session.add(element)
    db_session.flush()
    return element


def _link(db_session, control, element, org):
    from app.modules.compliance.services.control_model_service import link_control_to_element

    relationship = link_control_to_element(control.id, element.id, org.id)
    db_session.flush()
    return relationship


def _record_evidence(db_session, org, control, url="https://evidence.example/report"):
    from app.models.application_compliance import ApplicationComplianceControl

    row = (
        db_session.query(ApplicationComplianceControl)
        .filter_by(organization_id=org.id, control_id=control.id)
        .one()
    )
    row.evidence_url = url
    db_session.flush()


def _findings(app, org, **kwargs):
    from app.services.compliance.regulatory_framework_service import RegulatoryFrameworkService

    with app.test_request_context("/"):
        g.current_org_id = org.id
        return RegulatoryFrameworkService.compliance_findings(org.id, **kwargs)


def _of(result, kind):
    return [row for row in result["rows"] if row["finding"] == kind]


def test_an_adopted_control_linked_to_no_element_is_a_finding(app, db_session, make_org, library):
    org = make_org("fnd-a")
    c1, c2, c3 = _controls(db_session)
    _adopt(db_session, org)
    node = _element(db_session, org, "Core database host")
    _link(db_session, c1, node, org)
    db_session.commit()

    result = _findings(app, org, finding=NO_ELEMENT)

    ids = {row["control_id"] for row in _of(result, NO_ELEMENT)}
    assert c2.id in ids and c3.id in ids and c1.id not in ids
    row = next(r for r in result["rows"] if r["control_id"] == c2.id)
    assert row["framework_code"] == FRAMEWORK and row["code"] == c2.control_code
    assert row["element_id"] is None


def test_a_linked_element_with_no_evidence_is_a_finding_and_with_evidence_is_not(
    app, db_session, make_org, library
):
    org = make_org("fnd-b")
    c1, c2, _c3 = _controls(db_session)
    _adopt(db_session, org)
    host = _element(db_session, org, "Payments host")
    store = _element(db_session, org, "Archive store")
    relationship = _link(db_session, c1, host, org)
    _link(db_session, c2, store, org)
    _record_evidence(db_session, org, c2)
    db_session.commit()

    result = _findings(app, org, finding=NO_EVIDENCE)

    [row] = [r for r in _of(result, NO_EVIDENCE) if r["control_id"] == c1.id]
    assert row["element_id"] == host.id and row["element_name"] == "Payments host"
    assert row["relationship_id"] == relationship.id
    assert not [r for r in result["rows"] if r["control_id"] == c2.id], "a control with evidence is not a finding"


def test_a_waived_control_is_not_a_finding_for_missing_evidence(app, db_session, make_org, library):
    from app.models.application_compliance import ApplicationComplianceControl

    org = make_org("fnd-c")
    c1, *_ = _controls(db_session)
    _adopt(db_session, org)
    host = _element(db_session, org, "Payments host")
    _link(db_session, c1, host, org)
    row = db_session.query(ApplicationComplianceControl).filter_by(organization_id=org.id, control_id=c1.id).one()
    row.implementation_status = "waived"
    db_session.commit()

    result = _findings(app, org, finding=NO_EVIDENCE)

    assert not [r for r in result["rows"] if r["control_id"] == c1.id]


def test_an_element_of_an_addressed_type_that_no_control_covers_is_a_finding(
    app, db_session, make_org, library
):
    org = make_org("fnd-d")
    c1, *_ = _controls(db_session)
    _adopt(db_session, org)
    bare = _element(db_session, org, "Unprotected host")
    covered = _element(db_session, org, "Protected host")
    actor = _element(db_session, org, "Finance team", "BusinessActor", "business")
    _link(db_session, c1, covered, org)
    db_session.commit()

    result = _findings(app, org, finding=NO_CONTROL)

    ids = {row["element_id"] for row in _of(result, NO_CONTROL)}
    assert bare.id in ids
    assert covered.id not in ids, "an element a control covers is not a finding"
    assert actor.id not in ids, "a type the framework does not address is not a finding"
    row = next(r for r in result["rows"] if r["element_id"] == bare.id)
    assert row["framework_code"] == FRAMEWORK and row["control_id"] is None
    assert row["element_type"] == "Node"


def test_no_adopted_framework_means_no_findings(app, db_session, make_org, library):
    org = make_org("fnd-e")
    _element(db_session, org, "Some host")
    db_session.commit()

    result = _findings(app, org)

    assert result["rows"] == [] and result["total"] == 0 and result["frameworks"] == []


def test_a_finding_never_shows_another_organisations_elements(app, db_session, make_org, library):
    org_a, org_b = make_org("fnd-iso-a"), make_org("fnd-iso-b")
    c1, *_ = _controls(db_session)
    _adopt(db_session, org_a)
    _adopt(db_session, org_b)
    mine = _element(db_session, org_a, "Org A private host")
    theirs = _element(db_session, org_b, "Org B private host")
    _link(db_session, c1, mine, org_a)
    db_session.commit()

    a, b = _findings(app, org_a), _findings(app, org_b)

    a_elements = {r["element_id"] for r in a["rows"] if r["element_id"]}
    b_elements = {r["element_id"] for r in b["rows"] if r["element_id"]}
    assert theirs.id not in a_elements and mine.id in a_elements
    assert mine.id not in b_elements and theirs.id in b_elements
    assert not [r for r in b["rows"] if r["control_id"] == c1.id and r["element_id"] == mine.id]
    assert "Org A private host" not in str(b["rows"])


def test_the_envelope_carries_framework_control_element_and_citation(app, db_session, make_org, library):
    org = make_org("fnd-env")
    c1, *_ = _controls(db_session)
    _adopt(db_session, org)
    host = _element(db_session, org, "Payments host")
    bare = _element(db_session, org, "Unprotected host")
    relationship = _link(db_session, c1, host, org)
    db_session.commit()

    with app.test_request_context("/"):
        g.current_org_id = org.id
        record = envelope.ask_and_store("compliance", {})
        reopened = envelope.open_answer(record.address)

    assert record.question_kind == "compliance" and record.truth_class == "drawn"
    assert reopened.rows == record.rows
    row = next(r for r in record.rows if r["finding"] == NO_EVIDENCE and r["control_id"] == c1.id)
    assert row["framework_code"] == FRAMEWORK and row["element_id"] == host.id
    assert {"type": "compliance_control", "id": c1.id} in record.citations
    assert {"type": "relationship", "id": relationship.id} in record.citations
    assert {"type": "element", "id": host.id} in record.citations
    assert {"type": "regulatory_framework", "id": _framework(db_session).id} in record.citations
    assert any(r["finding"] == NO_CONTROL and r["element_id"] == bare.id for r in record.rows)
    assert record.summary["controls_without_evidence"] >= 1
    assert record.summary["elements_without_control"] >= 1
    assert FRAMEWORK in record.summary["frameworks"]
    assert str(host.id) in record.elements


def test_the_envelope_can_be_narrowed_to_one_framework_and_one_kind(app, db_session, make_org, library):
    org = make_org("fnd-narrow")
    _adopt(db_session, org)
    _element(db_session, org, "Unprotected host")
    db_session.commit()

    with app.test_request_context("/"):
        g.current_org_id = org.id
        record = envelope.ask_and_store("compliance", {"framework_code": FRAMEWORK, "finding": NO_CONTROL})
        other = envelope.ask_and_store("compliance", {"framework_code": "GDPR"})

    assert record.rows and {r["finding"] for r in record.rows} == {NO_CONTROL}
    assert record.params == {"framework_code": FRAMEWORK, "finding": NO_CONTROL}
    assert other.rows == [], "a framework the organisation has not adopted yields nothing"


def test_a_bad_finding_kind_is_refused(app, db_session, make_org):
    with pytest.raises(envelope.AnswerParamError, match="finding must be"):
        envelope.normalise_params("compliance", {"finding": "everything"})


def test_the_envelope_never_returns_another_organisations_elements(app, db_session, make_org, library):
    org_a, org_b = make_org("fnd-env-a"), make_org("fnd-env-b")
    _adopt(db_session, org_a)
    _adopt(db_session, org_b)
    theirs = _element(db_session, org_b, "Org B private host")
    db_session.commit()

    with app.test_request_context("/"):
        g.current_org_id = org_a.id
        record = envelope.ask_and_store("compliance", {})

    assert theirs.id not in {r["element_id"] for r in record.rows if r["element_id"]}
    assert "Org B private host" not in str(record.elements)
    assert {"type": "element", "id": theirs.id} not in record.citations


def _user(db_session, org, email):
    from app.models.user import User

    user = User(
        email=email, first_name="F", last_name="T", organization_id=org.id,
        confirmed=True, enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_ask_returns_the_findings_through_the_assistant_tool(app, db_session, make_org, library):
    org = make_org("fnd-ask")
    _adopt(db_session, org)
    bare = _element(db_session, org, "Unprotected host")
    user = _user(db_session, org, "fnd-ask@example.com")
    db_session.commit()

    with app.test_request_context("/"):
        g.current_org_id = org.id
        result = ToolExecutor(user.id).execute(
            ToolCall(id="c1", name="answer", arguments={"question": "compliance"})
        )

    assert result.get("success") is True, result
    rows = (result.get("result") or result)["rows"]
    assert any(r["finding"] == NO_CONTROL and r["element_id"] == bare.id for r in rows)


def test_the_compliance_page_shows_the_findings_and_only_this_organisations(
    app, db_session, make_org, library, client, login_as
):
    org_a, org_b = make_org("fnd-page-a"), make_org("fnd-page-b")
    _adopt(db_session, org_a)
    _element(db_session, org_a, "Alpha unprotected host")
    _element(db_session, org_b, "Beta unprotected host")
    user = _user(db_session, org_a, "fnd-page@example.com")
    db_session.commit()
    login_as(client, user)

    page = client.get("/dashboard/compliance")
    html = page.get_data(as_text=True)

    assert page.status_code == 200, html[:400]
    assert "data-compliance-findings" in html
    assert "Alpha unprotected host" in html
    assert "Beta unprotected host" not in html
    assert "unmet" not in html.lower()

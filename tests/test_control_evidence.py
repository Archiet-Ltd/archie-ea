"""Control evidence with a validity period, control tests and the ranked evidence-gap list.

Two real organisations in every isolation test, no mocked tenant context.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.modules.compliance.services import control_evidence as rule
from app.modules.compliance.services.control_assurance_service import (
    ControlNotFound,
    list_control_tests,
    record_control_test,
    update_control_evidence,
)
from app.modules.compliance.services.evidence_gap_service import (
    accept_control_evidence,
    evidence_gaps,
    propose_control_evidence,
)

NOW = datetime(2026, 10, 16, 12, 0, 0)
FIXTURE = Path(__file__).parent / "fixtures" / "control_evidence" / "connector_sync.json"


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _element(db_session, org_id, name):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _link(db_session, org_id, source, target):
    from app.models import ArchiMateRelationship

    db_session.add(
        ArchiMateRelationship(source_id=source.id, target_id=target.id, type="Serving", organization_id=org_id)
    )
    db_session.flush()


def _application(db_session, org_id, name):
    from app.models.application_portfolio import ApplicationComponent

    element = _element(db_session, org_id, name)
    component = ApplicationComponent(name=name, organization_id=org_id, archimate_element_id=element.id)
    db_session.add(component)
    db_session.flush()
    return component, element


def _framework(db_session):
    from app.models.compliance_models import RegulatoryFramework

    framework = RegulatoryFramework(
        code=f"FW{uuid.uuid4().hex[:6]}", name="Test Framework", category="security"
    )
    db_session.add(framework)
    db_session.flush()
    return framework


def _control(db_session, framework, code, priority="medium"):
    from app.models.compliance_models import ComplianceControl

    control = ComplianceControl(
        framework_id=framework.id, control_code=code, title=f"Control {code}", priority=priority
    )
    db_session.add(control)
    db_session.flush()
    return control


def _adopt(db_session, org_id, control, component=None, **fields):
    from app.models.application_compliance import ApplicationComplianceControl

    row = ApplicationComplianceControl(
        organization_id=org_id,
        application_id=component.id if component is not None else None,
        control_id=control.id,
        **fields,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _user(db_session, org_id, label):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Architect").first()
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.test",
        first_name=label,
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        role=role,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _in_org(app, org_id, call):
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_id
        return call()


# --------------------------------------------------------------------------- #
# The rule
# --------------------------------------------------------------------------- #


class _Row:
    def __init__(self, **fields):
        self.implementation_status = fields.get("implementation_status", "planned")
        self.evidence_url = fields.get("evidence_url")
        self.verified_date = fields.get("verified_date")
        self.evidence_valid_until = fields.get("evidence_valid_until")


@pytest.mark.parametrize(
    "fields, expected",
    [
        ({"implementation_status": "not_applicable"}, "not_required"),
        ({"implementation_status": "waived", "evidence_url": "https://e.test"}, "not_required"),
        ({}, "missing"),
        ({"evidence_url": "   "}, "missing"),
        ({"evidence_valid_until": NOW + timedelta(days=30)}, "missing"),
        ({"evidence_url": "https://e.test"}, "undated"),
        ({"verified_date": NOW - timedelta(days=400)}, "undated"),
        ({"evidence_url": "https://e.test", "evidence_valid_until": NOW - timedelta(days=1)}, "expired"),
        ({"evidence_url": "https://e.test", "evidence_valid_until": NOW + timedelta(days=30)}, "current"),
    ],
)
def test_evidence_state_gives_exactly_one_answer(fields, expected):
    assert rule.evidence_state(_Row(**fields), NOW) == expected


def test_effectiveness_needs_the_latest_design_and_operating_test_to_agree():
    class T:
        def __init__(self, kind, result, days, id_):
            self.test_kind, self.result, self.id = kind, result, id_
            self.tested_at = NOW + timedelta(days=days)

    assert rule.effectiveness([]) == "not_tested"
    assert rule.effectiveness([T("design", "effective", 0, 1)]) == "not_tested"
    both = [T("design", "effective", 0, 1), T("operating", "effective", 0, 2)]
    assert rule.effectiveness(both) == "effective"
    assert rule.effectiveness(both + [T("operating", "ineffective", 1, 3)]) == "ineffective"
    # a later passing test supersedes the failure
    assert rule.effectiveness(both + [T("operating", "ineffective", 1, 3), T("operating", "effective", 2, 4)]) == "effective"
    assert rule.effectiveness(both + [T("design", "exception", 1, 5)]) == "exception"
    # a failed test shows even when the other kind was never run
    assert rule.effectiveness([T("design", "ineffective", 0, 1)]) == "ineffective"


def test_the_three_readers_agree_on_one_expired_control(app, db_session, make_org):
    """The compliance lens, the risk-propagation answer and the framework control list all
    report the same state for the same adopted row; expired evidence is not present."""
    from app.modules.compliance.services.applicability_service import ApplicabilityService
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("agree")
    component, element = _application(db_session, org.id, "Payments")
    framework = _framework(db_session)
    control = _control(db_session, framework, "AC-1")
    row = _adopt(
        db_session, org.id, control, component,
        implementation_status="implemented",
        evidence_url="https://e.test/old",
        evidence_valid_until=datetime.utcnow() - timedelta(days=1),
    )
    db_session.commit()

    lens = _in_org(app, org.id, lambda: IntelligenceQueryService.compliance_for_element(element.id))
    [lens_row] = lens["controls"]
    assert lens_row["evidence_state"] == "expired" and lens_row["no_evidence"] is True
    assert "control_evidence_expired" in lens["reasons"]
    assert "no_control_evidence" not in lens["reasons"]

    assert rule.evidence_state(row) == "expired"
    # Missing and expired are different reasons, both raised when both exist.
    other = _control(db_session, framework, "AC-2")
    _adopt(db_session, org.id, other, component, implementation_status="planned")
    db_session.commit()
    lens = _in_org(app, org.id, lambda: IntelligenceQueryService.compliance_for_element(element.id))
    assert {"no_control_evidence", "control_evidence_expired"} <= set(lens["reasons"])

    # risk_propagation is checked in its own test below.
    from app.models.regulatory_framework import FrameworkAdoption

    adoption = FrameworkAdoption(
        organization_id=org.id, scope="tenant", framework_id=framework.id, status="active"
    )
    db_session.add(adoption)
    db_session.flush()
    row.adoption_id = adoption.id
    db_session.commit()
    listed = ApplicabilityService.evidence_status(org.id, adoption.id)
    assert [e["evidence_state"] for e in listed] == ["expired"]


def test_risk_propagation_control_entry_carries_the_evidence_state(app, db_session, make_org):
    from app.models import ArchiMateRelationship
    from app.models.risk import Risk, RiskStatus
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("riskprop")
    component, element = _application(db_session, org.id, "Billing")
    supplier, supplier_element = _application(db_session, org.id, "Supplier")
    db_session.add(
        ArchiMateRelationship(
            source_id=supplier_element.id, target_id=element.id, type="Serving", organization_id=org.id
        )
    )
    framework = _framework(db_session)
    control = _control(db_session, framework, "RP-1")
    _adopt(
        db_session, org.id, control, supplier, implementation_status="implemented",
        evidence_url="https://e.test/x", evidence_valid_until=datetime.utcnow() - timedelta(days=2),
    )
    db_session.add(
        Risk(
            title="Supplier outage", organization_id=org.id, archimate_element_id=supplier_element.id,
            likelihood=4, impact=5, status=RiskStatus.OPEN,
        )
    )
    db_session.commit()

    answer = _in_org(app, org.id, lambda: IntelligenceQueryService.risk_propagation(element.id))
    controls = [c for r in answer["rows"] for c in r["controls"]]
    assert controls and controls[0]["evidence_state"] == "expired"
    assert controls[0]["evidence_url_recorded"] is True


# --------------------------------------------------------------------------- #
# The gap list
# --------------------------------------------------------------------------- #


def test_the_gap_list_shows_missing_and_expired_and_not_current_evidence(app, db_session, make_org):
    org_a, org_b = make_org("gaps-a"), make_org("gaps-b")
    component, _el = _application(db_session, org_a.id, "Ledger")
    framework = _framework(db_session)
    missing = _adopt(db_session, org_a.id, _control(db_session, framework, "G-1"), component)
    expired = _adopt(
        db_session, org_a.id, _control(db_session, framework, "G-2"), component,
        evidence_url="https://e.test/2", evidence_valid_until=NOW - timedelta(days=1),
    )
    _adopt(
        db_session, org_a.id, _control(db_session, framework, "G-3"), component,
        evidence_url="https://e.test/3", evidence_valid_until=NOW + timedelta(days=30),
    )
    # Organisation B has its own gap, which A must never see.
    comp_b, _ = _application(db_session, org_b.id, "B ledger")
    foreign = _adopt(db_session, org_b.id, _control(db_session, framework, "G-4"), comp_b)
    db_session.commit()

    result = _in_org(app, org_a.id, lambda: evidence_gaps(org_a.id, NOW))
    states = {r["id"]: r["state"] for r in result["rows"]}
    assert states == {missing.id: "missing", expired.id: "expired"}
    assert foreign.id not in states

    other = _in_org(app, org_b.id, lambda: evidence_gaps(org_b.id, NOW))
    assert [r["id"] for r in other["rows"]] == [foreign.id]


def test_the_gap_list_is_empty_with_no_organisation(app, db_session, make_org):
    org = make_org("gaps-none")
    _adopt(db_session, org.id, _control(db_session, _framework(db_session), "N-1"))
    db_session.commit()
    assert evidence_gaps(None)["rows"] == []
    assert _in_org(app, None, lambda: evidence_gaps(None))["rows"] == []


def test_each_row_carries_evidence_provenance_confidence_and_trace(app, db_session, make_org):
    org = make_org("props")
    component, element = _application(db_session, org.id, "Props app")
    owner = _user(db_session, org.id, "Owner")
    framework = _framework(db_session)
    control = _control(db_session, framework, "P-1")
    row = _adopt(
        db_session, org.id, control, component, evidence_url="https://e.test/p",
        evidence_valid_until=NOW - timedelta(days=3), evidence_source="person",
        evidence_collected_at=NOW - timedelta(days=100), evidence_owner_id=owner.id,
    )
    db_session.commit()
    [gap] = _in_org(app, org.id, lambda: evidence_gaps(org.id, NOW))["rows"]
    assert gap["evidence"] == "https://e.test/p"
    assert gap["provenance"] == {"source": "person", "who": "Owner Tester", "collected_at": "2026-07-08"}
    assert gap["confidence"] is None and gap["confidence_basis"] == "drawn_facts_only"
    assert gap["trace"] == {
        "control_id": control.id, "framework_id": framework.id,
        "adopted_row_id": row.id, "application_id": component.id, "element_id": element.id,
    }
    assert gap["evidence_owner"] == "Owner Tester"


def test_a_missing_value_is_a_dash_and_never_an_email(app, db_session, make_org):
    org = make_org("dash")
    nameless = _user(db_session, org.id, "x")
    nameless.first_name = None
    nameless.last_name = None
    component, _ = _application(db_session, org.id, "Dash app")
    row = _adopt(db_session, org.id, _control(db_session, _framework(db_session), "D-1"), component,
                 evidence_owner_id=nameless.id)
    db_session.commit()
    [gap] = _in_org(app, org.id, lambda: evidence_gaps(org.id, NOW))["rows"]
    assert gap["id"] == row.id
    assert gap["evidence"] == "—" and gap["evidence_owner"] == "—"
    assert gap["valid_until"] is None and gap["latest_test_result"] is None
    assert "@" not in json.dumps(gap)


def test_ranking_is_priority_then_exposure_then_valid_until(app, db_session, make_org):
    org = make_org("rank")
    framework = _framework(db_session)
    wide, wide_el = _application(db_session, org.id, "Wide")
    narrow, narrow_el = _application(db_session, org.id, "Narrow")
    for i in range(3):
        _link(db_session, org.id, wide_el, _element(db_session, org.id, f"w{i}"))
    _link(db_session, org.id, narrow_el, _element(db_session, org.id, "n0"))
    critical = _adopt(db_session, org.id, _control(db_session, framework, "R-0", "critical"), narrow)
    equal_narrow = _adopt(db_session, org.id, _control(db_session, framework, "R-1", "high"), narrow)
    equal_wide = _adopt(db_session, org.id, _control(db_session, framework, "R-2", "high"), wide)
    low = _adopt(db_session, org.id, _control(db_session, framework, "R-3", "low"), wide)
    db_session.commit()

    rows = _in_org(app, org.id, lambda: evidence_gaps(org.id, NOW))["rows"]
    assert [r["id"] for r in rows] == [critical.id, equal_wide.id, equal_narrow.id, low.id]
    by_id = {r["id"]: r for r in rows}
    assert by_id[equal_wide.id]["exposure"] == 3 and by_id[equal_narrow.id]["exposure"] == 1


def test_exposure_is_one_set_impact_call_and_covers_25_applications(app, db_session, make_org, monkeypatch):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("cap")
    framework = _framework(db_session)
    for i in range(30):
        component, _ = _application(db_session, org.id, f"App {i:02d}")
        _adopt(db_session, org.id, _control(db_session, framework, f"C-{i:02d}"), component)
    db_session.commit()

    calls = []
    real = IntelligenceQueryService.set_impact

    def counted(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(IntelligenceQueryService, "set_impact", staticmethod(counted))
    rows = _in_org(app, org.id, lambda: evidence_gaps(org.id, NOW))["rows"]
    assert len(calls) == 1 and len(calls[0][0]) == 25
    with_exposure = [r for r in rows if r["exposure"] is not None]
    without = [r for r in rows if r["exposure"] is None]
    assert len(with_exposure) == 25 and len(without) == 5
    assert {r["exposure_reason"] for r in without} == {"not computed for this many applications"}


def test_the_gap_list_runs_a_bounded_number_of_statements(app, db_session, make_org):
    """The list does not issue a query per row: statements grow with the exposure walks only."""
    from sqlalchemy import event

    org = make_org("nplus1")
    framework = _framework(db_session)
    component, _ = _application(db_session, org.id, "Only")
    for i in range(40):
        _adopt(db_session, org.id, _control(db_session, framework, f"Q-{i:02d}"), component)
    db_session.commit()

    statements = []

    def count(conn, cursor, statement, *a):
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", count)
    try:
        _in_org(app, org.id, lambda: evidence_gaps(org.id, NOW))
    finally:
        event.remove(engine, "before_cursor_execute", count)
    # 40 gap rows over one application: far fewer statements than rows.
    assert len(statements) < 40


# --------------------------------------------------------------------------- #
# Control tests
# --------------------------------------------------------------------------- #


def test_recording_both_tests_makes_a_control_effective_and_a_later_failure_lists_it(app, db_session, make_org):
    org = make_org("tests")
    tester = _user(db_session, org.id, "Tester")
    component, _ = _application(db_session, org.id, "Tested app")
    row = _adopt(
        db_session, org.id, _control(db_session, _framework(db_session), "T-1"), component,
        evidence_url="https://e.test/t", evidence_valid_until=NOW + timedelta(days=60),
    )
    db_session.commit()

    def run():
        record_control_test(org.id, row.id, test_kind="design", result="effective", tested_by_id=tester.id)
        record_control_test(org.id, row.id, test_kind="operating", result="effective", tested_by_id=tester.id)
        db_session.commit()
        first = (list_control_tests(org.id, row.id)["effectiveness"], evidence_gaps(org.id, NOW)["rows"])
        record_control_test(
            org.id, row.id, test_kind="operating", result="ineffective", tested_by_id=tester.id,
            notes="Backups were not restorable",
            tested_at=datetime.utcnow() + timedelta(seconds=5),
        )
        db_session.commit()
        db_session.expire_all()
        return first, list_control_tests(org.id, row.id), evidence_gaps(org.id, NOW)["rows"]

    first, listing, rows = _in_org(app, org.id, run)
    assert first == ("effective", [])
    assert listing["effectiveness"] == "ineffective"
    [gap] = rows
    assert gap["latest_test_result"] == "ineffective" and gap["state"] == "current"


def test_a_test_or_evidence_write_on_another_organisations_control_is_refused(app, db_session, make_org):
    org_a, org_b = make_org("w-a"), make_org("w-b")
    comp_b, _ = _application(db_session, org_b.id, "B app")
    row_b = _adopt(db_session, org_b.id, _control(db_session, _framework(db_session), "W-1"), comp_b)
    # A decoy: A has an application whose id equals B's control row id.
    db_session.commit()
    before = {"tests": _tests_count(db_session), "url": row_b.evidence_url}

    with pytest.raises(ControlNotFound):
        record_control_test(org_a.id, row_b.id, test_kind="design", result="effective", tested_by_id=None)
    with pytest.raises(ControlNotFound):
        update_control_evidence(org_a.id, row_b.id, evidence_url="https://attacker.example/x")
    with pytest.raises(ControlNotFound):
        list_control_tests(org_a.id, row_b.id)
    with pytest.raises(ControlNotFound):
        record_control_test(None, row_b.id, test_kind="design", result="effective", tested_by_id=None)
    with pytest.raises(ControlNotFound):
        update_control_evidence(None, row_b.id, evidence_url="https://attacker.example/x")

    db_session.expire_all()
    assert _tests_count(db_session) == before["tests"]
    assert row_b.evidence_url == before["url"]


def _tests_count(db_session):
    from app.models.control_test import ControlTest

    return db_session.execute(
        __import__("sqlalchemy").select(__import__("sqlalchemy").func.count()).select_from(ControlTest)
    ).scalar_one()


def test_the_evidence_owner_must_belong_to_the_same_organisation(app, db_session, make_org):
    org_a, org_b = make_org("o-a"), make_org("o-b")
    outsider = _user(db_session, org_b.id, "Outsider")
    comp, _ = _application(db_session, org_a.id, "Owned app")
    row = _adopt(db_session, org_a.id, _control(db_session, _framework(db_session), "O-1"), comp)
    db_session.commit()
    with pytest.raises(ValueError):
        update_control_evidence(org_a.id, row.id, evidence_owner_id=outsider.id)
    insider = _user(db_session, org_a.id, "Insider")
    update_control_evidence(org_a.id, row.id, evidence_owner_id=insider.id)
    assert row.evidence_owner_id == insider.id


def test_a_person_recording_evidence_is_the_source(app, db_session, make_org):
    org = make_org("person")
    comp, _ = _application(db_session, org.id, "P app")
    row = _adopt(db_session, org.id, _control(db_session, _framework(db_session), "PS-1"), comp)
    db_session.commit()
    update_control_evidence(
        org.id, row.id, evidence_url="https://e.test/p", evidence_valid_until=NOW + timedelta(days=10)
    )
    assert row.evidence_source == "person" and row.evidence_collected_at is not None


# --------------------------------------------------------------------------- #
# Connector-sourced evidence through the approval queue
# --------------------------------------------------------------------------- #


def _fixture_items():
    data = json.loads(FIXTURE.read_text())
    return data["source_system"], data["items"]


def _stage_three(db_session, org):
    framework = _framework(db_session)
    rows = {}
    for ref in ("access-reviews", "mfa-enforced", "offboarding"):
        comp, _ = _application(db_session, org.id, f"App {ref}")
        rows[ref] = _adopt(db_session, org.id, _control(db_session, framework, ref), comp)
    db_session.commit()
    return rows


def _propose_all(org, rows, proposer_id):
    source, items = _fixture_items()
    approvals = []
    for item in items:
        approvals.append(
            propose_control_evidence(
                org.id, rows[item["control_ref"]].id,
                evidence_url=item["evidence_url"],
                collected_at=datetime.fromisoformat(item["collected_at"]),
                valid_until=datetime.fromisoformat(item["valid_until"]) if item["valid_until"] else None,
                source_system=source, summary=item["summary"], proposed_by_user_id=proposer_id,
            )
        )
    return approvals


def test_connector_evidence_becomes_pending_proposals_and_writes_nothing(app, db_session, make_org):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    org = make_org("conn")
    rows = _stage_three(db_session, org)
    approvals = _propose_all(org, rows, None)
    db_session.commit()
    assert len(approvals) == 3
    stored = AIChatCRUDApproval.query.filter_by(organization_id=org.id, entity_type="control_evidence").all()
    assert len(stored) == 3 and {a.status for a in stored} == {ApprovalStatus.PENDING}
    db_session.expire_all()
    assert all(r.evidence_url is None and r.evidence_source is None for r in rows.values())


def test_approving_a_proposal_writes_the_evidence_with_source_connector(app, db_session, make_org):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org = make_org("conn-ok")
    proposer, reviewer = _user(db_session, org.id, "Proposer"), _user(db_session, org.id, "Reviewer")
    rows = _stage_three(db_session, org)
    approvals = _propose_all(org, rows, proposer.id)
    db_session.commit()
    first = approvals[0]

    result = _in_org(app, org.id, lambda: AIChatApprovalService(reviewer.id).approve_and_execute(first.id, reviewer.id))
    assert result["success"] is True
    db_session.expire_all()
    written = rows["access-reviews"]
    assert written.evidence_url == "https://evidence.example.test/okta/access-review-q3"
    assert written.evidence_source == "connector"
    assert written.evidence_valid_until == datetime(2027, 1, 1)
    # The other two proposals are untouched.
    assert rows["mfa-enforced"].evidence_url is None and rows["offboarding"].evidence_url is None


def test_a_pending_proposal_cannot_be_accepted_around_the_queue(app, db_session, make_org):
    org = make_org("conn-direct")
    rows = _stage_three(db_session, org)
    [first, *_] = _propose_all(org, rows, None)
    db_session.commit()
    assert accept_control_evidence(first.id, org.id, None) is False
    db_session.expire_all()
    assert rows["access-reviews"].evidence_url is None


def test_self_approval_is_refused_and_writes_nothing(app, db_session, make_org):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService, SelfApprovalError

    org = make_org("conn-self")
    proposer = _user(db_session, org.id, "Solo")
    rows = _stage_three(db_session, org)
    [first, *_] = _propose_all(org, rows, proposer.id)
    db_session.commit()
    result = _in_org(app, org.id, lambda: AIChatApprovalService(proposer.id).approve_and_execute(first.id, proposer.id))
    assert result["success"] is False
    db_session.expire_all()
    assert rows["access-reviews"].evidence_url is None


def test_a_refused_proposal_can_never_be_accepted_afterwards(app, db_session, make_org):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org = make_org("conn-refuse")
    proposer, reviewer = _user(db_session, org.id, "P"), _user(db_session, org.id, "R")
    rows = _stage_three(db_session, org)
    [first, *_] = _propose_all(org, rows, proposer.id)
    db_session.commit()

    def run():
        refusal = AIChatApprovalService(reviewer.id).reject_approval(first.id, "not the right document")
        late = AIChatApprovalService(reviewer.id).approve_and_execute(first.id, reviewer.id)
        return refusal, late, accept_control_evidence(first.id, org.id, reviewer.id)

    refusal, late, direct = _in_org(app, org.id, run)
    assert refusal["success"] is True
    assert late["success"] is False and direct is False
    db_session.expire_all()
    assert rows["access-reviews"].evidence_url is None


def test_a_proposal_naming_another_organisations_control_is_refused(app, db_session, make_org):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService, create_approval_record

    org_a, org_b = make_org("x-a"), make_org("x-b")
    reviewer = _user(db_session, org_a.id, "Reviewer")
    comp_b, _ = _application(db_session, org_b.id, "B app")
    row_b = _adopt(db_session, org_b.id, _control(db_session, _framework(db_session), "X-1"), comp_b)
    db_session.commit()

    # The proposal function refuses to name a row of another organisation.
    with pytest.raises(ValueError):
        propose_control_evidence(
            org_a.id, row_b.id, evidence_url="https://e.test/x", collected_at=NOW, valid_until=None,
            source_system="Okta", summary="x",
        )
    # A forged proposal in A's queue naming B's row is refused at execution; B's row is untouched.
    forged = create_approval_record(
        organization_id=org_a.id, operation_type="update", entity_type="control_evidence",
        entity_id=row_b.id, summary="forged",
        operation_payload={"application_control_id": row_b.id, "evidence_url": "https://attacker.example/x",
                           "collected_at": NOW.isoformat(), "valid_until": None, "source_system": "Okta"},
        user_id=None,
    )
    db_session.commit()
    result = _in_org(app, org_a.id, lambda: AIChatApprovalService(reviewer.id).approve_and_execute(forged.id, reviewer.id))
    assert result["success"] is False
    db_session.expire_all()
    assert row_b.evidence_url is None and row_b.evidence_source is None
    assert AIChatCRUDApproval.query.filter_by(id=forged.id).first() is not None

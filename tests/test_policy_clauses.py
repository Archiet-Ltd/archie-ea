"""Versioned policies with clauses bound to controls, violation detection from clauses,
privacy-assessment flags and the one compliance findings list.

Two real organisations in every isolation test, no mocked tenant context.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.modules.solutions_strategic.v2.services.policy_monitoring_service import (
    PolicyMonitoringService as Service,
    PolicyNotFound,
    PolicyRefused,
)
from tests.test_control_evidence import (
    _adopt,
    _application,
    _control,
    _framework,
    _in_org,
    _user,
)


def _adopted_framework(db_session, org_id):
    from app.models.regulatory_framework import FrameworkAdoption

    framework = _framework(db_session)
    db_session.add(
        FrameworkAdoption(organization_id=org_id, scope="tenant", framework_id=framework.id, status="active")
    )
    db_session.flush()
    return framework


def _draft(db_session, org, name="Access policy", user=None):
    result = Service.create_policy(
        {"name": f"{name} {uuid.uuid4().hex[:5]}", "severity": "high", "is_active": False},
        organization_id=org.id,
    )
    assert result["success"], result
    return result["policy"]["id"]


def _owner(db_session, org, application, user):
    from app.models.application_owner import ApplicationOwner

    db_session.add(
        ApplicationOwner(application_id=application.id, user_id=user.id, ownership_type="primary", organization_id=org.id)
    )
    db_session.flush()


def _world(db_session, make_org):
    """One organisation with an adopted framework, a control, two applications mapped to it
    (one with expired evidence, one with current evidence) and a draft policy."""
    org = make_org("clauses")
    framework = _adopted_framework(db_session, org.id)
    control = _control(db_session, framework, "AC-9", "high")
    stale_app, _ = _application(db_session, org.id, "Stale evidence app")
    fresh_app, _ = _application(db_session, org.id, "Fresh evidence app")
    actor = _user(db_session, org.id, "Compliance")
    stale = _adopt(
        db_session, org.id, control, stale_app, implementation_status="implemented",
        evidence_url="https://e.test/old", evidence_valid_until=datetime.utcnow() - timedelta(days=2),
    )
    _adopt(
        db_session, org.id, control, fresh_app, implementation_status="implemented",
        evidence_url="https://e.test/new", evidence_valid_until=datetime.utcnow() + timedelta(days=60),
    )
    policy_id = _draft(db_session, org)
    db_session.commit()
    return org, framework, control, stale_app, fresh_app, stale, actor, policy_id


# --------------------------------------------------------------------------- #
# Clauses, publishing and governed applications
# --------------------------------------------------------------------------- #


def test_publishing_lists_the_applications_a_clause_governs_with_their_owners(app, db_session, make_org):
    org, _fw, control, stale_app, fresh_app, _stale, actor, policy_id = _world(db_session, make_org)
    _owner(db_session, org, stale_app, actor)
    Service.bind_clause(org.id, policy_id, "4.2", "Access is reviewed", control.id, actor.id)
    db_session.commit()

    published = Service.publish_policy(org.id, policy_id, actor.id)
    db_session.commit()

    assert published["policy"]["version_number"] == 1 and published["policy"]["is_active"] is True
    [clause] = Service.governed_applications(org.id, policy_id)
    assert clause["clause_ref"] == "4.2" and clause["control_code"] == "AC-9"
    names = {a["application_name"]: a for a in clause["applications"]}
    assert set(names) == {stale_app.name, fresh_app.name}
    assert names[stale_app.name]["owners"] == ["Compliance Tester"]
    assert names[stale_app.name]["met"] is False and names[fresh_app.name]["met"] is True


def test_a_policy_with_no_clause_cannot_be_published(app, db_session, make_org):
    org, *_rest, actor, policy_id = _world(db_session, make_org)
    with pytest.raises(PolicyRefused):
        Service.publish_policy(org.id, policy_id, actor.id)


def test_a_published_policy_cannot_be_changed_or_published_again(app, db_session, make_org):
    org, _fw, control, *_r, actor, policy_id = _world(db_session, make_org)
    binding = Service.bind_clause(org.id, policy_id, "1", None, control.id, actor.id)
    Service.publish_policy(org.id, policy_id, actor.id)
    with pytest.raises(PolicyRefused):
        Service.bind_clause(org.id, policy_id, "2", None, control.id, actor.id)
    with pytest.raises(PolicyRefused):
        Service.unbind_clause(org.id, policy_id, binding.id)
    with pytest.raises(PolicyRefused):
        Service.publish_policy(org.id, policy_id, actor.id)


# --------------------------------------------------------------------------- #
# Violations
# --------------------------------------------------------------------------- #


def _open_clause_violations(org):
    from app.models.policy_monitoring import PolicyViolation

    return PolicyViolation.query.filter(
        PolicyViolation.organization_id == org.id,
        PolicyViolation.violation_details.like("Clause %"),
        PolicyViolation.status.in_(["open", "acknowledged"]),
    ).all()


def test_publishing_raises_one_violation_and_renewing_evidence_then_checking_closes_it(app, db_session, make_org):
    from app.models.policy_monitoring import PolicyViolation

    org, _fw, control, stale_app, _fresh, stale, actor, policy_id = _world(db_session, make_org)
    Service.bind_clause(org.id, policy_id, "4.2", None, control.id, actor.id)
    published = Service.publish_policy(org.id, policy_id, actor.id)
    db_session.commit()

    assert published["check"]["raised"] == 1
    [violation] = _open_clause_violations(org)
    assert violation.entity_id == stale_app.id and violation.policy_id == policy_id
    assert violation.violation_details.startswith("Clause 4.2 needs control AC-9: evidence has expired")
    assert "policy version 1" in violation.violation_details and violation.severity == "high"

    # A second check raises no duplicate.
    again = Service.detect_clause_violations(org.id)
    assert again["raised"] == 0 and len(_open_clause_violations(org)) == 1

    # Renew the evidence, check again: remediated, not deleted.
    stale.evidence_valid_until = datetime.utcnow() + timedelta(days=90)
    db_session.commit()
    closed = Service.detect_clause_violations(org.id)
    db_session.commit()
    assert closed["remediated"] == 1 and _open_clause_violations(org) == []
    db_session.expire_all()
    fixed = db_session.get(PolicyViolation, violation.id)
    assert fixed.status == "remediated" and fixed.remediated_at is not None
    assert Service.detect_clause_violations(org.id)["raised"] == 0


def test_a_failed_test_also_raises_a_violation_and_the_reason_is_named(app, db_session, make_org):
    from app.modules.compliance.services.control_assurance_service import record_control_test

    org, _fw, control, stale_app, fresh_app, stale, actor, policy_id = _world(db_session, make_org)
    stale.evidence_valid_until = datetime.utcnow() + timedelta(days=30)
    db_session.flush()
    from sqlalchemy import select

    from app.models.application_compliance import ApplicationComplianceControl

    fresh = db_session.execute(
        select(ApplicationComplianceControl).where(ApplicationComplianceControl.application_id == fresh_app.id)
    ).scalar_one()
    record_control_test(org.id, fresh.id, test_kind="operating", result="ineffective", tested_by_id=actor.id)
    Service.bind_clause(org.id, policy_id, "7", None, control.id, actor.id)
    Service.publish_policy(org.id, policy_id, actor.id)
    [violation] = _open_clause_violations(org)
    assert violation.entity_id == fresh_app.id and "the latest test failed" in violation.violation_details


def test_an_exempted_violation_is_left_alone(app, db_session, make_org):
    org, _fw, control, *_r, actor, policy_id = _world(db_session, make_org)
    Service.bind_clause(org.id, policy_id, "4.2", None, control.id, actor.id)
    Service.publish_policy(org.id, policy_id, actor.id)
    [violation] = _open_clause_violations(org)
    violation.status = "exempted"
    db_session.commit()
    assert Service.detect_clause_violations(org.id)["raised"] == 0 and _open_clause_violations(org) == []


# --------------------------------------------------------------------------- #
# Versions
# --------------------------------------------------------------------------- #


def test_a_new_version_copies_the_clauses_and_publishing_it_retires_the_old_one(app, db_session, make_org):
    from app.models.policy_monitoring import ArchitecturePolicy, PolicyViolation

    org, _fw, control, *_r, actor, policy_id = _world(db_session, make_org)
    Service.bind_clause(org.id, policy_id, "4.2", "Access is reviewed", control.id, actor.id)
    Service.publish_policy(org.id, policy_id, actor.id)
    db_session.commit()
    [first_violation] = _open_clause_violations(org)

    draft_id = Service.new_policy_version(org.id, policy_id, actor.id)["policy"]["id"]
    db_session.commit()
    [copied] = Service.governed_applications(org.id, draft_id)
    assert copied["clause_ref"] == "4.2" and copied["clause_text"] == "Access is reviewed"
    with pytest.raises(PolicyRefused):
        Service.new_policy_version(org.id, policy_id, actor.id)  # a draft of the next version already exists

    published = Service.publish_policy(org.id, draft_id, actor.id)
    db_session.commit()
    assert published["policy"]["version_number"] == 2
    db_session.expire_all()
    old = db_session.get(ArchitecturePolicy, policy_id)
    assert old.is_active is False and old.version_number == 1
    assert db_session.get(ArchitecturePolicy, draft_id).supersedes_id == policy_id
    # The old version's violation keeps pointing at it; the new version raised its own.
    assert db_session.get(PolicyViolation, first_violation.id).policy_id == policy_id
    assert {v.policy_id for v in _open_clause_violations(org)} == {policy_id, draft_id}


def test_only_a_published_policy_can_have_a_new_version(app, db_session, make_org):
    org, *_rest, actor, policy_id = _world(db_session, make_org)
    with pytest.raises(PolicyRefused):
        Service.new_policy_version(org.id, policy_id, actor.id)


# --------------------------------------------------------------------------- #
# Tenancy
# --------------------------------------------------------------------------- #


def test_another_organisations_policy_and_controls_are_out_of_reach(app, db_session, make_org):
    org_a, org_b = make_org("pol-a"), make_org("pol-b")
    framework_a = _adopted_framework(db_session, org_a.id)
    control_a = _control(db_session, framework_a, "A-1")
    foreign_framework = _framework(db_session)  # a framework organisation A has not adopted
    foreign_control = _control(db_session, foreign_framework, "X-1")
    policy_b = _draft(db_session, org_b)
    policy_a = _draft(db_session, org_a)
    actor = _user(db_session, org_a.id, "Actor")
    db_session.commit()

    # A cannot bind to B's policy: not found, even though the id exists in B.
    with pytest.raises(PolicyNotFound):
        Service.bind_clause(org_a.id, policy_b, "1", None, control_a.id, actor.id)
    with pytest.raises(PolicyNotFound):
        Service.publish_policy(org_a.id, policy_b, actor.id)
    with pytest.raises(PolicyNotFound):
        Service.governed_applications(org_a.id, policy_b)
    # A cannot bind a control from a framework A has not adopted.
    with pytest.raises(PolicyRefused):
        Service.bind_clause(org_a.id, policy_a, "1", None, foreign_control.id, actor.id)
    # An organisation that tracks a control on its own row may bind it without adopting the framework.
    comp, _ = _application(db_session, org_a.id, "Tracks it")
    _adopt(db_session, org_a.id, foreign_control, comp)
    assert Service.bind_clause(org_a.id, policy_a, "1", None, foreign_control.id, actor.id).control_id == foreign_control.id
    # With no organisation every read is empty and every write refuses without writing.
    from app.models.policy_clause_control import PolicyClauseControl

    before = PolicyClauseControl.query.count()
    with pytest.raises(PolicyNotFound):
        Service.bind_clause(None, policy_a, "2", None, control_a.id, actor.id)
    with pytest.raises(PolicyNotFound):
        Service.publish_policy(None, policy_a, actor.id)
    assert Service.policies_overview(None) == [] and Service.bindable_controls(None) == []
    assert Service.detect_clause_violations(None) == {"raised": 0, "updated": 0, "remediated": 0, "open": 0}
    assert PolicyClauseControl.query.count() == before


def test_a_violation_in_one_organisation_never_appears_in_another(app, db_session, make_org):
    org, _fw, control, *_r, actor, policy_id = _world(db_session, make_org)
    other = make_org("pol-other")
    Service.bind_clause(org.id, policy_id, "4.2", None, control.id, actor.id)
    Service.publish_policy(org.id, policy_id, actor.id)
    db_session.commit()
    from app.modules.compliance.services.evidence_gap_service import compliance_findings

    assert len(_in_org(app, org.id, lambda: compliance_findings(org.id))["policy_violations"]) == 1
    nothing = _in_org(app, other.id, lambda: compliance_findings(other.id))
    assert nothing["policy_violations"] == [] and nothing["total"] == 0
    assert Service.detect_clause_violations(other.id)["open"] == 0


def test_scanning_another_organisations_application_answers_not_found(app, db_session, make_org):
    """Even with that application already loaded into the session (the identity-map case)."""
    org_a, org_b = make_org("scan-a"), make_org("scan-b")
    comp_b, _ = _application(db_session, org_b.id, "B only")
    db_session.commit()
    from app.models.application_portfolio import ApplicationComponent

    assert db_session.get(ApplicationComponent, comp_b.id) is not None  # warm the identity map
    result = _in_org(app, org_a.id, lambda: Service.scan_application_compliance(comp_b.id))
    assert result == {"success": False, "error": "Application not found"}
    assert _in_org(app, None, lambda: Service.scan_application_compliance(comp_b.id))["success"] is False
    own = _in_org(app, org_b.id, lambda: Service.scan_application_compliance(comp_b.id))
    assert own["success"] is True
    assert _in_org(app, org_a.id, lambda: Service.scan_all_applications())["applications_scanned"] == 0
    assert _in_org(app, org_b.id, lambda: Service.scan_all_applications())["applications_scanned"] == 1


def test_the_old_service_path_is_a_short_shim_for_the_one_service():
    import app.services.policy_monitoring_service as old

    path = Path(old.__file__)
    assert len(path.read_text().splitlines()) <= 15
    assert old.PolicyMonitoringService is Service


# --------------------------------------------------------------------------- #
# Privacy-assessment flags
# --------------------------------------------------------------------------- #


def _info_object(db_session, org_id, name, categories):
    from app.models import ArchiMateElement

    element = ArchiMateElement(
        name=name, type="DataObject", layer="application", organization_id=org_id,
        custom_properties={"data_categories": categories} if categories is not None else {},
    )
    db_session.add(element)
    db_session.flush()
    return element


def _accessed_by(db_session, org_id, data_object, system):
    from app.models import ArchiMateRelationship

    db_session.add(
        ArchiMateRelationship(source_id=system.id, target_id=data_object.id, type="Access", organization_id=org_id)
    )
    db_session.flush()


def _ai_system(db_session, org_id, name):
    from app.models import ArchiMateElement
    from app.modules.enterprise_genome.services.ai_system_profile import AI_SYSTEM_MARKER

    element = ArchiMateElement(
        name=name, type="ApplicationComponent", layer="application", organization_id=org_id,
        custom_properties={AI_SYSTEM_MARKER: {"provider": "test", "model_id": "m"}},
    )
    db_session.add(element)
    db_session.flush()
    return element


def test_a_processing_activity_is_flagged_with_its_criteria_named(app, db_session, make_org):
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    org = make_org("privacy")
    health = _info_object(db_session, org.id, "Patient records", ["Health data", "Contact details"])
    _accessed_by(db_session, org.id, health, _ai_system(db_session, org.id, "Triage model"))
    staff = _info_object(db_session, org.id, "Staff directory", ["Employee names"])
    _info_object(db_session, org.id, "Public catalogue", ["Product names"])
    _info_object(db_session, org.id, "Undescribed object", None)
    db_session.commit()

    flags = {f["activity_name"]: f for f in ApplicabilityService.privacy_assessment_flags(org.id)}

    assert set(flags) == {"Patient records", "Staff directory"}
    both = flags["Patient records"]
    assert both["reading"] == "assessment likely required"
    assert [c["key"] for c in both["criteria"]] == ["special_category_data", "automated_evaluation"]
    assert any("Health data" in c["facts"] for c in both["criteria"])
    assert any("Triage model" in c["facts"] for c in both["criteria"])
    assert flags["Staff directory"]["reading"] == "assessment to consider"
    assert [c["key"] for c in flags["Staff directory"]["criteria"]] == ["vulnerable_data_subjects"]
    assert flags["Staff directory"]["element_id"] == staff.id and flags["Staff directory"]["status"] is None


def test_the_assessment_status_is_written_through_the_typed_writer_and_persists(app, db_session, make_org):
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    org, other = make_org("priv-status"), make_org("priv-other")
    activity = _info_object(db_session, org.id, "Patient records", ["Health data"])
    foreign = _info_object(db_session, other.id, "Foreign records", ["Health data"])
    db_session.commit()

    ApplicabilityService.set_privacy_assessment_status(org.id, activity.id, "completed")
    db_session.commit()
    db_session.expire_all()
    [flag] = ApplicabilityService.privacy_assessment_flags(org.id)
    assert flag["status"] == "completed"
    assert activity.acm_properties["privacy_assessment_status"]["value"] == "completed"

    with pytest.raises(LookupError):
        ApplicabilityService.set_privacy_assessment_status(org.id, foreign.id, "completed")
    with pytest.raises(LookupError):
        ApplicabilityService.set_privacy_assessment_status(None, activity.id, "completed")
    with pytest.raises(ValueError):
        ApplicabilityService.set_privacy_assessment_status(org.id, activity.id, "done-ish")
    assert ApplicabilityService.privacy_assessment_flags(None) == []
    db_session.expire_all()
    assert not foreign.acm_properties or "privacy_assessment_status" not in foreign.acm_properties
    assert [f["activity_name"] for f in ApplicabilityService.privacy_assessment_flags(other.id)] == ["Foreign records"]


def test_a_non_processing_element_cannot_take_an_assessment_status(app, db_session, make_org):
    from app.models import ArchiMateElement
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    org = make_org("priv-type")
    node = ArchiMateElement(name="A server", type="Node", layer="technology", organization_id=org.id)
    db_session.add(node)
    db_session.commit()
    with pytest.raises(ValueError):
        ApplicabilityService.set_privacy_assessment_status(org.id, node.id, "completed")


def test_rule_matches_covers_the_operators_and_evaluate_node_is_unchanged(app):
    from app.modules.compliance.services.applicability_service import ApplicabilityService as A

    assert A.rule_matches("Cloud", {"operator": "in", "value": ["Cloud", "Hybrid"]}) is True
    assert A.rule_matches("OnPrem", {"operator": "in", "value": ["Cloud", "Hybrid"]}) is False
    assert A.rule_matches("x", {"operator": "eq", "value": "x"}) is True
    assert A.rule_matches('["DORA"]', {"operator": "contains", "value": "DORA"}) is True
    assert A.rule_matches("not json", {"operator": "contains", "value": "DORA"}) is False
    assert A.rule_matches(["Health data"], {"operator": "mentions_any", "value": ["health"]}) is True
    assert A.rule_matches(None, {"operator": "eq", "value": "x"}) is False
    assert A.rule_matches("anything", {"operator": "surprise", "value": 1}) is True


# --------------------------------------------------------------------------- #
# The findings list
# --------------------------------------------------------------------------- #


def test_the_findings_list_returns_all_three_kinds_each_with_the_four_keys(app, db_session, make_org):
    from app.modules.compliance.services.evidence_gap_service import compliance_findings

    org, _fw, control, *_r, actor, policy_id = _world(db_session, make_org)
    Service.bind_clause(org.id, policy_id, "4.2", None, control.id, actor.id)
    Service.publish_policy(org.id, policy_id, actor.id)
    _info_object(db_session, org.id, "Patient records", ["Health data"])
    db_session.commit()

    result = _in_org(app, org.id, lambda: compliance_findings(org.id))

    assert len(result["evidence"]) == 1 and len(result["policy_violations"]) == 1
    assert len(result["privacy_assessments"]) == 1 and result["total"] == 3
    for row in result["evidence"] + result["policy_violations"] + result["privacy_assessments"]:
        assert {"kind", "evidence", "provenance", "confidence", "trace"} <= set(row)
        assert row["confidence"] is None
    violation = result["policy_violations"][0]
    assert violation["trace"]["policy_version"] == 1 and violation["trace"]["clause_ref"] == "4.2"
    assert violation["trace"]["control_id"] == control.id and violation["trace"]["element_id"] is not None
    assert result["privacy_assessments"][0]["trace"]["facts"]["special_category_data"] == ["Health data"]


def test_the_findings_list_runs_a_bounded_number_of_statements(app, db_session, make_org):
    from sqlalchemy import event

    from app.modules.compliance.services.evidence_gap_service import compliance_findings

    org, _fw, control, stale_app, _fresh, _stale, actor, policy_id = _world(db_session, make_org)
    framework = _framework(db_session)
    for i in range(25):
        extra = _control(db_session, framework, f"EX-{i:02d}")
        _adopt(db_session, org.id, extra, stale_app)
        Service.bind_clause(org.id, policy_id, f"C{i:02d}", None, control.id, actor.id)
    Service.publish_policy(org.id, policy_id, actor.id)
    for i in range(10):
        _info_object(db_session, org.id, f"Activity {i}", ["Health data"])
    db_session.commit()

    statements = []
    engine = db_session.get_bind()

    def count(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        result = _in_org(app, org.id, lambda: compliance_findings(org.id))
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert result["total"] > 50
    assert len(statements) < 40, len(statements)


def test_the_workflow_compliance_scan_step_names_its_instances_organisation(app, db_session, monkeypatch):
    """The scan is fenced to one organisation, so the workflow step passes its own instead of
    relying on an ambient request that a background step does not have."""
    from types import SimpleNamespace

    from app.services.ea_workflow_engine import EAWorkflowEngine

    calls = []

    def fake_scan(organization_id=None):
        calls.append(organization_id)
        return {"success": True, "applications_scanned": 0, "total_violations_found": 0}

    monkeypatch.setattr(Service, "scan_all_applications", staticmethod(fake_scan))
    engine = EAWorkflowEngine.__new__(EAWorkflowEngine)
    engine._handle_compliance_scan(SimpleNamespace(id=None, organization_id=4242, triggered_by_user_id=None), {}, {})
    assert calls == [4242]

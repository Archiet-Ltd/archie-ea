"""Tests for ``IntelligenceQueryService.compliance_for_element`` (the Compliance question, L6).

Proves the tenant fences (another organisation's mapping rows, violations and scans), that
absence is stated and never filled (no mapping is not "0% compliant"; a never-scanned status
row's stored zeros are not returned), and that the evidence URL and verifier never appear.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.modules.intelligence.services.query_service import IntelligenceQueryService


def _element(db_session, org_id, name="Payments"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _component(db_session, org_id, element):
    from app.models.application_portfolio import ApplicationComponent

    component = ApplicationComponent(name=element.name, organization_id=org_id, archimate_element_id=element.id)
    db_session.add(component)
    db_session.flush()
    return component


def _control(db_session, code="AC-2", framework_code=None):
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework

    framework = RegulatoryFramework(
        code=framework_code or f"FW{uuid.uuid4().hex[:6]}", name="Test Framework", category="security",
    )
    db_session.add(framework)
    db_session.flush()
    control = ComplianceControl(framework_id=framework.id, control_code=code, title="Account management")
    db_session.add(control)
    db_session.flush()
    return control, framework


def _mapping(db_session, org_id, component, control, **fields):
    from app.models.application_compliance import ApplicationComplianceControl

    row = ApplicationComplianceControl(
        organization_id=org_id, application_id=component.id, control_id=control.id, **fields,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _policy(db_session, org_id, name="Encrypt at rest"):
    from app.models.policy_monitoring import ArchitecturePolicy

    policy = ArchitecturePolicy(name=name, organization_id=org_id)
    db_session.add(policy)
    db_session.flush()
    return policy


def _run(app, org_id, element_id):
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_id
        return IntelligenceQueryService.compliance_for_element(element_id)


def _world(db_session, make_org):
    org_a, org_b = make_org("compliance-a"), make_org("compliance-b")
    el = _element(db_session, org_a.id)
    component = _component(db_session, org_a.id, el)
    return org_a, org_b, el, component


def test_a_mapped_control_is_returned_with_its_global_names(app, db_session, make_org):
    org_a, _b, el, component = _world(db_session, make_org)
    control, framework = _control(db_session, "A.9.2.1", "ISO27001")
    _mapping(db_session, org_a.id, component, control, implementation_status="implemented",
             evidence_url="https://secret.example/evidence", verified_date=datetime(2026, 1, 5))
    db_session.commit()

    result = _run(app, org_a.id, el.id)

    [row] = result["controls"]
    assert row["code"] == "A.9.2.1" and row["framework_code"] == "ISO27001" and row["name"] == "Account management"
    assert row["implementation_status"] == "implemented" and row["verified"] is True
    assert row["evidence_url_recorded"] is True and row["no_evidence"] is False
    assert "no_compliance_controls_recorded" not in result["reasons"]
    assert "no_control_evidence" not in result["reasons"]


def test_the_evidence_url_and_verifier_are_never_returned(app, db_session, make_org):
    org_a, _b, el, component = _world(db_session, make_org)
    control, _ = _control(db_session)
    _mapping(db_session, org_a.id, component, control, evidence_url="https://secret.example/evidence",
             notes="private note")
    db_session.commit()

    text = str(_run(app, org_a.id, el.id))

    for leaked in ("secret.example", "private note", "verified_by"):
        assert leaked not in text


def test_no_mapping_is_stated_and_shows_no_percentage_or_zero(app, db_session, make_org):
    org_a, _b, el, _c = _world(db_session, make_org)
    db_session.commit()

    result = _run(app, org_a.id, el.id)

    assert result["controls"] == [] and result["open_violations"] == []
    assert result["reasons"] == ["no_compliance_controls_recorded", "no_policy_scan_recorded"]
    assert "percent" not in str(result).lower()


def test_a_control_with_no_evidence_and_no_verification_is_flagged(app, db_session, make_org):
    org_a, _b, el, component = _world(db_session, make_org)
    control, _ = _control(db_session)
    _mapping(db_session, org_a.id, component, control, implementation_status="planned")
    db_session.commit()

    result = _run(app, org_a.id, el.id)

    assert result["controls"][0]["no_evidence"] is True
    assert "no_control_evidence" in result["reasons"]


def test_not_applicable_and_waived_controls_are_not_flagged_as_missing_evidence(app, db_session, make_org):
    org_a, _b, el, component = _world(db_session, make_org)
    for status in ("not_applicable", "waived"):
        control, _ = _control(db_session, f"X-{status}")
        _mapping(db_session, org_a.id, component, control, implementation_status=status)
    db_session.commit()

    result = _run(app, org_a.id, el.id)

    assert all(c["no_evidence"] is False for c in result["controls"])
    assert "no_control_evidence" not in result["reasons"]


def test_another_organisations_mappings_are_never_returned(app, db_session, make_org):
    org_a, org_b, el, component = _world(db_session, make_org)
    control, _ = _control(db_session)
    _mapping(db_session, org_b.id, component, control, implementation_status="implemented")
    db_session.commit()
    org_a_id, el_id = org_a.id, el.id
    db_session.expunge_all()

    result = _run(app, org_a_id, el_id)

    assert result["controls"] == []
    assert result["reasons"][0] == "no_compliance_controls_recorded"


def test_an_element_of_another_organisation_reads_as_absent(app, db_session, make_org):
    org_a, org_b, _el, _c = _world(db_session, make_org)
    foreign = _element(db_session, org_b.id, "Foreign")
    _component(db_session, org_b.id, foreign)
    db_session.commit()
    org_a_id, foreign_id = org_a.id, foreign.id
    db_session.expunge_all()

    assert _run(app, org_a_id, foreign_id)["reasons"] == ["element_not_found"]


def test_an_element_with_no_application_component_says_so(app, db_session, make_org):
    org_a, _b, _el, _c = _world(db_session, make_org)
    lone = _element(db_session, org_a.id, "Not an app")
    db_session.commit()

    assert _run(app, org_a.id, lone.id)["reasons"] == ["no_application_component"]


def test_no_tenant_context_is_a_stated_absence(app, db_session, make_org):
    _a, _b, el, _c = _world(db_session, make_org)
    db_session.commit()

    assert _run(app, None, el.id)["reasons"] == ["no_tenant_context"]


def test_open_violations_are_returned_and_foreign_ones_are_not(app, db_session, make_org):
    from app.models.policy_monitoring import PolicyViolation

    org_a, org_b, el, component = _world(db_session, make_org)
    own_policy, foreign_policy = _policy(db_session, org_a.id), _policy(db_session, org_b.id, "Foreign policy")
    db_session.add(PolicyViolation(policy_id=own_policy.id, entity_type="application", entity_id=component.id,
                                   severity="high", status="open", organization_id=org_a.id))
    db_session.add(PolicyViolation(policy_id=foreign_policy.id, entity_type="application", entity_id=component.id,
                                   severity="high", status="open", organization_id=org_b.id))
    db_session.add(PolicyViolation(policy_id=own_policy.id, entity_type="application", entity_id=component.id,
                                   severity="low", status="remediated", organization_id=org_a.id))
    db_session.commit()
    org_a_id, el_id = org_a.id, el.id
    db_session.expunge_all()

    result = _run(app, org_a_id, el_id)

    assert [v["policy_name"] for v in result["open_violations"]] == ["Encrypt at rest"]
    assert "Foreign policy" not in str(result)


def test_a_status_row_reports_only_its_last_scan_never_its_stored_numbers(app, db_session, make_org):
    from app.models.policy_monitoring import ComplianceStatus

    org_a, _b, el, component = _world(db_session, make_org)
    db_session.add(ComplianceStatus(
        entity_type="application", entity_id=component.id, organization_id=org_a.id,
        last_scan_at=datetime(2026, 9, 1, 12, 0), compliance_percentage=0.0, violation_count=0, risk_score=0.0,
    ))
    db_session.commit()

    result = _run(app, org_a.id, el.id)

    assert result["last_scan_at"].startswith("2026-09-01")
    assert "no_policy_scan_recorded" not in result["reasons"]
    assert "compliance_percentage" not in str(result) and "violation_count" not in str(result)
    assert "risk_score" not in str(result)


def test_the_tenant_predicate_seam_carries_the_explicit_predicate(app, db_session, make_org, tenant_ctx, monkeypatch):
    """Mutation proof by divergence: the ambient organisation is B, the caller's is A. The
    explicit predicate must still refuse B's element; neutering the seam lets it through."""
    from sqlalchemy import true as sa_true

    org_a, org_b = make_org("compliance-seam-a"), make_org("compliance-seam-b")
    foreign = _element(db_session, org_b.id, "Foreign app")
    _component(db_session, org_b.id, foreign)
    db_session.commit()
    org_a_id, org_b_id, foreign_id = org_a.id, org_b.id, foreign.id

    def resolve():
        from app.models import ArchiMateElement

        return db_session.execute(
            db_session.query(ArchiMateElement.id)
            .filter(ArchiMateElement.id == foreign_id)
            .filter(IntelligenceQueryService._compliance_tenant_predicate(ArchiMateElement, org_a_id))
            .statement
        ).first()

    with tenant_ctx(org_b_id):
        assert resolve() is None
        monkeypatch.setattr(
            IntelligenceQueryService, "_compliance_tenant_predicate",
            staticmethod(lambda model, organization_id: sa_true()),
        )
        assert resolve() is not None
        monkeypatch.undo()
        assert resolve() is None

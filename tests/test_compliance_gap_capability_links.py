"""Compliance gaps from the register are linked only to the capability they
belong to, and the association joins the capability's own element to the gap."""

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _capability(db_session, org, name):
    from app.models import BusinessCapability

    cap = BusinessCapability(name="%s %s" % (name, uuid.uuid4().hex[:6]), organization_id=org.id)
    db_session.add(cap)
    db_session.flush()
    return cap


def _compliance_gap(db_session, org, capability_id):
    from app.models.implementation_migration import Gap

    gap = Gap(
        organization_id=org.id,
        name="Requirement gap %s" % uuid.uuid4().hex[:8],
        gap_type="compliance",
        source_table="compliance_requirement",
        gap_sub_types={"applies_to_capability_id": capability_id},
    )
    db_session.add(gap)
    db_session.flush()
    return gap


def test_requirement_gap_is_related_only_to_its_own_capability(app, db_session, make_org):
    from app.modules.capabilities.services.capability_gap_service import CapabilityGapAnalysisService

    org = make_org("capgap")
    linked = _capability(db_session, org, "Linked")
    other = _capability(db_session, org, "Other")
    gap = _compliance_gap(db_session, org, linked.id)

    service = CapabilityGapAnalysisService()
    assert [g.id for g in service._get_related_compliance_gaps(linked.id)] == [gap.id]
    assert service._get_related_compliance_gaps(other.id) == []


def test_association_joins_the_capability_element_to_the_gap_element(app, db_session, make_org):
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.implementation_migration import Gap
    from app.modules.capabilities.services.capability_gap_service import CapabilityGapAnalysisService

    org = make_org("capgap")
    capability = _capability(db_session, org, "Payments")
    # A capability id that is not the id of any element must not be mistaken
    # for one: make the capability id and the element ids differ.
    gap = Gap(organization_id=org.id, name="Cap gap %s" % uuid.uuid4().hex[:8],
              gap_type="compliance", source_capability_id=capability.id)
    db_session.add(gap)
    gap_element = ArchiMateElement(name="Gap element", type="Gap", layer="implementation_migration",
                                   architecture_id=None, organization_id=org.id)
    db_session.add(gap_element)
    db_session.flush()

    CapabilityGapAnalysisService()._ensure_compliance_relationships(gap, gap_element)
    db_session.flush()

    rel = ArchiMateRelationship.query.filter_by(target_id=gap_element.id, type="association").one()
    assert capability.archimate_element_id is not None
    assert rel.source_id == capability.archimate_element_id
    source = db_session.get(ArchiMateElement, rel.source_id)
    assert source.type == "Capability"
    assert source.name.endswith(capability.name) or capability.name in source.name


def test_only_compliance_gaps_come_back_for_a_capability(app, db_session, make_org):
    from app.models.implementation_migration import Gap
    from app.modules.capabilities.services.capability_gap_service import CapabilityGapAnalysisService

    org = make_org("capgap")
    capability = _capability(db_session, org, "Claims")
    functional = Gap(organization_id=org.id, name="Functional gap %s" % uuid.uuid4().hex[:8],
                     gap_type="functional", source_capability_id=capability.id)
    compliance = Gap(organization_id=org.id, name="Compliance gap %s" % uuid.uuid4().hex[:8],
                     gap_type="compliance", source_capability_id=capability.id)
    db_session.add_all([functional, compliance])
    db_session.flush()

    found = CapabilityGapAnalysisService()._get_related_compliance_gaps(capability.id)
    assert [g.id for g in found] == [compliance.id]


def test_rollup_compliance_gap_records_its_capability(app, db_session, make_org, monkeypatch):
    from app.models.implementation_migration import Gap
    from app.modules.capabilities.services import capability_gap_service as mod

    org = make_org("capgap")
    capability = _capability(db_session, org, "Lending")
    capability.current_maturity_level = 3
    functional = Gap(organization_id=org.id, name="Functional %s" % uuid.uuid4().hex[:8],
                     gap_type="functional", source_capability_id=capability.id)
    db_session.add(functional)
    db_session.flush()
    results = {"compliance_score": 40.0, "application_validations": [
        {"application_name": "Core", "gaps": [
            {"requirement_title": "Retention %s" % uuid.uuid4().hex[:6], "severity": "high"}]}]}
    monkeypatch.setattr(mod.ComplianceInheritanceService, "validate_capability_compliance",
                        staticmethod(lambda cid: results))
    monkeypatch.setattr(mod.ComplianceInheritanceService, "get_compliance_impact_on_maturity",
                        staticmethod(lambda cid: 5))

    out = mod.CapabilityGapAnalysisService().perform_compliance_rollup(capability.id)

    assert out["gaps_created"] == 1
    written = Gap.query.filter_by(organization_id=org.id, gap_type="compliance").one()
    assert written.source_capability_id == capability.id
    found = mod.CapabilityGapAnalysisService()._get_related_compliance_gaps(capability.id)
    assert [g.id for g in found] == [written.id]

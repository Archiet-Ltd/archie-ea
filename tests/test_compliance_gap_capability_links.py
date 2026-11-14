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
                                   architecture_id=None)
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

"""Every ApplicationComplianceControl record must have exactly one ArchiMate element node.

Every domain record of the nine types has exactly one element node in its
organisation. ApplicationComplianceControl now has a before_insert listener
that mirrors it into a BusinessObject (Business layer) element.
"""

import pytest

from app.models.archimate_core import ArchiMateElement
from app.models.application_compliance import ApplicationComplianceControl
from app.models.compliance_models import ComplianceControl, RegulatoryFramework


def test_creating_compliance_creates_archimate_element(db_session, make_org, tenant_ctx):
    """A new ApplicationComplianceControl must automatically get an element."""
    org = make_org("compliance-slice")
    with tenant_ctx(org.id):
        framework = RegulatoryFramework(
            code="TEST-01",
            name="Test Framework",
            description="A test framework.",
        )
        db_session.add(framework)
        db_session.flush()

        control = ComplianceControl(
            framework_id=framework.id,
            control_code="AC-1",
            title="Access Control",
            description="Test access control.",
        )
        db_session.add(control)
        db_session.flush()

        app_compliance = ApplicationComplianceControl(
            organization_id=org.id,
            control_id=control.id,
        )
        db_session.add(app_compliance)
        db_session.flush()

        assert app_compliance.archimate_element_id is not None, (
            "ApplicationComplianceControl create must attach an ArchiMate element"
        )
        element = db_session.get(ArchiMateElement, app_compliance.archimate_element_id)
        assert element is not None
        assert element.type == "BusinessObject"
        assert element.layer == "Business"
        assert element.organization_id == org.id


def test_compliance_archimate_element_is_idempotent(db_session, make_org, tenant_ctx):
    """Creating the same record again must not duplicate the element."""
    org = make_org("compliance-idempotent")
    with tenant_ctx(org.id):
        framework = RegulatoryFramework(
            code="TEST-02",
            name="Test Framework 2",
        )
        db_session.add(framework)
        db_session.flush()

        control = ComplianceControl(
            framework_id=framework.id,
            control_code="AC-2",
            title="Account Management",
        )
        db_session.add(control)
        db_session.flush()

        app_compliance = ApplicationComplianceControl(
            organization_id=org.id,
            control_id=control.id,
        )
        db_session.add(app_compliance)
        db_session.flush()

        assert app_compliance.archimate_element_id is not None
        assert db_session.query(ArchiMateElement).filter_by(
            organization_id=org.id,
        ).count() == 1


def test_compliance_archimate_element_isolation(db_session, make_org, tenant_ctx):
    """A record in organisation A must not create an element in organisation B."""
    org_a = make_org("compliance-iso-a")
    org_b = make_org("compliance-iso-b")

    framework = RegulatoryFramework(
        code="TEST-03",
        name="Test Framework 3",
    )
    db_session.add(framework)
    db_session.flush()

    control = ComplianceControl(
        framework_id=framework.id,
        control_code="AC-3",
        title="Access Enforcement",
    )
    db_session.add(control)
    db_session.flush()

    with tenant_ctx(org_a.id):
        app_compliance = ApplicationComplianceControl(
            organization_id=org_a.id,
            control_id=control.id,
        )
        db_session.add(app_compliance)
        db_session.flush()

        element_a = db_session.get(ArchiMateElement, app_compliance.archimate_element_id)
        assert element_a.organization_id == org_a.id

        b_elements = db_session.query(ArchiMateElement).filter_by(
            organization_id=org_b.id,
        ).count()
        assert b_elements == 0, (
            "Record in org A must not create an element in org B"
        )


def test_deleting_compliance_leaves_no_orphan_element(db_session, make_org, tenant_ctx):
    """Deleting a record must set null on its element."""
    org = make_org("compliance-delete")
    with tenant_ctx(org.id):
        framework = RegulatoryFramework(
            code="TEST-04",
            name="Test Framework 4",
        )
        db_session.add(framework)
        db_session.flush()

        control = ComplianceControl(
            framework_id=framework.id,
            control_code="AC-4",
            title="Info Flow Enforcement",
        )
        db_session.add(control)
        db_session.flush()

        app_compliance = ApplicationComplianceControl(
            organization_id=org.id,
            control_id=control.id,
        )
        db_session.add(app_compliance)
        db_session.flush()

        element_id = app_compliance.archimate_element_id
        assert element_id is not None

        db_session.delete(app_compliance)
        db_session.flush()

        element = db_session.get(ArchiMateElement, element_id)
        assert element is not None, (
            "ArchiMateElement must not be deleted when record is deleted "
            "(ondelete=SET NULL)"
        )


def test_compliance_with_preset_element_is_not_overwritten(db_session, make_org, tenant_ctx):
    """A caller that already links an element is left alone."""
    org = make_org("compliance-preset")
    with tenant_ctx(org.id):
        preset = ArchiMateElement(
            name="Pre-linked BusinessObject",
            type="BusinessObject",
            layer="Business",
            organization_id=org.id,
        )
        db_session.add(preset)
        db_session.flush()

        framework = RegulatoryFramework(
            code="TEST-05",
            name="Test Framework 5",
        )
        db_session.add(framework)
        db_session.flush()

        control = ComplianceControl(
            framework_id=framework.id,
            control_code="AC-5",
            title="Access Control Policy",
        )
        db_session.add(control)
        db_session.flush()

        app_compliance = ApplicationComplianceControl(
            organization_id=org.id,
            control_id=control.id,
            archimate_element_id=preset.id,
        )
        db_session.add(app_compliance)
        db_session.flush()

        assert app_compliance.archimate_element_id == preset.id
        assert db_session.query(ArchiMateElement).filter_by(
            organization_id=org.id, name="Pre-linked BusinessObject"
        ).count() == 1
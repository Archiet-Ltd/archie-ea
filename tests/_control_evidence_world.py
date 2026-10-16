"""Shared seed helpers for the control-evidence tests.

Two real organisations, real rows, no mocked organisation context: a test
enters the organisation it acts for with ``tenant_ctx`` (or a signed-in client)
exactly as a request would.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta


def utcnow():
    return datetime.utcnow()


def element(db_session, org_id, name, type_="ApplicationComponent", layer="application"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def serves(db_session, org_id, source, target):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(
        source_id=source.id, target_id=target.id, type="Serving", organization_id=org_id
    )
    db_session.add(rel)
    db_session.flush()
    return rel


def application(db_session, org_id, name):
    """An application with its ArchiMate element; returns (component, element)."""
    from app.models.application_portfolio import ApplicationComponent

    el = element(db_session, org_id, name)
    component = ApplicationComponent(name=name, organization_id=org_id, archimate_element_id=el.id)
    db_session.add(component)
    db_session.flush()
    return component, el


def framework(db_session, code=None):
    from app.models.compliance_models import RegulatoryFramework

    fw = RegulatoryFramework(
        code=code or f"FW{uuid.uuid4().hex[:6]}", name="Test Framework", category="security"
    )
    db_session.add(fw)
    db_session.flush()
    return fw


def control(db_session, fw, code="AC-2", title="Account management", priority="medium"):
    from app.models.compliance_models import ComplianceControl

    row = ComplianceControl(
        framework_id=fw.id, control_code=code, title=title, priority=priority
    )
    db_session.add(row)
    db_session.flush()
    return row


def adopted(db_session, org_id, ctl, component=None, status="implemented", **fields):
    """One adopted control row of the organisation (the per-organisation register)."""
    from app.models.application_compliance import ApplicationComplianceControl

    row = ApplicationComplianceControl(
        organization_id=org_id,
        application_id=component.id if component is not None else None,
        control_id=ctl.id,
        implementation_status=status,
        **fields,
    )
    db_session.add(row)
    db_session.flush()
    return row


def user(
    db_session, org_id, label="U", enterprise_role="compliance", first_name=None, last_name=None,
    role_name=None,
):
    from app.models.user import Role, User

    suffix = uuid.uuid4().hex[:8]
    row = User(
        email=f"{label.lower()}-{suffix}@example.com",
        first_name=label if first_name is None else first_name,
        last_name="Tester" if last_name is None else last_name,
        organization_id=org_id,
        confirmed=True,
        enterprise_role=enterprise_role,
    )
    role = Role.query.filter_by(name=role_name or "Architect").first()
    row.role = role
    db_session.add(row)
    db_session.flush()
    return row


def days(n):
    return utcnow() + timedelta(days=n)

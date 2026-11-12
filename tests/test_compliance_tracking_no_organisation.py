"""Compliance Tracking: with no organisation known, the capability list is empty, never every organisation's."""

from __future__ import annotations

import pytest
from flask import g

pytestmark = pytest.mark.usefixtures("db_session")


def _two_organisations_with_capabilities(db_session, make_org):
    from app.models.business_capability import BusinessCapability

    org_a = make_org("notenant-a")
    org_b = make_org("notenant-b")
    for org in (org_a, org_b):
        db_session.add(BusinessCapability(name="Payments %s" % org.id, organization_id=org.id))
    db_session.flush()
    return org_a, org_b


def test_no_organisation_lists_no_capabilities(app, db_session, make_org):
    from app.modules.solutions_strategic.v2.services.compliance_tracking_service import ComplianceTrackingService

    _two_organisations_with_capabilities(db_session, make_org)
    # No organisation known and no tenant fence: a job, a script or a client without a session.
    g.current_org_id = None
    assert ComplianceTrackingService()._get_all_capabilities() == []


def test_a_known_organisation_lists_only_its_own_capabilities(app, db_session, make_org):
    from app.modules.solutions_strategic.v2.services.compliance_tracking_service import ComplianceTrackingService

    org_a, org_b = _two_organisations_with_capabilities(db_session, make_org)
    g.current_org_id = org_a.id
    names = [c["name"] for c in ComplianceTrackingService()._get_all_capabilities()]
    assert "Payments %s" % org_a.id in names
    assert "Payments %s" % org_b.id not in names

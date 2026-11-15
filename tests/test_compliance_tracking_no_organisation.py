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


def _two_organisations_with_controls(db_session):
    """Two organisations, each with one capability, one application supporting it and two controls not in place."""
    from tests.test_compliance_one_answer import _world

    return _world(db_session, _make_org_factory(db_session), "ctrl-a"), _world(
        db_session, _make_org_factory(db_session), "ctrl-b"
    )


def _make_org_factory(db_session):
    import uuid

    from app.models.organization import Organization

    def _make(label="org"):
        suffix = uuid.uuid4().hex[:10]
        org = Organization(name="Test %s %s" % (label, suffix), slug="test-%s-%s" % (label, suffix))
        db_session.add(org)
        db_session.flush()
        return org

    return _make


def test_no_organisation_counts_no_controls(app, db_session):
    from app.services.compliance.compliance_inheritance_service import ComplianceInheritanceService as Service

    (org_a, _u, cap_a, _b, _c, app_a), (org_b, _u2, cap_b, _b2, _c2, app_b) = _two_organisations_with_controls(db_session)
    g.current_org_id = None
    counts = Service.controls_not_in_place()
    assert counts == {"not_started": 0, "in_progress": 0, "total": 0}
    assert Service.not_in_place_text(counts) == "0 controls not in place: 0 not started and 0 in progress"
    for assessment in Service.assess_capabilities([cap_a.id, cap_b.id]).values():
        assert assessment["assessed_controls"] == 0 and assessment["score"] is None and assessment["findings"] == []
    for assessment in Service.assess_applications([app_a.id, app_b.id]).values():
        assert assessment["assessed_controls"] == 0 and assessment["score"] is None and assessment["findings"] == []


def test_no_organisation_page_figures_are_zero(app, db_session):
    from app.modules.solutions_strategic.v2.services.compliance_tracking_service import ComplianceTrackingService

    _two_organisations_with_controls(db_session)
    g.current_org_id = None
    metrics = ComplianceTrackingService().analyze_compliance_portfolio()["portfolio_metrics"]
    assert metrics["missing_controls"] == 0
    assert metrics["controls_not_in_place"]["total"] == 0
    assert metrics["controls_not_in_place_text"].startswith("0 controls not in place")


def test_a_known_organisation_counts_only_its_own_controls(app, db_session):
    from app.modules.solutions_strategic.v2.services.compliance_tracking_service import ComplianceTrackingService

    (org_a, *_), (org_b, *_) = _two_organisations_with_controls(db_session)
    # Each organisation has one control in progress and one planned: two not in place, none shared.
    for org in (org_a, org_b):
        g.current_org_id = org.id
        metrics = ComplianceTrackingService().analyze_compliance_portfolio()["portfolio_metrics"]
        assert metrics["missing_controls"] == 2
        assert metrics["controls_not_in_place"] == {"not_started": 1, "in_progress": 1, "total": 2}

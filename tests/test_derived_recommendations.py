"""Recommendations are read from the backlog and the stored model-health report, per organisation."""
from __future__ import annotations

import uuid

import pytest

from app.datetime_helpers import utcnow
from app.models.drift_report import DriftReport
from app.models.implementation_migration import Gap
from app.models.unified_work_package import UnifiedWorkPackage
from app.modules.intelligence.services.recommendations import recommendations


def _finding(ftype, name, severity, eid):
    return {
        "type": ftype,
        "severity": severity,
        "why": "because %s" % name,
        "elements": [{"archimate_element_id": eid, "name": name, "archimate_type": "Node", "role": "subject"}],
    }


def _report(db_session, org, findings):
    db_session.add(DriftReport(
        organization_id=org.id,
        report_json={"findings": findings, "summary": {"total": len(findings)}},
        spec_hash="sha256:x",
        computed_at=utcnow(),
        finding_count=len(findings),
    ))
    db_session.flush()


@pytest.fixture
def orgs(make_org):
    return make_org("recs-a"), make_org("recs-b")


def _titles(result):
    return [i["title"] for i in result["items"]]


def test_both_sources_are_ordered_by_recorded_urgency_then_source_then_title(db_session, orgs, tenant_ctx):
    org_a, _ = orgs
    db_session.add(Gap(organization_id=org_a.id, name="Slow gap", priority="low"))
    db_session.add(UnifiedWorkPackage(organization_id=org_a.id, name="Urgent package", priority="critical"))
    _report(db_session, org_a, [
        _finding("orphaned_element", "Old box", "high", 1),
        _finding("capability_without_application_support", "Pay run", "high", 2),
        _finding("orphaned_element", "Dusty", "medium", 3),
    ])
    db_session.flush()
    with tenant_ctx(org_a.id):
        result = recommendations(org_a.id)
    assert _titles(result) == [
        "Give the work package “Urgent package” a start date",
        "Link or retire “Old box”",
        "Support the capability “Pay run” with an application, or record why none is needed",
        "Link or retire “Dusty”",
        "Plan a work package for the gap “Slow gap”",
    ]
    assert result["model_health_state"] == "current" and result["total"] == 5


def test_missing_report_is_stated_not_guessed(db_session, orgs, tenant_ctx):
    org_a, _ = orgs
    with tenant_ctx(org_a.id):
        result = recommendations(org_a.id)
    assert result["items"] == [] and result["model_health_state"] == "not_computed"


def test_list_is_capped_but_total_is_reported(db_session, orgs, tenant_ctx):
    org_a, _ = orgs
    for i in range(7):
        db_session.add(Gap(organization_id=org_a.id, name="Gap %02d" % i, priority="medium"))
    db_session.flush()
    with tenant_ctx(org_a.id):
        result = recommendations(org_a.id, limit=5)
    assert len(result["items"]) == 5 and result["total"] == 7


def test_other_organisation_never_contributes(db_session, orgs, tenant_ctx):
    org_a, org_b = orgs
    db_session.add(Gap(organization_id=org_b.id, name="B gap", priority="high"))
    _report(db_session, org_b, [_finding("orphaned_element", "B box", "high", 9)])
    db_session.flush()
    with tenant_ctx(org_a.id):
        result = recommendations(org_a.id)
    assert result["items"] == [] and result["model_health_state"] == "not_computed"
    with tenant_ctx(org_b.id):
        assert len(recommendations(org_b.id)["items"]) == 2


def test_page_renders_per_organisation(db_session, orgs, client, login_as):
    from app.models.user import User

    org_a, org_b = orgs
    db_session.add(Gap(organization_id=org_a.id, name="Zebra gap", priority="high"))
    users = []
    for org in (org_a, org_b):
        u = User(email="recs-%s@example.com" % uuid.uuid4().hex[:8], organization_id=org.id, confirmed=True)
        u.password = "not-used-in-tests-123"
        db_session.add(u)
        users.append(u)
    db_session.commit()
    login_as(client, users[0])
    body = client.get("/intelligence/recommendations").get_data(as_text=True)
    assert "Plan a work package for the gap" in body and "Zebra gap" in body
    login_as(client, users[1])
    body = client.get("/intelligence/recommendations").get_data(as_text=True)
    assert "Zebra gap" not in body
    assert "Nothing to recommend" in body

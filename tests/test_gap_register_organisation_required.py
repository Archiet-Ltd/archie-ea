"""Gap readers and counters stay inside one organisation."""

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def test_gap_sync_without_an_organisation_reads_nothing(app, db_session, make_org):
    from app.models.implementation_migration import Gap
    from app.services.roadmap_sync import RoadmapDataSync

    org = make_org("sync")
    db_session.add(Gap(organization_id=org.id, name="Open gap %s" % uuid.uuid4().hex[:6],
                       resolution_status="open"))
    db_session.flush()

    sync = RoadmapDataSync()
    sync.sync_batch_id = "test"
    result = sync.sync_gaps_to_initiatives(None)

    assert result["created"] == 0 and result["updated"] == 0
    assert result["errors"] and "organisation" in result["errors"][0]
    assert sync._sync_resolved_gaps(None) == 0


def test_rollup_counts_only_gaps_it_created(app, db_session, make_org, monkeypatch):
    from app.models import BusinessCapability
    from app.modules.capabilities.services import capability_gap_service as mod

    org = make_org("rollup")
    cap = BusinessCapability(name="Rollup %s" % uuid.uuid4().hex[:6], organization_id=org.id,
                             current_maturity_level=3)
    db_session.add(cap)
    db_session.flush()

    results = {"compliance_score": 0.4, "application_validations": [
        {"application_name": "Billing", "gaps": [
            {"requirement_title": "Encrypt at rest", "severity": "high"}]}]}
    monkeypatch.setattr(mod.ComplianceInheritanceService, "validate_capability_compliance",
                        staticmethod(lambda _id: results))
    monkeypatch.setattr(mod.ComplianceInheritanceService, "get_compliance_impact_on_maturity",
                        staticmethod(lambda _id: 5))

    service = mod.CapabilityGapAnalysisService()
    assert service.perform_compliance_rollup(cap.id)["gaps_created"] == 1
    assert service.perform_compliance_rollup(cap.id)["gaps_created"] == 0

"""The sixth ("model") dimension of the baseline-drift engine, and the pure
``compare_to_baseline`` seam.

Two organisations, A and B, throughout: every group proves either that one
tenant's model changes never appear in another tenant's comparison, or that
the seam genuinely writes nothing while ``analyze_drift`` still persists
exactly what it found.

``ArchiMateElement`` carries no per-row modification timestamp in this
codebase (only ``ArchiMateRelationship`` does), so the per-element and
per-relationship change signal is a content hash over the fields that make
each one what it is, not a timestamp comparison: renaming, re-typing or
re-layering an element, or changing a relationship's own properties,
changes its hash even though its id does not move. ``elements_changed`` /
``relationships_changed`` are real, measured counts, distinct from
``elements_added`` / ``elements_removed`` and their relationship
counterparts (id-set membership, not content).

Follows the ``db_session`` / ``make_org`` / ``tenant_ctx`` fixtures in
``tests/conftest.py``, the same shape the baseline-drift tenancy test file
uses.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
def _reset_monitoring_state():
    """Clear the module-level per-tenant cache before and after every test.

    ArchitectureMonitoringService's tenant-state cache lives for the life of the process,
    not the life of a request, so one test's cached baselines/alerts must
    not leak into the next test's fresh service instances.
    """
    from app.modules.architecture.services.architecture_monitoring_service import (
        ArchitectureMonitoringService,
    )

    ArchitectureMonitoringService.reset_state()
    yield
    ArchitectureMonitoringService.reset_state()


def _seed_health_bearing_capability(db_session, org_id, suffix):
    """One ArchiMateElement + BusinessCapability with a set maturity level.

    Without this, ``_capture_health_snapshot`` reports ``average_health``
    as ``None`` (an honest absence, not a fabricated 0) on both baseline
    and current sides, and ``_analyze_health_drift``'s ``current - baseline``
    then subtracts ``None - None`` -- the same reason
    ``test_architecture_monitoring_tenancy.py`` seeds one of these first.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_capabilities import BusinessCapability

    element = ArchiMateElement(
        name=f"Health-bearing capability {suffix}",
        type="Capability",
        layer="Strategy",
        organization_id=org_id,
    )
    db_session.add(element)
    db_session.flush()
    db_session.add(
        BusinessCapability(
            name=f"Health-bearing capability {suffix}",
            organization_id=org_id,
            level=1,
            archimate_element_id=element.id,
            current_maturity_level=3,
        )
    )
    db_session.flush()
    return element


def _make_unified_capability(db_session, org_id, suffix, current_maturity_level=3):
    """A UnifiedCapability row -- the maturity-regression alert's own source
    (_capture_capabilities_snapshot / _analyze_capability_drift), distinct
    from the BusinessCapability row _capture_health_snapshot reads.
    """
    from app.models.unified_capability import UnifiedCapability

    cap = UnifiedCapability(
        name=f"Cap {suffix}",
        code=f"CAP-{suffix}",
        organization_id=org_id,
        level=1,
        current_maturity_level=current_maturity_level,
    )
    db_session.add(cap)
    db_session.flush()
    return cap


def _element(db_session, org_id, suffix, label="El"):
    from app.models.archimate_core import ArchiMateElement

    element = ArchiMateElement(
        name=f"{label} {suffix}",
        type="ApplicationComponent",
        layer="Application",
        organization_id=org_id,
    )
    db_session.add(element)
    db_session.flush()
    return element


def _relationship(db_session, org_id, source, target):
    from app.models.archimate_core import ArchiMateRelationship

    rel = ArchiMateRelationship(
        type="serving",
        source_id=source.id,
        target_id=target.id,
        organization_id=org_id,
    )
    db_session.add(rel)
    db_session.flush()
    return rel


def _alert_count(db_session, org_id):
    return db_session.execute(
        text("SELECT COUNT(*) FROM monitoring_alerts WHERE organization_id = :org_id"),
        {"org_id": org_id},
    ).scalar()


# ----------------------------------------------------- (1) baseline JSON


def test_group1_persisted_json_carries_model_key_scoped_to_org(
    db_session, make_org, tenant_ctx
):
    from app.models.policy_monitoring import MonitoringBaseline
    from app.modules.architecture.services.architecture_monitoring_service import (
        ArchitectureMonitoringService,
    )

    suffix = uuid.uuid4().hex[:8]
    org_a, org_b = make_org("op1-g1-a"), make_org("op1-g1-b")

    _seed_health_bearing_capability(db_session, org_a.id, suffix)
    element_a = _element(db_session, org_a.id, suffix, "A")
    element_b = _element(db_session, org_b.id, suffix, "B")

    with tenant_ctx(org_a.id):
        service_a = ArchitectureMonitoringService(org_a.id)
        result = service_a.capture_baseline(name="A baseline", created_by="tester-a")
        assert result["success"] is True, result
        baseline_id = result["baseline"]["id"]

        row = MonitoringBaseline.query.filter_by(
            baseline_id=baseline_id, organization_id=org_a.id
        ).first()
        snapshot = json.loads(row.snapshot_data)

        assert "model" in snapshot
        element_ids = set(snapshot["model"]["elements"].keys())
        assert str(element_a.id) in element_ids
        assert str(element_b.id) not in element_ids


# ------------------------------------------- (2) + (3) cross-tenant model diff


def test_group2_edits_in_one_org_never_appear_in_the_others_comparison(
    db_session, make_org, tenant_ctx
):
    from app.modules.architecture.services.architecture_monitoring_service import (
        ArchitectureMonitoringService,
    )

    suffix = uuid.uuid4().hex[:8]
    org_a, org_b = make_org("op1-g2-a"), make_org("op1-g2-b")

    _seed_health_bearing_capability(db_session, org_a.id, suffix)
    a1 = _element(db_session, org_a.id, suffix, "A1")

    _seed_health_bearing_capability(db_session, org_b.id, suffix)
    b1 = _element(db_session, org_b.id, suffix, "B1")
    b2 = _element(db_session, org_b.id, suffix, "B2")
    b_rel = _relationship(db_session, org_b.id, b1, b2)

    with tenant_ctx(org_a.id):
        service_a = ArchitectureMonitoringService(org_a.id)
        baseline_a = service_a.capture_baseline(name="A baseline", created_by="tester-a")
        assert baseline_a["success"] is True, baseline_a
        baseline_a_id = baseline_a["baseline"]["id"]

    ArchitectureMonitoringService.reset_state(org_a.id)

    # Edits happen in B only: a new element, a new relationship, an existing
    # element renamed and re-layered, and an existing relationship's own
    # properties changed.
    with tenant_ctx(org_b.id):
        service_b = ArchitectureMonitoringService(org_b.id)
        service_b.capture_baseline(name="B baseline", created_by="tester-b")
    b3 = _element(db_session, org_b.id, suffix, "B3")
    _relationship(db_session, org_b.id, b2, b3)
    b1.name, b1.layer = f"B1 renamed {suffix}", "Business"
    b_rel.connection_spec = {"changed": True}
    db_session.flush()

    ArchitectureMonitoringService.reset_state(org_b.id)

    with tenant_ctx(org_a.id):
        service_a = ArchitectureMonitoringService(org_a.id)
        analysis = service_a.compare_to_baseline(baseline_a_id)

    md = analysis.model_drift
    # B's edits are invisible to A: every measured count is a genuine zero.
    assert md["elements_changed"] == 0
    assert md["elements_added"] == 0
    assert md["relationships_changed"] == 0
    assert md["relationships_added"] == 0
    assert md["changed_element_ids"] == []
    assert md["added_element_ids"] == []
    assert md["added_relationship_ids"] == []
    assert str(a1.id) not in md.get("removed_element_ids", [])


def test_group3_the_same_edits_in_its_own_org_are_measured_with_ids(
    db_session, make_org, tenant_ctx
):
    from app.modules.architecture.services.architecture_monitoring_service import (
        ArchitectureMonitoringService,
    )

    suffix = uuid.uuid4().hex[:8]
    org_a = make_org("op1-g3-a")

    _seed_health_bearing_capability(db_session, org_a.id, suffix)
    a1 = _element(db_session, org_a.id, suffix, "A1")
    a2 = _element(db_session, org_a.id, suffix, "A2")
    a_rel = _relationship(db_session, org_a.id, a1, a2)

    with tenant_ctx(org_a.id):
        service_a = ArchitectureMonitoringService(org_a.id)
        baseline_a = service_a.capture_baseline(name="A baseline", created_by="tester-a")
        assert baseline_a["success"] is True, baseline_a
        baseline_a_id = baseline_a["baseline"]["id"]

    # The same shape of edit as group 2, but inside A this time: one new
    # element, one new relationship, one existing element renamed and
    # re-layered, one existing relationship's own properties changed.
    a3 = _element(db_session, org_a.id, suffix, "A3")
    new_rel = _relationship(db_session, org_a.id, a1, a3)
    a1.name, a1.layer = f"A1 renamed {suffix}", "Business"
    a_rel.connection_spec = {"changed": True}
    db_session.flush()

    ArchitectureMonitoringService.reset_state(org_a.id)

    with tenant_ctx(org_a.id):
        service_a = ArchitectureMonitoringService(org_a.id)
        analysis = service_a.compare_to_baseline(baseline_a_id)

    md = analysis.model_drift
    assert md["elements_added"] == 1
    assert md["relationships_added"] == 1
    assert md["added_element_ids"] == [str(a3.id)]
    assert md["added_relationship_ids"] == [str(new_rel.id)]
    # A rename and a re-layer of an existing element, and a properties
    # change of an existing relationship, are both real in-place edits: the
    # content hash differs even though the id does not move.
    assert md["elements_changed"] == 1
    assert md["changed_element_ids"] == [str(a1.id)]
    assert md["relationships_changed"] == 1
    assert md["changed_relationship_ids"] == [str(a_rel.id)]
    assert a2.id is not None  # a2 present in both snapshots, unchanged


# --------------------------------------------------- (4) no baseline at all


def test_group4_no_baseline_raises_lookuperror_and_analyze_drift_keeps_old_shape(
    db_session, make_org, tenant_ctx
):
    from app.modules.architecture.services.architecture_monitoring_service import (
        ArchitectureMonitoringService,
    )

    org_b = make_org("op1-g4-b")

    with tenant_ctx(org_b.id):
        service_b = ArchitectureMonitoringService(org_b.id)

        with pytest.raises(LookupError, match="No valid baseline for comparison"):
            service_b.compare_to_baseline()

        result = service_b.analyze_drift()
        assert result == {"success": False, "error": "No valid baseline for comparison"}


# ------------------------------------------- (5) pre-model-dimension baseline


def test_group5_baseline_without_a_model_key_yields_reason_and_null_counts(
    db_session, make_org, tenant_ctx
):
    from app.models.policy_monitoring import MonitoringBaseline
    from app.modules.architecture.services.architecture_monitoring_service import (
        ArchitectureMonitoringService,
    )

    suffix = uuid.uuid4().hex[:8]
    org = make_org("op1-g5")
    _seed_health_bearing_capability(db_session, org.id, suffix)

    # Written directly, the shape a baseline captured before this dimension
    # existed persisted: no "model" key in its JSON blob at all.
    row = MonitoringBaseline(
        baseline_id=f"bl-premod-{suffix}",
        organization_id=org.id,
        name="Pre-model baseline",
        snapshot_data=json.dumps(
            {
                "capabilities": [],
                "coverage": {},
                "health": {},
                "gaps": [],
                "vendors": [],
                "metadata": {},
            }
        ),
        checksum="deadbeef",
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()

    with tenant_ctx(org.id):
        service = ArchitectureMonitoringService(org.id)
        analysis = service.compare_to_baseline(row.baseline_id)

    md = analysis.model_drift
    assert md["reason"] == "baseline_lacks_model_snapshot"
    assert md["elements_changed"] is None
    assert md["relationships_added"] is None
    assert md["relationships_removed"] is None
    assert md["derived_recomputed"] is None
    assert md["changed_element_ids"] == []
    assert md["added_relationship_ids"] == []
    assert md["removed_relationship_ids"] == []


# --------------------------------------- (6) the seam writes nothing, analyze_drift does


def test_group6_compare_to_baseline_writes_nothing_analyze_drift_persists_what_it_found(
    db_session, make_org, tenant_ctx
):
    from app.modules.architecture.services.architecture_monitoring_service import (
        ArchitectureMonitoringService,
    )

    suffix = uuid.uuid4().hex[:8]
    org_a = make_org("op1-g6-a")

    _seed_health_bearing_capability(db_session, org_a.id, suffix)
    cap = _make_unified_capability(db_session, org_a.id, suffix, current_maturity_level=3)

    with tenant_ctx(org_a.id):
        service_a = ArchitectureMonitoringService(org_a.id)
        baseline_a = service_a.capture_baseline(
            name="A baseline", created_by="tester-a", set_as_active=True
        )
        assert baseline_a["success"] is True, baseline_a
        baseline_a_id = baseline_a["baseline"]["id"]

    # A real maturity regression -- capture_baseline saw current_maturity_level
    # == 3; this drop makes _generate_drift_alerts produce at least one alert.
    cap.current_maturity_level = 1
    db_session.flush()

    alerts_before = _alert_count(db_session, org_a.id)

    with tenant_ctx(org_a.id):
        service_a = ArchitectureMonitoringService(org_a.id)
        state_alerts_before = dict(service_a._state.alerts)
        last_scan_time_before = service_a._state.last_scan_time

        analysis = service_a.compare_to_baseline(baseline_a_id)
        assert analysis.total_drifts >= 1, "need at least one alert for this proof to mean anything"

        alerts_after_compare = _alert_count(db_session, org_a.id)
        assert alerts_after_compare == alerts_before
        assert dict(service_a._state.alerts) == state_alerts_before
        assert service_a._state.last_scan_time == last_scan_time_before

        result = service_a.analyze_drift(baseline_a_id)
        assert result["success"] is True, result
        new_alert_count = len(result["alerts"])
        assert new_alert_count >= 1

        alerts_after_analyze = _alert_count(db_session, org_a.id)
        assert alerts_after_analyze == alerts_before + new_alert_count
        assert len(service_a._state.alerts) == len(state_alerts_before) + new_alert_count
        # analyze_drift does not touch last_scan_time either -- trigger_scan
        # is the only caller that sets it, unchanged by this task.
        assert service_a._state.last_scan_time == last_scan_time_before


# ------------------------------------------------- (7) derived_recomputed


def test_group7_derived_recomputed_reflects_a_real_derivation_run(
    db_session, make_org, tenant_ctx
):
    from app.modules.architecture.services.architecture_monitoring_service import (
        ArchitectureMonitoringService,
    )
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    suffix = uuid.uuid4().hex[:8]
    org = make_org("op1-g7")
    _seed_health_bearing_capability(db_session, org.id, suffix)
    src = _element(db_session, org.id, suffix, "Src")
    tgt = _element(db_session, org.id, suffix, "Tgt")

    with tenant_ctx(org.id):
        service = ArchitectureMonitoringService(org.id)
        baseline = service.capture_baseline(name="Baseline", created_by="tester")
        assert baseline["success"] is True, baseline
        baseline_id = baseline["baseline"]["id"]

        # No derivation has run since capture: 0, a completed comparison
        # finding no recomputation, not a fabricated placeholder.
        analysis_before = service.compare_to_baseline(baseline_id)
        assert analysis_before.model_drift["derived_recomputed"] == 0

    fact = DerivedRelationship(
        organization_id=org.id,
        source_element_id=src.id,
        target_element_id=tgt.id,
        derived_type="realizes",
        rule_id="op1-test-rule",
        chain=[999999],
        chain_element_ids=[src.id, tgt.id],
        depth=1,
        engine_version="op1-test-1",
        computed_at=datetime.utcnow() + timedelta(seconds=1),
    )
    db_session.add(fact)
    db_session.flush()

    ArchitectureMonitoringService.reset_state(org.id)

    with tenant_ctx(org.id):
        service = ArchitectureMonitoringService(org.id)
        analysis_after = service.compare_to_baseline(baseline_id)
        md_after = analysis_after.model_drift
        assert md_after["derived_recomputed"] == 1
        assert md_after["derived_count_delta"] == 1
        assert md_after["stale_count_delta"] == 0
        assert md_after["newly_stale_ids"] == []

    # The fact goes stale: derived_count drops back to the baseline's 0 (an
    # unchanged delta that alone would look like nothing happened) while
    # stale_count rises -- a bare computed_at comparison, or derived_count
    # alone, would both miss this.
    fact.stale = True
    fact.stale_since = datetime.utcnow()
    fact.stale_reason = "element_deleted"
    db_session.flush()

    ArchitectureMonitoringService.reset_state(org.id)

    with tenant_ctx(org.id):
        service = ArchitectureMonitoringService(org.id)
        analysis_stale = service.compare_to_baseline(baseline_id)
        md_stale = analysis_stale.model_drift
        assert md_stale["derived_count_delta"] == 0
        assert md_stale["stale_count_delta"] == 1
        assert md_stale["newly_stale_ids"] == [str(fact.id)]
        assert md_stale["resolved_stale_ids"] == []


# ------------------------------------------------------ (8) checksum


def test_group8_checksum_changes_when_the_model_snapshot_changes(
    db_session, make_org, tenant_ctx
):
    from app.modules.architecture.services.architecture_monitoring_service import (
        ArchitectureMonitoringService,
    )

    suffix = uuid.uuid4().hex[:8]
    org = make_org("op1-g8")
    _seed_health_bearing_capability(db_session, org.id, suffix)

    with tenant_ctx(org.id):
        service = ArchitectureMonitoringService(org.id)
        first = service.capture_baseline(
            name="First", created_by="tester", set_as_active=False
        )
        assert first["success"] is True, first
        checksum_1 = first["baseline"]["checksum"]

    # Capturing the identical, unchanged estate again must give the same
    # checksum: captured_at (wall-clock, different on every call) sits
    # outside the checksummed payload, so this is a real integrity proof,
    # not one that would pass even if the whole snapshot were hashed
    # including a value that always differs.
    ArchitectureMonitoringService.reset_state(org.id)
    with tenant_ctx(org.id):
        service = ArchitectureMonitoringService(org.id)
        repeat = service.capture_baseline(
            name="Repeat", created_by="tester", set_as_active=False
        )
        assert repeat["success"] is True, repeat
        checksum_repeat = repeat["baseline"]["checksum"]

    assert checksum_repeat == checksum_1

    # Only the model dimension changes between this capture and the first:
    # same capability catalogue, one more element.
    _element(db_session, org.id, suffix, "Extra")

    ArchitectureMonitoringService.reset_state(org.id)

    with tenant_ctx(org.id):
        service = ArchitectureMonitoringService(org.id)
        second = service.capture_baseline(
            name="Second", created_by="tester", set_as_active=False
        )
        assert second["success"] is True, second
        checksum_2 = second["baseline"]["checksum"]

    assert checksum_1 != checksum_2


# ---------------------------- (9) an edit outside the original five columns


def test_group9_a_field_outside_the_original_columns_still_counts_as_a_change(
    db_session, make_org, tenant_ctx
):
    """The content hash covers every mapped column bar identity and audit
    provenance, not a short, hand-picked field list -- so a real edit to a
    field the original five never looked at (an element's description, its
    properties and status together, or which element it sits under; a
    relationship's own label, description and access mode) is a measured
    one, never a zero.
    """
    from app.modules.architecture.services.architecture_monitoring_service import (
        ArchitectureMonitoringService,
    )

    suffix = uuid.uuid4().hex[:8]
    org = make_org("op1-g9")
    _seed_health_bearing_capability(db_session, org.id, suffix)

    el_description = _element(db_session, org.id, suffix, "Description")
    el_props_status = _element(db_session, org.id, suffix, "PropsStatus")
    el_child = _element(db_session, org.id, suffix, "Child")
    el_new_parent = _element(db_session, org.id, suffix, "NewParent")
    rel = _relationship(db_session, org.id, el_description, el_props_status)

    with tenant_ctx(org.id):
        service = ArchitectureMonitoringService(org.id)
        baseline = service.capture_baseline(name="G9 baseline", created_by="tester")
        assert baseline["success"] is True, baseline
        baseline_id = baseline["baseline"]["id"]

    # Four edits, none of them name, type, layer, custom_properties,
    # documentation, source_id, target_id or connection_spec -- the set the
    # content hash used to be limited to.
    el_description.description = f"Now documented {suffix}"
    el_props_status.properties = json.dumps({"tier": "gold"})
    el_props_status.status = "Approved"
    el_child.parent_id = el_new_parent.id
    rel.custom_label = f"custom {suffix}"
    rel.description = f"relationship note {suffix}"
    rel.access_mode = "write"
    db_session.flush()

    ArchitectureMonitoringService.reset_state(org.id)

    with tenant_ctx(org.id):
        service = ArchitectureMonitoringService(org.id)
        analysis = service.compare_to_baseline(baseline_id)

    md = analysis.model_drift
    assert md["elements_changed"] == 3
    assert set(md["changed_element_ids"]) == {
        str(el_description.id),
        str(el_props_status.id),
        str(el_child.id),
    }
    assert md["relationships_changed"] == 1
    assert md["changed_relationship_ids"] == [str(rel.id)]
    # el_new_parent itself was not edited: it must not be swept in as changed
    # just because another element now points at it.
    assert str(el_new_parent.id) not in md["changed_element_ids"]

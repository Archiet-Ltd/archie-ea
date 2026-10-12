"""``IntelligenceQueryService.operational_for_element`` and
``GET /api/v1/intelligence/operational/<element_id>`` (the Operational
lens): "what has changed around this element since we last checked, and is
anything out of date?"

Twelve groups, numbered for cross-reference. Grouped by what each needs, so
the file grows in step with the commits that add it:

  (7)                     the adapter contract alone -- no service, no route
  (2)-(6), (8)-(10)       the service method, called directly (no route yet)
  (1), (11), (12)         the route

Fixtures (``app``, ``db_session``, ``make_org``, ``client``, ``login_as``,
``tenant_ctx``) come from this module's own ``conftest.py``, which re-imports
them from ``tests/conftest.py``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import pytest


def _user(db_session, org, *, email=None):
    from app.models.user import Role, User

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").first()

    user = User(
        email=email or f"op2-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        role=admin_role,
        is_org_admin=True,
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _element(db_session, org_id, name_hint, type_="ApplicationComponent", layer="application"):
    from app.models import ArchiMateElement

    row = ArchiMateElement(
        name=f"E-{name_hint}", type=type_, layer=layer, organization_id=org_id
    )
    db_session.add(row)
    db_session.flush()
    return row


def _relationship(db_session, org_id, source, target, type_="Serving"):
    from app.models import ArchiMateRelationship

    row = ArchiMateRelationship(
        source_id=source.id, target_id=target.id, type=type_, organization_id=org_id
    )
    db_session.add(row)
    db_session.flush()
    return row


def _seed_health_bearing_capability(db_session, org_id):
    """One ArchiMateElement + BusinessCapability with a set maturity level,
    the same fixture tests/test_drift_model_dimension.py and
    tests/test_architecture_monitoring_tenancy.py seed first: without it,
    ``_capture_health_snapshot`` reports ``average_health`` as ``None`` (an
    honest absence, not a fabricated 0) on both baseline and current sides,
    and ``_analyze_health_drift``'s ``current - baseline`` then subtracts
    ``None`` from a number -- a real comparison must have something to
    compare, same reason the model-dimension tests need it before any
    ``compare_to_baseline`` call."""
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_capabilities import BusinessCapability

    suffix = uuid.uuid4().hex[:8]
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


def _insert_stale_derived_row(db_session, org_id, source, target, **overrides):
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    params = dict(
        organization_id=org_id,
        source_element_id=source.id,
        target_element_id=target.id,
        derived_type="Association",
        rule_id="fallback:X:X",
        chain=[],
        chain_element_ids=[source.id, target.id],
        depth=1,
        confidence="1.00",
        provenance="derivation",
        engine_version="1.0.0",
        computed_at=datetime.utcnow(),
        stale=True,
        stale_since=datetime.utcnow(),
        stale_reason="element_deleted",
    )
    params.update(overrides)
    row = DerivedRelationship(**params)
    db_session.add(row)
    db_session.flush()
    return row


def _insert_alert(db_session, org_id, element, *, acknowledged=False, suffix=None):
    from app.models.policy_monitoring import MonitoringAlert

    row = MonitoringAlert(
        organization_id=org_id,
        alert_id=f"alrt-{suffix or uuid.uuid4().hex[:10]}",
        alert_type="new_gap",
        severity="warning",
        title="Test alert",
        affected_element_id=element.id,
        acknowledged=acknowledged,
        created_at=datetime.utcnow(),
    )
    db_session.add(row)
    db_session.flush()
    return row


def _capture_baseline(tenant_ctx, org_id, *, name="Baseline"):
    from app.modules.architecture.services.architecture_monitoring_service import (
        ArchitectureMonitoringService,
    )

    with tenant_ctx(org_id):
        service = ArchitectureMonitoringService(org_id)
        result = service.capture_baseline(name=name, created_by="tester")
        assert result["success"] is True, result
        return result["baseline"]["id"]


def _run_derivation(app, org_id):
    from app.modules.intelligence.services.derivation_runner import DerivationRunner

    with app.app_context():
        DerivationRunner().run_and_persist(org_id, trigger="on_demand")


def _operational(tenant_ctx, org_id, element_id, **kwargs):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with tenant_ctx(org_id):
        return IntelligenceQueryService().operational_for_element(
            element_id, organization_id=org_id, **kwargs
        )


@pytest.fixture(autouse=True)
def _reset_monitoring_state():
    """Clear ``ArchitectureMonitoringService``'s module-level per-tenant
    cache before and after every test -- it lives for the life of the
    process, not the life of a request."""
    from app.modules.architecture.services.architecture_monitoring_service import (
        ArchitectureMonitoringService,
    )

    ArchitectureMonitoringService.reset_state()
    yield
    ArchitectureMonitoringService.reset_state()


# --- (7) the adapter contract, alone -----------------------------------------


def test_group7_no_adapter_registered_means_feed_not_connected():
    import re

    from app.modules.intelligence.services.operational_sources import (
        ChangeRecord,
        ExternalRef,
        IncidentRecord,
        OperationalSourceAdapter,
        TelemetrySample,
        registered_adapter,
    )

    assert registered_adapter(1) is None
    assert OperationalSourceAdapter is not None
    assert ExternalRef and IncidentRecord and ChangeRecord and TelemetrySample

    # No class implementing the adapter contract exists anywhere in this
    # module -- only the Protocol itself.
    source_path = "app/modules/intelligence/services/operational_sources.py"
    with open(source_path, encoding="utf-8") as fh:
        source_text = fh.read()
    class_defs = re.findall(
        r"^class\s+([A-Za-z0-9_]*Adapter[A-Za-z0-9_]*)\b", source_text, re.MULTILINE
    )
    assert class_defs == ["OperationalSourceAdapter"]


# --- (2)-(6), (8)-(10) the service method, called directly ------------------


def test_group2_own_element_shows_none_of_the_other_tenants_data(
    app, db_session, make_org, tenant_ctx
):
    org_a = make_org("op2-iso2-a")
    org_b = make_org("op2-iso2-b")
    a = _element(db_session, org_a.id, "A")
    a2 = _element(db_session, org_a.id, "A2")
    b = _element(db_session, org_b.id, "B")
    b2 = _element(db_session, org_b.id, "B2")
    _relationship(db_session, org_a.id, a, a2)
    _relationship(db_session, org_b.id, b, b2)
    db_session.commit()

    # A: an unacknowledged alert and a stale derived row on its own element.
    _insert_alert(db_session, org_a.id, a, suffix="a")
    _insert_stale_derived_row(db_session, org_a.id, a, a2)
    db_session.commit()

    # B: a real baseline (with the model dimension) and a completed run, so
    # its answer actually attempts to compute alerts/stale rows rather than
    # trivially short-circuiting on "no baseline"/"no run".
    _seed_health_bearing_capability(db_session, org_b.id)
    db_session.commit()
    _capture_baseline(tenant_ctx, org_b.id, name="B baseline")
    _run_derivation(app, org_b.id)

    data = _operational(tenant_ctx, org_b.id, b.id)

    assert data["moved_since_baseline"]["alerts"] == []
    assert data["stale_derivations"]["rows"] == []
    assert data["stale_derivations"]["count"] == 0
    assert data["stale_derivations"]["reason"] is None
    # b/b2 are wired to each other, so neither is orphaned -- and org A's
    # data (a different tenant) can never reach this list regardless.
    assert data["consistency_findings"] == []


def test_group3_state_holds_exactly_two_keys_after_both_orgs_answer(
    app, db_session, make_org, tenant_ctx
):
    from app.modules.architecture.services.architecture_monitoring_service import _STATE

    org_a = make_org("op2-iso3-a")
    org_b = make_org("op2-iso3-b")
    a = _element(db_session, org_a.id, "A")
    b = _element(db_session, org_b.id, "B")
    db_session.commit()

    _operational(tenant_ctx, org_a.id, a.id)
    _operational(tenant_ctx, org_b.id, b.id)

    assert set(_STATE.keys()) == {org_a.id, org_b.id}


def test_group4_no_baseline_yields_no_baseline_captured_and_null_counts(
    app, db_session, make_org, tenant_ctx
):
    org = make_org("op2-fab4")
    a = _element(db_session, org.id, "A")
    db_session.commit()

    data = _operational(tenant_ctx, org.id, a.id)

    assert data["baseline"]["reason"] == "no_baseline_captured"
    msb = data["moved_since_baseline"]
    assert msb["reason"] == "no_baseline_captured"
    assert msb["element_changed"] is None
    assert msb["relationships_added"] is None
    assert msb["relationships_removed"] is None
    assert msb["derived_recomputed"] is None
    assert msb["alerts"] == []
    assert "no_baseline_captured" in data["reasons"]


def test_group5_baseline_without_model_snapshot_yields_reason_and_null_counts(
    app, db_session, make_org, tenant_ctx
):
    from app.models.policy_monitoring import MonitoringBaseline

    org = make_org("op2-fab5")
    a = _element(db_session, org.id, "A")
    _seed_health_bearing_capability(db_session, org.id)
    db_session.commit()

    suffix = uuid.uuid4().hex[:8]
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
    db_session.commit()

    data = _operational(tenant_ctx, org.id, a.id)

    assert data["baseline"]["reason"] is None
    msb = data["moved_since_baseline"]
    assert msb["reason"] == "baseline_lacks_model_snapshot"
    assert msb["element_changed"] is None
    assert msb["relationships_added"] is None
    assert msb["relationships_removed"] is None
    assert msb["derived_recomputed"] is None
    assert "baseline_lacks_model_snapshot" in data["reasons"]


def test_group6_stale_derivations_not_computed_then_measured(
    app, db_session, make_org, tenant_ctx
):
    org = make_org("op2-fab6")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    db_session.commit()

    data = _operational(tenant_ctx, org.id, a.id)
    assert data["stale_derivations"]["count"] is None
    assert data["stale_derivations"]["reason"] == "derivation_not_computed"
    assert "derivation_not_computed" in data["reasons"]

    _run_derivation(app, org.id)

    data2 = _operational(tenant_ctx, org.id, a.id)
    assert data2["stale_derivations"]["count"] == 0
    assert data2["stale_derivations"]["reason"] is None

    _insert_stale_derived_row(db_session, org.id, a, b)
    db_session.commit()

    data3 = _operational(tenant_ctx, org.id, a.id)
    assert data3["stale_derivations"]["count"] == 1
    assert data3["stale_derivations"]["reason"] is None
    row = data3["stale_derivations"]["rows"][0]
    assert row["truth_class"] == "derived_intelligence"
    assert row["stale_reason"] == "derivation_stale"


def test_group8_probe_failure_is_reported_and_the_rest_of_the_answer_is_intact(
    app, db_session, make_org, tenant_ctx, monkeypatch
):
    from app.modules.monitoring.services.health_service import HealthService

    org = make_org("op2-fab8")
    a = _element(db_session, org.id, "A")
    db_session.commit()

    def _raise():
        raise RuntimeError("boom")

    monkeypatch.setattr(HealthService, "check_database", staticmethod(_raise))
    data = _operational(tenant_ctx, org.id, a.id)
    assert data["probe"]["reason"] == "source_unavailable"
    assert data["baseline"] is not None
    assert data["moved_since_baseline"] is not None
    assert data["stale_derivations"] is not None
    assert data["external"] is not None
    assert isinstance(data["consistency_findings"], list)
    monkeypatch.undo()

    def _unhealthy():
        return {"status": "unhealthy", "response_time_ms": None}

    monkeypatch.setattr(HealthService, "check_database", staticmethod(_unhealthy))
    data2 = _operational(tenant_ctx, org.id, a.id)
    assert data2["probe"]["reason"] == "source_unavailable"
    assert data2["baseline"] is not None
    assert data2["moved_since_baseline"] is not None
    assert data2["stale_derivations"] is not None
    assert data2["external"] is not None


def test_group9_healthy_probe_baseline_no_change_is_measured(
    app, db_session, make_org, tenant_ctx
):
    org = make_org("op2-fab9")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    _relationship(db_session, org.id, a, b)
    _seed_health_bearing_capability(db_session, org.id)
    db_session.commit()

    _capture_baseline(tenant_ctx, org.id, name="No-change baseline")

    data = _operational(tenant_ctx, org.id, a.id)

    assert data["probe"]["reason"] is None
    msb = data["moved_since_baseline"]
    assert msb["reason"] is None
    assert msb["element_changed"] is False
    # The one relationship touching A was already present when the baseline
    # was captured -- a genuine comparison against the seeded state finds
    # nothing new.
    assert msb["relationships_added"] == 0

    # No adapter is registered (module-level test_group7 covers the
    # contract itself) -- the answer's own external block carries the
    # absence honestly, not as an empty list standing in for "nothing
    # happened".
    assert data["external"] == {
        "incidents": None,
        "changes": None,
        "telemetry": None,
        "reason": "feed_not_connected",
    }
    assert "feed_not_connected" in data["reasons"]


def test_group10_the_read_never_writes(app, db_session, make_org, tenant_ctx):
    from app.models.policy_monitoring import MonitoringAlert
    from app.modules.architecture.services.architecture_monitoring_service import _STATE

    org = make_org("op2-fab10")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    _relationship(db_session, org.id, a, b)
    _seed_health_bearing_capability(db_session, org.id)
    db_session.commit()

    # A real baseline, so each call genuinely runs compare_to_baseline --
    # the seam this write-check exists to prove never persists anything,
    # unlike analyze_drift/trigger_scan.
    _capture_baseline(tenant_ctx, org.id, name="op2-fab10 baseline")

    before_count = MonitoringAlert.query.filter_by(organization_id=org.id).count()

    for _ in range(3):
        data = _operational(tenant_ctx, org.id, a.id)
        assert data["element"]["id"] == a.id
        assert data["moved_since_baseline"]["reason"] is None

    after_count = MonitoringAlert.query.filter_by(organization_id=org.id).count()
    assert after_count == before_count == 0
    assert len(_STATE[org.id].alerts) == 0


# --- (1), (11), (12) the route -----------------------------------------------


def test_group1_cross_tenant_element_404_is_identical_to_absent_id(
    app, db_session, make_org, client, login_as
):
    org_a = make_org("op2-iso-a")
    org_b = make_org("op2-iso-b")
    user_b = _user(db_session, org_b)
    a = _element(db_session, org_a.id, "A")
    db_session.commit()

    login_as(client, user_b)
    cross_tenant_resp = client.get(f"/api/v1/intelligence/operational/{a.id}")

    login_as(client, user_b)
    absent_resp = client.get("/api/v1/intelligence/operational/999999999")

    assert cross_tenant_resp.status_code == 404
    assert absent_resp.status_code == 404
    # meta.timestamp/request_id are per-request and expected to differ --
    # the stable, comparable part is the error body itself.
    assert cross_tenant_resp.get_json()["error"] == absent_resp.get_json()["error"]
    assert cross_tenant_resp.get_json()["success"] is False
    assert absent_resp.get_json()["success"] is False


def test_group11_include_stale_param(app, db_session, make_org, client, login_as, tenant_ctx):
    org = make_org("op2-fab11")
    user = _user(db_session, org)
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    db_session.commit()

    login_as(client, user)
    bad = client.get(f"/api/v1/intelligence/operational/{a.id}?include_stale=maybe")
    assert bad.status_code == 400

    _run_derivation(app, org.id)
    _insert_stale_derived_row(db_session, org.id, a, b)
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/operational/{a.id}?include_stale=false")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["stale_derivations"]["rows"] == []
    assert isinstance(data["stale_derivations"]["count"], int)
    assert data["stale_derivations"]["count"] == 1


def test_group12_latency_label_recorded(app, db_session, make_org, client, login_as, caplog):
    import logging

    org = make_org("op2-fab12")
    user = _user(db_session, org)
    a = _element(db_session, org.id, "A")
    db_session.commit()

    login_as(client, user)
    with caplog.at_level(logging.INFO, logger="archie.intelligence.oa2"):
        resp = client.get(f"/api/v1/intelligence/operational/{a.id}")
    assert resp.status_code == 200
    assert any(
        "intelligence.query_latency" in message and "operational_for_element" in message
        for message in caplog.messages
    )

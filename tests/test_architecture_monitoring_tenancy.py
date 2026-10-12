"""Architecture monitoring (baselines, alerts, snapshots, drift) is per organisation.

Before this fix ``monitoring_baselines``/``monitoring_alerts`` carried no
``organization_id`` at all, the service cached every baseline and alert in a
single process-wide dict shared by every tenant's instance, and its capability
and vendor snapshots read the whole catalogue with ``.query.all()``. This file
proves two organisations cannot see, mutate or be deactivated by each other's
monitoring data, that the per-tenant cache is actually per-tenant, that the
capability and vendor snapshots carry an explicit predicate (so a call with no
Flask request on the stack is scoped too), and that the canonical backfill
command derives or defers ``organization_id`` on pre-existing rows correctly.

Follows the ``db_session`` / ``make_org`` / ``tenant_ctx`` fixtures in
``tests/conftest.py`` -- see ``tests/test_arb_ea_tenant_isolation.py`` for the
pattern this repo standardizes on, and ``tests/test_backfill_layer_tenancy_
roadmap_tasks.py`` for the backfill test's shape.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

# No module-level usefixtures("db_session"): the backfill test below calls
# repair_layer_tenancy() twice and needs real commits between the two calls
# (see its docstring), so it deliberately does not take db_session. Every
# other test in this module requests it directly instead.


@pytest.fixture(autouse=True)
def _reset_monitoring_state():
    """Clear the module-level per-tenant cache before and after every test.

    ArchitectureMonitoringService._STATE lives for the life of the process,
    not the life of a request, so one test's cached baselines/alerts must not
    leak into the next test's fresh service instances.
    """
    from app.modules.architecture.services.architecture_monitoring_service import (
        ArchitectureMonitoringService,
    )

    ArchitectureMonitoringService.reset_state()
    yield
    ArchitectureMonitoringService.reset_state()


def _make_user(db_session, org_id, label):
    """Same three-line construction tests/test_arb_ea_tenant_isolation.py uses.

    There is no shared user-factory fixture on this branch's base; each module
    that needs one still builds its own inline.
    """
    from app.models.user import User

    suffix = uuid.uuid4().hex[:10]
    user = User(
        email=f"{label}-{suffix}@example.com",
        first_name="Test",
        last_name=label,
        organization_id=org_id,
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


# --------------------------------------------------------------- (1) baselines


def test_baseline_captured_for_one_org_is_invisible_to_the_other(
    db_session, make_org, tenant_ctx
):
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_capabilities import BusinessCapability
    from app.modules.architecture.services.architecture_monitoring_service import (
        ArchitectureMonitoringService,
    )

    org_a, org_b = make_org("dr3-bl-a"), make_org("dr3-bl-b")

    # CapabilityHealthService withholds average_health as None (not a
    # fabricated 0) when nothing is assessable, and the drift maths this
    # brief does not touch subtracts baseline from current unconditionally --
    # so a capability whose maturity is set (making it assessable) is needed
    # here for analyze_drift below to have a real number on both sides rather
    # than None - None.
    element_a = ArchiMateElement(
        name="Health-bearing capability", type="Capability", layer="Strategy",
        organization_id=org_a.id,
    )
    db_session.add(element_a)
    db_session.flush()
    db_session.add(
        BusinessCapability(
            name="Health-bearing capability",
            organization_id=org_a.id,
            level=1,
            archimate_element_id=element_a.id,
            current_maturity_level=3,
        )
    )
    db_session.flush()

    with tenant_ctx(org_a.id):
        service_a = ArchitectureMonitoringService(org_a.id)
        result = service_a.capture_baseline(name="A baseline", created_by="tester-a")
        assert result["success"] is True, result
        baseline_id = result["baseline"]["id"]

        listed = service_a.list_baselines()
        assert baseline_id in {b["id"] for b in listed["baselines"]}

        fetched = service_a.get_baseline(baseline_id)
        assert fetched["success"] is True

        drift = service_a.analyze_drift(baseline_id)
        assert drift["success"] is True

    ArchitectureMonitoringService.reset_state()

    with tenant_ctx(org_b.id):
        service_b = ArchitectureMonitoringService(org_b.id)

        listed_b = service_b.list_baselines()
        assert listed_b["baselines"] == []
        assert baseline_id not in {b["id"] for b in listed_b["baselines"]}

        fetched_b = service_b.get_baseline(baseline_id)
        assert fetched_b == {"success": False, "error": "Baseline not found"}


# ------------------------------------------------------------------- (2) alerts


def test_alert_raised_for_one_org_is_invisible_to_the_other_and_foreign_ack_is_a_noop(
    db_session, make_org, tenant_ctx
):
    from app.models.policy_monitoring import MonitoringAlert
    from app.modules.architecture.services.architecture_monitoring_service import (
        ArchitectureMonitoringService,
    )

    org_a, org_b = make_org("dr3-al-a"), make_org("dr3-al-b")

    row = MonitoringAlert(
        alert_id=f"alert-{uuid.uuid4().hex[:10]}",
        organization_id=org_a.id,
        alert_type="new_gap",
        severity="warning",
        title="Org A alert",
    )
    db_session.add(row)
    db_session.flush()
    alert_id = row.alert_id

    with tenant_ctx(org_a.id):
        service_a = ArchitectureMonitoringService(org_a.id)
        seen_a = service_a.get_alert(alert_id)
        assert seen_a["success"] is True

    ArchitectureMonitoringService.reset_state()

    with tenant_ctx(org_b.id):
        service_b = ArchitectureMonitoringService(org_b.id)

        seen_b = service_b.get_alert(alert_id)
        assert seen_b == {"success": False, "error": "Alert not found"}

        ack_result = service_b.acknowledge_alert(alert_id, acknowledged_by="user-b")
        assert ack_result == {"success": False, "error": "Alert not found"}

    # Raw SQL, deliberately outside any tenant_ctx block: a plain ORM refresh
    # here would still be caught by g.current_org_id left over from the block
    # above (Flask reuses the outer app context's g across nested
    # test_request_context blocks), which would filter it to org B and hide
    # org A's own row. The row's real database state is the assertion.
    row_state = db_session.execute(
        text("SELECT acknowledged, acknowledged_by FROM monitoring_alerts WHERE id = :id"),
        {"id": row.id},
    ).one()
    assert row_state.acknowledged is False
    assert row_state.acknowledged_by is None


# ------------------------------------------------------------- (3) activation


def test_activating_a_baseline_in_one_org_does_not_touch_the_others_active_baseline(
    db_session, make_org, tenant_ctx
):
    from app.modules.architecture.services.architecture_monitoring_service import (
        ArchitectureMonitoringService,
    )

    org_a, org_b = make_org("dr3-act-a"), make_org("dr3-act-b")

    with tenant_ctx(org_b.id):
        service_b = ArchitectureMonitoringService(org_b.id)
        result_b = service_b.capture_baseline(
            name="B baseline", created_by="tester-b", set_as_active=True
        )
        assert result_b["success"] is True, result_b
        baseline_b_id = result_b["baseline"]["id"]
        assert result_b["baseline"]["is_active"] is True

    ArchitectureMonitoringService.reset_state(org_b.id)

    with tenant_ctx(org_a.id):
        service_a = ArchitectureMonitoringService(org_a.id)
        first = service_a.capture_baseline(
            name="A baseline 1", created_by="tester-a", set_as_active=True
        )
        assert first["success"] is True, first
        # A second activation in A used to run MBModel.query.update(...) with
        # no organisation predicate, deactivating every tenant's active
        # baseline, including B's, captured above.
        second = service_a.capture_baseline(
            name="A baseline 2", created_by="tester-a", set_as_active=True
        )
        assert second["success"] is True, second
        assert second["baseline"]["is_active"] is True

    ArchitectureMonitoringService.reset_state(org_a.id)

    with tenant_ctx(org_b.id):
        fresh_b = ArchitectureMonitoringService(org_b.id)
        still_active = fresh_b.get_baseline(baseline_b_id)
        assert still_active["success"] is True
        assert still_active["baseline"]["is_active"] is True


# ---------------------------------------------------------------- (4) cache


def test_per_tenant_cache_is_isolated_and_state_holds_one_entry_per_org(
    db_session, make_org, tenant_ctx
):
    from app.models.policy_monitoring import MonitoringBaseline
    from app.modules.architecture.services.architecture_monitoring_service import (
        _STATE,
        ArchitectureMonitoringService,
    )

    org_a, org_b = make_org("dr3-cache-a"), make_org("dr3-cache-b")

    row_a = MonitoringBaseline(
        baseline_id=f"bl-{uuid.uuid4().hex[:10]}",
        organization_id=org_a.id,
        name="A cached baseline",
        snapshot_data="{}",
        checksum="deadbeef",
    )
    db_session.add(row_a)
    db_session.flush()

    with tenant_ctx(org_a.id):
        service_a = ArchitectureMonitoringService(org_a.id)
        assert row_a.baseline_id in service_a._state.baselines

    with tenant_ctx(org_b.id):
        service_b = ArchitectureMonitoringService(org_b.id)
        # A fresh org B instance must see none of org A's rows: no shared
        # class-level cache, only this org's entry in _STATE.
        assert service_b._state.baselines == {}

    assert set(_STATE.keys()) == {org_a.id, org_b.id}
    assert _STATE[org_a.id] is not _STATE[org_b.id]


# ------------------------------------------------------- (5) capability snapshot


def test_capability_snapshot_includes_own_and_reference_rows_excludes_other_org(
    db_session, make_org
):
    """No tenant_ctx here: the point is that the explicit predicate in the
    service, not the request-scoped do_orm_execute listener, is what scopes
    this read -- so a call with no Flask request on the stack is safe too.
    """
    from app.models.unified_capability import UnifiedCapability
    from app.modules.architecture.services.architecture_monitoring_service import (
        ArchitectureMonitoringService,
    )

    org_a, org_b = make_org("dr3-cap-a"), make_org("dr3-cap-b")
    suffix = uuid.uuid4().hex[:10]

    reference = UnifiedCapability(
        name=f"Reference {suffix}", code=f"REF-{suffix}", organization_id=None
    )
    own = UnifiedCapability(
        name=f"Own A {suffix}", code=f"A-{suffix}", organization_id=org_a.id
    )
    foreign = UnifiedCapability(
        name=f"Own B {suffix}", code=f"B-{suffix}", organization_id=org_b.id
    )
    db_session.add_all([reference, own, foreign])
    db_session.flush()

    service_a = ArchitectureMonitoringService(org_a.id)
    snapshot = service_a._capture_capabilities_snapshot()
    ids = {row["id"] for row in snapshot}

    assert reference.id in ids
    assert own.id in ids
    assert foreign.id not in ids


def test_reference_capability_mapping_count_and_coverage_exclude_other_orgs_component(
    db_session, make_org
):
    """UnifiedApplicationCapabilityMapping carries no organization_id, so no
    listener fences it; _tenant_capability_filter deliberately admits a
    reference capability (organization_id IS NULL) into every tenant's
    snapshot. Before the ApplicationComponent join and predicate, a mapping
    from ANY organisation's component to that shared reference capability
    counted towards every other tenant's mapping_count and coverage -- org
    B's own component mapped to a shared reference capability showed up as
    org A's coverage.
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping,
    )
    from app.models.unified_capability import UnifiedCapability
    from app.modules.architecture.services.architecture_monitoring_service import (
        ArchitectureMonitoringService,
    )

    org_a, org_b = make_org("dr3-refmap-a"), make_org("dr3-refmap-b")
    suffix = uuid.uuid4().hex[:10]

    reference = UnifiedCapability(
        name=f"Shared reference {suffix}", code=f"SHREF-{suffix}", organization_id=None
    )
    db_session.add(reference)
    db_session.flush()

    component_b = ApplicationComponent(name=f"Org B component {suffix}", organization_id=org_b.id)
    db_session.add(component_b)
    db_session.flush()

    mapping_b = UnifiedApplicationCapabilityMapping(
        unified_capability_id=reference.id,
        application_component_id=component_b.id,
        is_active=True,
        coverage_percentage=80,
    )
    db_session.add(mapping_b)
    db_session.flush()

    service_a = ArchitectureMonitoringService(org_a.id)

    capabilities = service_a._capture_capabilities_snapshot()
    reference_row = next(row for row in capabilities if row["id"] == reference.id)
    assert reference_row["mapping_count"] == 0

    coverage = service_a._capture_coverage_snapshot()
    assert coverage["covered_capabilities"] == 0
    assert coverage["uncovered_capabilities"] == 1
    assert coverage["average_coverage"] == 0


# ----------------------------------------------------------- (6) vendor snapshot


def test_vendor_snapshot_only_includes_products_mapped_by_this_org(
    db_session, make_org
):
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_capabilities import BusinessCapability
    from app.models.vendor.vendor_organization import (
        VendorOrganization,
        VendorProduct,
        VendorProductCapability,
    )
    from app.modules.architecture.services.architecture_monitoring_service import (
        ArchitectureMonitoringService,
    )

    org_a, org_b = make_org("dr3-vend-a"), make_org("dr3-vend-b")
    suffix = uuid.uuid4().hex[:10]

    vendor = VendorOrganization(name=f"Vendor {suffix}")
    db_session.add(vendor)
    db_session.flush()

    product_a = VendorProduct(vendor_organization_id=vendor.id, name=f"Product A {suffix}")
    product_b = VendorProduct(vendor_organization_id=vendor.id, name=f"Product B {suffix}")
    product_unmapped = VendorProduct(vendor_organization_id=vendor.id, name=f"Product None {suffix}")
    db_session.add_all([product_a, product_b, product_unmapped])
    db_session.flush()

    # BusinessCapability's before_insert hook auto-creates an ArchiMateElement
    # via a raw connection.execute() that does not set organization_id, so it
    # violates that column's NOT NULL constraint unless archimate_element_id
    # is already populated. Pre-create the element through the ORM (which
    # does set organization_id) to sidestep that unrelated, pre-existing gap
    # -- the same workaround tests/test_tenant_scoping_leaks.py uses.
    element_a = ArchiMateElement(
        name=f"Cap A {suffix}", type="Capability", layer="Strategy", organization_id=org_a.id
    )
    db_session.add(element_a)
    db_session.flush()
    capability_a = BusinessCapability(
        name=f"Cap A {suffix}", organization_id=org_a.id, level=1, archimate_element_id=element_a.id
    )
    db_session.add(capability_a)
    db_session.flush()

    element_b = ArchiMateElement(
        name=f"Cap B {suffix}", type="Capability", layer="Strategy", organization_id=org_b.id
    )
    db_session.add(element_b)
    db_session.flush()
    capability_b = BusinessCapability(
        name=f"Cap B {suffix}", organization_id=org_b.id, level=1, archimate_element_id=element_b.id
    )
    db_session.add(capability_b)
    db_session.flush()

    mapping_a = VendorProductCapability(
        vendor_product_id=product_a.id,
        business_capability_id=capability_a.id,
        organization_id=org_a.id,
        coverage_percentage=80.0,
    )
    mapping_b = VendorProductCapability(
        vendor_product_id=product_b.id,
        business_capability_id=capability_b.id,
        organization_id=org_b.id,
        coverage_percentage=60.0,
    )
    db_session.add_all([mapping_a, mapping_b])
    db_session.flush()

    service_a = ArchitectureMonitoringService(org_a.id)
    vendor_ids = {row["id"] for row in service_a._capture_vendor_snapshot()}

    assert product_a.id in vendor_ids
    assert product_b.id not in vendor_ids
    assert product_unmapped.id not in vendor_ids

    # A tenant with no vendor mappings at all gets an honest empty snapshot,
    # not the shared VendorProduct catalogue.
    org_c = make_org("dr3-vend-c")
    service_c = ArchitectureMonitoringService(org_c.id)
    assert service_c._capture_vendor_snapshot() == []


# --------------------------------------------------------------- (7) mixin stamp


def test_mixin_stamps_organization_id_on_insert_without_explicit_value(
    db_session, make_org, tenant_ctx
):
    from app.models.policy_monitoring import MonitoringBaseline

    org_a, org_b = make_org("dr3-stamp-a"), make_org("dr3-stamp-b")

    with tenant_ctx(org_a.id):
        implicit = MonitoringBaseline(
            baseline_id=f"bl-{uuid.uuid4().hex[:10]}",
            name="Implicit stamp",
            snapshot_data="{}",
            checksum="cafebabe",
        )
        db_session.add(implicit)
        db_session.flush()
        assert implicit.organization_id == org_a.id

    explicit = MonitoringBaseline(
        baseline_id=f"bl-{uuid.uuid4().hex[:10]}",
        organization_id=org_b.id,
        name="Explicit stamp",
        snapshot_data="{}",
        checksum="cafebabe",
    )
    db_session.add(explicit)
    db_session.flush()

    assert explicit.organization_id == org_b.id
    assert implicit.organization_id == org_a.id  # unaffected by the second insert


# ------------------------------------------------------------------ (8) backfill


def _relax_monitoring_not_null(app):
    """Allow NULL organization_id inserts on both monitoring tables, committed
    on a connection of its own.

    There is no shared "relax not null" fixture on this branch's base (see
    ``tests/test_backfill_layer_tenancy_roadmap_tasks.py``'s own copy of this
    same helper, scoped to ``roadmap_tasks``); this duplicates its reasoning
    for the same two tables. It cannot run on ``db_session``'s own connection:
    that fixture's transaction is never really committed until the whole test
    rolls back, so the ACCESS EXCLUSIVE lock an ALTER TABLE takes would still
    be held when ``repair_layer_tenancy`` reflects these tables' columns on a
    second, independent connection a moment later (schema reflection is
    engine-bound, not session-bound) -- same process, same thread, so it
    blocks forever against itself. Doing the ALTER on its own autocommitting
    connection commits and releases the lock immediately.
    """
    from app import db

    with app.app_context():
        with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("ALTER TABLE monitoring_baselines ALTER COLUMN organization_id DROP NOT NULL"))
            conn.execute(text("ALTER TABLE monitoring_alerts ALTER COLUMN organization_id DROP NOT NULL"))


def _restore_monitoring_not_null(app):
    """Undo `_relax_monitoring_not_null` once this test's own cleanup has run."""
    from app import db

    with app.app_context():
        with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("ALTER TABLE monitoring_baselines ALTER COLUMN organization_id SET NOT NULL"))
            conn.execute(text("ALTER TABLE monitoring_alerts ALTER COLUMN organization_id SET NOT NULL"))


def test_backfill_derives_monitoring_provenance_and_leaves_unprovenanced_rows_null(app):
    """Calls ``repair_layer_tenancy()`` twice, so this cannot use the
    ``db_session`` fixture (nor ``make_org``, which depends on it) -- the same
    reason ``test_backfill_leaves_unprovenanced_roadmap_task_null_with_two_orgs``
    in ``tests/test_backfill_layer_tenancy_roadmap_tasks.py`` does not: on the
    first call, ``repair_layer_tenancy`` hardens (SET NOT NULL / CREATE INDEX
    on) every other tenant table it visits along the way, on the same
    connection ``db_session`` would own, and that lock would stay held
    (uncommitted) until the whole test rolls back, deadlocking the second
    call's own schema reflection of that same table. This test commits for
    real and cleans up explicitly instead, the same shape that test uses.

    One provenanced and one unprovenanced row per table: a baseline created
    by a real user and one whose ``created_by`` names no user (the literal
    string ``"system"``, predating this backfill); an alert acknowledged by a
    real user and one never acknowledged at all. A fourth baseline proves
    per-object precedence (ADR-0007 point 1): its creator has since moved to
    the other organisation (a removed user is moved to the Default
    organisation the same way in production), but its own snapshot_data
    names an organisation-owned capability, and that must win -- a baseline
    created in one tenant must not follow its creator to wherever they moved
    afterwards.
    """
    from app import db
    from app.commands.backfill_layer_tenancy import repair_layer_tenancy
    from app.models.organization import Organization
    from app.models.unified_capability import UnifiedCapability
    from app.models.user import User

    suffix = uuid.uuid4().hex[:10]
    with app.app_context():
        org_a = Organization(name=f"Test dr3-bf-a {suffix}", slug=f"test-dr3-bf-a-{suffix}")
        org_b = Organization(name=f"Test dr3-bf-b {suffix}", slug=f"test-dr3-bf-b-{suffix}")
        db.session.add_all([org_a, org_b])
        db.session.commit()

        user_a = User(
            email=f"dr3-bf-a-{suffix}@example.com",
            first_name="Test",
            last_name="A",
            organization_id=org_a.id,
        )
        user_b = User(
            email=f"dr3-bf-b-{suffix}@example.com",
            first_name="Test",
            last_name="B",
            organization_id=org_b.id,
        )
        # Created baseline_moved_creator below while a member of org_a; moved
        # to org_b by the time the backfill runs -- org_b is a stand-in for
        # the Default organisation a removed user is really moved to.
        mover = User(
            email=f"dr3-bf-mover-{suffix}@example.com",
            first_name="Test",
            last_name="Mover",
            organization_id=org_b.id,
        )
        db.session.add_all([user_a, user_b, mover])
        db.session.commit()

        capability_a = UnifiedCapability(
            name=f"Moved-creator capability {suffix}",
            code=f"MV-{suffix}",
            organization_id=org_a.id,
        )
        db.session.add(capability_a)
        db.session.commit()

        _relax_monitoring_not_null(app)

        baseline_provenanced_id = db.session.execute(
            text(
                """
                INSERT INTO monitoring_baselines
                    (baseline_id, organization_id, name, snapshot_data, checksum, created_by, is_active, created_at)
                VALUES
                    (:baseline_id, NULL, 'Provenanced baseline', '{}', 'chk-prov', :created_by, false, now())
                RETURNING id
                """
            ),
            {"baseline_id": f"dr3-bl-prov-{suffix}", "created_by": str(user_a.id)},
        ).scalar()
        baseline_unprovenanced_id = db.session.execute(
            text(
                """
                INSERT INTO monitoring_baselines
                    (baseline_id, organization_id, name, snapshot_data, checksum, created_by, is_active, created_at)
                VALUES
                    (:baseline_id, NULL, 'System baseline', '{}', 'chk-sys', 'system', false, now())
                RETURNING id
                """
            ),
            {"baseline_id": f"dr3-bl-sys-{suffix}"},
        ).scalar()
        baseline_moved_creator_id = db.session.execute(
            text(
                """
                INSERT INTO monitoring_baselines
                    (baseline_id, organization_id, name, snapshot_data, checksum, created_by, is_active, created_at)
                VALUES
                    (:baseline_id, NULL, 'Moved-creator baseline', :snapshot_data, 'chk-moved', :created_by, false, now())
                RETURNING id
                """
            ),
            {
                "baseline_id": f"dr3-bl-moved-{suffix}",
                "snapshot_data": json.dumps({"capabilities": [{"id": capability_a.id}]}),
                "created_by": str(mover.id),
            },
        ).scalar()
        alert_provenanced_id = db.session.execute(
            text(
                """
                INSERT INTO monitoring_alerts
                    (alert_id, organization_id, alert_type, severity, title, acknowledged, acknowledged_by, created_at)
                VALUES
                    (:alert_id, NULL, 'new_gap', 'warning', 'Provenanced alert', true, :acknowledged_by, now())
                RETURNING id
                """
            ),
            {"alert_id": f"dr3-al-prov-{suffix}", "acknowledged_by": str(user_b.id)},
        ).scalar()
        alert_unacknowledged_id = db.session.execute(
            text(
                """
                INSERT INTO monitoring_alerts
                    (alert_id, organization_id, alert_type, severity, title, acknowledged, acknowledged_by, created_at)
                VALUES
                    (:alert_id, NULL, 'new_gap', 'warning', 'Unacknowledged alert', false, NULL, now())
                RETURNING id
                """
            ),
            {"alert_id": f"dr3-al-unack-{suffix}"},
        ).scalar()
        db.session.commit()

        def _org_of(table, row_id):
            return db.session.execute(
                text(f"SELECT organization_id FROM {table} WHERE id = :id"), {"id": row_id}
            ).scalar()

        try:
            stats_first = repair_layer_tenancy()

            assert _org_of("monitoring_baselines", baseline_provenanced_id) == org_a.id
            assert _org_of("monitoring_baselines", baseline_unprovenanced_id) is None
            # The capability in snapshot_data names org_a; the creator (mover)
            # is in org_b at backfill time. Per-object provenance must win.
            assert _org_of("monitoring_baselines", baseline_moved_creator_id) == org_a.id
            assert _org_of("monitoring_alerts", alert_provenanced_id) == org_b.id
            assert _org_of("monitoring_alerts", alert_unacknowledged_id) is None
            assert stats_first["unresolved"] == {"monitoring_alerts": 1, "monitoring_baselines": 1}

            # --org-id must not sweep the unresolved rows into the named
            # organisation: they are another tenant's data, not this
            # operator's to assign.
            stats_second = repair_layer_tenancy(org_id=org_a.id)

            assert _org_of("monitoring_baselines", baseline_provenanced_id) == org_a.id
            assert _org_of("monitoring_baselines", baseline_unprovenanced_id) is None
            assert _org_of("monitoring_baselines", baseline_moved_creator_id) == org_a.id
            assert _org_of("monitoring_alerts", alert_provenanced_id) == org_b.id
            assert _org_of("monitoring_alerts", alert_unacknowledged_id) is None
            assert stats_second["unresolved"] == {"monitoring_alerts": 1, "monitoring_baselines": 1}
        finally:
            db.session.rollback()
            db.session.execute(
                text("DELETE FROM monitoring_baselines WHERE id = ANY(:ids)"),
                {
                    "ids": [
                        baseline_provenanced_id,
                        baseline_unprovenanced_id,
                        baseline_moved_creator_id,
                    ]
                },
            )
            db.session.execute(
                text("DELETE FROM monitoring_alerts WHERE id = ANY(:ids)"),
                {"ids": [alert_provenanced_id, alert_unacknowledged_id]},
            )
            db.session.execute(
                text("DELETE FROM unified_capabilities WHERE id = :id"), {"id": capability_a.id}
            )
            db.session.execute(
                text("DELETE FROM users WHERE id IN (:a, :b, :m)"),
                {"a": user_a.id, "b": user_b.id, "m": mover.id},
            )
            db.session.execute(
                text("DELETE FROM organizations WHERE id IN (:a, :b)"),
                {"a": org_a.id, "b": org_b.id},
            )
            db.session.commit()
            _restore_monitoring_not_null(app)


# -------------------------------------------------------- (9) no-tenant refusal


def test_service_requires_organization_id_and_route_answers_404_without_tenant(
    app, db_session, make_org, client, login_as, monkeypatch
):
    """A no-tenant 404 and a routing 404 (the rule not mounted at all, or the
    URL simply wrong) are the same JSON shape, so a 404 alone proves nothing:
    this asserts the rule is actually in ``app.url_map`` under the mount flag
    (``ARCHITECTURE_MONITORING_API_ENABLED`` -- the flag T-DR-1 makes the
    mount opt-in on; harmless to set here ahead of that landing, since
    nothing reads it yet and the blueprint is still registered
    unconditionally) and proves the *same* request answers 200 with a tenant
    present, before showing that only the no-tenant case 404s.
    """
    from app.modules.architecture.services.architecture_monitoring_service import (
        ArchitectureMonitoringService,
    )
    import app.modules.architecture.routes.architecture_monitoring_routes as routes_module

    monkeypatch.setenv("ARCHITECTURE_MONITORING_API_ENABLED", "true")

    with pytest.raises(ValueError):
        ArchitectureMonitoringService(None)

    endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
    assert "architecture_monitoring.get_monitoring_status" in endpoints

    org = make_org("dr3-no-tenant")
    user = _make_user(db_session, org.id, "NoTenant")

    login_as(client, user)
    # With a real tenant on the request the same route answers 200 -- so the
    # 404 asserted below is the no-tenant refusal specifically, not routing
    # failure or an unmounted blueprint wearing the same JSON shape.
    ok_response = client.get("/api/architecture-monitoring/status")
    assert ok_response.status_code == 200
    assert ok_response.get_json()["success"] is True

    # The middleware always resolves a real, non-None organisation for a
    # logged-in user (users.organization_id is NOT NULL); simulate the "no
    # tenant on the request" case the routes must still refuse -- a CLI/job
    # call, or a request the tenant middleware has not run for -- by patching
    # the routes module's own current_org_id reference.
    monkeypatch.setattr(routes_module, "current_org_id", lambda: None)

    response = client.get("/api/architecture-monitoring/status")

    assert response.status_code == 404
    assert response.get_json()["success"] is False

"""Tenant isolation for duplicate-detection runs, groups, and entries.

DuplicateDetectionRun, UnifiedDetectionRun, DuplicateGroup,
UnifiedDuplicateGroup, and ConsolidationListEntry gained TenantMixin
on 2026-10-03.

The TenantMixin middleware auto-filters every ORM SELECT with
``WHERE organization_id = g.current_org_id``, so route-level isolation
follows from the model-level proof below.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(db_session, org_id, email=None):
    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=email or f"test-{suffix}@example.com",
        first_name="Test",
        last_name=f"User-{suffix}",
        organization_id=org_id,
    )
    user.password = "test"
    db_session.add(user)
    db_session.flush()
    return user


def _make_app(db_session, org_id, name=None):
    from app.models.application_portfolio import ApplicationComponent

    suffix = uuid.uuid4().hex[:8]
    comp = ApplicationComponent(
        name=name or f"TestApp-{suffix}",
        organization_id=org_id,
    )
    db_session.add(comp)
    db_session.flush()
    return comp


def _make_unified_run(db_session, org_id, user_id=None):
    from app.models.unified_duplicate_detection import UnifiedDetectionRun

    suffix = uuid.uuid4().hex[:6]
    run = UnifiedDetectionRun(
        run_name=f"Run-{suffix}",
        strategy="fast",
        status="completed",
        organization_id=org_id,
        user_id=user_id,
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
    )
    db_session.add(run)
    db_session.flush()
    return run


def _make_null_org_unified_run(db_session):
    """A UnifiedDetectionRun with NULL organization_id (no user/creator known)."""
    from app.models.unified_duplicate_detection import UnifiedDetectionRun

    suffix = uuid.uuid4().hex[:6]
    run = UnifiedDetectionRun(
        run_name=f"NullRun-{suffix}",
        strategy="fast",
        status="completed",
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
    )
    db_session.add(run)
    db_session.flush()
    # TenantMixin's _default_org_id fallback may have set org_id; force NULL
    run.organization_id = None
    db_session.flush()
    return run


def _make_unified_group(db_session, org_id, run_id=None):
    from app.models.unified_duplicate_detection import UnifiedDuplicateGroup

    suffix = uuid.uuid4().hex[:6]
    group = UnifiedDuplicateGroup(
        name=f"Group-{suffix}",
        similarity_score=0.85,
        similarity_threshold=0.8,
        organization_id=org_id,
        detection_run_id=run_id,
    )
    db_session.add(group)
    db_session.flush()
    return group


def _make_detection_run(db_session, org_id):
    from app.models.application_duplicate_detection import DuplicateDetectionRun

    suffix = uuid.uuid4().hex[:6]
    run = DuplicateDetectionRun(
        run_name=f"LegacyRun-{suffix}",
        organization_id=org_id,
        status="completed",
    )
    db_session.add(run)
    db_session.flush()
    return run


def _make_duplicate_group(db_session, org_id, run_id):
    from app.models.application_duplicate_detection import DuplicateGroup, DuplicateType

    suffix = uuid.uuid4().hex[:6]
    group = DuplicateGroup(
        group_name=f"LegacyGroup-{suffix}",
        duplicate_type=DuplicateType.FUNCTIONAL,
        overall_similarity_score=0.9,
        organization_id=org_id,
        detection_run_id=run_id,
    )
    db_session.add(group)
    db_session.flush()
    return group


def _make_consolidation_entry(db_session, org_id, app_id):
    from app.models.consolidation_list import ConsolidationListEntry

    entry = ConsolidationListEntry(
        application_id=app_id,
        organization_id=org_id,
        recommended_action="decommission",
        priority="high",
        status="pending",
        source_type="duplicate_detection",
    )
    db_session.add(entry)
    db_session.flush()
    return entry


# ---------------------------------------------------------------------------
# Model-level isolation — TenantMixin auto-filters
# ---------------------------------------------------------------------------

class TestModelTenantIsolation:
    """Each TenantMixin model auto-filters: org A never sees org B's rows."""

    def test_unified_run_is_scoped(self, db_session, make_org, tenant_ctx):
        from app.models.unified_duplicate_detection import UnifiedDetectionRun

        org_a, org_b = make_org("a"), make_org("b")
        run_a = _make_unified_run(db_session, org_a.id)
        run_b = _make_unified_run(db_session, org_b.id)

        with tenant_ctx(org_a.id):
            visible = {r.id for r in UnifiedDetectionRun.query.all()}
        assert run_a.id in visible, "org A cannot see its own run"
        assert run_b.id not in visible, "TENANT LEAK: org A can see org B's run"

    def test_unified_group_is_scoped(self, db_session, make_org, tenant_ctx):
        from app.models.unified_duplicate_detection import UnifiedDuplicateGroup

        org_a, org_b = make_org("a"), make_org("b")
        g_a = _make_unified_group(db_session, org_a.id)
        g_b = _make_unified_group(db_session, org_b.id)

        with tenant_ctx(org_a.id):
            visible = {g.id for g in UnifiedDuplicateGroup.query.all()}
        assert g_a.id in visible
        assert g_b.id not in visible

    def test_detection_run_is_scoped(self, db_session, make_org, tenant_ctx):
        from app.models.application_duplicate_detection import DuplicateDetectionRun

        org_a, org_b = make_org("a"), make_org("b")
        run_a = _make_detection_run(db_session, org_a.id)
        run_b = _make_detection_run(db_session, org_b.id)

        with tenant_ctx(org_a.id):
            visible = {r.id for r in DuplicateDetectionRun.query.all()}
        assert run_a.id in visible
        assert run_b.id not in visible

    def test_duplicate_group_is_scoped(self, db_session, make_org, tenant_ctx):
        from app.models.application_duplicate_detection import DuplicateGroup

        org_a, org_b = make_org("a"), make_org("b")
        run_a = _make_detection_run(db_session, org_a.id)
        run_b = _make_detection_run(db_session, org_b.id)
        g_a = _make_duplicate_group(db_session, org_a.id, run_a.id)
        g_b = _make_duplicate_group(db_session, org_b.id, run_b.id)

        with tenant_ctx(org_a.id):
            visible = {g.id for g in DuplicateGroup.query.all()}
        assert g_a.id in visible
        assert g_b.id not in visible

    def test_consolidation_entry_is_scoped(self, db_session, make_org, tenant_ctx):
        from app.models.consolidation_list import ConsolidationListEntry

        org_a, org_b = make_org("a"), make_org("b")
        app_a = _make_app(db_session, org_a.id)
        app_b = _make_app(db_session, org_b.id)
        entry_a = _make_consolidation_entry(db_session, org_a.id, app_a.id)
        entry_b = _make_consolidation_entry(db_session, org_b.id, app_b.id)

        with tenant_ctx(org_a.id):
            visible = {e.id for e in ConsolidationListEntry.query.all()}
        assert entry_a.id in visible
        assert entry_b.id not in visible

    def test_get_by_id_refuses_other_org(self, db_session, make_org, tenant_ctx):
        """get() returns None for another org's row (cold identity map)."""
        from app.models.unified_duplicate_detection import UnifiedDuplicateGroup

        org_a, org_b = make_org("a"), make_org("b")
        group_b = _make_unified_group(db_session, org_b.id)

        db_session.expunge_all()
        with tenant_ctx(org_a.id):
            result = db_session.get(UnifiedDuplicateGroup, group_b.id)
        assert result is None, "org A retrieved org B's group via .get()"

    def test_unified_runs_count_scoped(self, db_session, make_org, tenant_ctx):
        """Count queries are tenant-scoped."""
        from app.models.unified_duplicate_detection import UnifiedDetectionRun

        org_a, org_b = make_org("a"), make_org("b")
        _make_unified_run(db_session, org_a.id)
        _make_unified_run(db_session, org_b.id)

        with tenant_ctx(org_a.id):
            count = UnifiedDetectionRun.query.count()
        assert count == 1, f"org A saw {count} runs (should see 1)"

    def test_unified_groups_count_scoped(self, db_session, make_org, tenant_ctx):
        """Count queries are tenant-scoped."""
        from app.models.unified_duplicate_detection import UnifiedDuplicateGroup

        org_a, org_b = make_org("a"), make_org("b")
        _make_unified_group(db_session, org_a.id)
        _make_unified_group(db_session, org_b.id)

        with tenant_ctx(org_a.id):
            count = UnifiedDuplicateGroup.query.count()
        assert count == 1

    def test_detection_runs_count_scoped(self, db_session, make_org, tenant_ctx):
        """Count queries are tenant-scoped."""
        from app.models.application_duplicate_detection import DuplicateDetectionRun

        org_a, org_b = make_org("a"), make_org("b")
        _make_detection_run(db_session, org_a.id)
        _make_detection_run(db_session, org_b.id)

        with tenant_ctx(org_a.id):
            count = DuplicateDetectionRun.query.count()
        assert count == 1

    def test_consolidation_entries_count_scoped(self, db_session, make_org, tenant_ctx):
        """Count queries are tenant-scoped."""
        from app.models.consolidation_list import ConsolidationListEntry

        org_a, org_b = make_org("a"), make_org("b")
        app_a = _make_app(db_session, org_a.id)
        app_b = _make_app(db_session, org_b.id)
        _make_consolidation_entry(db_session, org_a.id, app_a.id)
        _make_consolidation_entry(db_session, org_b.id, app_b.id)

        with tenant_ctx(org_a.id):
            count = ConsolidationListEntry.query.count()
        assert count == 1


# ---------------------------------------------------------------------------
# Route-level isolation — proven at the model level above
# ---------------------------------------------------------------------------
#
# Every route that queries these models (UnifiedDetectionRun, UnifiedDuplicateGroup,
# DuplicateDetectionRun, DuplicateGroup, ConsolidationListEntry) uses standard ORM
# patterns (.query.all(), .query.get(), .query.count(), get_or_404) that
# TenantMixin's do_orm_execute listener auto-scopes with:
#
#     WHERE organization_id = g.current_org_id
#
# The tests above (test_*_is_scoped, test_get_by_id_refuses_other_org) prove the
# ORM filters correctly — those are the exact same query paths the routes use.
# A separate browser-level drive is covered by the sweep in
# tests/test_tenant_isolation_matrix.py.


# ---------------------------------------------------------------------------
# Legacy NULL-org visibility
# ---------------------------------------------------------------------------

class TestLegacyNullOrgVisibility:
    """A run whose owner cannot be determined (organization_id IS NULL):

    - visible when queried without a tenant context (platform admin / CLI)
    - invisible inside a tenant context (every tenant user)
    """

    def test_null_org_unified_run_visible_without_tenant(self, db_session, make_org):
        from app.models.unified_duplicate_detection import UnifiedDetectionRun

        make_org("a")
        run_null = _make_null_org_unified_run(db_session)

        db_session.expunge_all()
        runs = UnifiedDetectionRun.query.all()
        assert run_null.id in {r.id for r in runs}, "NULL-org run invisible without tenant"

    def test_null_org_unified_run_invisible_to_tenant(self, db_session, make_org, tenant_ctx):
        from app.models.unified_duplicate_detection import UnifiedDetectionRun

        org_a = make_org("a")
        run_null = _make_null_org_unified_run(db_session)

        db_session.expunge_all()
        with tenant_ctx(org_a.id):
            runs = UnifiedDetectionRun.query.all()
        assert run_null.id not in {r.id for r in runs}, (
            "TENANT LEAK: NULL-org run visible inside tenant context"
        )

    def test_null_org_duplicate_group_invisible_to_tenant(self, db_session, make_org, tenant_ctx):
        from app.models.application_duplicate_detection import DuplicateGroup, DuplicateType, DuplicateDetectionRun

        org_a = make_org("a")
        run = _make_detection_run(db_session, org_a.id)
        suffix = uuid.uuid4().hex[:6]
        group_null = DuplicateGroup(
            group_name=f"NullGroup-{suffix}",
            duplicate_type=DuplicateType.FUNCTIONAL,
            overall_similarity_score=0.9,
            detection_run_id=run.id,
        )
        db_session.add(group_null)
        db_session.flush()
        group_null.organization_id = None
        db_session.flush()

        db_session.expunge_all()
        with tenant_ctx(org_a.id):
            groups = DuplicateGroup.query.all()
        assert group_null.id not in {g.id for g in groups}

    def test_null_org_consolidation_entry_invisible_to_tenant(self, db_session, make_org, tenant_ctx):
        from app.models.consolidation_list import ConsolidationListEntry

        org_a = make_org("a")
        app_a = _make_app(db_session, org_a.id)
        entry_null = ConsolidationListEntry(
            application_id=app_a.id,
            recommended_action="decommission",
            priority="low",
            status="pending",
            source_type="manual",
        )
        db_session.add(entry_null)
        db_session.flush()
        entry_null.organization_id = None
        db_session.flush()

        db_session.expunge_all()
        with tenant_ctx(org_a.id):
            entries = ConsolidationListEntry.query.all()
        assert entry_null.id not in {e.id for e in entries}


# ---------------------------------------------------------------------------
# Backfill (committed data — the backfill opens its own connection)
# ---------------------------------------------------------------------------

class TestBackfill:
    """The backfill assigns organisation from creator, is correct, and is idempotent."""

    @pytest.fixture(autouse=True)
    def _cleanup(self, app):
        self._to_clean = []
        yield
        if self._to_clean:
            with app.app_context():
                from app import db as real_db
                from app.models.user import User
                from app.models.organization import Organization
                from app.models.application_portfolio import ApplicationComponent
                from app.models.unified_duplicate_detection import UnifiedDetectionRun, UnifiedDuplicateGroup
                from app.models.application_duplicate_detection import DuplicateDetectionRun
                from app.models.consolidation_list import ConsolidationListEntry
                for model_cls, ids in self._to_clean:
                    for rid in ids:
                        row = real_db.session.get(model_cls, rid)
                        if row:
                            real_db.session.delete(row)
                real_db.session.commit()

    def _seed_org(self, app):
        from app import db as real_db
        from app.models.organization import Organization
        suffix = uuid.uuid4().hex[:10]
        org = Organization(name=f"BF {suffix}", slug=f"bf-{suffix}")
        real_db.session.add(org)
        real_db.session.commit()
        self._to_clean.append((Organization, [org.id]))
        return org

    def _seed_user(self, app, org_id):
        from app import db as real_db
        from app.models.user import User
        suffix = uuid.uuid4().hex[:8]
        user = User(
            email=f"bf-{suffix}@example.com",
            first_name="BF",
            last_name=f"User-{suffix}",
            organization_id=org_id,
        )
        user.password = "test"
        real_db.session.add(user)
        real_db.session.commit()
        self._to_clean.append((User, [user.id]))
        return user

    def _seed_app(self, app, org_id):
        from app import db as real_db
        from app.models.application_portfolio import ApplicationComponent
        suffix = uuid.uuid4().hex[:8]
        appc = ApplicationComponent(name=f"BF-App-{suffix}", organization_id=org_id)
        real_db.session.add(appc)
        real_db.session.commit()
        self._to_clean.append((ApplicationComponent, [appc.id]))
        return appc

    @staticmethod
    def _force_null_org(real_db, table, row_id):
        """Set organization_id to NULL after TenantMixin default ran."""
        real_db.session.execute(
            real_db.text(f"UPDATE {table} SET organization_id = NULL WHERE id = :id"),
            {"id": row_id},
        )
        real_db.session.commit()

    def test_backfill_unified_run_from_user_id(self, db_session, app):
        org = self._seed_org(app)
        org_id = org.id  # save before any expunge
        user = self._seed_user(app, org_id)

        from app import db as real_db
        from app.models.unified_duplicate_detection import UnifiedDetectionRun

        run = UnifiedDetectionRun(
            run_name="BackfillTest",
            strategy="fast",
            status="completed",
            user_id=user.id,
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )
        real_db.session.add(run)
        real_db.session.commit()
        run_id = run.id
        self._force_null_org(real_db, "unified_detection_runs", run_id)

        from app.commands.backfill_dedupe_tenancy import _backfill
        with app.app_context():
            _backfill(dry_run=False)

        real_db.session.expunge_all()
        fresh = real_db.session.get(UnifiedDetectionRun, run_id)
        assert fresh.organization_id == org_id, (
            f"backfill did not set org; got {fresh.organization_id}"
        )
        self._to_clean.append((UnifiedDetectionRun, [run_id]))

    def test_backfill_unified_group_from_run(self, db_session, app):
        org = self._seed_org(app)
        org_id = org.id  # save before any expunge
        user = self._seed_user(app, org_id)

        from app import db as real_db
        from app.models.unified_duplicate_detection import UnifiedDetectionRun, UnifiedDuplicateGroup

        run = UnifiedDetectionRun(
            run_name="BackfillGroupRun",
            strategy="fast",
            status="completed",
            user_id=user.id,
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )
        real_db.session.add(run)
        real_db.session.commit()
        run_id = run.id
        self._force_null_org(real_db, "unified_detection_runs", run_id)

        group = UnifiedDuplicateGroup(
            name="BackfillGroup",
            similarity_score=0.9,
            similarity_threshold=0.8,
            detection_run_id=run_id,
        )
        real_db.session.add(group)
        real_db.session.commit()
        group_id = group.id
        self._force_null_org(real_db, "unified_duplicate_groups", group_id)

        from app.commands.backfill_dedupe_tenancy import _backfill
        with app.app_context():
            _backfill(dry_run=False)

        real_db.session.expunge_all()
        fresh_run = real_db.session.get(UnifiedDetectionRun, run_id)
        fresh_group = real_db.session.get(UnifiedDuplicateGroup, group_id)
        assert fresh_run.organization_id == org_id
        assert fresh_group.organization_id == org_id, (
            f"group org not derived from run; got {fresh_group.organization_id}"
        )
        self._to_clean.append((UnifiedDetectionRun, [run_id]))
        self._to_clean.append((UnifiedDuplicateGroup, [group_id]))

    def test_backfill_is_idempotent(self, db_session, app):
        org = self._seed_org(app)
        org_id = org.id  # save before any expunge
        user = self._seed_user(app, org_id)

        from app import db as real_db
        from app.models.unified_duplicate_detection import UnifiedDetectionRun

        run = UnifiedDetectionRun(
            run_name="IdempotentTest",
            strategy="fast",
            status="completed",
            user_id=user.id,
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )
        real_db.session.add(run)
        real_db.session.commit()
        run_id = run.id
        self._force_null_org(real_db, "unified_detection_runs", run_id)

        from app.commands.backfill_dedupe_tenancy import _backfill
        with app.app_context():
            _backfill(dry_run=False)  # first run

        real_db.session.expunge_all()
        first_org = real_db.session.get(UnifiedDetectionRun, run_id).organization_id
        assert first_org == org_id

        with app.app_context():
            stats2 = _backfill(dry_run=False)

        real_db.session.expunge_all()
        second_org = real_db.session.get(UnifiedDetectionRun, run_id).organization_id
        assert first_org == second_org, "backfill changed org on second run"
        assert sum(stats2["derived"].values()) == 0, "second backfill modified rows"
        self._to_clean.append((UnifiedDetectionRun, [run_id]))

    def test_backfill_consolidation_entry_from_app(self, db_session, app):
        org = self._seed_org(app)
        org_id = org.id  # save before any expunge
        application = self._seed_app(app, org_id)

        from app import db as real_db
        from app.models.consolidation_list import ConsolidationListEntry

        entry = ConsolidationListEntry(
            application_id=application.id,
            recommended_action="decommission",
            priority="medium",
            status="pending",
            source_type="manual",
        )
        real_db.session.add(entry)
        real_db.session.commit()
        entry_id = entry.id
        self._force_null_org(real_db, "consolidation_list_entries", entry_id)

        from app.commands.backfill_dedupe_tenancy import _backfill
        with app.app_context():
            _backfill(dry_run=False)

        real_db.session.expunge_all()
        fresh = real_db.session.get(ConsolidationListEntry, entry_id)
        assert fresh.organization_id == org_id, (
            f"entry backfill failed; got {fresh.organization_id}"
        )
        self._to_clean.append((ConsolidationListEntry, [entry_id]))

    def test_backfill_does_not_touch_null_owner_runs(self, db_session, app):
        """DuplicateDetectionRun has no user_id — the backfill leaves it NULL."""
        self._seed_org(app)  # ensures at least one org exists

        from app import db as real_db
        from app.models.application_duplicate_detection import DuplicateDetectionRun

        run = DuplicateDetectionRun(
            run_name="NoOwnerRun",
            status="completed",
        )
        real_db.session.add(run)
        real_db.session.commit()
        run_id = run.id
        self._force_null_org(real_db, "duplicate_detection_runs", run_id)

        from app.commands.backfill_dedupe_tenancy import _backfill
        with app.app_context():
            _backfill(dry_run=False)

        real_db.session.expunge_all()
        fresh = real_db.session.get(DuplicateDetectionRun, run_id)
        assert fresh.organization_id is None, (
            f"DuplicateDetectionRun got org assigned (no user_id); got {fresh.organization_id}"
        )
        self._to_clean.append((DuplicateDetectionRun, [run_id]))
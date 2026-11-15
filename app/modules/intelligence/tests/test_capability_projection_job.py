"""T-002 task 01: the scheduled projection job and the maturity accessor.

Uses committed rows (not the ``db_session`` savepoint fixture) because
``execute_projection_with_audit`` opens its own connection via
``db.engine.connect()`` — a savepoint held on a *different* connection would
be invisible to it. Every row created here is committed and explicitly
cleaned up in a ``finally`` block, following the pattern PR #23's own
projection tests use (see the class docstring on ``ProjectionBlocked`` in
``app/commands/project_capabilities.py`` for why the projection cannot run
inside a nested transaction).
"""

from __future__ import annotations

import uuid

import pytest
from flask import g


pytestmark = pytest.mark.usefixtures("app")


def _make_org(db, label="t002"):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"T002 {label} {suffix}", slug=f"t002-{label}-{suffix}")
    db.session.add(org)
    db.session.commit()
    return org


def _make_capability(db, org, *, name, current=None, target=None):
    from app.models.business_capabilities import BusinessCapability

    cap = BusinessCapability(
        name=name,
        code=f"CAP-{uuid.uuid4().hex[:10]}",
        organization_id=org.id,
        level=1,
        current_maturity_level=current,
        target_maturity_level=target,
    )
    db.session.add(cap)
    db.session.commit()
    return cap


@pytest.fixture
def db(app):
    from app import db as _db

    with app.app_context():
        yield _db


@pytest.fixture
def cleanup(db):
    """Track rows created directly (bypassing db_session) and delete them after.

    R3-3: `run_capability_projection_job()` projects EVERY `business_capability`
    row in the ENTIRE database in one all-tenant pass, not just this test's own
    fixture rows. A shared `flask_test` database can already hold thousands of
    other organizations' rows, so a before/after id-diff of `unified_capabilities`
    across a job run is an unscoped, unbounded, cross-tenant snapshot — deleting
    that diff would delete every org's newly-projected rows, not just this
    test's own. That approach has been removed entirely.

    The only safe teardown is provenance-scoped: delete `unified_capabilities`
    rows keyed by `source_table='business_capability' AND source_id IN
    (<this test's own tracked business_capability ids>)` -- rows this test's
    own fixture data produced, whether by the job's write pass or by PR #23's
    write-time ORM sync listener (which fires at capability-insert time,
    before any job run). Never delete by "every id that appeared after my
    run" -- that shape cannot be tenant-safe against an all-tenant job.
    """
    created = {"business_capability": [], "organizations": []}
    yield created
    from app.models.business_capabilities import BusinessCapability
    from app.models.organization import Organization
    from app.models.unified_capability import UnifiedCapability

    if created["business_capability"]:
        UnifiedCapability.query.filter(
            UnifiedCapability.source_table == "business_capability",
            UnifiedCapability.source_id.in_(
                [str(cap_id) for cap_id in created["business_capability"]]
            ),
        ).delete(synchronize_session=False)
    if created["business_capability"]:
        BusinessCapability.query.filter(
            BusinessCapability.id.in_(created["business_capability"])
        ).delete(synchronize_session=False)
    db.session.commit()
    if created["organizations"]:
        from tests._outbox_cleanup import purge_outbox_events

        purge_outbox_events(db.session, created["organizations"])
        # The capabilities this test wrote are in the audit trail, which holds the organisation.
        db.session.execute(
            db.text("DELETE FROM soc2_audit_log WHERE organization_id = ANY(:ids)"),
            {"ids": list(created["organizations"])},
        )
        Organization.query.filter(Organization.id.in_(created["organizations"])).delete(
            synchronize_session=False
        )
    db.session.commit()


def _ensure_provenance_index(db):
    from app.commands.project_capabilities import _has_provenance_index

    with db.engine.connect() as conn:
        if _has_provenance_index(conn):
            return
    pytest.skip(
        "uq_unified_capabilities_provenance is absent on this database; run "
        "`flask apply-unified-capability-provenance-migration` first"
    )


def _run_projection_tracked(db, cleanup):
    """R3-3: `run_capability_projection_job` projects EVERY `business_capability`
    row in the ENTIRE database, across every tenant, in one pass. There is no
    safe way to diff `unified_capabilities` ids before/after a run and treat
    the diff as "this test's rows" -- on a shared database that already holds
    other organizations' capabilities, that diff includes their newly
    reprojected rows too (e.g. a stale-checksum reproject touching an
    existing row is invisible to an id-diff, but a genuinely new row from
    another org's freshly-inserted capability is not). This helper no longer
    tracks anything itself; teardown is entirely provenance-scoped against
    `cleanup["business_capability"]`, set by each test before calling this.
    """
    from app.jobs.capability_projection_job import run_capability_projection_job

    return run_capability_projection_job()


class TestCapabilityProjectionJob:
    def test_ac2_run_projects_every_source_row_with_provenance(self, app, db, cleanup):
        _ensure_provenance_index(db)
        from app.models.unified_capability import UnifiedCapability

        with app.app_context():
            org = _make_org(db, "ac2")
            cleanup["organizations"].append(org.id)
            cap = _make_capability(db, org, name="AC2 capability", current=2, target=4)
            cleanup["business_capability"].append(cap.id)

            run = _run_projection_tracked(db, cleanup)
            assert run.status == "ok", run.error

            projected = UnifiedCapability.query.filter(
                UnifiedCapability.source_table == "business_capability",
                UnifiedCapability.source_id == str(cap.id),
            ).one()

            assert projected.source_id == str(cap.id)
            assert projected.source_checksum is not None
            assert projected.current_maturity_level == 2
            assert projected.target_maturity_level == 4

    def test_ac3_idempotent_second_run_writes_nothing(self, app, db, cleanup):
        _ensure_provenance_index(db)
        from app.models.unified_capability import UnifiedCapability

        with app.app_context():
            org = _make_org(db, "ac3")
            cleanup["organizations"].append(org.id)
            cap = _make_capability(db, org, name="AC3 capability", current=1, target=3)
            cleanup["business_capability"].append(cap.id)

            first = _run_projection_tracked(db, cleanup)
            assert first.status == "ok", first.error
            assert UnifiedCapability.query.filter(
                UnifiedCapability.source_table == "business_capability",
                UnifiedCapability.source_id == str(cap.id),
            ).count() == 1

            second = _run_projection_tracked(db, cleanup)
            assert second.status == "ok", second.error
            assert second.payload["writes"]["inserted_or_updated"] == 0

    def test_ac4_raw_sql_maturity_write_reflected_after_next_run(self, app, db, cleanup):
        """Reproduces the maturity_routes.py raw-SQL UPDATE shape directly —
        it must not go through the ORM, or it proves the wrong thing (an ORM
        event PR #23 already handles, not the gap this job closes)."""
        _ensure_provenance_index(db)
        from app.models.unified_capability import UnifiedCapability

        with app.app_context():
            org = _make_org(db, "ac4")
            cleanup["organizations"].append(org.id)
            cap = _make_capability(db, org, name="AC4 capability", current=1, target=3)
            cleanup["business_capability"].append(cap.id)

            first = _run_projection_tracked(db, cleanup)
            assert first.status == "ok", first.error
            projected = UnifiedCapability.query.filter(
                UnifiedCapability.source_table == "business_capability",
                UnifiedCapability.source_id == str(cap.id),
            ).one()
            assert projected.current_maturity_level == 1

            # Raw-SQL write, bypassing every ORM listener — the same
            # statement shape as maturity_routes.py:177-199's UPDATE,
            # including its organization_id predicate.
            db.session.execute(
                db.text(
                    "UPDATE business_capability SET current_maturity_level = :lvl "
                    "WHERE id = :id AND organization_id = :org_id"
                ),
                {"lvl": 5, "id": cap.id, "org_id": org.id},
            )
            db.session.commit()

            second = _run_projection_tracked(db, cleanup)
            assert second.status == "ok", second.error
            db.session.expire_all()
            refreshed = UnifiedCapability.query.get(projected.id)
            assert refreshed.current_maturity_level == 5

    def test_ac5_no_tenant_context_across_multiple_orgs(self, app, db, cleanup):
        _ensure_provenance_index(db)
        from app.models.unified_capability import UnifiedCapability

        with app.app_context():
            org_a = _make_org(db, "ac5a")
            org_b = _make_org(db, "ac5b")
            cleanup["organizations"] += [org_a.id, org_b.id]
            cap_a = _make_capability(db, org_a, name="AC5-A", current=1, target=2)
            cap_b = _make_capability(db, org_b, name="AC5-B", current=2, target=3)
            cleanup["business_capability"] += [cap_a.id, cap_b.id]

            assert getattr(g, "current_org_id", None) is None
            run = _run_projection_tracked(db, cleanup)
            assert run.status == "ok", run.error
            assert getattr(g, "current_org_id", None) is None

            for cap in (cap_a, cap_b):
                projected = UnifiedCapability.query.filter(
                    UnifiedCapability.source_table == "business_capability",
                    UnifiedCapability.source_id == str(cap.id),
                ).one()
                assert projected.organization_id in (org_a.id, org_b.id)

    def test_ac6_contended_lock_records_one_run_and_one_skip(self, app, db, cleanup):
        """Note: `_make_capability` itself already produces a projected row
        (PR #23's write-time ORM sync listener fires on the BusinessCapability
        insert), so "0 rows" is never the right assertion for a skip -- the
        real, mutation-resistant signal is whether a change made WHILE the
        lock is contended reaches `unified_capabilities`: it must not, because
        skipped_locked means the job body never ran at all.
        """
        _ensure_provenance_index(db)
        from app.jobs.tenant_safe_job import job_lock
        from app.models.unified_capability import UnifiedCapability

        with app.app_context():
            org = _make_org(db, "ac6")
            cleanup["organizations"].append(org.id)
            cap = _make_capability(db, org, name="AC6 capability", current=2, target=4)
            cleanup["business_capability"].append(cap.id)

            # Prime the projected row (via the ORM sync listener triggered by
            # the insert above) so a later change has something to diverge
            # from.
            baseline_run = _run_projection_tracked(db, cleanup)
            assert baseline_run.status == "ok", baseline_run.error
            projected = UnifiedCapability.query.filter(
                UnifiedCapability.source_table == "business_capability",
                UnifiedCapability.source_id == str(cap.id),
            ).one()
            assert projected.current_maturity_level == 2

            # Raw-SQL write, bypassing every ORM listener -- the only way to
            # make the source diverge from the projection without the write
            # itself re-syncing it.
            db.session.execute(
                db.text(
                    "UPDATE business_capability SET current_maturity_level = :lvl "
                    "WHERE id = :id AND organization_id = :org_id"
                ),
                {"lvl": 5, "id": cap.id, "org_id": org.id},
            )
            db.session.commit()

            # Hold the lock on a separate connection to simulate a concurrent
            # invocation, then assert the job records a skip rather than a
            # silent no-op or a second run.
            with job_lock("capability_projection", required=True):
                skipped_run = _run_projection_tracked(db, cleanup)
                assert skipped_run.status == "skipped_locked"
                # D-10: the skip must not have reconciled the divergence --
                # the stale value from before the raw-SQL write must persist.
                db.session.expire_all()
                still_stale = UnifiedCapability.query.get(projected.id)
                assert still_stale.current_maturity_level == 2

            # D-10: once the contending lock is released, exactly one real
            # run must go through and reconcile the divergence -- the skip
            # must not have swallowed the work permanently.
            real_run = _run_projection_tracked(db, cleanup)
            assert real_run.status == "ok", real_run.error
            db.session.expire_all()
            reconciled = UnifiedCapability.query.get(projected.id)
            assert reconciled.current_maturity_level == 5
            assert (
                UnifiedCapability.query.filter(
                    UnifiedCapability.source_table == "business_capability",
                    UnifiedCapability.source_id == str(cap.id),
                ).count()
                == 1
            )

    def test_ac7_job_not_registered_under_testing(self, app):
        scheduler = app.extensions.get("ea_workflow_scheduler")
        # init_scheduler returns early under app.testing, so no scheduler —
        # and therefore no job id — is registered at all in a testing app.
        assert app.testing is True
        assert scheduler is None or scheduler.get_job("capability_projection") is None

    def test_ac7b_registration_function_registers_job_when_not_testing(self, app):
        """D-8: the assertion above is true unconditionally under app.testing —
        it would still pass even if the whole registration block in
        extensions.py were deleted. This test calls init_scheduler() directly
        with app.testing forced False, so it can actually distinguish
        "registered" from "not registered": if the registration block were
        removed, this test — not the one above — would go red.
        """
        from app._bootstrap.extensions import init_scheduler

        original_testing = app.testing
        app.testing = False
        try:
            init_scheduler(app)
            scheduler = app.extensions.get("ea_workflow_scheduler")
            assert scheduler is not None, (
                "init_scheduler() did not register a scheduler at all when "
                "app.testing=False"
            )
            job = scheduler.get_job("capability_projection")
            assert job is not None, (
                "capability_projection job was not registered by "
                "init_scheduler() when app.testing=False"
            )
        finally:
            # R3-9: read the scheduler out of app.extensions here, not out of
            # a local variable captured earlier in the `try` block. If
            # init_scheduler() starts the APScheduler instance and registers
            # it into app.extensions but then raises before this function's
            # own `scheduler = app.extensions.get(...)` line runs, a local
            # variable set only on the success path would stay unset (or
            # stale None) and this teardown would silently leak a running
            # BackgroundScheduler into the rest of the pytest session.
            # Re-reading app.extensions here catches that case too.
            leaked_scheduler = app.extensions.get("ea_workflow_scheduler")
            if leaked_scheduler is not None:
                try:
                    leaked_scheduler.shutdown(wait=False)
                except Exception:
                    pass
                app.extensions.pop("ea_workflow_scheduler", None)
            app.testing = original_testing

    def test_ac9_accessor_returns_no_maturity_recorded_not_zero(self, app, db, cleanup):
        _ensure_provenance_index(db)
        from app.models.unified_capability import UnifiedCapability

        with app.app_context():
            org = _make_org(db, "ac9")
            cleanup["organizations"].append(org.id)
            cap = _make_capability(db, org, name="AC9 no maturity", current=None, target=None)
            cleanup["business_capability"].append(cap.id)

            run = _run_projection_tracked(db, cleanup)
            assert run.status == "ok", run.error
            assert UnifiedCapability.query.filter(
                UnifiedCapability.source_table == "business_capability",
                UnifiedCapability.source_id == str(cap.id),
            ).count() == 1

            result = UnifiedCapability.maturity_for_source(
                "business_capability", cap.id, organization_id=org.id
            )
            assert result["reason_code"] == "no_maturity_recorded"
            assert result["current_maturity_level"] is None
            assert result["current_maturity_level"] != 0

    def test_accessor_missing_source_row_returns_no_maturity_recorded(self, app):
        from app.models.unified_capability import UnifiedCapability

        with app.app_context():
            result = UnifiedCapability.maturity_for_source("business_capability", -999999)
            assert result["reason_code"] == "no_maturity_recorded"

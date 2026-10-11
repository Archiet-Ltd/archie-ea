"""T-003 / task 03 acceptance criteria 1, 2, 5, 6."""

from __future__ import annotations

import time



def _make_element(db_session, org_id, name_hint, type_="ApplicationComponent"):
    from app.models import ArchiMateElement

    row = ArchiMateElement(
        name=f"E-{name_hint}", type=type_, layer="application", organization_id=org_id
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_relationship(db_session, org_id, source, target, type_):
    from app.models import ArchiMateRelationship

    row = ArchiMateRelationship(
        source_id=source.id, target_id=target.id, type=type_, organization_id=org_id
    )
    db_session.add(row)
    db_session.flush()
    return row


def _insert_stale_row(db_session, org_id, source, target, **overrides):
    import datetime as _dt

    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    params = dict(
        organization_id=org_id,
        source_element_id=source.id,
        target_element_id=target.id,
        derived_type="Association",
        rule_id="fallback:X:X",
        chain=[999],
        chain_element_ids=[source.id, target.id],
        depth=1,
        confidence="1.00",
        provenance="derivation",
        engine_version="0.0.0-stale-fixture",
        computed_at=_dt.datetime.utcnow(),
        stale=True,
        stale_since=_dt.datetime.utcnow(),
        stale_reason="relationship_updated",
    )
    params.update(overrides)
    row = DerivedRelationship(**params)
    db_session.add(row)
    db_session.flush()
    return row


# --- Acceptance item 1 (brief 8): sweep visits only stale-carrying tenants --


def test_recompute_visits_only_stale_carrying_tenants(app, db_session, make_org):
    from app.modules.intelligence.services.recompute_job import recompute_derived_facts

    org_stale = make_org("rc-stale")
    org_clean = make_org("rc-clean")
    a = _make_element(db_session, org_stale.id, "a")
    b = _make_element(db_session, org_stale.id, "b")
    _make_relationship(db_session, org_stale.id, a, b, "Serving")
    _insert_stale_row(db_session, org_stale.id, a, b)
    db_session.commit()

    org_stale_id, org_clean_id = org_stale.id, org_clean.id

    run = recompute_derived_facts(app)

    visited_ids = {r.organization_id for r in run.results}
    assert org_stale_id in visited_ids
    assert org_clean_id not in visited_ids
    assert all(r.ok for r in run.results if r.organization_id == org_stale_id)


def test_recompute_one_tenant_failure_does_not_abort_the_others(
    app, db_session, make_org, monkeypatch
):
    from app.modules.intelligence.services import recompute_job

    org_a = make_org("rc-fail-a")
    org_b = make_org("rc-fail-b")
    a1 = _make_element(db_session, org_a.id, "a1")
    a2 = _make_element(db_session, org_a.id, "a2")
    _make_relationship(db_session, org_a.id, a1, a2, "Serving")
    _insert_stale_row(db_session, org_a.id, a1, a2)

    b1 = _make_element(db_session, org_b.id, "b1")
    b2 = _make_element(db_session, org_b.id, "b2")
    _make_relationship(db_session, org_b.id, b1, b2, "Serving")
    _insert_stale_row(db_session, org_b.id, b1, b2)
    db_session.commit()

    org_a_id, org_b_id = org_a.id, org_b.id

    real_one_tenant = recompute_job._recompute_one_tenant

    def _flaky(organization_id):
        if organization_id == org_a_id:
            raise RuntimeError("simulated failure for org_a")
        return real_one_tenant(organization_id, trigger="on_demand")

    monkeypatch.setattr(recompute_job, "_recompute_one_tenant", _flaky)

    run = recompute_job.run_for_each_tenant(
        app,
        "derived_facts_recompute_test",
        _flaky,
        organization_ids=[org_a_id, org_b_id],
        use_lock=False,
    )

    assert run.failed == 1
    assert run.succeeded == 1
    failure_org_ids = {f["organization_id"] for f in run.as_dict()["failures"]}
    assert failure_org_ids == {org_a_id}


# --- Acceptance item 2 (brief 9): concurrency, shared lock name -------------


def test_scheduled_and_on_demand_resolve_to_the_same_lock_name(app):
    from app.modules.intelligence.services.recompute_job import per_tenant_lock_name

    assert per_tenant_lock_name(42) == per_tenant_lock_name(42)
    assert per_tenant_lock_name(42) != per_tenant_lock_name(43)
    assert per_tenant_lock_name(42) == "derived_facts_recompute:org:42"


def test_held_lock_reports_skipped_locked_not_silent_success(app, db_session, make_org):
    from app.jobs.tenant_safe_job import job_lock
    from app.modules.intelligence.services.recompute_job import (
        per_tenant_lock_name,
        recompute_derived_facts_on_demand,
    )

    org = make_org("rc-locked")
    a = _make_element(db_session, org.id, "a")
    b = _make_element(db_session, org.id, "b")
    _make_relationship(db_session, org.id, a, b, "Serving")
    db_session.commit()
    org_id = org.id

    with app.app_context():
        with job_lock(per_tenant_lock_name(org_id), required=True):
            run = recompute_derived_facts_on_demand(app, org_id)

    assert run.results, "the on-demand path must still attempt the tenant"
    result = run.results[0]
    assert result.ok is True
    assert result.value.get("skipped_locked") is True


def test_different_tenant_recompute_proceeds_while_one_tenant_is_locked(
    app, db_session, make_org
):
    from app.jobs.tenant_safe_job import job_lock
    from app.modules.intelligence.services.recompute_job import (
        per_tenant_lock_name,
        recompute_derived_facts_on_demand,
    )

    org_locked = make_org("rc-locked-a")
    org_free = make_org("rc-free-b")
    a = _make_element(db_session, org_free.id, "a")
    b = _make_element(db_session, org_free.id, "b")
    _make_relationship(db_session, org_free.id, a, b, "Serving")
    db_session.commit()

    org_locked_id, org_free_id = org_locked.id, org_free.id

    with app.app_context():
        with job_lock(per_tenant_lock_name(org_locked_id), required=True):
            run = recompute_derived_facts_on_demand(app, org_free_id)

    assert run.results[0].ok is True
    assert run.results[0].value.get("skipped_locked") is False


def test_on_demand_recompute_records_non_zero_wall_clock_duration(
    app, db_session, make_org, monkeypatch
):
    from app.modules.intelligence.services.derivation_runner import DerivationRunner
    from app.modules.intelligence.services.recompute_job import (
        recompute_derived_facts_on_demand,
    )

    org = make_org("rc-duration")
    a = _make_element(db_session, org.id, "a")
    b = _make_element(db_session, org.id, "b")
    c = _make_element(db_session, org.id, "c")
    _make_relationship(db_session, org.id, a, b, "Serving")
    _make_relationship(db_session, org.id, b, c, "Serving")
    db_session.commit()

    original = DerivationRunner.run_and_persist

    def _slow_real_run(self, organization_id, *, trigger):
        time.sleep(0.02)
        return original(self, organization_id, trigger=trigger)

    monkeypatch.setattr(DerivationRunner, "run_and_persist", _slow_real_run)

    run = recompute_derived_facts_on_demand(app, org.id)

    assert run.results[0].ok is True
    assert run.results[0].duration_ms > 0
    assert run.results[0].value["duration_ms"] > 0
    assert run.results[0].duration_ms >= run.results[0].value["duration_ms"]


# --- Acceptance item 5 (brief 16): job registration parity ------------------


def test_derived_recompute_job_registered_with_max_instances_one(monkeypatch):
    """Exercises the real ``init_scheduler`` registration block against a
    fake ``BackgroundScheduler`` that records ``add_job`` calls, so the
    assertion is against the real source, not a duplicated description of it.
    """

    from app._bootstrap.extensions import init_scheduler

    added_jobs = []

    class _FakeScheduler:
        def add_job(self, **kwargs):
            added_jobs.append(kwargs)

        def start(self):
            pass

        def pause(self):
            pass

        def shutdown(self, wait=False):
            pass

    monkeypatch.setattr(
        "apscheduler.schedulers.background.BackgroundScheduler", _FakeScheduler
    )

    class _StubLogger:
        def info(self, *a, **k):
            pass

        def warning(self, *a, **k):
            pass

        def error(self, *a, **k):
            pass

    class _StubApp:
        testing = False
        config = {
            "DERIVED_RECOMPUTE_INTERVAL_MINUTES": "10",
            "CAPABILITY_PROJECTION_INTERVAL_MINUTES": "15",
        }
        extensions = {}
        logger = _StubLogger()

    stub = _StubApp()
    init_scheduler(stub)

    derived_jobs = [j for j in added_jobs if j.get("id") == "derived_facts_recompute"]
    assert len(derived_jobs) == 1
    assert derived_jobs[0]["max_instances"] == 1
    assert derived_jobs[0]["replace_existing"] is True


def test_init_scheduler_still_returns_early_under_app_testing():
    from app._bootstrap.extensions import init_scheduler

    class _StubApp:
        testing = True

    # Must return without touching scheduler machinery at all.
    assert init_scheduler(_StubApp()) is None


def test_recompute_reimplements_no_tenant_enumeration_or_locking():
    """Job-registration parity (brief item 16): the job body IS
    ``run_for_each_tenant`` -- assert by source inspection that
    ``recompute_derived_facts`` calls it rather than hand-rolling a loop.
    """
    import inspect

    from app.modules.intelligence.services.recompute_job import recompute_derived_facts

    source = inspect.getsource(recompute_derived_facts)
    assert "run_for_each_tenant" in source
    assert "for organization_id in" not in source
    assert "pg_try_advisory_lock" not in source, "locking belongs to job_lock, not here"

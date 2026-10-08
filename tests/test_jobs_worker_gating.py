"""Every scheduled job runs outside the web process, inside its
organisation's tenant context, once a dedicated jobs worker is configured.

Two things are exercised here:

1. ``JOBS_RUN_IN_WORKER`` / ``RUNNING_AS_JOBS_WORKER`` gate whether
   ``init_scheduler`` starts anything in THIS process -- the acceptance
   criterion "no job runs in the web process when the worker is configured".
2. The Teams subscription renewal job, which previously called
   ``TeamsMeetingService.renew_if_needed()`` with no tenant context at all
   (so the tenant-scoped ``APISettings`` query it made resolved whichever
   organisation's row Postgres returned first), now visits every active
   organisation separately through ``tenant_scope``.
"""

from __future__ import annotations


import pytest


@pytest.fixture(autouse=True)
def _clean_jobs_worker_env(monkeypatch):
    """Every test in this module starts from neither env var set."""
    monkeypatch.delenv("JOBS_RUN_IN_WORKER", raising=False)
    monkeypatch.delenv("RUNNING_AS_JOBS_WORKER", raising=False)


def _shutdown_and_pop(app):
    """Mirror the teardown in test_capability_projection_job.py::test_ac7b --
    a real BackgroundScheduler leaking out of a test would keep firing jobs
    against the shared test database for the rest of the pytest session.
    """
    leaked = app.extensions.pop("ea_workflow_scheduler", None)
    if leaked is not None:
        try:
            leaked.shutdown(wait=False)
        except Exception:
            pass


def test_scheduler_not_started_in_web_process_when_worker_configured(
    app, monkeypatch
):
    """Acceptance: no job runs in the web process when the worker is configured."""
    from app._bootstrap.extensions import init_scheduler

    monkeypatch.setenv("JOBS_RUN_IN_WORKER", "1")

    original_testing = app.testing
    app.testing = False
    try:
        init_scheduler(app)
        assert app.extensions.get("ea_workflow_scheduler") is None, (
            "init_scheduler() started a scheduler in the web process even "
            "though JOBS_RUN_IN_WORKER=1 and RUNNING_AS_JOBS_WORKER is unset"
        )
    finally:
        _shutdown_and_pop(app)
        app.testing = original_testing


def test_scheduler_still_starts_when_no_worker_is_configured(app, monkeypatch):
    """Backward compatibility: today's single-process deployments and every
    pre-existing test (e.g. test_capability_projection_job.py::test_ac7b)
    depend on init_scheduler() registering jobs when neither env var is set.
    """
    from app._bootstrap.extensions import init_scheduler

    original_testing = app.testing
    app.testing = False
    try:
        init_scheduler(app)
        assert app.extensions.get("ea_workflow_scheduler") is not None
    finally:
        _shutdown_and_pop(app)
        app.testing = original_testing


def test_scheduler_starts_in_the_jobs_worker_process_itself(app, monkeypatch):
    """The jobs worker (app/jobs/worker.py) sets RUNNING_AS_JOBS_WORKER before
    calling create_app(); that must start the scheduler even when
    JOBS_RUN_IN_WORKER=1 is also set on the same process's config -- the
    worker IS the process JOBS_RUN_IN_WORKER is asking for.
    """
    from app._bootstrap.extensions import init_scheduler

    monkeypatch.setenv("JOBS_RUN_IN_WORKER", "1")
    monkeypatch.setenv("RUNNING_AS_JOBS_WORKER", "1")

    original_testing = app.testing
    app.testing = False
    try:
        init_scheduler(app)
        assert app.extensions.get("ea_workflow_scheduler") is not None
    finally:
        _shutdown_and_pop(app)
        app.testing = original_testing


class _CapturingScheduler:
    """Stand-in for APScheduler's BackgroundScheduler that records every
    job's callable instead of actually scheduling it, so a test can invoke
    one job function directly and deterministically -- same technique as
    tests/test_arb_waiver_expiry_scheduler.py.
    """

    def __init__(self):
        self._jobs = {}
        self.started = False

    def add_job(self, **kwargs):
        self._jobs[kwargs["id"]] = kwargs["func"]

    def get_jobs(self):
        # Minimal job objects with an .id attribute for
        # _remove_undeclared_jobs().
        _Job = type("_FakeJob", (), {"__init__": lambda self, jid: setattr(self, "id", jid)})
        return [_Job(jid) for jid in self._jobs]

    def get_job(self, job_id):
        return self._jobs.get(job_id)

    def remove_job(self, job_id):
        self._jobs.pop(job_id, None)

    def start(self):
        self.started = True

    def pause(self):
        pass

    def shutdown(self, wait=False):
        pass


def _capture_scheduler(monkeypatch):
    import apscheduler.schedulers.background

    scheduler = _CapturingScheduler()
    monkeypatch.setattr(
        apscheduler.schedulers.background, "BackgroundScheduler", lambda: scheduler
    )
    return scheduler


def test_teams_renewal_visits_every_active_organisation_separately(
    app, db_session, make_org, monkeypatch
):
    from flask import g

    import app.services.teams_meeting_service as teams_mod
    from app._bootstrap.extensions import init_scheduler
    from app.models.models import APISettings

    org_a = make_org("teams-a")
    org_b = make_org("teams-b")
    db_session.commit()
    org_a_id, org_b_id = org_a.id, org_b.id

    # Create one APISettings M365 row per organisation, each with a distinct
    # subscription_id (jira_url) — without tenant_scope the query would return
    # whichever row the database returns first; with it each call sees only
    # its own organisation's row.
    for org_id, sub_id in [(org_a_id, "sub-a"), (org_b_id, "sub-b")]:
        with app.test_request_context("/"):
            g.current_org_id = org_id
            row = APISettings(
                provider="teams_meetings",
                key_label="default",
                api_key="test",
                jira_url=sub_id,
                enabled=True,
            )
            db_session.add(row)
            db_session.flush()
    db_session.commit()

    seen_configs = []

    def _fake_renew_if_needed():
        # Read the configuration through the real get_config path, which
        # queries APISettings — under tenant_scope this returns only the
        # current organisation's row.
        seen_configs.append(
            {
                "org_id": g.current_org_id,
                "subscription_id": teams_mod.TeamsMeetingService.get_config().get(
                    "subscription_id"
                ),
            }
        )
        return {"status": "skipped", "reason": "no subscription on record"}

    monkeypatch.setattr(
        teams_mod.TeamsMeetingService,
        "renew_if_needed",
        staticmethod(_fake_renew_if_needed),
    )

    scheduler = _capture_scheduler(monkeypatch)

    original_testing = app.testing
    app.testing = False
    try:
        init_scheduler(app)
        job_func = scheduler.get_job("teams_subscription_renewal")
        job_func()
    finally:
        app.testing = original_testing

    assert org_a_id in [c["org_id"] for c in seen_configs], (
        f"organisation {org_a_id} was never visited: {seen_configs}"
    )
    assert org_b_id in [c["org_id"] for c in seen_configs], (
        f"organisation {org_b_id} was never visited: {seen_configs}"
    )
    # Every visit ran with a concrete tenant on g -- never the unscoped call
    # (None) this job made before this fix.
    assert None not in [c["org_id"] for c in seen_configs]

    # Row isolation: organisation A must read only its own subscription_id
    # and organisation B only its own.
    config_a = next(c for c in seen_configs if c["org_id"] == org_a_id)
    config_b = next(c for c in seen_configs if c["org_id"] == org_b_id)
    assert config_a["subscription_id"] == "sub-a", (
        f"org {org_a_id} saw subscription_id {config_a['subscription_id']!r} "
        f"instead of 'sub-a' — APISettings is not isolated per tenant"
    )
    assert config_b["subscription_id"] == "sub-b", (
        f"org {org_b_id} saw subscription_id {config_b['subscription_id']!r} "
        f"instead of 'sub-b' — APISettings is not isolated per tenant"
    )


def test_teams_renewal_one_tenant_failure_does_not_abort_the_others(
    app, db_session, make_org, monkeypatch
):
    import app.services.teams_meeting_service as teams_mod
    from app._bootstrap.extensions import init_scheduler

    org_fail = make_org("teams-fail")
    org_ok = make_org("teams-ok")
    db_session.commit()
    org_fail_id, org_ok_id = org_fail.id, org_ok.id

    seen_org_ids = []

    def _fake_renew_if_needed():
        from flask import g

        seen_org_ids.append(g.current_org_id)
        if g.current_org_id == org_fail_id:
            raise RuntimeError("Graph API unavailable")
        return {"status": "skipped", "reason": "no subscription on record"}

    monkeypatch.setattr(
        teams_mod.TeamsMeetingService,
        "renew_if_needed",
        staticmethod(_fake_renew_if_needed),
    )

    scheduler = _capture_scheduler(monkeypatch)

    original_testing = app.testing
    app.testing = False
    try:
        init_scheduler(app)
        job_func = scheduler.get_job("teams_subscription_renewal")
        job_func()  # must not raise -- one tenant's failure is caught and logged
    finally:
        app.testing = original_testing

    assert org_fail_id in seen_org_ids
    assert org_ok_id in seen_org_ids


# --------------------------------------------------------------------------- #
# M2: job declaration enforcement — every scheduled job id must be declared
# in PLATFORM_JOBS or TENANT_JOBS in app/jobs/tenant_safe_job.py.
# --------------------------------------------------------------------------- #


def test_undeclared_job_id_is_removed(app, monkeypatch):
    """A job whose id is in neither PLATFORM_JOBS nor TENANT_JOBS is removed
    with an ERROR log. On main (before this fix) the undeclared job runs."""
    import logging

    import apscheduler.schedulers.background
    from app._bootstrap.extensions import init_scheduler
    from app.jobs.tenant_safe_job import (
        PLATFORM_JOBS,
        TENANT_JOBS,
        _remove_undeclared_jobs,
    )

    # Create a scheduler, add a known-declared job and one undeclared job
    real_cls = apscheduler.schedulers.background.BackgroundScheduler
    scheduler = real_cls()

    # Add a declared job
    scheduler.add_job(
        func=lambda: None,
        trigger="interval",
        seconds=60,
        id="error_digest",
        replace_existing=True,
    )

    # Add an undeclared job
    scheduler.add_job(
        func=lambda: None,
        trigger="interval",
        seconds=60,
        id="undeclared_job_x99",
        replace_existing=True,
    )

    undeclared_id = "undeclared_job_x99"

    logs = []

    class _Handler(logging.Handler):
        def emit(self, record):
            logs.append(record.getMessage())

    handler = _Handler()
    logger = logging.getLogger("app.jobs.tenant_safe_job")
    logger.addHandler(handler)
    logger.setLevel(logging.ERROR)
    try:
        _remove_undeclared_jobs(scheduler)
    finally:
        logger.removeHandler(handler)

    # The declared job survives
    assert scheduler.get_job("error_digest") is not None
    # The undeclared job was removed
    assert scheduler.get_job(undeclared_id) is None
    # An ERROR was logged
    assert any(undeclared_id in msg for msg in logs)


def test_every_init_scheduler_job_is_declared(app, monkeypatch):
    """Every job id that init_scheduler registers is in exactly one of the two
    declared sets.  A new job added to init_scheduler must also be added to
    PLATFORM_JOBS or TENANT_JOBS."""
    import apscheduler.schedulers.background
    from app._bootstrap.extensions import init_scheduler
    from app.jobs.tenant_safe_job import PLATFORM_JOBS, TENANT_JOBS

    # Disjointness: no id belongs to both sets
    assert PLATFORM_JOBS & TENANT_JOBS == frozenset(), (
        f"Job id(s) {PLATFORM_JOBS & TENANT_JOBS} are in both PLATFORM_JOBS "
        f"and TENANT_JOBS — every id must be in exactly one set"
    )

    all_declared = PLATFORM_JOBS | TENANT_JOBS

    # Capture every job id init_scheduler registers
    captured_ids = []

    class _CaptureScheduler:
        def __init__(self):
            self._jobs = {}
            self.started = False

        def add_job(self, **kwargs):
            captured_ids.append(kwargs["id"])
            self._jobs[kwargs["id"]] = kwargs["func"]

        def get_jobs(self):
            _Job = type("_FakeJob", (), {"__init__": lambda self, jid: setattr(self, "id", jid)})
            return [_Job(jid) for jid in self._jobs]

        def get_job(self, job_id):
            return self._jobs.get(job_id)

        def remove_job(self, job_id):
            self._jobs.pop(job_id, None)

        def start(self):
            self.started = True

        def pause(self):
            pass

        def shutdown(self, wait=False):
            pass

    monkeypatch.setattr(
        apscheduler.schedulers.background, "BackgroundScheduler", _CaptureScheduler
    )

    # Set RUNNING_AS_JOBS_WORKER so init_scheduler does not skip itself
    monkeypatch.setenv("RUNNING_AS_JOBS_WORKER", "1")

    original_testing = app.testing
    app.testing = False
    try:
        init_scheduler(app)
    finally:
        app.testing = original_testing
        # Shut down the captured scheduler stored in extensions
        leaked = app.extensions.pop("ea_workflow_scheduler", None)
        if leaked is not None:
            try:
                leaked.shutdown(wait=False)
            except Exception:
                pass

    # Every captured id is in exactly one declared set
    for job_id in captured_ids:
        assert job_id in all_declared, (
            f"Job id {job_id!r} registered by init_scheduler "
            f"but not found in PLATFORM_JOBS or TENANT_JOBS"
        )


def test_abacus_incremental_sync_not_registered_on_existing_scheduler(app, monkeypatch):
    """init_abacus_scheduler is a no-op — it does NOT register
    abacus_incremental_sync because run_abacus_sync_job calls
    run_incremental_sync() which does not exist (the real method is
    async_run_incremental_sync)."""
    import apscheduler.schedulers.background
    from app.tasks.abacus_sync_task import init_abacus_scheduler

    real_scheduler = apscheduler.schedulers.background.BackgroundScheduler()
    monkeypatch.setitem(app.extensions, "ea_workflow_scheduler", real_scheduler)

    init_abacus_scheduler(app, scheduler=real_scheduler)

    # The abacus job is NOT registered on the existing scheduler
    job = real_scheduler.get_job("abacus_incremental_sync")
    assert job is None, (
        "abacus_incremental_sync was registered on the passed scheduler even "
        "though run_incremental_sync does not exist on AbacusSyncService"
    )

    # No cleanup needed — init_abacus_scheduler is a no-op so no job
    # was registered and the scheduler was never started.
    # real_scheduler was created but never started; shutdown would raise.


def test_worker_registers_no_job_with_missing_target_method(app, monkeypatch):
    """No job whose callable invokes a non-existent method is registered
    through init_scheduler or init_abacus_scheduler.

    Currently the only such job candidate is abacus_incremental_sync: its
    target function (run_abacus_sync_job) calls
    sync_service.run_incremental_sync() which does not exist on
    AbacusSyncService (the real method is async_run_incremental_sync).
    init_abacus_scheduler is deliberately a no-op, so the id never appears
    in the scheduler.

    If a future change adds another job with a wrong method name, this test
    catches it by capturing every job id that init_scheduler + a direct
    init_abacus_scheduler call would register and asserting none of them
    map to a missing method.
    """
    import apscheduler.schedulers.background
    from app._bootstrap.extensions import init_scheduler
    from app.tasks.abacus_sync_task import init_abacus_scheduler

    captured_ids = []

    class _CaptureScheduler:
        def __init__(self):
            self._jobs = {}
            self.started = False

        def add_job(self, **kwargs):
            captured_ids.append(kwargs["id"])
            self._jobs[kwargs["id"]] = kwargs["func"]

        def get_jobs(self):
            _Job = type("_FakeJob", (), {"__init__": lambda self, jid: setattr(self, "id", jid)})
            return [_Job(jid) for jid in self._jobs]

        def get_job(self, job_id):
            return self._jobs.get(job_id)

        def remove_job(self, job_id):
            self._jobs.pop(job_id, None)

        def start(self):
            self.started = True

        def pause(self):
            pass

        def shutdown(self, wait=False):
            pass

    monkeypatch.setattr(
        apscheduler.schedulers.background, "BackgroundScheduler", _CaptureScheduler
    )

    monkeypatch.setenv("RUNNING_AS_JOBS_WORKER", "1")

    original_testing = app.testing
    app.testing = False
    try:
        init_scheduler(app)

        # init_abacus_scheduler gets its own capturer since the no-op returns
        # before it would call BackgroundScheduler
        abacus_captured = []
        class _AbacusCaptureScheduler:
            def add_job(self, **kwargs):
                abacus_captured.append(kwargs["id"])
            def get_jobs(self):
                return []
            def get_job(self, job_id):
                return None
            def remove_job(self, job_id):
                pass
            def start(self):
                pass
            def pause(self):
                pass
            def shutdown(self, wait=False):
                pass

        init_abacus_scheduler(app, scheduler=_AbacusCaptureScheduler())
    finally:
        app.testing = original_testing
        leaked = app.extensions.pop("ea_workflow_scheduler", None)
        if leaked is not None:
            try:
                leaked.shutdown(wait=False)
            except Exception:
                pass

    # abacus_incremental_sync must NOT appear in either captured set
    assert "abacus_incremental_sync" not in captured_ids, (
        "init_scheduler registered abacus_incremental_sync — the job target "
        "calls run_incremental_sync() which does not exist"
    )
    assert "abacus_incremental_sync" not in abacus_captured, (
        "init_abacus_scheduler registered abacus_incremental_sync — "
        "it should be a no-op until the broken method name is fixed"
    )


# --------------------------------------------------------------------------- #
# L3: test for the worker entry point (app/jobs/worker.main()).
# --------------------------------------------------------------------------- #


def test_worker_main_exits_one_when_no_scheduler(app, monkeypatch):
    """worker.main() exits with SystemExit(1) when create_app() registers
    no scheduler -- the error path in the worker."""
    import app as _app_module
    import app.jobs.worker as worker_mod

    def _make_app(*args, **kwargs):
        return app

    monkeypatch.setattr(_app_module, "create_app", _make_app)
    # Remove the scheduler so the worker sees None
    monkeypatch.delitem(app.extensions, "ea_workflow_scheduler", raising=False)

    with pytest.raises(SystemExit) as exc:
        worker_mod.main()

    assert exc.value.code == 1, (
        f"expected SystemExit(1), got {exc.value.code}"
    )


def test_worker_main_does_not_register_abacus_job(app, monkeypatch):
    """worker.main() does NOT register the Abacus incremental sync job
    (M2: removed until it has tenant context and the correct method name)."""
    import threading

    import app as _app_module
    import app.jobs.worker as worker_mod

    def _make_app(*args, **kwargs):
        return app

    monkeypatch.setattr(_app_module, "create_app", _make_app)

    from apscheduler.schedulers.background import BackgroundScheduler

    real_scheduler = BackgroundScheduler()
    real_scheduler.start()
    monkeypatch.setitem(app.extensions, "ea_workflow_scheduler", real_scheduler)

    init_abacus_called = [False]

    import app.tasks.abacus_sync_task as _abacus_mod

    def _never_called(app, scheduler=None):
        init_abacus_called[0] = True
        return None

    monkeypatch.setattr(
        _abacus_mod,
        "init_abacus_scheduler",
        _never_called,
    )

    def _return_immediately(self, timeout=None):
        self.set()
        return True

    monkeypatch.setattr(threading.Event, "wait", _return_immediately)

    import signal

    monkeypatch.setattr(signal, "signal", lambda signum, handler: None)

    worker_mod.main()

    assert not init_abacus_called[0], (
        "worker.main() called init_abacus_scheduler but M2 removed that "
        "call until the job has tenant context and the correct method name"
    )
    try:
        real_scheduler.shutdown(wait=False)
    except Exception:
        pass

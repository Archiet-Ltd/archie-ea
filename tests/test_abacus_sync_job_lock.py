"""The Abacus sync schedule is a named platform job (there is one
ExternalSystem row for the whole platform, not one per tenant -- see the
module docstring on app/tasks/abacus_sync_task.py), so it is guarded by a
cross-process advisory lock instead of tenant_scope.  The job is not
currently registered by the worker (see M2 in the review); these tests
verify the lock behaviour for when it is re-registered.
"""

from __future__ import annotations


def test_abacus_sync_skipped_when_lock_held_by_another_process(app, monkeypatch):
    from app.jobs.tenant_safe_job import job_lock
    from app.tasks import abacus_sync_task

    calls = []
    monkeypatch.setattr(
        abacus_sync_task, "get_sync_service", lambda: calls.append("called")
    )

    with app.app_context():
        with job_lock("abacus_incremental_sync", required=True):
            # A second, concurrent invocation (e.g. another gunicorn/worker
            # replica) must see the lock held and do nothing -- not raise,
            # not run the sync a second time.
            abacus_sync_task.run_abacus_sync_job(app)

    assert calls == [], "the sync ran even though another process held the lock"


def test_abacus_sync_runs_when_lock_is_free(app, monkeypatch):
    from app.tasks import abacus_sync_task

    calls = []

    class _FakeSyncService:
        async def run_incremental_sync(self):
            calls.append("ran")
            return {"status": "ok"}

    monkeypatch.setattr(
        abacus_sync_task, "get_sync_service", lambda: _FakeSyncService()
    )

    abacus_sync_task.run_abacus_sync_job(app)

    assert calls == ["ran"]

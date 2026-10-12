"""The CSP real-app smoke must not leave a live scheduler in the pytest process.

``tests/csp/verify_real_pages.py`` boots ``create_app("development")`` inside the
test process. That config starts APScheduler, whose event-log relay then polls
the shared database every few seconds for the rest of the session. Tests that
ran afterwards in the same CI shard hit it as intermittent failures that pass
when run alone: a deadlock on ``transformation_outbox_events`` and the relay
borrowing the ``db.session`` that the gateway savepoint tests patch in.
"""
import importlib.util
import os
import threading
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "csp" / "verify_real_pages.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("verify_real_pages_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scheduler_threads():
    return [t for t in threading.enumerate() if t.name == "APScheduler" and t.is_alive()]


class _FakeScheduler:
    running = True

    def __init__(self):
        self.shutdown_calls = []

    def shutdown(self, wait=True):
        self.shutdown_calls.append(wait)
        self.running = False


class _FakeApp:
    def __init__(self, scheduler):
        self.extensions = {"ea_workflow_scheduler": scheduler}


def test_main_stops_the_scheduler_even_when_the_smoke_raises(monkeypatch):
    module = _load_script()
    scheduler = _FakeScheduler()
    monkeypatch.setattr(module, "_boot_app", lambda: _FakeApp(scheduler))

    def boom(_app):
        raise RuntimeError("login seeding failed")

    monkeypatch.setattr(module, "_main", boom)
    with pytest.raises(RuntimeError, match="login seeding failed"):
        module.main()
    assert scheduler.shutdown_calls == [True], "must wait for an in-flight job"


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ, reason="boots the real app against the test database"
)
def test_real_development_boot_leaves_no_scheduler_thread():
    before = len(_scheduler_threads())
    module = _load_script()
    app = module._boot_app()
    try:
        assert app.extensions.get("ea_workflow_scheduler") is not None, (
            "the development boot no longer starts a scheduler; this test is moot"
        )
    finally:
        module._stop_background_work(app)
    assert len(_scheduler_threads()) == before
    assert "ea_workflow_scheduler" not in app.extensions

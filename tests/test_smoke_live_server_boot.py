"""Smoke live-server boot repairs nullable schema drift before serving pages."""

from __future__ import annotations

from types import SimpleNamespace

from tests.smoke import conftest as smoke_conftest


class _DummyRequest:
    def __init__(self):
        self.finalizers = []
        self.session = SimpleNamespace(testsfailed=0)

    def addfinalizer(self, finalizer):
        self.finalizers.append(finalizer)


class _FakeResponse:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return b"ok"


class _FakeProcess:
    def __init__(self):
        self._poll = None

    def poll(self):
        return self._poll

    def terminate(self):
        self._poll = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self._poll = -9


def test_boot_live_server_reconciles_schema_before_starting(app, monkeypatch):
    request = _DummyRequest()
    process = _FakeProcess()
    reconcile_calls = []

    db_url = app.config["SQLALCHEMY_DATABASE_URI"]
    monkeypatch.setenv("TEST_DATABASE_URL", db_url)
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setattr(smoke_conftest, "_free_port", lambda: 43123)
    monkeypatch.setattr(smoke_conftest, "_has_gunicorn", lambda: False)
    monkeypatch.setattr(
        smoke_conftest,
        "subprocess",
        SimpleNamespace(Popen=lambda *args, **kwargs: process, STDOUT=smoke_conftest.subprocess.STDOUT),
    )
    monkeypatch.setattr(
        "app.commands.reconcile_schema._reconcile",
        lambda dry_run=False: (reconcile_calls.append(dry_run) or [], [], [], []),
    )

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: _FakeResponse())

    server = smoke_conftest.boot_live_server(request, ai_protocol_stub=None, app=app)

    try:
        assert str(server) == "http://127.0.0.1:43123"
        assert reconcile_calls == [False]
        assert request.finalizers, "boot_live_server should register server cleanup"
    finally:
        for finalizer in reversed(request.finalizers):
            finalizer()

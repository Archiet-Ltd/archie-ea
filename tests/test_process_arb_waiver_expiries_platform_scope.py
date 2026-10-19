"""The manual waiver-expiry command must run inside platform_scope, like the scheduler.

Without it the row-level-security policies hide every due waiver from the
runtime role, so the command reports nothing to expire.
"""

from __future__ import annotations

from types import SimpleNamespace

from flask import Flask, g

from app.commands import process_arb_waiver_expiries as command_module


def test_command_runs_the_batch_inside_platform_scope(monkeypatch):
    app = Flask("arb-expiry-cli-test")
    seen = {}

    def fake_run(*, organization_ids, batch_size):
        seen["scope"] = getattr(g, "_platform_scope", None)
        return SimpleNamespace(as_dict=lambda: {"ok": True}, failed_count=0)

    # platform_scope also touches db.session; only the scope flag matters here.
    from contextlib import contextmanager

    @contextmanager
    def flag_only(reason):
        g._platform_scope = reason
        try:
            yield reason
        finally:
            g.pop("_platform_scope", None)

    monkeypatch.setattr(command_module, "platform_scope", flag_only)
    monkeypatch.setattr(
        command_module.ARBWaiverExpiryBatchService, "run", staticmethod(fake_run)
    )
    command_module.init_app(app)
    result = app.test_cli_runner().invoke(args=["process-arb-waiver-expiries", "--organization-id", "7"])

    assert result.exit_code == 0, result.output
    assert seen["scope"], "batch ran outside platform_scope"

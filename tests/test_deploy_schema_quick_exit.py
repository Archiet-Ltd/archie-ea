"""The schema step leaves the old server alone when there is nothing to do.

The shell half runs the real scripts/database/deploy-schema.sh with a scripted
`flask`; the revision half asks the real revision chain what is pending.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.commands import schema_migrations as sm

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = "scripts/database/deploy-schema.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

AT_HEAD = "schema-status: at head"
CLEAN = "reconcile-schema: 0 column(s) would add."

FAKE_FLASK = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_FLASK_LOG"
case "$*" in
  *schema-status*) printf '%s\\n' "$FAKE_STATUS_OUT"; exit "${FAKE_STATUS_RC:-0}" ;;
  *"reconcile-schema --dry-run"*) printf '%s\\n' "$FAKE_DRIFT_OUT"; exit 0 ;;
esac
exit 0
"""


def _run_script(tmp_path, *, status, status_rc=0, drift=CLEAN, **extra):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    flask = bin_dir / "flask"
    flask.write_text(FAKE_FLASK, encoding="utf-8")
    flask.chmod(0o755)
    log = tmp_path / "flask.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_FLASK_LOG": str(log),
        "FAKE_STATUS_OUT": status,
        "FAKE_STATUS_RC": str(status_rc),
        "FAKE_DRIFT_OUT": drift,
        "ARCHIE_BACKUP_MARKER": str(tmp_path / "no-marker"),
        **extra,
    }
    result = subprocess.run([BASH, SCRIPT], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, calls


def test_a_database_at_head_exits_quickly_and_says_so(tmp_path):
    result, calls = _run_script(tmp_path, status=AT_HEAD)

    assert result.returncode == 0, result.stderr
    assert "already at the latest revision" in result.stdout
    assert not any("schema-upgrade" in c or "init-db" in c for c in calls), calls
    assert len(calls) == 2, calls


def test_one_pending_migration_runs_the_full_pass(tmp_path):
    result, calls = _run_script(
        tmp_path, status="schema-status: 1 pending: 20261101_example", status_rc=10
    )

    assert result.returncode == 0, result.stderr
    assert "already at the latest revision" not in result.stdout
    assert any("schema-upgrade" in c for c in calls), calls
    assert len(calls) > 10, calls


def test_drift_the_models_would_add_runs_the_full_pass(tmp_path):
    result, calls = _run_script(
        tmp_path, status=AT_HEAD, drift="reconcile-schema: 2 column(s) would add.\n  + t.c"
    )

    assert result.returncode == 0, result.stderr
    assert any("schema-upgrade" in c for c in calls), calls


def test_log_noise_from_background_jobs_does_not_defeat_the_quick_exit(tmp_path):
    noise = "[2026-10-31 13:02:14] ERROR in tenant_safe_job: event-log-relay FAILED for organization_id=32\n" + CLEAN
    result, calls = _run_script(tmp_path, status=AT_HEAD, drift=noise)

    assert "already at the latest revision" in result.stdout
    assert not any("schema-upgrade" in c for c in calls), calls


@pytest.mark.parametrize("line", ["1 column(s) FAILED:", "3 table(s) absent - run 'flask init-db'"])
def test_a_reconcile_failure_or_missing_table_runs_the_full_pass(tmp_path, line):
    result, calls = _run_script(tmp_path, status=AT_HEAD, drift=CLEAN + "\n\n" + line)

    assert any("schema-upgrade" in c for c in calls), calls


def test_a_forced_pass_ignores_the_quick_exit(tmp_path):
    _, calls = _run_script(tmp_path, status=AT_HEAD, FORCE_FULL_SCHEMA_PASS="1")

    assert any("schema-upgrade" in c for c in calls), calls


def test_a_downtime_migration_stops_before_changing_anything(tmp_path):
    status = "schema-status: 1 pending: x\nschema-status: downtime-required: x cannot run"
    result, calls = _run_script(tmp_path, status=status, status_rc=11)

    assert result.returncode == 3
    assert "downtime window" in result.stderr
    assert not any("init-db" in c or "schema-upgrade" in c for c in calls), calls


def test_a_downtime_migration_runs_once_the_window_is_agreed(tmp_path):
    status = "schema-status: 1 pending: x\nschema-status: downtime-required: x cannot run"
    result, calls = _run_script(tmp_path, status=status, status_rc=11, ALLOW_DOWNTIME_MIGRATIONS="1")

    assert result.returncode == 0, result.stderr
    assert any("schema-upgrade" in c for c in calls), calls


# ---------------------------------------------------------------- the chain


@pytest.fixture
def chain(app, tmp_path, monkeypatch):
    """The real revision directory plus one extra revision on top."""
    with app.app_context():
        real = Path(sm._migrations_directory())
        copy = tmp_path / "migrations"
        shutil.copytree(real, copy, ignore=shutil.ignore_patterns("__pycache__", "_archive_pre_baseline"))
        from alembic.script import ScriptDirectory

        config = sm._alembic_config()
        head = ScriptDirectory.from_config(config).get_current_head()
        monkeypatch.setattr(sm, "_migrations_directory", lambda: str(copy))

        def add(name, downtime):
            (copy / "versions" / f"{name}.py").write_text(
                f"revision = '{name}'\ndown_revision = '{head}'\nbranch_labels = None\n"
                f"depends_on = None\n{'REQUIRES_DOWNTIME = True' if downtime else ''}\n"
                "def upgrade():\n    pass\n\ndef downgrade():\n    pass\n",
                encoding="utf-8",
            )

        yield head, add


def test_a_pending_revision_is_never_reported_as_at_head(chain, monkeypatch):
    head, add = chain
    add("29990101_pending_example", downtime=False)
    monkeypatch.setattr(sm, "recorded_revisions", lambda bind: [head])

    pending, downtime = sm.pending_revisions(None)

    assert pending == ["29990101_pending_example"]
    assert downtime == []


def test_a_database_recorded_at_head_has_nothing_pending(chain, monkeypatch):
    head, _ = chain
    monkeypatch.setattr(sm, "recorded_revisions", lambda bind: [head])

    assert sm.pending_revisions(None) == ([], [])


def test_a_revision_marked_for_downtime_is_reported_as_such(chain, monkeypatch):
    head, add = chain
    add("29990102_needs_downtime", downtime=True)
    monkeypatch.setattr(sm, "recorded_revisions", lambda bind: [head])

    pending, downtime = sm.pending_revisions(None)

    assert pending == downtime == ["29990102_needs_downtime"]


def test_an_unrecognised_recorded_revision_leaves_everything_pending(chain, monkeypatch):
    monkeypatch.setattr(sm, "recorded_revisions", lambda bind: ["not_a_revision"])

    pending, _ = sm.pending_revisions(None)

    assert len(pending) > 50  # every revision, baseline included

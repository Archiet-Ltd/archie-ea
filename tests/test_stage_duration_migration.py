"""The stage-time columns on unified_value_stream_stages land through a migration.

Each scenario builds a scratch database at the revision before, removes the columns so "absent" is
constructed rather than assumed, and runs the real ``schema-upgrade``.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_REVISION = "20261104_force_rls"
REVISION = "20261107_stage_duration"
COLUMNS = {
    "jira_statuses", "duration_unit", "duration_source", "duration_median_hours",
    "duration_sample_size", "duration_measured_at", "duration_recorded_by", "duration_samples",
}
_BASE = os.environ.get("TEST_DATABASE_URL") or "postgresql://postgres:postgres@127.0.0.1:5432/archie_test"


def _admin_engine():
    return create_engine(make_url(_BASE).set(database="postgres"), isolation_level="AUTOCOMMIT")


def _run_flask(url, *args):
    env = dict(os.environ)
    env.update(DATABASE_URL=url, TEST_DATABASE_URL=url, DEV_DATABASE_URL=url)
    env.setdefault("FLASK_CONFIG", "testing")
    return subprocess.run(
        [sys.executable, "-m", "flask", "--app", "manage", *args],
        cwd=REPO_ROOT, env=env, capture_output=True, timeout=1800, encoding="utf-8", errors="replace",
    )


@pytest.fixture
def scratch_db():
    name = f"archie_stagedur_{uuid.uuid4().hex[:8]}"
    admin = _admin_engine()
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    yield make_url(_BASE).set(database=name).render_as_string(hide_password=False)
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _present(engine):
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'unified_value_stream_stages' AND column_name = ANY(:c)"
        ), {"c": sorted(COLUMNS)}).fetchall()
    return {name: nullable for name, nullable in rows}


def _prepared(scratch_db):
    proc = _run_flask(scratch_db, "schema-upgrade", "--to", PRE_REVISION)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    init = _run_flask(scratch_db, "init-db")
    assert init.returncode == 0, init.stdout + init.stderr
    engine = create_engine(scratch_db)
    with engine.begin() as conn:
        for name in COLUMNS:
            conn.execute(text(f"ALTER TABLE unified_value_stream_stages DROP COLUMN IF EXISTS {name}"))
    assert _present(engine) == {}
    return engine


def test_a_database_migrated_from_empty_has_every_nullable_column(scratch_db):
    proc = _run_flask(scratch_db, "schema-upgrade")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    init = _run_flask(scratch_db, "init-db")
    assert init.returncode == 0, init.stdout + init.stderr
    engine = create_engine(scratch_db)
    try:
        assert _present(engine) == {name: "YES" for name in COLUMNS}
    finally:
        engine.dispose()


def test_upgrade_adds_the_columns_and_runs_again_on_a_database_that_has_them(scratch_db):
    engine = _prepared(scratch_db)
    try:
        assert _run_flask(scratch_db, "schema-upgrade", "--to", REVISION).returncode == 0
        assert _present(engine) == {name: "YES" for name in COLUMNS}
        with engine.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": PRE_REVISION})
        again = _run_flask(scratch_db, "schema-upgrade", "--to", REVISION)
        assert again.returncode == 0, again.stdout + again.stderr
        assert _present(engine) == {name: "YES" for name in COLUMNS}
    finally:
        engine.dispose()


def test_downgrade_drops_the_columns_and_upgrade_restores_them(scratch_db):
    engine = _prepared(scratch_db)
    try:
        assert _run_flask(scratch_db, "schema-upgrade", "--to", REVISION).returncode == 0
        down = _run_flask(scratch_db, "db", "downgrade", PRE_REVISION)
        assert down.returncode == 0, down.stdout + down.stderr
        assert _present(engine) == {}
        up = _run_flask(scratch_db, "schema-upgrade", "--to", REVISION)
        assert up.returncode == 0, up.stdout + up.stderr
        assert _present(engine) == {name: "YES" for name in COLUMNS}
    finally:
        engine.dispose()

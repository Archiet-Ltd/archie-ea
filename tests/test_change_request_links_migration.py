"""The change request's capability and solution links land through a migration.

The model carries ``architecture_change_requests.capability_id`` and ``solution_id``; a real deploy needs a
revision to add them. Each scenario builds a scratch database at the revision before, removes the columns so
"absent" is constructed rather than assumed, and runs the real ``schema-upgrade``.
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
PRE_REVISION = "20261107_stage_duration"
REVISION = "20261107_change_request_links"
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
    name = f"archie_crlinks_{uuid.uuid4().hex[:8]}"
    admin = _admin_engine()
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    yield make_url(_BASE).set(database=name).render_as_string(hide_password=False)
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _links(engine):
    """Column name -> (is_nullable, referenced table, delete rule) for the two link columns."""
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT c.column_name, c.is_nullable, ccu.table_name, rc.delete_rule "
            "FROM information_schema.columns c "
            "LEFT JOIN information_schema.key_column_usage kcu ON kcu.table_name = c.table_name "
            "  AND kcu.column_name = c.column_name "
            "LEFT JOIN information_schema.referential_constraints rc ON rc.constraint_name = kcu.constraint_name "
            "LEFT JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = rc.constraint_name "
            "WHERE c.table_name = 'architecture_change_requests' "
            "AND c.column_name IN ('capability_id', 'solution_id')"
        )).fetchall()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


EXPECTED = {
    "capability_id": ("YES", "unified_capabilities", "SET NULL"),
    "solution_id": ("YES", "solutions", "SET NULL"),
}


def _prepared(scratch_db):
    proc = _run_flask(scratch_db, "schema-upgrade", "--to", PRE_REVISION)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    engine = create_engine(scratch_db)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE architecture_change_requests DROP COLUMN IF EXISTS capability_id"))
        conn.execute(text("ALTER TABLE architecture_change_requests DROP COLUMN IF EXISTS solution_id"))
    assert _links(engine) == {}
    return engine


def test_a_database_migrated_from_empty_has_both_nullable_links(scratch_db):
    proc = _run_flask(scratch_db, "schema-upgrade")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    engine = create_engine(scratch_db)
    try:
        assert _links(engine) == EXPECTED
    finally:
        engine.dispose()


def test_upgrade_adds_the_links_and_a_second_run_on_a_database_that_has_them_succeeds(scratch_db):
    engine = _prepared(scratch_db)
    try:
        assert _run_flask(scratch_db, "schema-upgrade", "--to", REVISION).returncode == 0
        assert _links(engine) == EXPECTED
        with engine.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": PRE_REVISION})
        again = _run_flask(scratch_db, "schema-upgrade", "--to", REVISION)
        assert again.returncode == 0, again.stdout + again.stderr
        assert _links(engine) == EXPECTED
    finally:
        engine.dispose()


def test_downgrade_drops_the_links_and_upgrade_restores_them(scratch_db):
    engine = _prepared(scratch_db)
    try:
        assert _run_flask(scratch_db, "schema-upgrade", "--to", REVISION).returncode == 0
        down = _run_flask(scratch_db, "db", "downgrade", PRE_REVISION)
        assert down.returncode == 0, down.stdout + down.stderr
        assert _links(engine) == {}
        up = _run_flask(scratch_db, "schema-upgrade", "--to", REVISION)
        assert up.returncode == 0, up.stdout + up.stderr
        assert _links(engine) == EXPECTED
    finally:
        engine.dispose()


def test_upgrade_adds_the_foreign_keys_to_columns_that_already_exist_without_them(scratch_db):
    engine = _prepared(scratch_db)
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE architecture_change_requests ADD COLUMN capability_id INTEGER"))
            conn.execute(text("ALTER TABLE architecture_change_requests ADD COLUMN solution_id INTEGER"))
        assert _links(engine) == {
            "capability_id": ("YES", None, None),
            "solution_id": ("YES", None, None),
        }
        up = _run_flask(scratch_db, "schema-upgrade", "--to", REVISION)
        assert up.returncode == 0, up.stdout + up.stderr
        assert _links(engine) == EXPECTED
    finally:
        engine.dispose()

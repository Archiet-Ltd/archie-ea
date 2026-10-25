"""The canvas type and the box an element sits in land through a migration.

The models carry ``business_model_canvases.canvas_type`` and ``saved_diagram_elements.canvas_box``; a real
deploy needs the revision to add them. The scenario builds a scratch database at the revision before, removes both
columns so "absent" is constructed rather than assumed, and runs the real ``schema-upgrade``.
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
PRE_REVISION = "20261025_price_plans_canvas"
REVISION = "20261025_canvas_types"
COLUMNS = (("business_model_canvases", "canvas_type"), ("saved_diagram_elements", "canvas_box"))
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
    name = f"archie_canvastypes_{uuid.uuid4().hex[:8]}"
    admin = _admin_engine()
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    yield make_url(_BASE).set(database=name).render_as_string(hide_password=False)
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _columns(engine):
    with engine.connect() as conn:
        return {
            (r[0], r[1]): (r[2], r[3])
            for r in conn.execute(text(
                "SELECT table_name, column_name, is_nullable, character_maximum_length FROM information_schema.columns "
                "WHERE (table_name, column_name) IN (('business_model_canvases', 'canvas_type'), "
                "('saved_diagram_elements', 'canvas_box'))"
            ))
        }


def _prepared(scratch_db):
    proc = _run_flask(scratch_db, "schema-upgrade", "--to", PRE_REVISION)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    engine = create_engine(scratch_db)
    with engine.begin() as conn:
        for table, column in COLUMNS:
            conn.execute(text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}"))
    assert _columns(engine) == {}
    return engine


def test_upgrade_adds_both_columns_nullable_and_leaves_existing_canvases_as_business_model_canvases(scratch_db):
    engine = _prepared(scratch_db)
    try:
        with engine.begin() as conn:
            org = conn.execute(text("INSERT INTO organizations (name, slug) VALUES ('O', :s) RETURNING id"),
                               {"s": f"ct-{uuid.uuid4().hex[:6]}"}).scalar()
            conn.execute(text("INSERT INTO business_model_canvases (name, organization_id) VALUES ('Old', :o)"), {"o": org})
        proc = _run_flask(scratch_db, "schema-upgrade", "--to", REVISION)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        found = _columns(engine)
        with engine.connect() as conn:
            old = conn.execute(text("SELECT canvas_type FROM business_model_canvases WHERE name = 'Old'")).scalar()
    finally:
        engine.dispose()
    assert found == {
        ("business_model_canvases", "canvas_type"): ("YES", 64),
        ("saved_diagram_elements", "canvas_box"): ("YES", 64),
    }
    assert old is None


def test_upgrade_run_again_and_downgrade_are_clean(scratch_db):
    engine = _prepared(scratch_db)
    try:
        assert _run_flask(scratch_db, "schema-upgrade", "--to", REVISION).returncode == 0
        with engine.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": PRE_REVISION})
        again = _run_flask(scratch_db, "schema-upgrade", "--to", REVISION)
        assert again.returncode == 0, again.stdout + again.stderr
        assert len(_columns(engine)) == 2
        down = _run_flask(scratch_db, "db", "downgrade", PRE_REVISION)
        assert down.returncode == 0, down.stdout + down.stderr
        assert _columns(engine) == {}
        up = _run_flask(scratch_db, "schema-upgrade", "--to", REVISION)
        assert up.returncode == 0, up.stdout + up.stderr
        assert len(_columns(engine)) == 2
    finally:
        engine.dispose()

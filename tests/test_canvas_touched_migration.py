"""The touched-canvas column lands through a migration.

``business_model_canvases.touched_at`` records that a person changed a canvas sign-up made. The model and its
writers use it, so a database built from the migrations alone must have it. Each scenario builds a scratch
database with the real ``schema-upgrade`` (no create_all) and then runs the real writer against it.
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
PRE_REVISION = "20261101_risk_loss_assessments"
REVISION = "20261102_canvas_touched_at"
_BASE = os.environ.get("TEST_DATABASE_URL") or "postgresql://postgres:postgres@127.0.0.1:5432/archie_test"

WRITER = """
from app import create_app
from app.extensions import db
from app.models import Organization
from app.models.business_model import BusinessModelCanvas
from app.modules.business_model_canvas import model_service, service

app = create_app()
with app.app_context():
    org = Organization.query.filter_by(slug="touch-writer").first()
    canvas = BusinessModelCanvas(name="Lean Canvas", description="A note", organization_id=org.id)
    db.session.add(canvas)
    db.session.commit()
    assert canvas.touched_at is None
    service.update_canvas_meta(canvas, name="Our canvas")
    db.session.expire_all()
    assert BusinessModelCanvas.query.get(canvas.id).touched_at is not None
    print("WRITER-OK")
"""


def _admin_engine():
    return create_engine(make_url(_BASE).set(database="postgres"), isolation_level="AUTOCOMMIT")


def _run(url, *args, plain=False):
    env = dict(os.environ)
    env.update(DATABASE_URL=url, TEST_DATABASE_URL=url, DEV_DATABASE_URL=url)
    env.setdefault("FLASK_CONFIG", "testing")
    return subprocess.run(
        [sys.executable, *(args if plain else ("-m", "flask", "--app", "manage", *args))],
        cwd=REPO_ROOT, env=env, capture_output=True, timeout=1800, encoding="utf-8", errors="replace",
    )


@pytest.fixture
def scratch_db():
    name = f"archie_touched_{uuid.uuid4().hex[:8]}"
    admin = _admin_engine()
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    yield make_url(_BASE).set(database=name).render_as_string(hide_password=False)
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _has_column(engine):
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'business_model_canvases' AND column_name = 'touched_at'")).scalar()


def _org(conn, slug):
    return conn.execute(text("INSERT INTO organizations (name, slug) VALUES (:s, :s) RETURNING id"), {"s": slug}).scalar()


def test_a_database_at_the_previous_release_accepts_the_touched_canvas_writer_once_upgraded(scratch_db):
    # The baseline builds tables from the models, so only a database already past it can lack the column.
    assert _run(scratch_db, "schema-upgrade", "--to", PRE_REVISION).returncode == 0
    engine = create_engine(scratch_db)
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE business_model_canvases DROP COLUMN IF EXISTS touched_at"))
            _org(conn, "touch-writer")
        assert _has_column(engine) is None
        proc = _run(scratch_db, "schema-upgrade")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _has_column(engine) == "YES"
    finally:
        engine.dispose()
    done = _run(scratch_db, "-c", WRITER, plain=True)
    assert "WRITER-OK" in done.stdout, done.stdout[-1500:] + done.stderr[-1500:]


def test_canvases_made_before_the_release_stay_untouched_unless_a_later_save_shows_a_person_changed_them(scratch_db):
    assert _run(scratch_db, "schema-upgrade", "--to", PRE_REVISION).returncode == 0
    engine = create_engine(scratch_db)
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE business_model_canvases DROP COLUMN IF EXISTS touched_at"))
            one, two = _org(conn, "touch-one"), _org(conn, "touch-two")
            for org, name, created, updated in (
                (one, "Fresh", "2026-10-01 10:00:00", "2026-10-01 10:00:05"),
                (one, "Worked on", "2026-10-01 10:00:00", "2026-10-09 15:30:00"),
                (two, "Other org worked on", "2026-10-01 10:00:00", "2026-10-05 08:00:00"),
            ):
                conn.execute(text(
                    "INSERT INTO business_model_canvases (name, organization_id, created_at, updated_at) "
                    "VALUES (:n, :o, :c, :u)"), {"n": name, "o": org, "c": created, "u": updated})
        assert _has_column(engine) is None
        proc = _run(scratch_db, "schema-upgrade", "--to", REVISION)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        with engine.connect() as conn:
            found = dict(conn.execute(text("SELECT name, touched_at IS NOT NULL FROM business_model_canvases")).all())
        again = _run(scratch_db, "schema-upgrade", "--to", REVISION)
        assert again.returncode == 0, again.stdout + again.stderr
        down = _run(scratch_db, "db", "downgrade", PRE_REVISION)
        assert down.returncode == 0, down.stdout + down.stderr
        assert _has_column(engine) is None
    finally:
        engine.dispose()
    assert found == {"Fresh": False, "Worked on": True, "Other org worked on": True}

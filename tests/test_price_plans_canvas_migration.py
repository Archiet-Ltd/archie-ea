"""price_plans and the canvas link columns land through a migration.

The models carry ``price_plans`` and ``business_model_canvases.saved_diagram_id``
/ ``vision_element_id``; without a revision a real deploy has no such table or
columns and every canvas page fails. Each scenario builds a scratch database at
the revision before, removes the three objects so "absent" is constructed rather
than assumed, and runs the real ``schema-upgrade``.
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
PRE_REVISION = "20261024_mfa_recovery_codes"
REVISION = "20261025_price_plans_canvas"
CANVAS_COLUMNS = ("saved_diagram_id", "vision_element_id")
_BASE = os.environ.get("TEST_DATABASE_URL") or "postgresql://postgres:postgres@127.0.0.1:5432/archie_test"


def _admin_engine():
    return create_engine(make_url(_BASE).set(database="postgres"), isolation_level="AUTOCOMMIT")


def _run_flask(url, *args):
    env = dict(os.environ)
    env.update(DATABASE_URL=url, TEST_DATABASE_URL=url, DEV_DATABASE_URL=url)
    env.setdefault("FLASK_CONFIG", "testing")
    return subprocess.run(
        [sys.executable, "-m", "flask", "--app", "manage", *args],
        cwd=REPO_ROOT, env=env, capture_output=True, timeout=1800,
        encoding="utf-8", errors="replace",
    )


@pytest.fixture
def scratch_db():
    name = f"archie_priceplans_{uuid.uuid4().hex[:8]}"
    admin = _admin_engine()
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(_BASE).set(database=name).render_as_string(hide_password=False)
    yield url
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _remove_new_objects(engine):
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS price_plans CASCADE"))
        for column in CANVAS_COLUMNS:
            conn.execute(text(f"ALTER TABLE business_model_canvases DROP COLUMN IF EXISTS {column}"))


def _state(engine):
    with engine.connect() as conn:
        table = conn.execute(text("SELECT to_regclass('price_plans')")).scalar()
        columns = {
            r[0]: r[1]
            for r in conn.execute(text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'business_model_canvases' "
                "AND column_name IN ('saved_diagram_id', 'vision_element_id')"
            ))
        }
        policies = {r[0] for r in conn.execute(text("SELECT policyname FROM pg_policies WHERE tablename = 'price_plans'"))}
        rls = conn.execute(text("SELECT relrowsecurity FROM pg_class WHERE relname = 'price_plans'")).scalar()
        org = conn.execute(text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'price_plans' AND column_name = 'organization_id'"
        )).scalar()
        indexes = {r[0] for r in conn.execute(text("SELECT indexname FROM pg_indexes WHERE tablename = 'price_plans'"))}
    return table, columns, policies, rls, org, indexes


def _prepared(scratch_db):
    proc = _run_flask(scratch_db, "schema-upgrade", "--to", PRE_REVISION)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    engine = create_engine(scratch_db)
    _remove_new_objects(engine)
    assert _state(engine)[0] is None and _state(engine)[1] == {}
    return engine


def test_upgrade_creates_fenced_table_and_canvas_columns(scratch_db):
    engine = _prepared(scratch_db)
    try:
        proc = _run_flask(scratch_db, "schema-upgrade", "--to", REVISION)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        table, columns, policies, rls, org, indexes = _state(engine)
    finally:
        engine.dispose()
    assert table is not None
    assert columns == {"saved_diagram_id": "YES", "vision_element_id": "YES"}
    assert org == "NO" and "ix_price_plans_organization_id" in indexes
    assert rls is True
    assert policies == {"tenant_select", "tenant_insert", "tenant_update", "tenant_delete"}


def test_upgrade_run_again_and_downgrade_are_clean(scratch_db):
    engine = _prepared(scratch_db)
    try:
        assert _run_flask(scratch_db, "schema-upgrade", "--to", REVISION).returncode == 0
        with engine.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": PRE_REVISION})
        again = _run_flask(scratch_db, "schema-upgrade", "--to", REVISION)
        assert again.returncode == 0, again.stdout + again.stderr
        assert _state(engine)[0] is not None

        down = _run_flask(scratch_db, "db", "downgrade", PRE_REVISION)
        assert down.returncode == 0, down.stdout + down.stderr
        table, columns, *_ = _state(engine)
        assert table is None and columns == {}
        up = _run_flask(scratch_db, "schema-upgrade", "--to", REVISION)
        assert up.returncode == 0, up.stdout + up.stderr
        assert _state(engine)[0] is not None
    finally:
        engine.dispose()


def test_one_organisation_cannot_read_anothers_price_plans(scratch_db):
    engine = _prepared(scratch_db)
    role = f"pp_reader_{uuid.uuid4().hex[:8]}"
    admin = _admin_engine()
    try:
        assert _run_flask(scratch_db, "schema-upgrade", "--to", REVISION).returncode == 0
        with admin.connect() as conn:
            conn.execute(text(f"CREATE ROLE {role} NOLOGIN"))
        with engine.begin() as conn:
            conn.execute(text(f"GRANT SELECT ON price_plans TO {role}"))
            orgs = []
            for slug in ("one", "two"):
                orgs.append(conn.execute(text(
                    "INSERT INTO organizations (name, slug) VALUES (:n, :s) RETURNING id"
                ), {"n": f"Org {slug}", "s": f"pp-{slug}-{uuid.uuid4().hex[:6]}"}).scalar())
            for org in orgs:
                element = conn.execute(text(
                    "INSERT INTO archimate_elements (name, type, layer, organization_id) "
                    "VALUES ('Product', 'Product', 'business', :o) RETURNING id"
                ), {"o": org}).scalar()
                conn.execute(text(
                    "INSERT INTO price_plans (organization_id, element_id, name) VALUES (:o, :e, :n)"
                ), {"o": org, "e": element, "n": f"plan-{org}"})
        seen = {}
        for org in orgs:
            with engine.begin() as conn:
                conn.execute(text(f"SET LOCAL ROLE {role}"))
                conn.execute(text("SELECT set_config('archie.organization_id', :o, true)"), {"o": str(org)})
                seen[org] = [r[0] for r in conn.execute(text("SELECT name FROM price_plans"))]
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP OWNED BY {role}"))
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()
    assert seen == {orgs[0]: [f"plan-{orgs[0]}"], orgs[1]: [f"plan-{orgs[1]}"]}

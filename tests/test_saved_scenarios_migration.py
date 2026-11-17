"""saved_scenarios lands through a migration, fenced by organisation.

The model carries ``saved_scenarios``; without a revision a real deploy has
no such table and saving or comparing a scenario fails. Each scenario
builds a scratch database at the revision before, removes the table so "absent"
is constructed rather than assumed, and runs the real ``schema-upgrade``.
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
PRE_REVISION = "20261108_impact_capability"
REVISION = "20261109_saved_scenarios"
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
    name = f"archie_scenarios_{uuid.uuid4().hex[:8]}"
    admin = _admin_engine()
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(_BASE).set(database=name).render_as_string(hide_password=False)
    yield url
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _state(engine):
    with engine.connect() as conn:
        table = conn.execute(text("SELECT to_regclass('saved_scenarios')")).scalar()
        policies = {r[0] for r in conn.execute(text(
            "SELECT policyname FROM pg_policies WHERE tablename = 'saved_scenarios'"))}
        rls = conn.execute(text("SELECT relrowsecurity FROM pg_class WHERE relname = 'saved_scenarios'")).scalar()
        org = conn.execute(text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'saved_scenarios' AND column_name = 'organization_id'"
        )).scalar()
        indexes = {r[0] for r in conn.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'saved_scenarios'"))}
        forced = conn.execute(text("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'saved_scenarios'")).scalar()
    return table, policies, rls, org, indexes, forced


def _prepared(scratch_db):
    proc = _run_flask(scratch_db, "schema-upgrade", "--to", PRE_REVISION)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    engine = create_engine(scratch_db)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS saved_scenarios CASCADE"))
    assert _state(engine)[0] is None
    return engine


def test_upgrade_creates_fenced_table(scratch_db):
    engine = _prepared(scratch_db)
    try:
        proc = _run_flask(scratch_db, "schema-upgrade", "--to", REVISION)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        table, policies, rls, org, indexes, forced = _state(engine)
    finally:
        engine.dispose()
    assert table is not None
    assert org == "NO"
    assert {"ix_saved_scenarios_organization_id", "ix_saved_scenarios_element_id"} <= indexes
    assert forced is True
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
        assert _state(engine)[0] is None
        up = _run_flask(scratch_db, "schema-upgrade", "--to", REVISION)
        assert up.returncode == 0, up.stdout + up.stderr
        assert _state(engine)[0] is not None
    finally:
        engine.dispose()


def test_one_organisation_cannot_read_anothers_scenarios(scratch_db):
    engine = _prepared(scratch_db)
    role = f"scn_reader_{uuid.uuid4().hex[:8]}"
    admin = _admin_engine()
    try:
        assert _run_flask(scratch_db, "schema-upgrade", "--to", REVISION).returncode == 0
        with admin.connect() as conn:
            conn.execute(text(f"CREATE ROLE {role} NOLOGIN"))
        with engine.begin() as conn:
            conn.execute(text(f"GRANT SELECT ON saved_scenarios TO {role}"))
            orgs = []
            for slug in ("one", "two"):
                orgs.append(conn.execute(text(
                    "INSERT INTO organizations (name, slug) VALUES (:n, :s) RETURNING id"
                ), {"n": f"Org {slug}", "s": f"sg-{slug}-{uuid.uuid4().hex[:6]}"}).scalar())
            for org in orgs:
                conn.execute(text(
                    "INSERT INTO saved_scenarios (organization_id, name, change_type, created_at) "
                    "VALUES (:o, :t, 'RETIRE', now())"
                ), {"o": org, "t": f"scenario-{org}"})
        seen = {}
        for org in orgs:
            with engine.begin() as conn:
                conn.execute(text(f"SET LOCAL ROLE {role}"))
                conn.execute(text("SELECT set_config('archie.organization_id', :o, true)"), {"o": str(org)})
                seen[org] = [r[0] for r in conn.execute(text("SELECT name FROM saved_scenarios"))]
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP OWNED BY {role}"))
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()
    assert seen == {orgs[0]: [f"scenario-{orgs[0]}"], orgs[1]: [f"scenario-{orgs[1]}"]}

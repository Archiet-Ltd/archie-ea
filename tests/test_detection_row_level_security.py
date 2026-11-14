"""Row-level security on detection runs, duplicate groups and consolidation
entries is switched on and forced, and a database role bound to one
organisation reads none of another organisation's rows."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

REPO = Path(__file__).resolve().parents[1]
MIGRATIONS = (
    "20261113_detect_consol_org",
    "20261113_duplicate_group_org",
)
FENCED = ("unified_detection_runs", "consolidation_list_entries", "unified_duplicate_groups")
ROLE_PASSWORD = "detection-rls-test"


def _url_for(base, database, user=None, password=None):
    parts = urlsplit(base)
    netloc = parts.netloc
    if user:
        netloc = f"{user}:{password}@{parts.netloc.rsplit('@', 1)[-1]}"
    return urlunsplit((parts.scheme.split("+")[0], netloc, f"/{database}", "", ""))


@pytest.fixture(scope="module")
def upgraded():
    """A scratch copy of the current schema with both migrations run on it."""
    base = os.environ["TEST_DATABASE_URL"]
    source = urlsplit(base).path.lstrip("/")
    name = f"archie_test_detection_rls_{uuid.uuid4().hex[:8]}"
    admin = psycopg2.connect(_url_for(base, "postgres"))
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    role = f"detection_rls_{uuid.uuid4().hex[:8]}"
    try:
        dump = subprocess.run(
            ["pg_dump", "--no-owner", "--no-privileges", "--schema-only", _url_for(base, source)],
            check=True, capture_output=True, text=True,
        ).stdout
        target = _url_for(base, name)
        subprocess.run(["psql", "-q", "-v", "ON_ERROR_STOP=0", target], input=dump,
                       check=True, capture_output=True, text=True)
        engine = create_engine(target, poolclass=NullPool)
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                for revision in MIGRATIONS:
                    spec = importlib.util.spec_from_file_location(
                        revision, REPO / "migrations" / "versions" / f"{revision}.py")
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    module.upgrade()
        engine.dispose()
        with admin.cursor() as cur:
            cur.execute(f"SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            if not cur.fetchone()[0]:
                pytest.skip("needs a role that may create roles")
            cur.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{ROLE_PASSWORD}' NOSUPERUSER NOBYPASSRLS")
        yield {"super": target, "role": role, "name": name,
               "member": _url_for(base, name, role, ROLE_PASSWORD)}
    finally:
        with admin.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (name,))
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
            cur.execute(f"DROP ROLE IF EXISTS {role}")
        admin.close()


def test_every_fenced_table_has_row_level_security_enabled_and_forced(upgraded):
    engine = create_engine(upgraded["super"], poolclass=NullPool)
    with engine.connect() as c:
        rows = c.execute(
            text("SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                 "WHERE oid = ANY (ARRAY[to_regclass('public.unified_detection_runs'), "
                 "to_regclass('public.consolidation_list_entries'), "
                 "to_regclass('public.unified_duplicate_groups')]) ORDER BY 1")
        ).fetchall()
    engine.dispose()
    assert [r[0] for r in rows] == sorted(FENCED)
    assert [(r[0], r[1], r[2]) for r in rows] == [(t, True, True) for t in sorted(FENCED)]


def test_a_role_bound_to_one_organisation_reads_none_of_anothers_detection_rows(upgraded):
    engine = create_engine(upgraded["super"], poolclass=NullPool)
    ids = {}
    with engine.begin() as c:
        c.execute(text("SET session_replication_role = replica"))
        for key in ("a", "b"):
            org = c.execute(
                text("INSERT INTO organizations (name, slug) VALUES (:n, :s) RETURNING id"),
                {"n": f"Detection RLS {key}", "s": f"detection-rls-{key}-{uuid.uuid4().hex[:6]}"},
            ).scalar()
            ids[key] = org
            c.execute(text("INSERT INTO unified_detection_runs (run_name, strategy, organization_id) "
                           "VALUES (:n, 'fast', :o)"), {"n": f"run {key}", "o": org})
            c.execute(text("INSERT INTO unified_duplicate_groups (name, similarity_score, similarity_threshold, "
                           "organization_id) VALUES (:n, 0.9, 0.5, :o)"), {"n": f"group {key}", "o": org})
            c.execute(text("INSERT INTO consolidation_list_entries (application_id, organization_id) "
                           "VALUES (:a, :o)"), {"a": org, "o": org})
        for table in FENCED:
            c.execute(text(f"GRANT SELECT ON public.{table} TO {upgraded['role']}"))
    engine.dispose()
    member = create_engine(upgraded["member"], poolclass=NullPool)
    with member.connect() as c:
        flags = c.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")).scalar()
        assert flags is False
        assert c.execute(text("SELECT count(*) FROM unified_detection_runs")).scalar() == 0
        c.execute(text("SELECT set_config('archie.organization_id', :o, false)"), {"o": str(ids["a"])})
        for table in FENCED:
            own = c.execute(text(f"SELECT count(*) FROM public.{table} WHERE organization_id = :o"),
                            {"o": ids["a"]}).scalar()
            other = c.execute(text(f"SELECT count(*) FROM public.{table} WHERE organization_id = :o"),
                              {"o": ids["b"]}).scalar()
            assert (table, own, other) == (table, 1, 0)
    member.dispose()

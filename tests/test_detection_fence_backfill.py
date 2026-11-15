"""The organisation fence migrations keep every row that belongs to one
organisation: a detection run that spans two organisations is split, its
groups follow their members, consolidation entries made by people are
assigned rather than dropped, and the few rows that cannot be assigned are
written to a record table first and put back by the downgrade."""
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
MIGRATIONS = ("20261113_detect_consol_org", "20261113_duplicate_group_org")
TABLES = ("unified_detection_runs", "consolidation_list_entries", "unified_duplicate_groups")


def _url_for(base, database):
    parts = urlsplit(base)
    return urlunsplit((parts.scheme.split("+")[0], parts.netloc, f"/{database}", "", ""))


def _load(revision):
    spec = importlib.util.spec_from_file_location(revision, REPO / "migrations" / "versions" / f"{revision}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine, revisions, direction):
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for revision in revisions:
                getattr(_load(revision), direction)()


@pytest.fixture(scope="module")
def scratch():
    base = os.environ["TEST_DATABASE_URL"]
    source = urlsplit(base).path.lstrip("/")
    name = f"archie_test_detection_fill_{uuid.uuid4().hex[:8]}"
    admin = psycopg2.connect(_url_for(base, "postgres"))
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    try:
        dump = subprocess.run(
            ["pg_dump", "--no-owner", "--no-privileges", "--schema-only", _url_for(base, source)],
            check=True, capture_output=True, text=True,
        ).stdout
        target = _url_for(base, name)
        subprocess.run(["psql", "-q", "-v", "ON_ERROR_STOP=0", target], input=dump,
                       check=True, capture_output=True, text=True)
        engine = create_engine(target, poolclass=NullPool)
        with engine.begin() as c:
            # The shape the tables had before the fence: no organisation required.
            for table in TABLES:
                c.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
                c.execute(text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
                c.execute(text(f"ALTER TABLE {table} ALTER COLUMN organization_id DROP NOT NULL"))
            c.execute(text("ALTER TABLE application_components ALTER COLUMN organization_id DROP NOT NULL"))
            c.execute(text("ALTER TABLE users ALTER COLUMN organization_id DROP NOT NULL"))
        yield engine
        engine.dispose()
    finally:
        with admin.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (name,))
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.close()


def _seed(c):
    """Two real organisations and one run that links applications of both."""
    ids = {}
    c.execute(text("SET session_replication_role = replica"))
    for key in ("a", "b"):
        ids[key] = c.execute(
            text("INSERT INTO organizations (name, slug) VALUES (:n, :s) RETURNING id"),
            {"n": f"Fill {key}", "s": f"fill-{key}-{uuid.uuid4().hex[:6]}"},
        ).scalar()
    ids["user_a"] = c.execute(
        text("INSERT INTO users (email, organization_id) VALUES (:e, :o) RETURNING id"),
        {"e": f"fill-{uuid.uuid4().hex[:6]}@example.test", "o": ids["a"]},
    ).scalar()
    for key, org, count in (("a", "a", 2), ("b", "b", 2)):
        for n in range(count):
            ids[f"app_{key}{n}"] = c.execute(
                text("INSERT INTO application_components (name, organization_id) VALUES (:n, :o) RETURNING id"),
                {"n": f"fill app {key}{n}", "o": ids[org]},
            ).scalar()
    ids["app_none"] = c.execute(
        text("INSERT INTO application_components (name, organization_id) VALUES ('fill app none', NULL) RETURNING id")
    ).scalar()

    def run(name, user=None):
        return c.execute(
            text("INSERT INTO unified_detection_runs (run_name, strategy, user_id) VALUES (:n, 'fast', :u) RETURNING id"),
            {"n": name, "u": user},
        ).scalar()

    def group(name, run_id, members):
        gid = c.execute(
            text("INSERT INTO unified_duplicate_groups (name, similarity_score, similarity_threshold, detection_run_id) "
                 "VALUES (:n, 0.9, 0.5, :r) RETURNING id"),
            {"n": name, "r": run_id},
        ).scalar()
        for app in members:
            c.execute(text("INSERT INTO unified_group_members (group_id, application_id) VALUES (:g, :a)"),
                      {"g": gid, "a": ids[app]})
        return gid

    ids["run_mixed"] = run("mixed run")
    ids["g_a"] = group("only a", ids["run_mixed"], ["app_a0", "app_a1"])
    ids["g_b"] = group("only b", ids["run_mixed"], ["app_b0", "app_b1"])
    ids["g_across"] = group("across", ids["run_mixed"], ["app_a0", "app_b0"])
    ids["g_empty"] = group("empty", ids["run_mixed"], [])
    ids["run_a"] = run("single a")
    ids["g_single"] = group("single", ids["run_a"], ["app_a0", "app_a1"])
    ids["run_orphan"] = run("orphan")

    def entry(app, by=None):
        return c.execute(
            text("INSERT INTO consolidation_list_entries (application_id, added_by) VALUES (:a, :by) RETURNING id"),
            {"a": ids[app], "by": by},
        ).scalar()

    ids["e_app"] = entry("app_a0")
    ids["e_adder"] = entry("app_none", "adder@example.test")
    ids["e_orphan"] = entry("app_none")
    c.execute(text("UPDATE users SET email = 'adder@example.test' WHERE id = :i"), {"i": ids["user_a"]})
    return ids


def _scalar(engine, sql, **params):
    with engine.connect() as c:
        return c.execute(text(sql), params).scalar()


def test_cross_organisation_run_is_split_and_unassignable_rows_are_recorded_then_restored(scratch):
    with scratch.begin() as c:
        ids = _seed(c)
    _run(scratch, MIGRATIONS, "upgrade")

    def org_of(table, row_id):
        return _scalar(scratch, f"SELECT organization_id FROM {table} WHERE id = :i", i=row_id)

    # The mixed run is now one run per organisation, each holding its own groups.
    assert _scalar(scratch, "SELECT count(*) FROM unified_detection_runs WHERE run_name = 'mixed run'") == 2
    assert org_of("unified_duplicate_groups", ids["g_a"]) == ids["a"]
    assert org_of("unified_duplicate_groups", ids["g_b"]) == ids["b"]
    assert _scalar(scratch, "SELECT r.organization_id FROM unified_detection_runs r JOIN unified_duplicate_groups g "
                            "ON g.detection_run_id = r.id WHERE g.id = :g", g=ids["g_b"]) == ids["b"]
    assert _scalar(scratch, "SELECT r.organization_id FROM unified_detection_runs r JOIN unified_duplicate_groups g "
                            "ON g.detection_run_id = r.id WHERE g.id = :g", g=ids["g_a"]) == ids["a"]
    # A group in a single-organisation run is untouched.
    assert org_of("unified_duplicate_groups", ids["g_single"]) == ids["a"]
    # Members of the kept groups are still there.
    assert _scalar(scratch, "SELECT count(*) FROM unified_group_members WHERE group_id = :g", g=ids["g_b"]) == 2
    # Only the groups and run no single organisation owns are gone, and recorded.
    for key in ("g_across", "g_empty"):
        assert _scalar(scratch, "SELECT count(*) FROM unified_duplicate_groups WHERE id = :i", i=ids[key]) == 0
        assert _scalar(scratch, "SELECT count(*) FROM detection_fence_removed_rows WHERE source_table = "
                                "'unified_duplicate_groups' AND source_id = :i", i=ids[key]) == 1
    assert _scalar(scratch, "SELECT count(*) FROM unified_detection_runs WHERE id = :i", i=ids["run_orphan"]) == 0
    assert _scalar(scratch, "SELECT count(*) FROM detection_fence_removed_rows WHERE source_table = "
                            "'unified_detection_runs' AND source_id = :i", i=ids["run_orphan"]) == 1
    # Consolidation entries people made are assigned, not dropped.
    assert org_of("consolidation_list_entries", ids["e_app"]) == ids["a"]
    assert org_of("consolidation_list_entries", ids["e_adder"]) == ids["a"]
    assert _scalar(scratch, "SELECT count(*) FROM consolidation_list_entries WHERE id = :i", i=ids["e_orphan"]) == 0
    assert _scalar(scratch, "SELECT reason FROM detection_fence_removed_rows WHERE source_table = "
                            "'consolidation_list_entries' AND source_id = :i", i=ids["e_orphan"])

    _run(scratch, reversed(MIGRATIONS), "downgrade")
    for table, key in (("unified_duplicate_groups", "g_across"), ("unified_duplicate_groups", "g_empty"),
                       ("unified_detection_runs", "run_orphan"), ("consolidation_list_entries", "e_orphan")):
        assert _scalar(scratch, f"SELECT count(*) FROM {table} WHERE id = :i", i=ids[key]) == 1, key
    assert _scalar(scratch, "SELECT count(*) FROM unified_group_members WHERE group_id = :g", g=ids["g_across"]) == 2
    _run(scratch, MIGRATIONS, "upgrade")

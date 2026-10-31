"""The capability, relationship and application tables are fenced per organisation.

Two real organisations, each with its own application, solution and capabilities, and a child row in
each of a spread of the batch's tables (the application-to-capability mapping, capability coverage,
process support, a roadmap work package and a solution embedding). Read back under the database role
the application actually runs as, with the tenant context set to one organisation at a time: each
organisation sees only its own rows and never the other's, and a no-organisation read sees neither.

Before the fence these tables have no organization_id and no row-level-security policy, so the role
reads every row regardless of context: the test is red. After the migration adds the column, the
backfill and the four policies, it is green.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.pool import NullPool

REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "migrations" / "versions" / "20261027_fence_capability.py"
ROLE = "archie_rls_test_runtime"
PASSWORD = uuid.uuid4().hex

# (table, parent-less columns to write besides organization_id, filled per organisation at seed time)
FENCED_TABLES = (
    "unified_application_capability_mapping",
    "application_capability_coverage",
    "application_process_support",
    "roadmap_work_packages",
    "solution_embeddings",
)


def _load():
    spec = importlib.util.spec_from_file_location("fence_capability_under_test", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def world(app, _schema):
    from app import db

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        owner = db.engine
        with owner.begin() as c:
            if not c.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": ROLE}).scalar():
                c.execute(text(f"CREATE ROLE {ROLE}"))  # nosec B608 - module constant
            c.execute(text(
                f"ALTER ROLE {ROLE} WITH LOGIN PASSWORD '{PASSWORD}' "  # nosec B608 - test-only role
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"))
            c.execute(text(f"GRANT USAGE ON SCHEMA public TO {ROLE}"))  # nosec B608
            c.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {ROLE}"))  # nosec B608
            c.execute(text(f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {ROLE}"))  # nosec B608
        module = _load()
        with owner.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()

        ids = {}
        with owner.begin() as c:
            for side in ("a", "b"):
                org = c.execute(text(
                    "INSERT INTO organizations (name, slug) VALUES (:n, :s) RETURNING id"),
                    {"n": f"Cap {side.upper()} {marker}", "s": f"cap-{side}-{marker}"}).scalar()
                user = c.execute(text(
                    "INSERT INTO users (email, organization_id) VALUES (:e, :o) RETURNING id"),
                    {"e": f"{side}-{marker}@example.test", "o": org}).scalar()
                app_c = c.execute(text(
                    "INSERT INTO application_components (name, organization_id) VALUES (:n, :o) RETURNING id"),
                    {"n": f"App {side} {marker}", "o": org}).scalar()
                sol = c.execute(text(
                    "INSERT INTO solutions (name, organization_id) VALUES (:n, :o) RETURNING id"),
                    {"n": f"Sol {side} {marker}", "o": org}).scalar()
                ucap = c.execute(text(
                    "INSERT INTO unified_capabilities (name, level) VALUES (:n, 1) RETURNING id"),
                    {"n": f"UCap {side} {marker}"}).scalar()
                bcap = c.execute(text(
                    "INSERT INTO business_capability (name, level, organization_id) VALUES (:n, 1, :o) RETURNING id"),
                    {"n": f"BCap {side} {marker}", "o": org}).scalar()
                proc = c.execute(text(
                    "INSERT INTO business_processes (name, organization_id) VALUES (:n, :o) RETURNING id"),
                    {"n": f"Proc {side} {marker}", "o": org}).scalar()
                # one child row per fenced table, organisation stamped explicitly
                rows = {}
                rows["unified_application_capability_mapping"] = c.execute(text(
                    "INSERT INTO unified_application_capability_mapping "
                    "(unified_capability_id, application_component_id, organization_id) "
                    "VALUES (:u, :a, :o) RETURNING id"), {"u": ucap, "a": app_c, "o": org}).scalar()
                rows["application_capability_coverage"] = c.execute(text(
                    "INSERT INTO application_capability_coverage "
                    "(application_component_id, capability_id, organization_id) "
                    "VALUES (:a, :b, :o) RETURNING id"), {"a": app_c, "b": bcap, "o": org}).scalar()
                rows["application_process_support"] = c.execute(text(
                    "INSERT INTO application_process_support "
                    "(application_component_id, business_process_id, organization_id) "
                    "VALUES (:a, :p, :o) RETURNING id"), {"a": app_c, "p": proc, "o": org}).scalar()
                rows["roadmap_work_packages"] = c.execute(text(
                    "INSERT INTO roadmap_work_packages "
                    "(name, status, business_capability, created_by, organization_id) "
                    "VALUES (:n, 'planned', :b, :u, :o) RETURNING id"),
                    {"n": f"WP {side} {marker}", "b": f"BCap {side} {marker}", "u": user, "o": org}).scalar()
                rows["solution_embeddings"] = c.execute(text(
                    "INSERT INTO solution_embeddings (solution_id, created_at, organization_id) "
                    "VALUES (:s, now(), :o) RETURNING id"), {"s": sol, "o": org}).scalar()
                ids[side] = {"org": org, "rows": rows}

        runtime = sa.create_engine(owner.url.set(username=ROLE, password=PASSWORD), poolclass=NullPool)
        yield ids, runtime, owner
        runtime.dispose()
        with owner.begin() as c:
            for side in ("a", "b"):
                for table, row_id in ids[side]["rows"].items():
                    c.execute(text(f"DELETE FROM {table} WHERE id = :i"), {"i": row_id})  # nosec B608 - literals


def _read_ids(runtime, org_id, table, both_ids):
    from app.middleware.tenant_isolation import set_database_tenant_context

    conn = runtime.connect()
    tx = conn.begin()
    try:
        set_database_tenant_context(conn, org_id)
        sql = f"SELECT id FROM {table} WHERE id = ANY(:i)"  # nosec B608 - literal table name
        return {r[0] for r in conn.execute(text(sql), {"i": both_ids})}
    finally:
        tx.rollback()
        conn.close()


def test_each_organisation_sees_only_its_own_rows_in_every_batch_table(world):
    ids, runtime, _ = world
    for table in FENCED_TABLES:
        a_id = ids["a"]["rows"][table]
        b_id = ids["b"]["rows"][table]
        both = [a_id, b_id]
        assert _read_ids(runtime, ids["a"]["org"], table, both) == {a_id}, f"A saw a foreign row in {table}"
        assert _read_ids(runtime, ids["b"]["org"], table, both) == {b_id}, f"B saw a foreign row in {table}"


def test_a_read_with_no_organisation_sees_no_rows(world):
    ids, runtime, _ = world
    for table in FENCED_TABLES:
        both = [ids["a"]["rows"][table], ids["b"]["rows"][table]]
        assert _read_ids(runtime, None, table, both) == set(), f"a contextless read saw rows in {table}"


def test_an_organisation_cannot_write_a_row_for_another_organisation(world):
    from app.middleware.tenant_isolation import set_database_tenant_context
    from sqlalchemy.exc import DBAPIError

    ids, runtime, _ = world
    a_org, b_org = ids["a"]["org"], ids["b"]["org"]
    conn = runtime.connect()
    tx = conn.begin()
    try:
        set_database_tenant_context(conn, a_org)
        with pytest.raises(DBAPIError):
            conn.execute(text(
                "INSERT INTO unified_application_capability_mapping "
                "(unified_capability_id, application_component_id, organization_id) VALUES (1, 1, :o)"),
                {"o": b_org})
    finally:
        tx.rollback()
        conn.close()

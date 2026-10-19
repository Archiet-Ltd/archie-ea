"""The vendor analysis fence on a database in the state a real deploy finds it.

``deploy-schema.sh`` runs ``init-db``, ``schema-upgrade``, then
``reconcile-schema``. On an existing database the five child tables therefore
reach ``schema-upgrade`` with NO ``organization_id`` column: ``create_all`` does
not alter existing tables and ``reconcile-schema`` has not run yet. A revision
that only checks for the column applies no policy there and is still recorded as
applied. Every test here builds that state by hand on a scratch database (never
``create_all``), runs the revision alone, and reads the result.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

REPO = Path(__file__).resolve().parents[1]
REVISION = REPO / "migrations" / "versions" / "20261018_vendor_analysis_rls.py"

CHILDREN = (
    "vendor_options",
    "analysis_recommendations",
    "analysis_scenarios",
    "required_capabilities",
    "analysis_audit_logs",
)
POLICIES = {"tenant_select", "tenant_insert", "tenant_update", "tenant_delete"}


def _load():
    spec = importlib.util.spec_from_file_location("vendor_analysis_rls_under_test", REVISION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgrade(engine):
    module = _load()
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()


@pytest.fixture()
def scratch():
    """A scratch database in the pre-reconcile shape: no organization_id on any child."""
    base = os.environ["TEST_DATABASE_URL"]
    parts = urlsplit(base)
    name = f"vendor_rls_{uuid.uuid4().hex[:10]}"
    admin_url = urlunsplit(parts._replace(scheme="postgresql", path="/postgres"))
    scratch_url = urlunsplit(parts._replace(scheme="postgresql+psycopg2", path=f"/{name}"))
    admin = create_engine(
        admin_url.replace("postgresql://", "postgresql+psycopg2://"),
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",
    )
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))  # nosec B608 - generated name
    engine = create_engine(scratch_url, poolclass=NullPool)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE organizations (id serial PRIMARY KEY, name text)"))
        connection.execute(text("INSERT INTO organizations (name) VALUES ('A'), ('B')"))
        connection.execute(
            text("CREATE TABLE business_capability (id serial PRIMARY KEY, name text, organization_id integer)")
        )
        # options_analysis carries its own organisation, stamped from the creator's
        # organisation; capability_id is client-supplied and may name another organisation's.
        connection.execute(
            text(
                "CREATE TABLE options_analysis (id serial PRIMARY KEY, capability_id integer, "
                "created_by_id integer, organization_id integer)"
            )
        )
        for table in CHILDREN:
            connection.execute(
                text(f"CREATE TABLE {table} (id serial PRIMARY KEY, analysis_id integer, note text)")  # nosec B608
            )
        connection.execute(
            text(
                "INSERT INTO business_capability (name, organization_id) VALUES "
                "('cap-a', 1), ('cap-b', 2), ('cap-orphan', NULL)"
            )
        )
        # analysis 1 -> own organisation B, capability belongs to A (mismatch: B must win);
        # analysis 2 -> own organisation B, capability B; analysis 3 -> no organisation,
        # capability with none either; analysis 4 -> no organisation, no capability row;
        # analysis 5 -> no organisation, capability A (falls back to A).
        connection.execute(
            text(
                "INSERT INTO options_analysis (capability_id, organization_id) VALUES "
                "(1, 2), (2, 2), (3, NULL), (99, NULL), (1, NULL)"
            )
        )
        for table in CHILDREN:
            connection.execute(
                text(f"INSERT INTO {table} (analysis_id, note) VALUES (1,'a'), (2,'b'), (3,'x'), (4,'y'), (5,'f')")  # nosec B608
            )
    yield engine
    engine.dispose()
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))  # nosec B608


def _org_by_note(engine, table):
    with engine.connect() as connection:
        return dict(
            connection.execute(text(f"SELECT note, organization_id FROM {table}")).all()  # nosec B608
        )


def test_the_scratch_database_really_has_no_column_before_the_revision(scratch):
    with scratch.connect() as connection:
        for table in CHILDREN:
            assert not connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns WHERE table_name = :t "
                    "AND column_name = 'organization_id'"
                ),
                {"t": table},
            ).scalar()


@pytest.mark.parametrize("table", CHILDREN)
def test_revision_alone_adds_the_column_index_and_foreign_key(scratch, table):
    _upgrade(scratch)
    with scratch.connect() as connection:
        column = connection.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns WHERE table_name = :t "
                "AND column_name = 'organization_id'"
            ),
            {"t": table},
        ).scalar()
        assert column == "YES"
        assert connection.execute(
            text("SELECT 1 FROM pg_indexes WHERE tablename = :t AND indexname = :i"),
            {"t": table, "i": f"ix_{table}_organization_id"},
        ).scalar()
        assert connection.execute(
            text("SELECT 1 FROM pg_constraint WHERE conname = :n AND contype = 'f'"),
            {"n": f"fk_{table}_organization_id"},
        ).scalar()


@pytest.mark.parametrize("table", CHILDREN)
def test_child_keeps_its_analysis_organisation_even_when_the_capability_belongs_to_another(scratch, table):
    _upgrade(scratch)
    orgs = _org_by_note(scratch, table)
    assert orgs["a"] == 2, "analysis organisation (B) must beat its capability's organisation (A)"
    assert orgs["b"] == 2


@pytest.mark.parametrize("table", CHILDREN)
def test_null_parent_falls_back_to_the_capability_and_unresolved_rows_stay_null(scratch, table):
    _upgrade(scratch)
    orgs = _org_by_note(scratch, table)
    assert orgs["f"] == 1
    # unresolved rows stay NULL, nothing is guessed, and the revision did not raise
    assert orgs["x"] is None
    assert orgs["y"] is None


def test_an_existing_organisation_is_never_overwritten(scratch):
    _upgrade(scratch)
    with scratch.begin() as connection:
        connection.execute(text("UPDATE vendor_options SET organization_id = 1 WHERE note = 'a'"))
    # RLS is now enabled but the migration role owns the table, so it still sees the row.
    _upgrade(scratch)
    assert _org_by_note(scratch, "vendor_options")["a"] == 1


def test_an_existing_foreign_key_under_another_name_is_not_duplicated(scratch):
    with scratch.begin() as connection:
        connection.execute(text("ALTER TABLE vendor_options ADD COLUMN organization_id integer"))
        connection.execute(
            text(
                "ALTER TABLE vendor_options ADD CONSTRAINT legacy_vendor_org_fk "
                "FOREIGN KEY (organization_id) REFERENCES organizations (id)"
            )
        )
    _upgrade(scratch)
    with scratch.connect() as connection:
        count = connection.execute(
            text(
                "SELECT count(*) FROM pg_constraint WHERE contype = 'f' "
                "AND conrelid = to_regclass('public.vendor_options') "
                "AND conname IN ('legacy_vendor_org_fk', 'fk_vendor_options_organization_id')"
            )
        ).scalar()
    assert count == 1


@pytest.mark.parametrize("table", CHILDREN)
def test_policies_are_actually_active_after_the_revision(scratch, table):
    _upgrade(scratch)
    with scratch.connect() as connection:
        enabled = connection.execute(
            text("SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass(:t)"),
            {"t": f"public.{table}"},
        ).scalar()
        assert enabled is True
        names = {
            row[0]
            for row in connection.execute(
                text("SELECT policyname FROM pg_policies WHERE tablename = :t"), {"t": table}
            )
        }
        assert names == POLICIES


def test_a_non_owner_role_sees_only_its_organisations_rows(scratch):
    _upgrade(scratch)
    role = f"vendor_rls_rt_{uuid.uuid4().hex[:8]}"
    with scratch.begin() as connection:
        connection.execute(
            text(f"CREATE ROLE {role} NOSUPERUSER NOBYPASSRLS")  # nosec B608 - generated name
        )
        for table in CHILDREN:
            connection.execute(text(f"GRANT SELECT ON {table} TO {role}"))  # nosec B608
    try:
        for table in CHILDREN:
            with scratch.connect() as connection:
                connection.execute(text(f"SET ROLE {role}"))  # nosec B608
                connection.execute(text("SELECT set_config('archie.organization_id', '1', false)"))
                assert [r[0] for r in connection.execute(text(f"SELECT note FROM {table}"))] == ["f"]  # nosec B608
                connection.execute(text("SELECT set_config('archie.organization_id', '2', false)"))
                assert sorted(r[0] for r in connection.execute(text(f"SELECT note FROM {table}"))) == ["a", "b"]  # nosec B608
                connection.execute(text("SELECT set_config('archie.organization_id', '', false)"))
                assert list(connection.execute(text(f"SELECT note FROM {table}"))) == []  # nosec B608
    finally:
        with scratch.begin() as connection:
            for table in CHILDREN:
                connection.execute(text(f"REVOKE ALL ON {table} FROM {role}"))  # nosec B608
            connection.execute(text(f"DROP ROLE {role}"))  # nosec B608


def test_running_the_revision_twice_changes_nothing(scratch):
    _upgrade(scratch)
    first = {t: _org_by_note(scratch, t) for t in CHILDREN}
    _upgrade(scratch)
    assert {t: _org_by_note(scratch, t) for t in CHILDREN} == first


def test_unresolved_rows_are_reported_not_fatal(scratch, capsys):
    _upgrade(scratch)
    out = capsys.readouterr().out
    for table in CHILDREN:
        assert f"2 row(s) in {table} have no derivable organisation" in out


def test_reconcile_schema_no_longer_carries_a_second_derivation():
    source = (REPO / "app" / "commands" / "reconcile_schema.py").read_text(encoding="utf-8")
    assert "_backfill_options_analysis_organizations" not in source
    assert "vendor_options" not in source

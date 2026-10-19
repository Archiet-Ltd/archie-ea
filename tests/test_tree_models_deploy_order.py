"""The tree-model organisation fence on a database in the state a real deploy
finds it.

``deploy-schema.sh`` runs ``init-db``, ``schema-upgrade``, then
``reconcile-schema``. On an existing database the six tables therefore reach
``schema-upgrade`` with NO ``organization_id`` column: ``create_all`` does not
alter existing tables and ``reconcile-schema`` has not run yet. A revision
that only checks for the column applies no policy there and is still
recorded as applied. Every test here builds that state by hand on a scratch
database (never ``create_all``), runs the two revisions alone, and reads the
result. Modeled on ``tests/test_vendor_analysis_rls_deploy_order.py``.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

REPO = Path(__file__).resolve().parents[1]
ORG_ID_REVISION = REPO / "migrations" / "versions" / "20261018_tree_models_org_id.py"
RLS_REVISION = REPO / "migrations" / "versions" / "20261018_tree_models_rls.py"

TABLES = (
    "tasks",
    "portfolio_initiatives",
    "system_boundaries",
    "compliance_requirements",
    "vendor_capability_hierarchy",
    "vendor_process_hierarchy",
)
# Tables whose migration entry carries a creating-user fallback.
CREATOR_FALLBACK_TABLES = ("portfolio_initiatives", "system_boundaries", "compliance_requirements")
POLICIES = {"tenant_select", "tenant_insert", "tenant_update", "tenant_delete"}


def _load(path):
    spec = importlib.util.spec_from_file_location(f"under_test_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine, *modules):
    for module in modules:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()


def _upgrade_org_id(engine):
    _run(engine, _load(ORG_ID_REVISION))


def _upgrade_both(engine):
    _run(engine, _load(ORG_ID_REVISION), _load(RLS_REVISION))


def _downgrade_both(engine):
    rls = _load(RLS_REVISION)
    org_id = _load(ORG_ID_REVISION)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            rls.downgrade()
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            org_id.downgrade()


@pytest.fixture()
def scratch(request):
    """A scratch database in the pre-reconcile shape: no organization_id on any target table.

    Seeds, per table, a row attributable through its own hangs-off source
    ("a"), one through its parent only ("p"), one through its parent AND a
    creating user with a *different* organisation -- the parent must win
    ("x"), one through the creating user only, nothing else resolvable
    ("c"), and one with no provenance at all ("n").
    """
    import os
    from urllib.parse import urlsplit, urlunsplit

    base = os.environ["TEST_DATABASE_URL"]
    parts = urlsplit(base)
    name = f"tree_org_{uuid.uuid4().hex[:10]}"
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
            text("CREATE TABLE archimate_elements (id serial PRIMARY KEY, organization_id integer)")
        )
        connection.execute(
            text("CREATE TABLE business_capability (id serial PRIMARY KEY, organization_id integer)")
        )
        connection.execute(text("CREATE TABLE projects (id serial PRIMARY KEY, organization_id integer)"))
        connection.execute(text("CREATE TABLE requirements (id serial PRIMARY KEY, organization_id integer)"))
        connection.execute(
            text("CREATE TABLE vendor_stack_templates (id serial PRIMARY KEY, organization_id integer)")
        )
        connection.execute(text("CREATE TABLE users (id serial PRIMARY KEY, organization_id integer)"))
        connection.execute(
            text(
                "INSERT INTO archimate_elements (organization_id) VALUES (1), (2) "
                "RETURNING id"
            )
        )
        connection.execute(text("INSERT INTO business_capability (organization_id) VALUES (1)"))
        connection.execute(text("INSERT INTO projects (organization_id) VALUES (1)"))
        connection.execute(text("INSERT INTO requirements (organization_id) VALUES (1)"))
        connection.execute(text("INSERT INTO vendor_stack_templates (organization_id) VALUES (1)"))
        # User 1 belongs to organisation 2 -- deliberately NOT the same
        # organisation as element/project/template id 1 (organisation 1), so
        # a row resolvable through its own hangs-off link or its parent, and
        # one resolvable only through this user, land on visibly different
        # organisations and a test asserting the wrong source would fail.
        connection.execute(text("INSERT INTO users (organization_id) VALUES (2)"))

        connection.execute(
            text(
                "CREATE TABLE tasks (id serial PRIMARY KEY, project_id integer, "
                "requirement_id integer, parent_task_id integer, note text)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE portfolio_initiatives (id serial PRIMARY KEY, archimate_element_id integer, "
                "created_by_id integer, parent_initiative_id integer, note text)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE system_boundaries (id serial PRIMARY KEY, archimate_element_id integer, "
                "created_by_id integer, parent_boundary_id integer, note text)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE compliance_requirements (id serial PRIMARY KEY, archimate_element_id integer, "
                "applies_to_capability_id integer, created_by_id integer, parent_requirement_id integer, "
                "note text)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE vendor_capability_hierarchy (id serial PRIMARY KEY, template_id integer, "
                "parent_id integer, note text)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE vendor_process_hierarchy (id serial PRIMARY KEY, template_id integer, "
                "parent_id integer, note text)"
            )
        )

        # tasks: hangs-off source only (no creator fallback on this table).
        connection.execute(
            text(
                "INSERT INTO tasks (project_id, note) VALUES (1, 'a'), (NULL, 'n')"
            )
        )
        root = connection.execute(
            text("INSERT INTO tasks (project_id, note) VALUES (1, 'root') RETURNING id")
        ).scalar()
        connection.execute(
            text("INSERT INTO tasks (parent_task_id, note) VALUES (:p, 'p')"), {"p": root}
        )

        for table, parent_col in (
            ("portfolio_initiatives", "parent_initiative_id"),
            ("system_boundaries", "parent_boundary_id"),
        ):
            connection.execute(
                text(
                    f"INSERT INTO {table} (archimate_element_id, note) VALUES (1, 'a')"  # nosec B608 - literal names
                )
            )
            root_id = connection.execute(
                text(f"INSERT INTO {table} (archimate_element_id, note) VALUES (1, 'root') RETURNING id")  # nosec B608
            ).scalar()
            connection.execute(
                text(
                    f"INSERT INTO {table} ({parent_col}, created_by_id, note) "  # nosec B608
                    "VALUES (:p, 1, 'x')"
                ),
                {"p": root_id},
            )
            connection.execute(
                text(f"INSERT INTO {table} (created_by_id, note) VALUES (1, 'c')")  # nosec B608
            )
            connection.execute(text(f"INSERT INTO {table} (note) VALUES ('n')"))  # nosec B608

        connection.execute(
            text(
                "INSERT INTO compliance_requirements (archimate_element_id, note) VALUES (1, 'a')"
            )
        )
        root_cr = connection.execute(
            text(
                "INSERT INTO compliance_requirements (archimate_element_id, note) VALUES (1, 'root') "
                "RETURNING id"
            )
        ).scalar()
        connection.execute(
            text(
                "INSERT INTO compliance_requirements (parent_requirement_id, created_by_id, note) "
                "VALUES (:p, 1, 'x')"
            ),
            {"p": root_cr},
        )
        connection.execute(
            text("INSERT INTO compliance_requirements (created_by_id, note) VALUES (1, 'c')")
        )
        connection.execute(text("INSERT INTO compliance_requirements (note) VALUES ('n')"))

        for table in ("vendor_capability_hierarchy", "vendor_process_hierarchy"):
            connection.execute(
                text(f"INSERT INTO {table} (template_id, note) VALUES (1, 'a')")  # nosec B608
            )
            root_id = connection.execute(
                text(f"INSERT INTO {table} (template_id, note) VALUES (1, 'root') RETURNING id")  # nosec B608
            ).scalar()
            connection.execute(
                text(f"INSERT INTO {table} (parent_id, note) VALUES (:p, 'p')"),  # nosec B608
                {"p": root_id},
            )
            connection.execute(text(f"INSERT INTO {table} (note) VALUES ('n')"))  # nosec B608

    yield engine
    engine.dispose()
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))  # nosec B608


def _org_by_note(engine, table):
    with engine.connect() as connection:
        return dict(
            connection.execute(text(f"SELECT note, organization_id FROM {table}")).all()  # nosec B608
        )


def test_scratch_has_no_column_before_the_revision(scratch):
    with scratch.connect() as connection:
        for table in TABLES:
            assert not connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns WHERE table_name = :t "
                    "AND column_name = 'organization_id'"
                ),
                {"t": table},
            ).scalar()


@pytest.mark.parametrize("table", TABLES)
def test_revision_alone_adds_the_column_index_and_foreign_key(scratch, table):
    _upgrade_org_id(scratch)
    with scratch.connect() as connection:
        column = connection.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns WHERE table_name = :t "
                "AND column_name = 'organization_id'"
            ),
            {"t": table},
        ).scalar()
        # Every table in this fixture carries an unresolved ('n') row, so the
        # NOT NULL tightening must stay skipped.
        assert column == "YES"
        assert connection.execute(
            text("SELECT 1 FROM pg_indexes WHERE tablename = :t AND indexname = :i"),
            {"t": table, "i": f"ix_{table}_organization_id"},
        ).scalar()
        assert connection.execute(
            text("SELECT 1 FROM pg_constraint WHERE conname = :n AND contype = 'f'"),
            {"n": f"fk_{table}_organization_id"},
        ).scalar()


@pytest.mark.parametrize("table", TABLES)
def test_hangs_off_source_resolves_row_a(scratch, table):
    _upgrade_org_id(scratch)
    orgs = _org_by_note(scratch, table)
    assert orgs["a"] == 1


@pytest.mark.parametrize("table", CREATOR_FALLBACK_TABLES)
def test_parent_wins_over_the_creating_user(scratch, table):
    """Row 'x' has both a resolvable parent (organisation 1, via 'root') and a
    creating user in organisation 2. Parent-derivation must win: the
    established convention (app/commands/backfill_layer_tenancy.py) runs the
    creating-user fallback last, after the row's own parent, never before it.
    """
    _upgrade_org_id(scratch)
    orgs = _org_by_note(scratch, table)
    assert orgs["root"] == 1
    assert orgs["x"] == 1, "the parent's organisation (1) must beat the creator's (2)"


@pytest.mark.parametrize("table", CREATOR_FALLBACK_TABLES)
def test_creating_user_is_the_last_resort(scratch, table):
    """Row 'c' has no hangs-off source and no resolvable parent -- only a
    creating user. It must still resolve, just last."""
    _upgrade_org_id(scratch)
    orgs = _org_by_note(scratch, table)
    assert orgs["c"] == 2


@pytest.mark.parametrize("table", TABLES)
def test_unresolved_rows_stay_null(scratch, table):
    _upgrade_org_id(scratch)
    orgs = _org_by_note(scratch, table)
    assert orgs["n"] is None


@pytest.mark.parametrize("table", ("tasks", "vendor_capability_hierarchy", "vendor_process_hierarchy"))
def test_parent_chain_resolves_a_table_with_no_creator_fallback(scratch, table):
    _upgrade_org_id(scratch)
    orgs = _org_by_note(scratch, table)
    assert orgs["root"] == 1
    assert orgs["p"] == 1


def test_an_existing_organisation_is_never_overwritten(scratch):
    _upgrade_org_id(scratch)
    # organisation 2 (not 1, task 'a''s own project-derived organisation) so
    # a passing test can only mean the value survived untouched, not that
    # the backfill happened to recompute the same answer.
    with scratch.begin() as connection:
        connection.execute(text("UPDATE tasks SET organization_id = 2 WHERE note = 'a'"))
    _upgrade_org_id(scratch)
    assert _org_by_note(scratch, "tasks")["a"] == 2


def test_an_existing_foreign_key_under_another_name_is_not_duplicated(scratch):
    with scratch.begin() as connection:
        connection.execute(text("ALTER TABLE tasks ADD COLUMN organization_id integer"))
        connection.execute(
            text(
                "ALTER TABLE tasks ADD CONSTRAINT legacy_tasks_org_fk "
                "FOREIGN KEY (organization_id) REFERENCES organizations (id)"
            )
        )
    _upgrade_org_id(scratch)
    with scratch.connect() as connection:
        count = connection.execute(
            text(
                "SELECT count(*) FROM pg_constraint WHERE contype = 'f' "
                "AND conrelid = to_regclass('public.tasks') "
                "AND conname IN ('legacy_tasks_org_fk', 'fk_tasks_organization_id')"
            )
        ).scalar()
    assert count == 1


def test_running_the_org_id_revision_twice_changes_nothing(scratch):
    _upgrade_org_id(scratch)
    first = {t: _org_by_note(scratch, t) for t in TABLES}
    _upgrade_org_id(scratch)
    assert {t: _org_by_note(scratch, t) for t in TABLES} == first


def test_running_both_revisions_twice_changes_nothing(scratch):
    _upgrade_both(scratch)
    first = {t: _org_by_note(scratch, t) for t in TABLES}
    _upgrade_both(scratch)
    assert {t: _org_by_note(scratch, t) for t in TABLES} == first


@pytest.mark.parametrize("table", TABLES)
def test_policies_are_actually_active_after_both_revisions(scratch, table):
    _upgrade_both(scratch)
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


def test_unresolved_rows_are_reported_not_fatal(scratch, capsys):
    _upgrade_org_id(scratch)
    out = capsys.readouterr().out
    for table in TABLES:
        assert f"in {table} have no derivable organisation" in out


@pytest.mark.parametrize("table", TABLES)
def test_downgrade_removes_column_index_foreign_key_and_policies(scratch, table):
    _upgrade_both(scratch)
    _downgrade_both(scratch)
    with scratch.connect() as connection:
        assert not connection.execute(
            text(
                "SELECT 1 FROM information_schema.columns WHERE table_name = :t "
                "AND column_name = 'organization_id'"
            ),
            {"t": table},
        ).scalar()
        assert not connection.execute(
            text("SELECT 1 FROM pg_constraint WHERE conname = :n"),
            {"n": f"fk_{table}_organization_id"},
        ).scalar()
        names = {
            row[0]
            for row in connection.execute(
                text("SELECT policyname FROM pg_policies WHERE tablename = :t"), {"t": table}
            )
        }
        assert names == set()


def test_required_capabilities_is_not_touched_by_either_revision():
    """required_capabilities is the generic tree API's seventh registered
    tree, but its derivation and fence are 20261018_vendor_analysis_rls's
    alone -- these two revisions must not list it."""
    org_id = _load(ORG_ID_REVISION)
    rls = _load(RLS_REVISION)
    assert "required_capabilities" not in org_id.TABLES
    assert "required_capabilities" not in rls.TENANT_TABLES

"""The solution-notes migration backfills organization_id from the solution (T-sec02).

Runs the revision against a throw-away database holding the legacy shape of the table (no
organization_id), once with rows and once empty, then a second time to prove it is a no-op.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

REPO = Path(__file__).resolve().parents[1]
PATH = REPO / "migrations" / "versions" / "20261027_stakeholder_comm_org.py"


def _load():
    spec = importlib.util.spec_from_file_location("stakeholder_comm_org_migration", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def legacy_db():
    admin_url = os.environ.get("DATABASE_ADMIN_URL")
    if not admin_url:
        pytest.skip("DATABASE_ADMIN_URL not set")
    name = f"mig_comm_{uuid.uuid4().hex[:8]}"
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    engine = sa.create_engine(sa.engine.make_url(admin_url).set(database=name))
    with engine.begin() as c:
        c.execute(text("CREATE TABLE organizations (id serial primary key)"))
        c.execute(text("CREATE TABLE solutions (id serial primary key, organization_id integer)"))
        c.execute(text(
            "CREATE TABLE stakeholder_communications (id serial primary key, solution_id integer, "
            "subject varchar(255) not null, logged_by integer)"))
    yield engine
    engine.dispose()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _upgrade(engine):
    module = _load()
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            module.upgrade()


def test_upgrade_backfills_existing_rows_and_is_repeatable(legacy_db):
    with legacy_db.begin() as c:
        c.execute(text("INSERT INTO organizations DEFAULT VALUES"))
        c.execute(text("INSERT INTO organizations DEFAULT VALUES"))
        c.execute(text("INSERT INTO solutions (organization_id) VALUES (1), (2)"))
        c.execute(text(
            "INSERT INTO stakeholder_communications (solution_id, subject) "
            "VALUES (1, 'a1'), (1, 'a2'), (2, 'b1')"))
    _upgrade(legacy_db)
    with legacy_db.connect() as c:
        rows = dict(c.execute(text("SELECT subject, organization_id FROM stakeholder_communications")).all())
        nullable = c.execute(text(
            "SELECT is_nullable FROM information_schema.columns WHERE table_name = "
            "'stakeholder_communications' AND column_name = 'organization_id'")).scalar()
        rls = c.execute(text(
            "SELECT relrowsecurity FROM pg_class WHERE relname = 'stakeholder_communications'")).scalar()
        policies = c.execute(text(
            "SELECT count(*) FROM pg_policies WHERE tablename = 'stakeholder_communications'")).scalar()
    assert rows == {"a1": 1, "a2": 1, "b1": 2}
    assert nullable == "NO"
    assert rls is True and policies == 4
    _upgrade(legacy_db)  # second run changes nothing and does not raise


def test_upgrade_on_an_empty_table_and_with_an_orphan_row(legacy_db):
    _upgrade(legacy_db)
    with legacy_db.connect() as c:
        assert c.execute(text(
            "SELECT is_nullable FROM information_schema.columns WHERE table_name = "
            "'stakeholder_communications' AND column_name = 'organization_id'")).scalar() == "NO"


def test_orphan_note_is_left_unowned_not_guessed(legacy_db):
    with legacy_db.begin() as c:
        c.execute(text("INSERT INTO stakeholder_communications (solution_id, subject) VALUES (NULL, 'orphan')"))
    _upgrade(legacy_db)
    with legacy_db.connect() as c:
        assert c.execute(text("SELECT organization_id FROM stakeholder_communications")).scalar() is None

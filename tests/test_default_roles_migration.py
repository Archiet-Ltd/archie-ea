"""A database built from migrations has the product's roles.

The roles used to be created only by a command run after the schema step, so
a new install, a restore drill or a new environment had none: the first owner
who signed up had no role and the welcome page answered 403.

These drive the same commands the deploy runs against throwaway databases.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from app.models.user import DEFAULT_ROLES
from tests.test_schema_migrations import (  # noqa: F401  (fixture re-exported)
    _conn_scalar,
    _flask,
    scratch_databases,
)

REVISION = "20261101_seed_default_roles"
PREVIOUS = "20261031_provenance_index"
_ROLES = "SELECT name, permissions, \"index\", \"default\" FROM roles ORDER BY name"


def _roles(url):
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return [tuple(r) for r in conn.execute(text(_ROLES)).all()]
    finally:
        engine.dispose()


def _run_sql(url, sql):
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text(sql))
    finally:
        engine.dispose()


def _expected():
    return sorted((n, p, i, d) for n, (p, i, d) in DEFAULT_ROLES.items())


@pytest.fixture
def migrated_db(scratch_databases):
    url = scratch_databases("roles")
    _flask(url, ["schema-upgrade"])
    return url


def test_database_migrated_from_empty_has_every_role_the_command_creates(migrated_db):
    assert _roles(migrated_db) == _expected()
    assert _conn_scalar(migrated_db, 'SELECT count(*) FROM roles WHERE "default"') == 1


def test_the_old_command_run_afterwards_changes_nothing(migrated_db):
    before = _roles(migrated_db)
    ids = _conn_scalar(migrated_db, "SELECT string_agg(id::text, ',' ORDER BY id) FROM roles")

    _flask(migrated_db, ["backfill-architect-role"])

    assert _roles(migrated_db) == before
    assert _conn_scalar(
        migrated_db, "SELECT string_agg(id::text, ',' ORDER BY id) FROM roles"
    ) == ids


def test_upgrade_leaves_an_existing_role_and_its_assignment_alone(migrated_db):
    _flask(migrated_db, ["db", "downgrade", PREVIOUS])
    _run_sql(migrated_db, "DELETE FROM roles WHERE name NOT IN ('Architect', 'Viewer')")
    _run_sql(migrated_db, "UPDATE roles SET permissions = 7 WHERE name = 'Architect'")
    before = [r for r in _roles(migrated_db)]

    _flask(migrated_db, ["db", "upgrade", REVISION])

    after = {r[0]: r for r in _roles(migrated_db)}
    assert after["Architect"][1] == 7
    assert [r for r in before if r[0] in ("Architect", "Viewer")] == [
        after["Architect"], after["Viewer"]
    ]
    assert sorted(after) == sorted(DEFAULT_ROLES)


def test_downgrade_then_upgrade_keeps_one_row_per_role(migrated_db):
    _flask(migrated_db, ["db", "downgrade", PREVIOUS])
    _flask(migrated_db, ["schema-upgrade"])
    assert _roles(migrated_db) == _expected()

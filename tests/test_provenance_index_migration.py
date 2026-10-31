"""A database built from migrations has the capability provenance index.

The projection of new capabilities into unified_capabilities needs the unique
index uq_unified_capabilities_provenance. It used to be created only by the
apply-unified-capability-provenance-migration command, so a database built
from migrations alone (a new install, a restore drill, a new environment) had
none and every new capability was skipped without an error.

These drive the same commands the deploy runs against throwaway databases.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from tests.test_schema_migrations import (  # noqa: F401  (fixture re-exported)
    _conn_scalar,
    _flask,
    _snapshot,
    scratch_databases,
)

REVISION = "20261031_provenance_index"
PREVIOUS = "20261031_fw_adopt_source"
INDEX = "uq_unified_capabilities_provenance"

_INDEX_STATE = (
    "SELECT i.indisunique AND i.indisvalid FROM pg_index i "
    f"JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = '{INDEX}'"
)


def _index_ok(url):
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(_INDEX_STATE)).scalar()
    finally:
        engine.dispose()


def _add_capability(url, name, source_id):
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO unified_capabilities (name, level, source_table, source_id) "
                    "VALUES (:name, 1, 'business_capability', :source_id)"
                ),
                {"name": name, "source_id": source_id},
            )
    finally:
        engine.dispose()


@pytest.fixture
def migrated_db(scratch_databases):
    """A database built from empty by the migrations alone, as a new install is."""
    url = scratch_databases("provenance")
    _flask(url, ["schema-upgrade"])
    return url


def test_database_migrated_from_empty_has_the_provenance_index(migrated_db):
    assert _index_ok(migrated_db) is True


def test_upgrade_downgrade_upgrade_cycle(migrated_db):
    _flask(migrated_db, ["db", "downgrade", PREVIOUS])
    assert _index_ok(migrated_db) is None

    _flask(migrated_db, ["schema-upgrade"])
    assert _index_ok(migrated_db) is True


def test_command_changes_nothing_once_the_index_exists(migrated_db):
    before = _snapshot(migrated_db)

    _, output = _flask(migrated_db, ["apply-unified-capability-provenance-migration"])

    assert "ensured" in output
    assert _snapshot(migrated_db) == before
    assert _conn_scalar(migrated_db, "SELECT count(*) FROM unified_capabilities") == 0


def test_shared_sources_are_cleared_not_deleted_and_the_count_is_logged(migrated_db):
    _flask(migrated_db, ["db", "downgrade", PREVIOUS])
    _add_capability(migrated_db, "First", "7")
    _add_capability(migrated_db, "Second", "7")
    _add_capability(migrated_db, "Other", "8")

    _, output = _flask(migrated_db, ["schema-upgrade"])

    assert _index_ok(migrated_db) is True
    assert _conn_scalar(migrated_db, "SELECT count(*) FROM unified_capabilities") == 3
    assert _conn_scalar(
        migrated_db, "SELECT source_id FROM unified_capabilities WHERE name = 'First'"
    ) == "7"
    assert _conn_scalar(
        migrated_db, "SELECT source_id IS NULL FROM unified_capabilities WHERE name = 'Second'"
    ) is True
    assert "1 capability row(s) shared a source with an older row" in output
    assert "No row was deleted" in output

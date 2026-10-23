"""The 20261011_uwp_source_copy_unique migration actually creates its index.

The model declares uq_unified_wp_source_copy (app/models/unified_work_package.py,
__table_args__), but until this migration nothing created it on an existing database:
create_all() only creates missing tables, reconcile-schema is add-column-only, and
no migration had ever built this specific index -- unlike archimate_element_id's
sibling index, which 20261008_uwp_element_unique already covers (see
tests/test_work_package_round9.py::test_r9_element_unique_index_exists for that one).

Without the index, "INSERT ... ON CONFLICT DO NOTHING RETURNING id" for the same
(source_table, source_id) has nothing to conflict against, so a second insert for an
already-imported source row silently creates a duplicate copy instead of being
skipped -- confirmed directly against a database that predates this migration via
tests/test_row_level_security.py::test_unified_work_package_unique_element_and_conflict_inserts_are_not_hidden_by_the_fence.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parent.parent
INDEX = "uq_unified_wp_source_copy"


def _scalar(db_session, sql):
    return db_session.execute(text(sql)).scalar()


def _load_revision():
    path = ROOT / "migrations" / "versions" / "20261011_uwp_source_copy_unique.py"
    spec = importlib.util.spec_from_file_location("uwp_source_copy_unique_revision", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_upgrade(connection):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    revision = _load_revision()
    with Operations.context(MigrationContext.configure(connection)):
        revision.upgrade()
    return revision


def test_index_exists_with_the_right_shape(db_session):
    definition = _scalar(
        db_session,
        f"SELECT indexdef FROM pg_indexes WHERE tablename = 'unified_work_packages' AND indexname = '{INDEX}'",
    )
    assert definition, "the index is missing"
    assert "UNIQUE" in definition
    assert "source_table" in definition and "source_id" in definition
    assert re.search(r"WHERE \(?\(?source_table IS NOT NULL", definition), definition


def test_migration_collapses_duplicates_then_indexes_and_is_idempotent(db_session, make_org):
    org = make_org("uwp-source-dup")
    db_session.execute(
        text(f'DROP INDEX IF EXISTS {INDEX}')
    )
    db_session.flush()

    rows = []
    for _ in range(3):
        row = db_session.execute(
            text(
                "INSERT INTO unified_work_packages (name, organization_id, source_table, source_id, context, scope) "
                "VALUES ('dup copy', :o, 'legacy_table', 42, 'architecture', 'enterprise') RETURNING id"
            ),
            {"o": org.id},
        ).scalar_one()
        rows.append(row)
    db_session.flush()

    connection = db_session.connection()
    revision = _run_upgrade(connection)
    _run_upgrade(connection)  # a second run changes nothing further

    db_session.expire_all()
    survivors = db_session.execute(
        text("SELECT id, source_table FROM unified_work_packages WHERE id = ANY(:ids) ORDER BY id"),
        {"ids": rows},
    ).fetchall()
    assert survivors[0].source_table == "legacy_table"
    assert all(s.source_table is None for s in survivors[1:]), survivors

    assert _scalar(
        db_session,
        "SELECT count(*) FROM (SELECT source_table, source_id FROM unified_work_packages "
        "WHERE source_table IS NOT NULL GROUP BY 1, 2 HAVING count(*) > 1) d",
    ) == 0
    assert _scalar(db_session, f"SELECT count(*) FROM pg_indexes WHERE indexname = '{INDEX}'") == 1
    assert revision.revision == "20261011_uwp_source_copy_unique"


def test_conflict_insert_is_skipped_not_duplicated(db_session, make_org):
    org = make_org("uwp-source-conflict")
    first = db_session.execute(
        text(
            "INSERT INTO unified_work_packages (name, organization_id, source_table, source_id, context, scope) "
            "VALUES ('wp1', :o, 'conflict_src', 7, 'architecture', 'enterprise') "
            "ON CONFLICT DO NOTHING RETURNING id"
        ),
        {"o": org.id},
    ).scalar()
    assert first is not None

    again = db_session.execute(
        text(
            "INSERT INTO unified_work_packages (name, organization_id, source_table, source_id, context, scope) "
            "VALUES ('wp1', :o, 'conflict_src', 7, 'architecture', 'enterprise') "
            "ON CONFLICT DO NOTHING RETURNING id"
        ),
        {"o": org.id},
    ).scalar()
    assert again is None

    count = _scalar(
        db_session,
        "SELECT count(*) FROM unified_work_packages WHERE source_table = 'conflict_src' AND source_id = 7",
    )
    assert count == 1

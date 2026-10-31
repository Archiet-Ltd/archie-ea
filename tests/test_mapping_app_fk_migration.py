"""The migration that adds the application foreign key to capability mappings records what it removes.

A mapping row whose application no longer exists cannot coexist with the constraint, so the
migration removes it; before removing, it copies the whole row into an archive table. The whole
exercise runs in one transaction that is rolled back.
"""

import importlib.util
import pathlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

_MIGRATION = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions" / "20261029_mapping_app_fk.py"
_CONSTRAINT = "unified_application_capability_ma_application_component_id_fkey"


def _migration():
    spec = importlib.util.spec_from_file_location("mapping_app_fk_migration", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_orphan_mapping_rows_are_copied_to_an_archive_before_they_are_removed(app):
    from app import db

    module = _migration()
    assert module.revision == "20261029_mapping_app_fk"
    assert "Revision ID: 20261029_mapping_app_fk\n" in module.__doc__

    with app.app_context():
        connection = db.engine.connect()
        outer = connection.begin()
        try:
            connection.execute(text(
                "ALTER TABLE unified_application_capability_mapping DROP CONSTRAINT IF EXISTS %s" % _CONSTRAINT))
            capability = connection.execute(text(
                "INSERT INTO unified_capabilities (name, level, version) VALUES ('Orphan probe', 1, 1) RETURNING id"
            )).scalar()
            orphan = connection.execute(text(
                "INSERT INTO unified_application_capability_mapping (unified_capability_id, application_component_id) "
                "VALUES (:c, 2147000000) RETURNING id"), {"c": capability}).scalar()

            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()

            assert connection.execute(text(
                "SELECT 1 FROM unified_application_capability_mapping WHERE id = :id"), {"id": orphan}
            ).first() is None
            archived = connection.execute(text(
                "SELECT unified_capability_id, application_component_id, archived_at "
                "FROM unified_application_capability_mapping_removed WHERE id = :id"), {"id": orphan}).one()
            assert archived[0] == capability and archived[1] == 2147000000 and archived[2] is not None
            assert connection.execute(text(
                "SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": _CONSTRAINT}).first() is not None
        finally:
            outer.rollback()
            connection.close()

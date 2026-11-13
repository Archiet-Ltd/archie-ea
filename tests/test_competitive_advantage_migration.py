"""The Competitive Advantage migration never erases an answer a person could have recorded.

An import can record No on purpose, so a stored False is kept and still reads "No" after the
upgrade. Yes stays Yes and an unset value stays unset. The run happens inside one transaction
that is rolled back, so the shared test database is left as it was.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from sqlalchemy import text

MIGRATION_FILE = (
    Path(__file__).resolve().parents[1] / "migrations" / "versions" / "20261112_competitive_advantage_nullable.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("migration_competitive_advantage", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _insert(conn, sql, **values):
    return conn.execute(text(sql + " RETURNING id"), values).scalar_one()


def _values(conn, ids):
    rows = conn.execute(
        text("SELECT id, competitive_advantage FROM application_components WHERE id = ANY(:ids)"), {"ids": list(ids)}
    )
    return {r[0]: r[1] for r in rows}


def test_a_recorded_no_survives_the_upgrade_the_downgrade_and_a_second_upgrade(app):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app import db

    module = _load()
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        conn = db.engine.connect()
        trans = conn.begin()
        try:
            # Put the column back in the form it had before the migration.
            conn.execute(text("UPDATE application_components SET competitive_advantage = false WHERE competitive_advantage IS NULL"))
            conn.execute(text("ALTER TABLE application_components ALTER COLUMN competitive_advantage SET DEFAULT false"))
            conn.execute(text("ALTER TABLE application_components ALTER COLUMN competitive_advantage SET NOT NULL"))
            orgs = [
                _insert(conn, "INSERT INTO organizations (name, slug) VALUES (:n, :s)", n="Org %s %s" % (i, suffix), s="o%s-%s" % (i, suffix))
                for i in (1, 2)
            ]

            def app_row(org, name, value):
                return _insert(
                    conn,
                    "INSERT INTO application_components (name, organization_id, competitive_advantage) VALUES (:n, :o, :v)",
                    n="%s %s" % (name, suffix), o=org, v=value,
                )

            imported_no = app_row(orgs[0], "Imported no", False)
            unset_in_other_org = app_row(orgs[1], "Never set", False)
            yes = app_row(orgs[1], "Chosen yes", True)
            ids = [imported_no, unset_in_other_org, yes]
            before = _values(conn, ids)

            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
            after_up = _values(conn, ids)
            assert after_up[imported_no] is False, "an imported No must still read No"
            assert after_up[yes] is True
            assert after_up[unset_in_other_org] is False, "no stored answer is erased"
            assert after_up == before

            nullable = conn.execute(text(
                "SELECT is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = 'application_components' AND column_name = 'competitive_advantage'"
            )).one()
            assert nullable[0] == "YES" and nullable[1] is None

            unset = app_row(orgs[0], "Unset", None)
            with Operations.context(MigrationContext.configure(conn)):
                module.downgrade()
            after_down = _values(conn, ids + [unset])
            assert [after_down[i] for i in ids] == [before[i] for i in ids]
            assert after_down[unset] is False, "the downgrade loses no row"

            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
            assert _values(conn, ids) == before
        finally:
            trans.rollback()
            conn.close()

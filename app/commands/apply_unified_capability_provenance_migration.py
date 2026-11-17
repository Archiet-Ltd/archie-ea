"""
Schema fix: apply-unified-capability-provenance-migration.

`reconcile-schema` is ADD-COLUMN-only (`ALTER TABLE ... ADD COLUMN IF NOT
EXISTS`) and can never create an index, so the unique index on
`unified_capabilities (source_table, source_id)` that `project-capabilities`
requires (`app/commands/project_capabilities.py:PROVENANCE_INDEX`) is created by
`scripts/migrate_unified_capability_provenance.sql`, which nothing applied
before this command existed.

This command executes that SQL file verbatim, inside a single transaction, so
the deploy chain has a `flask --app manage` step for it rather than a second
`psql -f` convention for one job (the runtime containers never receive
`DATABASE_ADMIN_URL`; only the schema-owner one-shot container that already
runs every other `flask --app manage` step in `scripts/database/deploy-schema.sh`
does). The SQL itself is idempotent (`CREATE UNIQUE INDEX IF NOT EXISTS`,
wrapped in its own `BEGIN/COMMIT`) — this command re-applies it safely on every
deploy.

    flask --app manage apply-unified-capability-provenance-migration
"""

from pathlib import Path

import logging

import click
from flask.cli import with_appcontext
from sqlalchemy import text

from app import db

log = logging.getLogger(__name__)

MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "scripts" / "migrate_unified_capability_provenance.sql"
)


def provenance_index_sql() -> str:
    """The index statement from the SQL file, without its own BEGIN/COMMIT.

    The file wraps its statement in BEGIN/COMMIT; those two lines are dropped so
    the caller's transaction (this command's session, or an Alembic revision)
    owns the boundary instead of nesting one inside the other.
    """
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    return "\n".join(
        line for line in sql.splitlines()
        if line.strip().upper() not in {"BEGIN;", "COMMIT;"}
    )


# Rows that share a (source_table, source_id) pair would make the unique index
# fail. The oldest row of each pair keeps its provenance; every later row keeps
# all of its data, loses the two provenance values and is marked as retired into
# the row that kept them (ADR 0008 rule 4), so nothing is deleted, nothing that
# points at a capability is orphaned, and pickers can leave the duplicate out.
_CLEAR_DUPLICATE_PROVENANCE = """
UPDATE unified_capabilities AS u
   SET source_table = NULL, source_id = NULL,
       retired_into_id = COALESCE(u.retired_into_id, ranked.kept_id)
  FROM (
        SELECT id,
               row_number() OVER (
                   PARTITION BY source_table, source_id ORDER BY id
               ) AS position,
               first_value(id) OVER (
                   PARTITION BY source_table, source_id ORDER BY id
               ) AS kept_id
          FROM unified_capabilities
         WHERE source_table IS NOT NULL AND source_id IS NOT NULL
       ) AS ranked
 WHERE u.id = ranked.id AND ranked.position > 1
"""


def clear_duplicate_provenance(connection) -> int:
    """Strip the provenance from later duplicates; return how many rows changed."""
    cleared = connection.execute(text(_CLEAR_DUPLICATE_PROVENANCE)).rowcount
    if cleared:
        log.warning(
            "unified_capabilities: %d capability row(s) shared a source with an "
            "older row; the older row keeps its source and these %d now have no "
            "source recorded and are marked as retired into it. No row was deleted.",
            cleared, cleared,
        )
    return cleared


def ensure_provenance_index(connection) -> int:
    """Clear duplicate provenance, then create the unique index if it is absent."""
    cleared = clear_duplicate_provenance(connection)
    connection.execute(text(provenance_index_sql()))
    return cleared


@click.command("apply-unified-capability-provenance-migration")
@with_appcontext
def apply_unified_capability_provenance_migration():
    """Ensure the provenance unique index on unified_capabilities. Idempotent."""

    ensure_provenance_index(db.session.connection())
    db.session.commit()
    click.echo(
        "apply-unified-capability-provenance-migration: "
        "uq_unified_capabilities_provenance ensured."
    )


def init_app(app):
    """Register the apply-unified-capability-provenance-migration CLI command."""
    app.cli.add_command(apply_unified_capability_provenance_migration)

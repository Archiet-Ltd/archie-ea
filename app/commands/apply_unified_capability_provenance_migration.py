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

The index is now also declared on
`UnifiedCapability.__table_args__` and created by migration
`20261029_cap_prov_index`, so on every database this
command now runs against, the index is usually already there (from
`create_all`, the migration chain, or a previous run of this same command).
The command checks first and reports "already present" rather than
re-running DDL that has nothing left to do; it still applies the SQL file
the one time a database genuinely lacks the index (an existing deployment
that predates all three of those).

    flask --app manage apply-unified-capability-provenance-migration
"""

from pathlib import Path

import click
from flask.cli import with_appcontext
from sqlalchemy import text

from app import db
from app.commands.project_capabilities import PROVENANCE_INDEX, _has_provenance_index

MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "scripts" / "migrate_unified_capability_provenance.sql"
)


@click.command("apply-unified-capability-provenance-migration")
@with_appcontext
def apply_unified_capability_provenance_migration():
    """Apply scripts/migrate_unified_capability_provenance.sql. Idempotent."""

    conn = db.session.connection()
    if _has_provenance_index(conn):
        click.echo(
            f"apply-unified-capability-provenance-migration: {PROVENANCE_INDEX} "
            "already present."
        )
        return

    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    # The file already wraps its own statement in BEGIN/COMMIT; strip those two
    # lines so SQLAlchemy's own transaction (which `with_appcontext` + the ORM
    # engine manage) owns the boundary instead of nesting one inside the other.
    statements = [
        line for line in sql.splitlines()
        if line.strip().upper() not in {"BEGIN;", "COMMIT;"}
    ]
    body = "\n".join(statements)

    conn.execute(text(body))
    db.session.commit()
    click.echo(
        "apply-unified-capability-provenance-migration: "
        "uq_unified_capabilities_provenance ensured."
    )


def init_app(app):
    """Register the apply-unified-capability-provenance-migration CLI command."""
    app.cli.add_command(apply_unified_capability_provenance_migration)

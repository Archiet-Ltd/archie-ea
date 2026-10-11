"""
Schema fix: apply-entity-history-trigger.

``reconcile-schema`` is ADD-COLUMN-only and can never create a trigger, so
the generic trigger every change to ``archimate_elements`` and
``archimate_relationships`` must go through needs its own deploy step, the
same convention ``apply_unified_capability_provenance_migration.py`` already
uses for "a DDL object reconcile-schema cannot create".

One PL/pgSQL function, ``entity_history_record_version()``, fired ``BEFORE``
INSERT/UPDATE/DELETE (not ``AFTER``: the owned time columns on the base row
itself, not just the history copy, must be stamped before the row is
written, or they stay NULL forever):

- On INSERT: stamps ``NEW.valid_from``/``recorded_at`` and writes one open
  (``valid_to IS NULL``) history version.
- On UPDATE: closes the row's current open version (``valid_to``,
  ``superseded_at`` both set to the same timestamp), re-stamps
  ``NEW.valid_from``/``recorded_at`` for the new current state, and opens a
  new history version -- never two writes that could observe different
  "now()" values, since both happen in one trigger invocation with one
  captured timestamp.
- On DELETE: closes the row's current open history version with the delete
  timestamp. There is no base row left to stamp; the closed history entry
  is itself the record that the entity stopped existing at that time.

Idempotent: ``CREATE OR REPLACE FUNCTION`` and ``DROP TRIGGER IF
EXISTS``/``CREATE TRIGGER`` are both safe to re-run; safe to re-run on every
deploy, matching ``apply-unified-capability-provenance-migration``'s own
convention of re-applying rather than checking a version marker.

    flask --app manage apply-entity-history-trigger
"""

import click
from flask.cli import with_appcontext
from sqlalchemy import text

from app import db

TABLES = ("archimate_elements", "archimate_relationships")

FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION entity_history_record_version()
RETURNS TRIGGER AS $$
DECLARE
    -- statement_timestamp(), not NOW()/transaction_timestamp(): NOW() is
    -- stable for the whole transaction, so two updates on the same row in
    -- one transaction (no intervening COMMIT) would both capture the same
    -- value, closing the first version at the exact instant it opened --
    -- a zero-length interval.
    now_ts TIMESTAMP := statement_timestamp();
BEGIN
    IF TG_OP = 'DELETE' THEN
        UPDATE entity_history
        SET valid_to = now_ts, superseded_at = now_ts
        WHERE table_name = TG_TABLE_NAME
          AND record_id = OLD.id
          AND valid_to IS NULL;
        RETURN OLD;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        UPDATE entity_history
        SET valid_to = now_ts, superseded_at = now_ts
        WHERE table_name = TG_TABLE_NAME
          AND record_id = NEW.id
          AND valid_to IS NULL;
    END IF;

    -- BEFORE trigger: mutating NEW here is what actually reaches the row
    -- being written. An AFTER trigger's NEW mutation is a no-op -- the
    -- earlier version of this fix wrote only to entity_history, leaving
    -- the base row's own valid_from/recorded_at permanently NULL.
    NEW.valid_from := now_ts;
    NEW.valid_to := NULL;
    NEW.recorded_at := now_ts;
    NEW.superseded_at := NULL;

    INSERT INTO entity_history
        (organization_id, table_name, record_id, snapshot,
         valid_from, valid_to, recorded_at, source)
    VALUES
        (NEW.organization_id, TG_TABLE_NAME, NEW.id, row_to_json(NEW),
         now_ts, NULL, now_ts, 'trigger');

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


@click.command("apply-entity-history-trigger")
@click.option("--dry-run", is_flag=True, help="Report what would change; change nothing.")
@with_appcontext
def apply_entity_history_trigger(dry_run):
    """Create/replace the entity_history trigger on archimate_elements and
    archimate_relationships. Idempotent."""
    from sqlalchemy import inspect

    insp = inspect(db.engine)
    existing_tables = set(insp.get_table_names())

    if "entity_history" not in existing_tables:
        click.echo(
            "  - entity_history table absent (init-db/reconcile-schema runs "
            "before this step) — nothing to do this run"
        )
        return

    if dry_run:
        click.echo("  - would CREATE OR REPLACE FUNCTION entity_history_record_version()")
        for table in TABLES:
            click.echo(f"  - would ensure trigger entity_history_trg on {table}")
        return

    conn = db.session.connection()
    conn.execute(text(FUNCTION_SQL))

    for table in TABLES:
        if table not in existing_tables:
            click.echo(f"  - {table}: table absent, skipping its trigger")
            continue
        conn.execute(text(f'DROP TRIGGER IF EXISTS entity_history_trg ON "{table}"'))
        conn.execute(text(f"""
            CREATE TRIGGER entity_history_trg
            BEFORE INSERT OR UPDATE OR DELETE ON "{table}"
            FOR EACH ROW
            EXECUTE FUNCTION entity_history_record_version()
        """))
        click.echo(f"  + {table}: entity_history_trg ensured")

    db.session.commit()
    click.echo("apply-entity-history-trigger: done.")


def init_app(app):
    """Register the apply-entity-history-trigger CLI command."""
    app.cli.add_command(apply_entity_history_trigger)

"""
Tenancy backfill: backfill-outcome-org.

`Outcome` (app/models/models.py) shipped with no organization_id column at
all -- a live gap present in the model that has always run in production.
The tenant filter in
app/middleware/tenant_isolation.py attaches
``organization_id == g.current_org_id`` to every SELECT on a TenantMixin
model, so a model without the column was not merely unfiltered -- it was
structurally impossible to filter. Any authenticated user of any tenant
could read every other tenant's outcomes.

Outcome now uses TenantMixin (organization_id nullable, same reasoning as
Principle -- reconcile-schema is ADD-only). This command completes the
change on an existing database.

Derivation strategy, in order, since `--org-id` cannot be right for more
than one tenant:

1. Derive from `archimate_element_id` -> archimate_elements.organization_id
   (ArchiMateElement is tenant-scoped). Most outcomes have this set
   (Basecoat pattern).
2. For anything still NULL, derive from `architecture_id` ->
   architecture_models.organization_id (ArchitectureModel is tenant-scoped).
3. Anything still NULL after both has no tenant-scoped parent to derive
   from at all; report it rather than guessing.

Idempotent; safe to re-run. Run AFTER reconcile-schema:

    flask --app manage backfill-outcome-org --dry-run
    flask --app manage backfill-outcome-org
"""

import click
from flask.cli import with_appcontext

from app import db


def _backfill_outcome_org(dry_run):
    from sqlalchemy import inspect, text

    TABLE = "outcomes"
    insp = inspect(db.engine)
    if TABLE not in set(insp.get_table_names()):
        click.echo(f"  - {TABLE}: table absent, nothing to do")
        return

    conn = db.session.connection()
    cols = {c["name"] for c in insp.get_columns(TABLE)}
    if "organization_id" not in cols:
        if dry_run:
            click.echo(f"  - {TABLE}: would ADD COLUMN organization_id")
            click.echo("dry-run: no changes committed.")
            db.session.rollback()
            return
        conn.execute(
            text(f'ALTER TABLE "{TABLE}" ADD COLUMN IF NOT EXISTS organization_id INTEGER')
        )
        click.echo(f"  + {TABLE}: added organization_id")

    total_orphans = conn.execute(
        # TABLE is the hardcoded literal "outcomes" (line 44), never request
        # input; only the bound :id-style values would need parameterizing,
        # and there are none in this query.
        text(f'SELECT count(*) FROM "{TABLE}" WHERE organization_id IS NULL')  # nosec B608
    ).scalar()
    if not total_orphans:
        click.echo("  no orphaned rows — nothing to assign")
        if not dry_run:
            _add_index_and_fk(conn, TABLE)
            db.session.commit()
        else:
            db.session.rollback()
        return

    if dry_run:
        click.echo(f"  - {TABLE}: {total_orphans} orphaned row(s) found")
        click.echo(f"  - {TABLE}: would derive org from archimate_element_id, then architecture_id")
        db.session.rollback()
        return

    conn.execute(text(
        # tenancy-ok: one-time backfill, retirement 2026-12-31
        'UPDATE "outcomes" AS o SET organization_id = e.organization_id '
        'FROM "archimate_elements" AS e '
        'WHERE o.archimate_element_id = e.id AND o.organization_id IS NULL'
    ))
    after_element = conn.execute(
        # TABLE is the hardcoded literal "outcomes" (line 44), never request input.
        text(f'SELECT count(*) FROM "{TABLE}" WHERE organization_id IS NULL')  # nosec B608
    ).scalar()
    click.echo(f"  + {TABLE}: derived org for {total_orphans - after_element} row(s) from archimate_elements")

    if after_element:
        conn.execute(text(
            # tenancy-ok: one-time backfill, retirement 2026-12-31
            'UPDATE "outcomes" AS o SET organization_id = m.organization_id '
            'FROM "architecture_models" AS m '
            'WHERE o.architecture_id = m.id AND o.organization_id IS NULL'
        ))
        remaining = conn.execute(
            # TABLE is the hardcoded literal "outcomes" (line 44), never request input.
            text(f'SELECT count(*) FROM "{TABLE}" WHERE organization_id IS NULL')  # nosec B608
        ).scalar()
        click.echo(f"  + {TABLE}: derived org for {after_element - remaining} more row(s) from architecture_models")
        if remaining:
            click.echo(
                f"  ! {TABLE}: {remaining} row(s) still NULL — no archimate_element_id or "
                "architecture_id to derive an owner from; these outcomes remain hidden from "
                "every tenant-scoped view until assigned manually"
            )

    _add_index_and_fk(conn, TABLE)
    db.session.commit()
    click.echo(f"backfill {TABLE}: done.")


def _add_index_and_fk(conn, table):
    from sqlalchemy import text

    for ddl, label in (
        (f'CREATE INDEX IF NOT EXISTS ix_{table}_organization_id ON "{table}" (organization_id)', "index"),
        (
            f'ALTER TABLE "{table}" ADD CONSTRAINT fk_{table}_organization '
            "FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE",
            "foreign key",
        ),
    ):
        try:
            conn.execute(text(ddl))
            click.echo(f"  + {table}: {label}")
        except Exception as exc:  # noqa: BLE001
            click.echo(f"  ! {table}: {label} skipped ({str(exc)[:100]})")


@click.command("backfill-outcome-org")
@click.option("--dry-run", is_flag=True, help="Report what would change; change nothing.")
@with_appcontext
def backfill_outcome_org(dry_run):
    """Backfill organization_id on the outcomes table, deriving from its ArchiMate parent."""
    _backfill_outcome_org(dry_run)


def init_app(app):
    """Register the backfill-outcome-org CLI command."""
    app.cli.add_command(backfill_outcome_org)

"""Give every untyped capability a starting type (strategic, operational or supporting).

Typed rows are never touched, so the command is idempotent and safe to run on every deploy.
The rule is the one in ``app/services/capability_type.py``.
"""
from __future__ import annotations

import click
from flask.cli import with_appcontext
from sqlalchemy import text

from app import db
from app.services.capability_type import backfill_capability_types


@click.command("backfill-capability-types")
@click.option("--dry-run", is_flag=True, help="Count the untyped capabilities without writing.")
@click.option("--apply", "apply_changes", is_flag=True, help="Give them their starting type.")
@with_appcontext
def backfill_capability_types_command(dry_run, apply_changes):
    if dry_run == apply_changes:
        raise click.UsageError("choose exactly one of --dry-run or --apply")
    untyped_sql = text(
        "SELECT count(*) FROM unified_capabilities "
        "WHERE capability_type IS NULL OR trim(capability_type) = ''"
    )
    with db.engine.begin() as connection:
        before = connection.execute(untyped_sql).scalar_one()
        if dry_run:
            click.echo(f"untyped capabilities: {before}")
            return
        changed = backfill_capability_types(connection)
        after = connection.execute(untyped_sql).scalar_one()
    click.echo(f"untyped before: {before}, typed now: {changed}, untyped after: {after}")


def init_app(app):
    app.cli.add_command(backfill_capability_types_command)

"""Deliberate backfill: give every pre-existing `programme_workstreams` row
its ArchiMate WorkPackage element (R1-05, US-11).

New workstreams get this automatically from R1-05's manual-path sync calls
and (once built) R1-06's instantiate-template handler. Old rows created
before this feature shipped do not, and nothing on a deploy does this for
them -- CLI/scheduler paths run with no `g.current_org_id`, and this command
must never guess a tenant, so `--org` is mandatory and the command never
runs on deploy.

Idempotent: `sync_archimate_element` leaves an already-synced row alone.

    flask --app manage backfill-workstream-elements --dry-run --org 12
    flask --app manage backfill-workstream-elements --apply --org 12
"""

from __future__ import annotations

import click
from flask.cli import with_appcontext
from sqlalchemy import select

from app import db


def _run(*, dry_run: bool, organization_id: int) -> dict:
    from app.models.transformation_programme import ProgrammeWorkstream
    from app.services.archimate_backbone import sync_archimate_element

    session = db.session
    stmt = select(ProgrammeWorkstream).where(
        ProgrammeWorkstream.organization_id == organization_id,
        ProgrammeWorkstream.archimate_element_id.is_(None),
    )
    before = session.scalar(
        select(db.func.count()).select_from(stmt.subquery())
    )
    click.echo(f"org {organization_id}: {before} workstream(s) with no ArchiMate element")

    if dry_run:
        click.echo("dry-run: no changes committed.")
        session.rollback()
        return {"before": before, "after": before, "synced": 0}

    rows = session.scalars(stmt).all()
    synced = 0
    for workstream in rows:
        result = sync_archimate_element(workstream, session=session, organization_id=organization_id)
        if result is not None:
            synced += 1
    session.commit()

    after_stmt = select(ProgrammeWorkstream).where(
        ProgrammeWorkstream.organization_id == organization_id,
        ProgrammeWorkstream.archimate_element_id.is_(None),
    )
    after = session.scalar(select(db.func.count()).select_from(after_stmt.subquery()))
    click.echo(f"org {organization_id}: {synced} workstream(s) synced. before={before} after={after}")
    return {"before": before, "after": after, "synced": synced}


@click.command("backfill-workstream-elements")
@click.option("--dry-run", is_flag=True, help="Report what would change; change nothing.")
@click.option("--apply", "apply_changes", is_flag=True, help="Apply the backfill.")
@click.option("--org", "organization_id", type=int, required=True, help="Organisation id (mandatory).")
@with_appcontext
def backfill_workstream_elements(dry_run, apply_changes, organization_id):
    """Backfill ArchiMate WorkPackage elements for pre-existing workstreams, one org at a time."""
    if dry_run == apply_changes:
        raise click.UsageError("choose exactly one of --dry-run or --apply")
    _run(dry_run=dry_run, organization_id=organization_id)


def init_app(app):
    """Register the backfill-workstream-elements CLI command. Never runs on deploy."""
    app.cli.add_command(backfill_workstream_elements)

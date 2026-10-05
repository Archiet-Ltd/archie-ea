"""Backfill for app/models/business_capabilities.py's write-time hierarchy sync.

The `after_insert`/`after_update` listeners on `BusinessCapability` project a
capability's `parent_capability_id` into `archimate_relationships` (a
`composition` row, `derived_from='capability-hierarchy'`) going forward. This
command does the one-time catch-up for every capability that was created
before those listeners existed -- the exact rows behind the "elements with no
relationships" count an org sees today.

Idempotent: re-running it after the listeners have already caught a row up
is a no-op for that row (same INSERT-if-absent / DELETE-if-stale logic the
listener itself runs, applied once per existing row instead of once per write).
"""
from __future__ import annotations

import click
from flask.cli import with_appcontext

from app import db
from app.models.business_capabilities import (
    _sync_capability_hierarchy_relationship,
)
from app.models.business_capabilities import BusinessCapability


@click.command("sync-capability-hierarchy-relationships")
@click.option("--dry-run", is_flag=True, help="Report what would change without writing.")
@click.option("--apply", "apply_changes", is_flag=True, help="Apply the sync.")
@with_appcontext
def sync_capability_hierarchy_relationships(dry_run, apply_changes):
    """Backfill archimate_relationships for existing BusinessCapability parent links."""
    if dry_run == apply_changes:
        raise click.UsageError("choose exactly one of --dry-run or --apply")

    connection = db.engine.connect()
    # tenancy-ok: this is a one-time, cross-organisation backfill CLI (same
    # category as `project-capabilities`) -- it exists specifically to catch
    # up every org's pre-existing capabilities in one run, and every write it
    # makes is scoped downstream by `_sync_capability_hierarchy_relationship`,
    # which reads and writes organization_id from the row itself.
    rows = connection.execute(
        db.text(
            "SELECT id FROM business_capability "
            "WHERE archimate_element_id IS NOT NULL "
            "ORDER BY id"
        )
    ).fetchall()

    # tenancy-ok: deliberately global count for this cross-organisation
    # backfill CLI, same reasoning as the row read above.
    before = connection.execute(
        db.text(
            "SELECT count(*) FROM archimate_relationships "
            "WHERE derived_from = 'capability-hierarchy'"
        )
    ).scalar()

    if dry_run:
        # tenancy-ok: deliberately global count, reported in the dry-run
        # summary alongside the org-agnostic totals above.
        with_parent = connection.execute(
            db.text(
                "SELECT count(*) FROM business_capability "
                "WHERE archimate_element_id IS NOT NULL "
                "AND parent_capability_id IS NOT NULL"
            )
        ).scalar()
        click.echo(
            f"dry-run: {len(rows)} capabilities on the ArchiMate backbone, "
            f"{with_parent} carry a parent_capability_id, "
            f"{before} capability-hierarchy relationships exist today"
        )
        connection.close()
        return

    transaction = connection.begin()
    try:
        for (capability_id,) in rows:
            target = BusinessCapability.query.get(capability_id)
            if target is None:
                continue
            _sync_capability_hierarchy_relationship(connection, target)
        transaction.commit()
    except Exception:
        transaction.rollback()
        raise
    finally:
        connection.close()

    after_connection = db.engine.connect()
    # tenancy-ok: reporting the before/after total for a deliberately
    # cross-organisation backfill (see the marker above the initial read).
    after = after_connection.execute(
        db.text(
            "SELECT count(*) FROM archimate_relationships "
            "WHERE derived_from = 'capability-hierarchy'"
        )
    ).scalar()
    after_connection.close()
    click.echo(
        f"applied: {before} -> {after} capability-hierarchy relationships "
        f"({len(rows)} capabilities checked)"
    )


def init_app(app):
    app.cli.add_command(sync_capability_hierarchy_relationships)

"""flask backfill-meaning-tenancy -- assign organisation to Meaning rows.

``Meaning`` (app/models/motivation.py) gained TenantMixin before this
brief; existing rows predate that and carry organization_id=NULL. Each
row derives its organisation from its linked ArchiMateElement (itself
tenant-scoped) when one exists; a Meaning with no element link, or whose
element has no organisation, stays NULL rather than guessed.

    flask --app manage backfill-meaning-tenancy --dry-run
    flask --app manage backfill-meaning-tenancy
"""
import click
from flask.cli import with_appcontext
from sqlalchemy import text

from app import db


def _backfill(dry_run=False):
    conn = db.session.connection()
    result = conn.execute(text(
        """
        UPDATE meanings m
           SET organization_id = e.organization_id
          FROM archimate_elements e
         WHERE m.archimate_element_id = e.id
           AND m.organization_id IS NULL
           AND e.organization_id IS NOT NULL
        """
    ))
    derived = result.rowcount or 0

    orphans = conn.execute(
        text("SELECT count(*) FROM meanings WHERE organization_id IS NULL")
    ).scalar()

    if dry_run:
        db.session.rollback()
    else:
        db.session.commit()

    return derived, orphans


@click.command("backfill-meaning-tenancy")
@click.option("--dry-run", is_flag=True, help="Report what would change; change nothing.")
@with_appcontext
def backfill_meaning_tenancy(dry_run):
    """Backfill organization_id on meanings from their linked element."""
    derived, orphans = _backfill(dry_run=dry_run)
    verb = "would derive" if dry_run else "derived"
    click.echo(f"  {verb} organisation for {derived} meaning(s); {orphans} left NULL (no provenance).")


def init_app(app):
    app.cli.add_command(backfill_meaning_tenancy)

"""flask backfill-dedupe-tenancy -- assign organisation to duplicate-detection rows.

The five duplicate-detection tables gained TenantMixin on 2026-10-03 and
share a common backfill shape: the run table has a user_id, and child rows
(duplicate groups, consolidation list entries) take their organisation from
the parent run or application they reference.

    flask --app manage backfill-dedupe-tenancy --dry-run
    flask --app manage backfill-dedupe-tenancy
"""
import click
from flask.cli import with_appcontext

from app import db
from sqlalchemy import text


_DERIVABLE = {
    # UnifiedDetectionRun has user_id -- backfill from that user's
    # organisation, but only when the user belongs to exactly one org
    # (no org_roles in another org).  A genuine orphan (no user_id, or
    # user_id pointing at a deleted user) stays NULL.
    "unified_detection_runs": """
        UPDATE unified_detection_runs r
           SET organization_id = u.organization_id
          FROM users u
         WHERE r.user_id = u.id
           AND r.organization_id IS NULL
           AND u.organization_id IS NOT NULL
           AND NOT EXISTS (
                 SELECT 1 FROM org_roles o
                  WHERE o.user_id = u.id
                    AND o.organization_id != u.organization_id
               )
    """,
    # UnifiedDuplicateGroup takes its run's organisation.
    "unified_duplicate_groups": """
        UPDATE unified_duplicate_groups g
           SET organization_id = r.organization_id
          FROM unified_detection_runs r
         WHERE g.detection_run_id = r.id
           AND g.organization_id IS NULL
           AND r.organization_id IS NOT NULL
    """,
    # DuplicateGroup takes its run's organisation.
    "duplicate_groups": """
        UPDATE duplicate_groups g
           SET organization_id = r.organization_id
          FROM duplicate_detection_runs r
         WHERE g.detection_run_id = r.id
           AND g.organization_id IS NULL
           AND r.organization_id IS NOT NULL
    """,
    # ConsolidationListEntry takes its application's organisation.
    "consolidation_list_entries": """
        UPDATE consolidation_list_entries e
           SET organization_id = a.organization_id
          FROM application_components a
         WHERE e.application_id = a.id
           AND e.organization_id IS NULL
           AND a.organization_id IS NOT NULL
    """,
}

# Tables where a remaining NULL is expected (no user_id to resolve).
_NO_DERIVATION = {
    "duplicate_detection_runs",
}

# Fixed SQL for orphan-count queries (no string-built SQL — Bandit B608).
_ORPHAN_COUNT_SQL = {
    "duplicate_detection_runs": "SELECT count(*) FROM duplicate_detection_runs WHERE organization_id IS NULL",
    "duplicate_groups": "SELECT count(*) FROM duplicate_groups WHERE organization_id IS NULL",
    "unified_detection_runs": "SELECT count(*) FROM unified_detection_runs WHERE organization_id IS NULL",
    "unified_duplicate_groups": "SELECT count(*) FROM unified_duplicate_groups WHERE organization_id IS NULL",
    "consolidation_list_entries": "SELECT count(*) FROM consolidation_list_entries WHERE organization_id IS NULL",
}


def _backfill(org_id=None, dry_run=False):
    conn = db.session.connection()
    stats = {"derived": {}, "unresolved": {}, "skipped": []}

    for table, sql in _DERIVABLE.items():
        result = conn.execute(text(sql))
        if result.rowcount:
            verb, label = ("would derive", "-") if dry_run else ("derived", "+")
            click.echo(f"  {label} {table}: {verb} org for {result.rowcount} row(s)")
            stats["derived"][table] = result.rowcount

    for table in sorted(
        {**_DERIVABLE, **{t: None for t in _NO_DERIVATION}}.keys()
    ):
        orphans = conn.execute(
            text(_ORPHAN_COUNT_SQL[table])
        ).scalar()
        if orphans:
            verb, label = ("would leave", "!") if dry_run else ("left", "!")
            click.echo(f"  {label} {table}: {orphans} row(s) NULL (no provenance)")
            stats["unresolved"][table] = orphans

    if dry_run:
        db.session.rollback()
    else:
        db.session.commit()

    return stats


@click.command("backfill-dedupe-tenancy")
@click.option("--dry-run", is_flag=True, help="Report what would change; change nothing.")
@with_appcontext
def backfill_dedupe_tenancy(dry_run):
    """Backfill organization_id on duplicate-detection tables."""
    stats = _backfill(dry_run=dry_run)
    derived = sum(stats["derived"].values())
    unresolved = sum(stats["unresolved"].values())
    click.echo(
        f"  {'would' if dry_run else ''} backfilled {derived} row(s); "
        f"{unresolved} unresolved."
    )


def init_app(app):
    app.cli.add_command(backfill_dedupe_tenancy)
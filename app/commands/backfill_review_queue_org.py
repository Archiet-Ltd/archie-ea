"""Attribute pre-existing review_queue_items rows to an organisation.

``ReviewQueueItem`` gained ``TenantMixin``, so ``do_orm_execute`` now filters every
read by ``organization_id``. Rows written before that have it NULL and are
therefore invisible to every tenant --- live review queues would empty on deploy.

The attribution is determinable rather than guessed. Each item references
either the reviewed entity (item_type / item_id) or a user FK that carries an
organisation. Resolution order, tried in sequence for each NULL-org row:

1. *Reviewed item* --- join the polymorphic ``item_type`` / ``item_id``
   reference to the owning table and read its ``organization_id``:

   ==========================  ===========================
   item_type                   target table
   ==========================  ===========================
   ``capability_mapping``      ``application_components``
   ``process_classification``  ``application_components``
   ``process_mapping``         ``application_components``
   ``vendor_analysis``         ``application_components``
   ``taxonomy_validation``     ``application_components``
   ``archimate_generation``    ``application_components``
   ``archimate_element``       ``archimate_elements``
   ==========================  ===========================

   Every target table is a ``TenantMixin`` model with an ``organization_id``
   column. This step resolves pending, unassigned items --- precisely the
   items the review queue exists to show.

2. ``assigned_to_id`` → ``users.organization_id`` (the reviewer assigned to the
   item belongs to the organisation that owns it)

3. ``reviewed_by_id`` → ``users.organization_id`` (the person who reviewed it)

4. ``escalated_to_id`` → ``users.organization_id`` (the escalation target)

A type not listed above is reported by name with its count, never guessed.
``threshold_id`` points at ``confidence_thresholds``, which has no organisation
column, so it is not used.

Idempotent (only touches NULLs) and non-destructive. A row that cannot be
resolved is left NULL and reported --- inventing an owner would be worse than
leaving it unattributed.

    flask --app manage backfill-review-queue-org --dry-run
    flask --app manage backfill-review-queue-org
"""
import click
from flask.cli import with_appcontext


def init_app(app):
    app.cli.add_command(backfill_review_queue_org)


_ITEM_TYPE_TABLE = {
    "capability_mapping": "application_components",
    "process_classification": "application_components",
    "process_mapping": "application_components",
    "vendor_analysis": "application_components",
    "taxonomy_validation": "application_components",
    "archimate_generation": "application_components",
    "archimate_element": "archimate_elements",
}


@click.command("backfill-review-queue-org")
@click.option("--dry-run", is_flag=True, help="Report what would change, write nothing.")
@with_appcontext
def backfill_review_queue_org(dry_run):
    """Set organization_id on review_queue_items rows that have none."""
    from app.extensions import db

    total = db.session.execute(
        db.text("SELECT COUNT(*) FROM review_queue_items")
    ).scalar() or 0
    orphaned = db.session.execute(
        db.text("SELECT COUNT(*) FROM review_queue_items WHERE organization_id IS NULL")
    ).scalar() or 0

    if orphaned == 0:
        click.echo(f"review_queue_items: {total} row(s), none unattributed — nothing to do.")
        return

    # ---- count resolvable rows ----

    # Step 1: via reviewed item (item_type → target table → organization_id)
    r0 = 0
    r0_counts = {}
    r0_unknown = 0
    r0_unknown_types = {}

    for item_type, table_name in sorted(_ITEM_TYPE_TABLE.items()):
        count = db.session.execute(
            db.text(
                f"SELECT COUNT(*) FROM review_queue_items r "
                f"JOIN {table_name} t ON t.id = r.item_id "
                f"WHERE r.organization_id IS NULL "
                f"AND r.item_type = :item_type "
                f"AND t.organization_id IS NOT NULL"  # nosec B608 -- only fixed table and column names from a mapping in code are interpolated; values are bound parameters
            ),
            {"item_type": item_type},
        ).scalar() or 0
        r0_counts[item_type] = count
        r0 += count

    # Count NULL-org rows with unregistered item_type.
    # A tuple bound to `NOT IN :known` compiles fine under psycopg2 but is a
    # syntax error under psycopg 3 (which does not expand a single bound
    # parameter into a list). SQLAlchemy's expanding bind parameter compiles
    # to the right placeholder count for either driver.
    from sqlalchemy import bindparam

    unknown_types = db.session.execute(
        db.text(
            "SELECT item_type, COUNT(*) FROM review_queue_items "
            "WHERE organization_id IS NULL "
            "AND item_type NOT IN :known "
            "GROUP BY item_type"
        ).bindparams(bindparam("known", expanding=True)),
        {"known": list(_ITEM_TYPE_TABLE.keys())},
    ).fetchall()
    for row in unknown_types:
        r0_unknown_types[row[0]] = row[1]
        r0_unknown += row[1]

    # Step 2: via assigned_to_id (only rows not resolved by step 1)
    r1 = db.session.execute(
        db.text(
            "SELECT COUNT(*) FROM review_queue_items r "
            "JOIN users u ON u.id = r.assigned_to_id "
            "WHERE r.organization_id IS NULL AND u.organization_id IS NOT NULL"
        )
    ).scalar() or 0

    # Step 3: via reviewed_by_id (only rows still NULL)
    r2 = db.session.execute(
        db.text(
            "SELECT COUNT(*) FROM review_queue_items r "
            "JOIN users u ON u.id = r.reviewed_by_id "
            "WHERE r.organization_id IS NULL "
            "AND u.organization_id IS NOT NULL"
        )
    ).scalar() or 0

    # Step 4: via escalated_to_id (only rows still NULL)
    r3 = db.session.execute(
        db.text(
            "SELECT COUNT(*) FROM review_queue_items r "
            "JOIN users u ON u.id = r.escalated_to_id "
            "WHERE r.organization_id IS NULL "
            "AND u.organization_id IS NOT NULL"
        )
    ).scalar() or 0

    resolvable = r0 + r1 + r2 + r3
    unresolvable = orphaned - resolvable

    click.echo(f"review_queue_items: {total} row(s), {orphaned} unattributed.")
    click.echo(f"  resolvable via reviewed item:     {r0}")
    for item_type, count in sorted(r0_counts.items()):
        if count:
            click.echo(f"    └─ {item_type}: {count}")
    if r0_unknown:
        click.echo(
            f"  UNKNOWN item_types (reported, not guessed): {r0_unknown}"
        )
        for item_type, count in sorted(r0_unknown_types.items()):
            click.echo(f"    └─ {item_type}: {count}")
    click.echo(f"  resolvable via assigned_to_id:   {r1}")
    click.echo(f"  resolvable via reviewed_by_id:   {r2}")
    click.echo(f"  resolvable via escalated_to_id:  {r3}")
    if unresolvable:
        click.echo(
            f"  NOT resolvable (no reviewed item, user FKs, or org): {unresolvable} — left NULL"
        )

    if dry_run:
        click.echo("dry run — nothing written.")
        if r0_unknown:
            click.echo(
                "\nUnrecognised item_type values are reported above. "
                "Add each to the _ITEM_TYPE_TABLE mapping in "
                "app/commands/backfill_review_queue_org.py and re-run."
            )
        return

    updated = 0

    # Step 1: via reviewed item (item_type → target table → organization_id)
    for item_type, table_name in sorted(_ITEM_TYPE_TABLE.items()):
        if r0_counts.get(item_type, 0) == 0:
            continue
        result = db.session.execute(
            # tenancy-ok: one-time backfill, retirement 2026-12-31
            db.text(
                f"UPDATE review_queue_items r SET organization_id = t.organization_id "
                f"FROM {table_name} t "
                f"WHERE t.id = r.item_id "
                f"AND r.organization_id IS NULL "
                f"AND r.item_type = :item_type "
                f"AND t.organization_id IS NOT NULL"  # nosec B608 -- only fixed table and column names from a mapping in code are interpolated; values are bound parameters
            ),
            {"item_type": item_type},
        )
        updated += result.rowcount

    # Step 2: via assigned_to_id → users.organization_id
    result = db.session.execute(
        # tenancy-ok: one-time backfill, retirement 2026-12-31
        db.text(
            "UPDATE review_queue_items r SET organization_id = u.organization_id "
            "FROM users u "
            "WHERE u.id = r.assigned_to_id "
            "AND r.organization_id IS NULL "
            "AND u.organization_id IS NOT NULL"
        )
    )
    updated += result.rowcount

    # Step 3: via reviewed_by_id → users.organization_id (only rows still NULL)
    result = db.session.execute(
        # tenancy-ok: one-time backfill, retirement 2026-12-31
        db.text(
            "UPDATE review_queue_items r SET organization_id = u.organization_id "
            "FROM users u "
            "WHERE u.id = r.reviewed_by_id "
            "AND r.organization_id IS NULL "
            "AND u.organization_id IS NOT NULL"
        )
    )
    updated += result.rowcount

    # Step 4: via escalated_to_id → users.organization_id (only rows still NULL)
    result = db.session.execute(
        # tenancy-ok: one-time backfill, retirement 2026-12-31
        db.text(
            "UPDATE review_queue_items r SET organization_id = u.organization_id "
            "FROM users u "
            "WHERE u.id = r.escalated_to_id "
            "AND r.organization_id IS NULL "
            "AND u.organization_id IS NOT NULL"
        )
    )
    updated += result.rowcount

    db.session.commit()
    click.echo(f"attributed {updated} row(s).")

    if r0_unknown:
        click.echo(
            "\nUnrecognised item_type values are reported above. "
            "Add each to the _ITEM_TYPE_TABLE mapping in "
            "app/commands/backfill_review_queue_org.py and re-run."
        )
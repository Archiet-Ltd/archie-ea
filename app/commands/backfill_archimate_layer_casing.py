"""flask backfill-archimate-layer-casing — one casing for archimate_elements.layer.

`archimate_elements.layer` accumulated two casings, and Postgres `=` is
case-sensitive, so the column silently partitioned itself:

    [Application] 47   [application] 52
    [Strategy]   274   [strategy]      4
    [Motivation]   8   [motivation]   13
    [Technology]   1   [technology]   24
    [business]    11   <- no capitalised "Business" row ever existed

The code disagrees the same way — roughly 70 call sites spell the layer in
lower case and ~30 capitalise it. So the nine sites querying `layer="Business"`
returned nothing at all, and the thirteen querying `layer="strategy"` saw 4
rows out of 278.

`ArchiMateElement.layer` is now an `_ArchiMateLayerType` column
(app/models/models.py), which canonicalises both the values it writes and the
literals in `layer == "Business"` / `filter_by(layer="Strategy")` /
`layer.in_([...])`. That makes every *new* row canonical and every comparison
casing-agnostic, but it cannot touch rows that are already in the table: a
legacy "Strategy" row is still found only by a query that happens to be
compiled the same way — which it now always is, since both spellings compile to
`'strategy'`. So until this command has run, a legacy capitalised row matches
*nothing*, because the bind parameter is lower-cased while the stored value is
not.

This command closes that gap by rewriting the stored values to the canonical
lower case. It is the last of the three steps and must be run once per
database after deploying the model change.

Idempotent; safe to re-run — it only touches rows where `layer <> lower(layer)`.

    flask --app manage backfill-archimate-layer-casing --dry-run
    flask --app manage backfill-archimate-layer-casing
    flask --app manage backfill-archimate-layer-casing --org-id 3

`--org-id` is a filter, not a target. `archimate_elements` is tenant-scoped,
but this command uses raw SQL, which the ORM tenant filter in
app/middleware/tenant_isolation.py does not cover (it only rewrites ORM
SELECTs). Without the option an operator repairing one tenant would rewrite
every tenant's rows in the same statement, so the option exists to keep a
single-tenant repair single-tenant. Omitting it deliberately means "all
organisations", which is the normal one-off migration case.
"""

import click
from flask.cli import with_appcontext

from app import db
from app.models.constants import ArchiMateLayer

TABLE = "archimate_elements"

def _canonical_layer(layer):
    """Return the canonical stored layer value for a raw database value."""
    return ArchiMateLayer.normalize(layer)


def canonicalise_layer_rows(dry_run=False, org_id=None):
    """Rewrite non-canonical ``archimate_elements.layer`` values in place.

    Returns a report dict so the CLI wrapper and the tests read the same
    numbers::

        {"scanned": int, "would_update": int, "updated": int,
         "by_value": {"<stored value>": count, ...}}

    ``by_value`` is the *pre-change* distribution of the offending values, which
    is what an operator needs to sanity-check a dry run before committing.
    """
    from sqlalchemy import inspect, text

    insp = inspect(db.engine)
    if TABLE not in set(insp.get_table_names()):
        return {"scanned": 0, "would_update": 0, "updated": 0, "by_value": {}, "skipped": TABLE}

    scope = ""
    params = {}
    if org_id is not None:
        scope = " AND organization_id = :org"
        params["org"] = org_id

    conn = db.session.connection()

    scanned_sql = "SELECT count(*) FROM archimate_elements WHERE TRUE"
    rows_sql = (
        "SELECT id, layer FROM archimate_elements "
        "WHERE layer IS NOT NULL"
    )

    scanned = conn.execute(text(scanned_sql + scope), params).scalar() or 0

    rows = conn.execute(
        text(
            rows_sql + scope + " ORDER BY id"  # tenancy-ok: --org-id scopes this; a repair is deliberately cross-tenant by default
        ),
        params,
    ).fetchall()
    pending_ids = []
    by_value = {}
    canonical_by_id = {}
    for row_id, stored_layer in rows:
        canonical = _canonical_layer(stored_layer)
        if canonical != stored_layer:
            pending_ids.append(row_id)
            by_value[stored_layer] = by_value.get(stored_layer, 0) + 1
            canonical_by_id[row_id] = canonical
    pending = len(pending_ids)

    report = {
        "scanned": scanned,
        "would_update": pending if dry_run else 0,
        "updated": 0,
        "by_value": by_value,
    }
    if dry_run or not pending:
        # Nothing was written, so there is nothing to roll back.
        return report

    update_stmt = text(
        "UPDATE archimate_elements SET layer = :layer WHERE id = :id"  # tenancy-ok: --org-id scopes this; a repair is deliberately cross-tenant by default
    )
    updated = 0
    for row_id in pending_ids:
        result = conn.execute(update_stmt, {"id": row_id, "layer": canonical_by_id[row_id]})
        updated += result.rowcount or 0
    report["updated"] = updated
    db.session.commit()
    return report


@click.command("backfill-archimate-layer-casing")
@click.option("--dry-run", is_flag=True, help="Report what would change; change nothing.")
@click.option("--org-id", type=int, default=None, help="Only repair this organization's rows.")
@with_appcontext
def backfill_archimate_layer_casing(dry_run, org_id):
    """Canonicalise archimate_elements.layer to lower case."""
    report = canonicalise_layer_rows(dry_run=dry_run, org_id=org_id)

    if report.get("skipped"):
        click.echo(f"  - {report['skipped']}: table absent, nothing to do")
        return

    where = "all organizations" if org_id is None else f"organization {org_id}"
    click.echo(f"  scanned {report['scanned']} element(s) in {where}")

    if not report["by_value"]:
        click.echo("  no non-canonical layer values — nothing to do")
        return

    for value, count in sorted(report["by_value"].items()):
        click.echo(f"    {value!r} -> {_canonical_layer(value)!r}: {count} row(s)")

    if dry_run:
        click.echo(f"dry-run: would update {report['would_update']} row(s); no changes committed.")
        return

    click.echo(f"  + updated {report['updated']} row(s)")
    click.echo("backfill-archimate-layer-casing: done.")


def init_app(app):
    """Register the backfill-archimate-layer-casing CLI command."""
    app.cli.add_command(backfill_archimate_layer_casing)

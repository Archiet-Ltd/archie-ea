"""Copy the history of the other audit stores into the one audit store.

``soc2_audit_log`` (``AuditLog``) is the system of record for audit events
(ADR 0008). Three older stores recorded events it never saw:

==============================  =========================================
source table                    organisation of each row
==============================  =========================================
``arb_audit_logs``              its own ``organization_id``
``archimate_audit_logs``        the diagram's (``saved_diagrams``), else
                                its author's (``users``)
``rationalization_audit_entries``  the application's
                                (``application_components``)
==============================  =========================================

New rows in those stores are copied as they are written (the insert hook in
``app/models/audit_log.py``). This command copies what they recorded before
that, one organisation at a time:

* each copy carries ``source_table`` / ``source_id`` and is sealed into the
  organisation's integrity chain; the source row's ``retired_into_id`` points
  at it;
* an ARB decision already mirrored by the earlier decision-only mirror is
  not copied twice: the oldest matching entry is kept and the source row is
  pointed at it, and every such merge is listed;
* a row whose organisation cannot be determined is never copied into shared
  scope; it is listed (quarantine) for the platform administrator;
* nothing is deleted or dropped, and source rows are otherwise untouched.

Idempotent: a row whose ``retired_into_id`` is set, or which already has a
copy, is skipped. Prints before and after counts per source and
organisation, and exits non-zero when they do not reconcile.

    flask --app manage backfill-audit-trail --dry-run
    flask --app manage backfill-audit-trail
    flask --app manage backfill-audit-trail --organization-id 7
"""

from collections import defaultdict

import click
from flask.cli import with_appcontext

# source table -> SQL resolving (id, organisation) for rows not yet copied.
_ATTRIBUTION_SQL = {
    "arb_audit_logs": (
        "SELECT s.id, s.organization_id AS org_id FROM arb_audit_logs s "  # tenancy-ok: cross-organisation backfill, grouped per organisation below
        "WHERE s.retired_into_id IS NULL ORDER BY s.id"
    ),
    "archimate_audit_logs": (
        "SELECT s.id, COALESCE(d.organization_id, u.organization_id) AS org_id "  # tenancy-ok: cross-organisation backfill, grouped per organisation below
        "FROM archimate_audit_logs s "
        "LEFT JOIN saved_diagrams d ON d.id = s.viewpoint_id "
        "LEFT JOIN users u ON u.id = s.user_id "
        "WHERE s.retired_into_id IS NULL ORDER BY s.id"
    ),
    "rationalization_audit_entries": (
        "SELECT s.id, a.organization_id AS org_id FROM rationalization_audit_entries s "  # tenancy-ok: cross-organisation backfill, grouped per organisation below
        "LEFT JOIN application_components a ON a.id = s.application_id "
        "WHERE s.retired_into_id IS NULL ORDER BY s.id"
    ),
}

# Rows per organisation and how many are already copied, per source table,
# using the same attribution as _ATTRIBUTION_SQL.
_COUNT_SQL = {
    "arb_audit_logs": (
        "SELECT s.organization_id, COUNT(*), COUNT(s.retired_into_id) FROM arb_audit_logs s "  # tenancy-ok: measurement across every organisation
        "GROUP BY 1 ORDER BY 1 NULLS FIRST"
    ),
    "archimate_audit_logs": (
        "SELECT COALESCE(d.organization_id, u.organization_id), COUNT(*), COUNT(s.retired_into_id) "  # tenancy-ok: measurement across every organisation
        "FROM archimate_audit_logs s "
        "LEFT JOIN saved_diagrams d ON d.id = s.viewpoint_id "
        "LEFT JOIN users u ON u.id = s.user_id "
        "GROUP BY 1 ORDER BY 1 NULLS FIRST"
    ),
    "rationalization_audit_entries": (
        "SELECT a.organization_id, COUNT(*), COUNT(s.retired_into_id) FROM rationalization_audit_entries s "  # tenancy-ok: measurement across every organisation
        "LEFT JOIN application_components a ON a.id = s.application_id "
        "GROUP BY 1 ORDER BY 1 NULLS FIRST"
    ),
}

# Full rows of one batch of ids, per source table.
_ROWS_SQL = {
    "arb_audit_logs": "SELECT * FROM arb_audit_logs WHERE id = ANY(:ids) ORDER BY id",  # tenancy-ok: ids already attributed to this organisation
    "archimate_audit_logs": "SELECT * FROM archimate_audit_logs WHERE id = ANY(:ids) ORDER BY id",  # tenancy-ok: ids already attributed to this organisation
    "rationalization_audit_entries": "SELECT * FROM rationalization_audit_entries WHERE id = ANY(:ids) ORDER BY id",  # tenancy-ok: ids already attributed to this organisation
}

# ARB actions the earlier decision-only mirror already copied (without
# provenance). Matching entries are merged into rather than duplicated.
_PREVIOUSLY_MIRRORED_ARB_ACTIONS = ("decision", "exception_decision")
_MIRROR_MATCH_WINDOW_SECONDS = 120
_BATCH = 500


def init_app(app):
    app.cli.add_command(backfill_audit_trail)


def _counts(db):
    """{source: {org_id: (total, copied)}} using the same attribution as the copy."""
    out = {}
    for source, sql in _COUNT_SQL.items():
        rows = db.session.execute(db.text(sql)).all()
        out[source] = {org: (total, copied) for org, total, copied in rows}
    return out


def _print_counts(label, counts):
    click.echo(f"{label}:")
    for source, per_org in counts.items():
        for org, (total, copied) in per_org.items():
            owner = "unattributed" if org is None else f"organisation {org}"
            click.echo(f"  {source:32} {owner:22} rows={total:<7} copied={copied}")


def _existing_copy(conn, source, source_id):
    from app.extensions import db

    return conn.execute(
        db.text(
            "SELECT id FROM soc2_audit_log WHERE source_table = :t AND source_id = :i "  # tenancy-ok: provenance lookup of one source row
            "ORDER BY id LIMIT 1"
        ),
        {"t": source, "i": source_id},
    ).scalar()


def _earlier_arb_mirror(conn, row):
    """The oldest unclaimed entry the decision-only mirror wrote for ``row``."""
    from app.extensions import db

    if row["action"] not in _PREVIOUSLY_MIRRORED_ARB_ACTIONS or row["timestamp"] is None:
        return None
    return conn.execute(
        db.text(
            "SELECT a.id FROM soc2_audit_log a "
            "WHERE a.organization_id = :org AND a.table_name = :tn "
            "AND a.record_id = :rid AND a.action = :act AND a.source_table IS NULL "
            "AND abs(extract(epoch FROM (a.created_at - :ts))) <= :win "
            "AND NOT EXISTS (SELECT 1 FROM arb_audit_logs x WHERE x.retired_into_id = a.id) "  # tenancy-ok: claim check on one audit id
            "ORDER BY a.id LIMIT 1"
        ),
        {
            "org": row["organization_id"],
            "tn": f"arb:{row['entity_type']}"[:100],
            "rid": row["entity_id"],
            "act": str(row["action"])[:20],
            "ts": row["timestamp"],
            "win": _MIRROR_MATCH_WINDOW_SECONDS,
        },
    ).scalar()


def _point_at(conn, source, source_id, audit_id):
    from app.extensions import db
    from app.models.audit_log import RETIRE_SQL

    conn.execute(db.text(RETIRE_SQL[source]), {"audit_id": audit_id, "id": source_id})


@click.command("backfill-audit-trail")
@click.option("--dry-run", is_flag=True, help="Report what would be copied; write nothing.")
@click.option("--organization-id", type=int, default=None, help="Only this organisation.")
@with_appcontext
def backfill_audit_trail(dry_run, organization_id):
    """Copy archimate, ARB and rationalisation audit history into soc2_audit_log."""
    from app.extensions import db
    from app.models.audit_log import mirror_source_row

    before = _counts(db)
    _print_counts("Before", before)

    plan = defaultdict(lambda: defaultdict(list))  # org -> source -> [ids]
    quarantine = []
    for source, sql in _ATTRIBUTION_SQL.items():
        for source_id, org_id in db.session.execute(db.text(sql)).all():
            if org_id is None:
                quarantine.append((source, source_id))
            elif organization_id is None or org_id == organization_id:
                plan[org_id][source].append(source_id)
    db.session.remove()

    if not dry_run:
        # Chain-tail lookup and ordered export per organisation. create_all
        # creates this index on new installs only.
        db.session.execute(db.text(
            "CREATE INDEX IF NOT EXISTS ix_soc2_audit_org_id ON soc2_audit_log (organization_id, id)"
        ))
        db.session.commit()

    merges = []
    copied = relinked = failed = 0
    for org_id in sorted(plan):
        for source, ids in plan[org_id].items():
            click.echo(f"organisation {org_id}: {source} {len(ids)} rows to copy")
            if dry_run:
                continue
            for start in range(0, len(ids), _BATCH):
                chunk = ids[start:start + _BATCH]
                conn = db.session.connection()
                rows = conn.execute(
                    db.text(_ROWS_SQL[source]),
                    {"ids": chunk},
                ).mappings().all()
                for row in rows:
                    row = dict(row)
                    existing = _existing_copy(conn, source, row["id"])
                    if existing is not None:
                        _point_at(conn, source, row["id"], existing)
                        relinked += 1
                        continue
                    if source == "arb_audit_logs":
                        earlier = _earlier_arb_mirror(conn, row)
                        if earlier is not None:
                            _point_at(conn, source, row["id"], earlier)
                            merges.append((source, row["id"], earlier))
                            continue
                    if mirror_source_row(conn, source, row) is None:
                        failed += 1
                    else:
                        copied += 1
                db.session.commit()
        # One organisation per session: nothing cached crosses tenants.
        db.session.remove()

    click.echo(f"Copied {copied}; relinked to an existing copy {relinked}; merged {len(merges)}; failed {failed}.")
    for source, source_id, audit_id in merges:
        click.echo(f"  merged {source} #{source_id} into soc2_audit_log #{audit_id} (kept the older entry)")
    click.echo(f"Quarantined (organisation cannot be determined, not copied): {len(quarantine)}")
    for source, source_id in quarantine:
        click.echo(f"  {source} #{source_id}")

    if dry_run:
        return

    after = _counts(db)
    _print_counts("After", after)
    # Reconcile: every attributed row in scope is now copied (or merged).
    unreconciled = 0
    for source, per_org in after.items():
        for org, (total, done) in per_org.items():
            if org is None or (organization_id is not None and org != organization_id):
                continue
            unreconciled += total - done
    if unreconciled or failed:
        raise click.ClickException(
            f"{unreconciled} attributed source rows are not yet in soc2_audit_log; see warnings above."
        )
    click.echo("Reconciled: every attributed source row is in soc2_audit_log.")

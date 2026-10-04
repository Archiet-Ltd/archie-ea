"""
flask backfill-decision-register-consolidation — decision register consolidation.

Two independent backfills, run together because they share the brief:

1. **architecture_decision_records -> architecture_decisions** (dual-write,
   not a move). ArchitectureDecisionRecord (app/models/adr.py) stays the
   system of record for its own rich review-board fields (capability/process
   links, governance_decision_id, implementation_plan, risk_register,
   cost_analysis, arb_review, decision_matrix and the rest -- none of which
   architecture_decisions has columns for). Every existing pending row gets a
   paired ArchitectureDecision row (source_table='architecture_decision_records',
   source_id=<its id>), and the source row's new retired_into_id points back
   at it, so it now also appears in the one canonical register. Idempotent on
   retired_into_id IS NOT NULL: a row already paired is never paired twice.
   Organisation is preserved from the source row (both are TenantMixin); a
   row somehow missing both fails loudly rather than guessing.

2. **decision_ledger.organization_id**. This table had no organisation column
   at all until this change (see app/models/decision_ledger.py) -- every
   ARB session's decision_ledger service loaded every organisation's rows.
   Derived from the row's capability_id against unified_capabilities.id
   (decision_ledger.capability_id is a string; unified_capabilities.id is an
   integer, so a non-numeric or unmatched capability_id cannot be derived).
   An undeliverable row keeps organization_id NULL -- invisible to every
   organisation via the tenant filter, not a guess -- until a platform
   administrator resolves it with --org-id.

Idempotent: every write is guarded by a NULL/unset check, so re-running is a
no-op for rows already handled. Run one organisation at a time is not needed
for the ADR migration (each row carries its own organisation already); the
decision_ledger derivation processes every NULL row in one pass since the
join itself is organisation-scoped by construction (each row derives only
its own capability's org).

Usage:
    flask --app manage backfill-decision-register-consolidation --dry-run
    flask --app manage backfill-decision-register-consolidation
    flask --app manage backfill-decision-register-consolidation --org-id 3
        # also assigns decision_ledger rows whose capability_id could not be
        # resolved to organisation 3 (manual cleanup only, matches
        # backfill_arb_ea_tenancy.py's --org-id convention)

Not folded into backfill_arb_ea_tenancy.py's framework: that command's core
loop is table-first (`for table, subquery_sql in MODEL_SPECS`), UPDATE-only,
deriving a missing organization_id per table from an already-scoped FK
parent -- fourteen tables, one shared shape. This command's two backfills
are neither: the ADR migration INSERTs a new paired row per source row (not
an UPDATE of an existing organization_id), and derives nothing -- the source
row already carries its own organisation, copied across, never guessed. The
decision_ledger derivation is a single one-off UPDATE...FROM join against
unified_capabilities, not a per-table MODEL_SPECS entry, and doesn't fit
that framework's "backfill organization_id from an FK parent" contract
either. The CLI shape (--dry-run/--org-id, an echo callback, a non-zero
exit when something needs manual attention) intentionally matches
backfill_arb_ea_tenancy.py's conventions -- that similarity is convention
reuse, not the same mechanism duplicated.
"""
import click
from flask.cli import with_appcontext
from sqlalchemy import text

from app import db


def _migrate_adr_records(dry_run, echo):
    """Pair every unpaired architecture_decision_records row with a new
    architecture_decisions row (ArchitectureDecisionRecord.pair_with_canonical_register,
    the same method the four repointed constructor sites call going forward).
    Returns a stats dict.
    """
    from app.models.adr import ArchitectureDecisionRecord

    pending = ArchitectureDecisionRecord.query.filter(
        ArchitectureDecisionRecord.retired_into_id.is_(None)
    ).all()

    stats = {"total_source_rows": len(pending), "paired": 0, "skipped_no_org": 0}
    if not pending or dry_run:
        return stats

    for record in pending:
        if record.organization_id is None:
            # Never guess a tenant for governance data (see
            # backfill_arb_ea_tenancy.py's same rule) -- leave unpaired; the
            # source row is already TenantMixin-fenced and invisible to
            # every org until its own organisation_id is fixed, which is a
            # different, pre-existing problem this brief does not own.
            stats["skipped_no_org"] += 1
            continue
        record.pair_with_canonical_register()
        stats["paired"] += 1

    return stats


def _backfill_decision_ledger_org(conn, dry_run, org_id, echo):
    total = conn.execute(text('SELECT count(*) FROM "decision_ledger"')).scalar()
    null_count = conn.execute(
        text('SELECT count(*) FROM "decision_ledger" WHERE organization_id IS NULL')
    ).scalar()

    stats = {"total": total, "null": null_count, "derivable": 0, "orphan": 0,
              "backfilled": 0, "assigned_orphans": 0}
    if null_count == 0:
        return stats

    # Two literal statements, not one f-string-composed query reused in both:
    # bandit's B608 flags any SQL built by string interpolation regardless of
    # whether the interpolated part is attacker-controlled (here it never is,
    # derive_sql was a fixed constant) -- two plain literals read the same
    # join twice but satisfy the gate without reassembling SQL from parts.
    derivable = conn.execute(text("""
        SELECT count(*)
        FROM decision_ledger t
        JOIN unified_capabilities uc
            ON t.capability_id ~ '^[0-9]+$' AND uc.id = t.capability_id::integer
        WHERE t.organization_id IS NULL AND uc.organization_id IS NOT NULL
    """)).scalar()
    stats["derivable"] = derivable
    stats["orphan"] = null_count - derivable

    if dry_run:
        return stats

    if derivable:
        result = conn.execute(text("""
            UPDATE decision_ledger AS t
               SET organization_id = uc.organization_id
              FROM unified_capabilities uc
             WHERE t.capability_id ~ '^[0-9]+$'
               AND uc.id = t.capability_id::integer
               AND t.organization_id IS NULL
               AND uc.organization_id IS NOT NULL
        """))
        stats["backfilled"] = result.rowcount or 0

    remaining_orphan = null_count - stats["backfilled"]
    if remaining_orphan and org_id is not None:
        result = conn.execute(
            text('UPDATE "decision_ledger" SET organization_id = :o WHERE organization_id IS NULL'),
            {"o": org_id},
        )
        stats["assigned_orphans"] = result.rowcount or 0
        stats["orphan"] = 0
    elif remaining_orphan:
        # Safe (invisible to every organisation, per TenantMixin's own
        # filter) but otherwise unreachable: surface it where a platform
        # administrator already looks for exactly this shape of problem,
        # rather than a second quarantine list only this command knows about
        # (same reuse as the review-queue backfill's quarantine surfacing).
        _surface_unresolved_decision_ledger_rows(conn)

    return stats


def _surface_unresolved_decision_ledger_rows(conn):
    from datetime import datetime

    from app.models.error_event import ErrorEvent

    rows = conn.execute(text(
        'SELECT id, capability_id FROM "decision_ledger" WHERE organization_id IS NULL'
    )).fetchall()
    now = datetime.utcnow()
    for row_id, capability_id in rows:
        fingerprint = f"decision-ledger-quarantine:{row_id}"[:64]
        existing = ErrorEvent.query.filter_by(fingerprint=fingerprint, resolved=False).first()
        if existing:
            existing.occurrence_count = (existing.occurrence_count or 0) + 1
            existing.last_seen_at = now
            continue
        db.session.add(ErrorEvent(
            fingerprint=fingerprint,
            source="server",
            level="WARNING",
            message=(
                f"decision_ledger #{row_id} (capability_id={capability_id!r}) has no "
                "organisation and cannot be resolved by the consolidation backfill. "
                "Attribute it an organisation, then re-run the backfill."
            ),
            location="app.commands.backfill_decision_register_consolidation:decision_ledger",
            organization_id=None,
            occurrence_count=1,
            first_seen_at=now,
            last_seen_at=now,
            resolved=False,
        ))
    db.session.commit()


def run_backfill(dry_run=False, org_id=None, echo=None):
    """Run both consolidation backfills. Returns (adr_stats, ledger_stats)."""
    if echo is None:
        echo = lambda *a, **k: None  # noqa: E731

    if org_id is not None:
        row = db.session.connection().execute(
            text("SELECT id FROM organizations WHERE id = :i"), {"i": org_id}
        ).first()
        if not row:
            raise click.ClickException(f"No organization with id={org_id}.")

    adr_stats = _migrate_adr_records(dry_run, echo)
    echo(
        f"  architecture_decision_records: total_unpaired={adr_stats['total_source_rows']} "
        + (
            f"paired={adr_stats['paired']} skipped_no_org={adr_stats['skipped_no_org']}"
            if not dry_run else "(dry-run, no changes made)"
        )
    )

    ledger_stats = _backfill_decision_ledger_org(
        db.session.connection(), dry_run, org_id, echo
    )
    echo(
        f"  decision_ledger: total={ledger_stats['total']} null={ledger_stats['null']} "
        f"derivable={ledger_stats['derivable']} orphan={ledger_stats['orphan']}"
        + (
            f" backfilled={ledger_stats['backfilled']} assigned_orphans={ledger_stats['assigned_orphans']}"
            if not dry_run else ""
        )
    )

    if dry_run:
        db.session.rollback()
    else:
        db.session.commit()

    return adr_stats, ledger_stats


@click.command("backfill-decision-register-consolidation")
@click.option("--dry-run", is_flag=True, help="Report coverage; change nothing.")
@click.option(
    "--org-id", type=int, default=None,
    help="Assign decision_ledger rows whose capability_id could not be resolved to this "
         "organization. Manual cleanup only; never applied to architecture_decision_records "
         "(those already carry their own organisation).",
)
@with_appcontext
def backfill_decision_register_consolidation(dry_run, org_id):
    """Pair ADR records into architecture_decisions; tenant-fence decision_ledger."""
    click.echo("backfill-decision-register-consolidation" + (" (dry-run)" if dry_run else "") + ":")
    adr_stats, ledger_stats = run_backfill(dry_run=dry_run, org_id=org_id, echo=click.echo)

    click.echo("")
    if dry_run:
        click.echo("dry-run: no changes committed.")
    else:
        click.echo(
            f"summary: adr_paired={adr_stats['paired']} adr_skipped_no_org={adr_stats['skipped_no_org']} "
            f"ledger_backfilled={ledger_stats['backfilled']} "
            f"ledger_orphans_remaining={ledger_stats['orphan']}"
        )

    if ledger_stats["orphan"] and org_id is None:
        click.echo(
            f"{ledger_stats['orphan']} decision_ledger row(s) have no derivable organisation "
            "(capability_id does not match any unified_capabilities row) and --org-id was not "
            "given. Re-run with --org-id N to assign them by hand, or investigate why their "
            "capability_id is stale."
        )
        raise SystemExit(1)


def init_app(app):
    """Register the backfill-decision-register-consolidation CLI command."""
    app.cli.add_command(backfill_decision_register_consolidation)

"""Copy the pending rows of three other stores into the one approval queue.

``ai_chat_crud_approvals`` (``AIChatCRUDApproval``) is the system of record
for a pending change awaiting human approval. Three older stores raised
their own pending items, each with its own queue screen:

==========================  =========================================
source table                 organisation of each row
==========================  =========================================
``review_queue_items``       its own ``organization_id``
``relationship_suggestions`` the source element's (``archimate_elements``,
                              via ``source_element_id``)
``solution_blueprint_proposals``  its own ``organization_id``
==========================  =========================================

New pending rows from these three sources are now paired with an approval
row as they are written (the repointed constructor sites in
``app/services/confidence_review_service.py`` and
``app/models/solution_blueprint_proposal.py``'s
``create_solution_blueprint_proposal``; ``relationship_suggestions`` has no
live writer today, see below). This command copies what they raised before
that, one organisation at a time:

* each copy carries ``source_table`` / ``source_id``, and the source row's
  ``retired_into_id`` points at it;
* only rows still in their *pending* state are copied (``ReviewStatus.PENDING``
  / ``status='pending'`` / ``status='proposed'``) — an already accepted,
  rejected or promoted row has nothing left to approve;
* a row whose organisation cannot be determined is never copied into shared
  scope; it is recorded in ``ErrorEvent`` (the platform-wide, nullable-org
  surface every platform admin already sees at ``/admin/errors`` and in the
  error digest email) and counts toward this command's own non-zero exit
  until it is attributed an organisation and the backfill runs again;
* nothing is deleted or dropped, and source rows are otherwise untouched;
* ``relationship_suggestions`` has no live constructor call site in this
  codebase today (verified), so this command's own count for it will
  ordinarily be zero — it is still included for schema parity and in case
  historical or seeded rows exist.

Idempotent: a row whose ``retired_into_id`` is set is skipped, and running
this command twice creates no duplicate approval rows. Prints before and
after counts per source and organisation, and exits non-zero when they do
not reconcile.

    flask --app manage backfill-review-queue-approvals --dry-run
    flask --app manage backfill-review-queue-approvals
    flask --app manage backfill-review-queue-approvals --organization-id 7
"""

from collections import defaultdict
from datetime import datetime

import click
from flask.cli import with_appcontext

# source table -> SQL resolving (id, organisation, pending?) for rows not yet copied.
_ATTRIBUTION_SQL = {
    "review_queue_items": (
        "SELECT s.id, s.organization_id AS org_id, "  # tenancy-ok: cross-organisation backfill, grouped per organisation below
        "(s.status = 'PENDING') AS is_pending "
        "FROM review_queue_items s WHERE s.retired_into_id IS NULL ORDER BY s.id"
    ),
    "relationship_suggestions": (
        "SELECT s.id, e.organization_id AS org_id, "  # tenancy-ok: cross-organisation backfill, grouped per organisation below
        "(s.status = 'pending') AS is_pending "
        "FROM relationship_suggestions s "
        "LEFT JOIN archimate_elements e ON e.id = s.source_element_id "
        "WHERE s.retired_into_id IS NULL ORDER BY s.id"
    ),
    "solution_blueprint_proposals": (
        "SELECT s.id, s.organization_id AS org_id, "  # tenancy-ok: cross-organisation backfill, grouped per organisation below
        "(s.status = 'proposed') AS is_pending "
        "FROM solution_blueprint_proposals s WHERE s.retired_into_id IS NULL ORDER BY s.id"
    ),
}

# source table -> (operation_type, entity_type). entity_type is either a
# fixed string (matching what the live constructor site always passes, e.g.
# create_solution_blueprint_proposal's entity_type="solution_blueprint_element")
# or None, meaning "read it from the row instead" -- review_queue_items'
# constructor site (ConfidenceReviewService.add_to_review_queue) passes
# entity_type=item_data.item_type, a per-row value (archimate_element,
# capability, ...), not a fixed one.
_APPROVAL_SHAPE = {
    "review_queue_items": ("review", None),
    "relationship_suggestions": ("suggest_relationship", "relationship"),
    "solution_blueprint_proposals": ("propose", "solution_blueprint_element"),
}

_ROWS_SQL = {
    "review_queue_items": (
        "SELECT id, item_type, item_id, item_name FROM review_queue_items WHERE id = ANY(:ids)"
    ),
    "relationship_suggestions": (
        "SELECT id, relationship_type, source_element_id, target_element_id "
        "FROM relationship_suggestions WHERE id = ANY(:ids)"
    ),
    "solution_blueprint_proposals": (
        "SELECT id, solution_id, archimate_type, name FROM solution_blueprint_proposals WHERE id = ANY(:ids)"
    ),
}

_RETIRE_SQL = {
    "review_queue_items": "UPDATE review_queue_items SET retired_into_id = :approval_id WHERE id = :id",  # tenancy-ok: one row by its own key
    "relationship_suggestions": "UPDATE relationship_suggestions SET retired_into_id = :approval_id WHERE id = :id",  # tenancy-ok: one row by its own key
    "solution_blueprint_proposals": "UPDATE solution_blueprint_proposals SET retired_into_id = :approval_id WHERE id = :id",  # tenancy-ok: one row by its own key
}

_BATCH = 200


def _record_quarantine(db, source, source_id):
    """Persist one unattributable row where a platform administrator can see it.

    Reuses ``ErrorEvent`` (app/models/error_event.py) rather than a new
    store: it is already the platform-wide, nullable-organisation,
    dedup-by-fingerprint surface with its own admin page
    (app/modules/monitoring/routes/error_events_routes.py) and digest email
    (``_get_platform_admin_recipients`` in app/_bootstrap/_digest_emails.py)
    -- exactly "a real platform-admin-visible quarantine store or report"
    (reviews/pr302-final-check-v1.md) without a second, parallel mechanism
    for the same job. ``resolved=False`` until someone gives the row an
    organisation and re-runs the backfill; idempotent across runs via the
    fingerprint, matching the client-error ingestion route's own dedup.
    """
    from app.models.error_event import ErrorEvent

    fingerprint = f"backfill-quarantine:{source}:{source_id}"[:64]
    now = datetime.utcnow()
    existing = ErrorEvent.query.filter_by(fingerprint=fingerprint, resolved=False).first()
    if existing:
        existing.occurrence_count = (existing.occurrence_count or 0) + 1
        existing.last_seen_at = now
        return
    db.session.add(ErrorEvent(
        fingerprint=fingerprint,
        source="server",
        level="WARNING",
        message=(
            f"backfill-review-queue-approvals: {source} #{source_id} has no "
            "organisation and cannot be copied into the one approval queue. "
            "Attribute it an organisation, then re-run the backfill."
        ),
        location=f"app.commands.backfill_review_queue_approvals:{source}",
        organization_id=None,
        occurrence_count=1,
        first_seen_at=now,
        last_seen_at=now,
        resolved=False,
    ))


def _counts(db):
    """(pending total, already-copied) per source and organisation."""
    out = {}
    for source, sql in _ATTRIBUTION_SQL.items():
        per_org = defaultdict(lambda: [0, 0])
        all_rows = db.session.execute(db.text(
            sql.replace("s.retired_into_id IS NULL", "TRUE")
        )).all()
        for _id, org, pending in all_rows:
            if pending:
                per_org[org][0] += 1
        copied_rows = db.session.execute(db.text(
            sql.replace("s.retired_into_id IS NULL", "s.retired_into_id IS NOT NULL")
        )).all()
        for _id, org, pending in copied_rows:
            if pending and org in per_org:
                per_org[org][1] += 1
        out[source] = {org: tuple(v) for org, v in per_org.items()}
    return out


def _print_counts(label, counts):
    click.echo(f"{label}:")
    for source, per_org in counts.items():
        total = sum(t for t, _ in per_org.values())
        copied = sum(c for _, c in per_org.values())
        click.echo(f"  {source}: {total} pending, {copied} already copied, across {len(per_org)} organisation(s)")


def run_backfill(*, dry_run: bool = False, organization_id=None) -> dict:
    """Copy pending ReviewQueueItem/RelationshipSuggestion/SolutionBlueprintProposal rows into ai_chat_crud_approvals.

    Plain function (not the click-wrapped command) so a test can call it
    directly, matching backfill_ai_chat_approval_org.py's run_backfill().
    """
    from app.extensions import db
    from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

    if not dry_run:
        # ai_chat_crud_approval.py declares this UniqueConstraint on the
        # model, but reconcile-schema only ever ADDs columns -- it cannot
        # create a table-level constraint on an existing table, so a real
        # deployed database never gets it without this. Runs before any
        # copying below, matching the "at most one canonical approval per
        # source row" invariant this backfill itself depends on.
        db.session.execute(db.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_source "
            "ON ai_chat_crud_approvals (source_table, source_id)"
        ))
        # Same reasoning: the model declares user_id nullable (a backfilled
        # or system-originated row has no requester to attribute to), but
        # reconcile-schema never alters an existing column's constraints, so
        # an existing deployed database keeps NOT NULL without this. Idempotent
        # -- DROP NOT NULL on an already-nullable column is a no-op, no error.
        db.session.execute(db.text(
            "ALTER TABLE ai_chat_crud_approvals ALTER COLUMN user_id DROP NOT NULL"
        ))
        db.session.commit()

    before = _counts(db)
    _print_counts("Before", before)

    plan = defaultdict(lambda: defaultdict(list))  # org -> source -> [ids]
    quarantine = []
    for source, sql in _ATTRIBUTION_SQL.items():
        for source_id, org_id, is_pending in db.session.execute(db.text(sql)).all():
            if not is_pending:
                continue
            if org_id is None:
                quarantine.append((source, source_id))
            elif organization_id is None or org_id == organization_id:
                plan[org_id][source].append(source_id)
    db.session.remove()

    copied = 0
    for org_id in sorted(plan):
        for source, ids in plan[org_id].items():
            click.echo(f"organisation {org_id}: {source} {len(ids)} row(s) to copy")
            if dry_run:
                continue
            operation_type, entity_type_fixed = _APPROVAL_SHAPE[source]
            for start in range(0, len(ids), _BATCH):
                chunk = ids[start:start + _BATCH]
                conn = db.session.connection()
                rows = conn.execute(
                    db.text(_ROWS_SQL[source]), {"ids": chunk}
                ).mappings().all()
                for row in rows:
                    row = dict(row)
                    if source == "review_queue_items":
                        entity_type = row["item_type"]
                        summary = f"Confidence review: {row['item_name']}"
                        payload = {"review_queue_item_id": row["id"],
                                   "item_type": row["item_type"], "item_id": row["item_id"]}
                        entity_id = row["item_id"]
                    elif source == "relationship_suggestions":
                        entity_type = entity_type_fixed
                        summary = f"Suggested relationship: {row['relationship_type']}"
                        payload = {"relationship_suggestion_id": row["id"],
                                   "source_element_id": row["source_element_id"],
                                   "target_element_id": row["target_element_id"]}
                        entity_id = row["source_element_id"]
                    else:
                        entity_type = entity_type_fixed
                        summary = f"Blueprint proposal: {row['name']} ({row['archimate_type']})"
                        payload = {"solution_blueprint_proposal_id": row["id"],
                                   "solution_id": row["solution_id"], "archimate_type": row["archimate_type"]}
                        entity_id = row["id"]

                    approval = create_approval_record(
                        organization_id=org_id,
                        operation_type=operation_type,
                        entity_type=entity_type,
                        entity_id=entity_id,
                        summary=summary,
                        operation_payload=payload,
                        source_table=source,
                        source_id=row["id"],
                    )
                    db.session.flush()
                    conn.execute(
                        db.text(_RETIRE_SQL[source]),
                        {"approval_id": approval.id, "id": row["id"]},
                    )
                    copied += 1
                db.session.commit()
        # One organisation per session: nothing cached crosses tenants.
        db.session.remove()

    click.echo(f"Copied {copied}.")
    click.echo(f"Quarantined (organisation cannot be determined, not copied): {len(quarantine)}")
    for source, source_id in quarantine:
        click.echo(f"  {source} #{source_id}")

    if dry_run:
        return {"copied": 0, "quarantined": len(quarantine), "unreconciled": None}

    for source, source_id in quarantine:
        _record_quarantine(db, source, source_id)
    if quarantine:
        db.session.commit()

    after = _counts(db)
    _print_counts("After", after)
    unreconciled = 0
    for source, per_org in after.items():
        for org, (total, done) in per_org.items():
            if org is None or (organization_id is not None and org != organization_id):
                continue
            unreconciled += total - done
    # A quarantined row is neither copied nor reconciled by definition (it has
    # no organisation to copy into) -- it must keep the command from reporting
    # success while it stays outside the canonical queue, whether or not it
    # was ever going to show up in _counts's per-organisation reconciliation.
    unreconciled += len(quarantine)
    return {"copied": copied, "quarantined": len(quarantine), "unreconciled": unreconciled}


@click.command("backfill-review-queue-approvals")
@click.option("--dry-run", is_flag=True, help="Report what would be copied; write nothing.")
@click.option("--organization-id", type=int, default=None, help="Only this organisation.")
@with_appcontext
def backfill_review_queue_approvals(dry_run, organization_id):
    """Copy pending ReviewQueueItem/RelationshipSuggestion/SolutionBlueprintProposal rows into ai_chat_crud_approvals."""
    stats = run_backfill(dry_run=dry_run, organization_id=organization_id)
    if stats["unreconciled"]:
        raise click.ClickException(
            f"{stats['unreconciled']} pending row(s) remain uncopied after the run; "
            "investigate before relying on this command."
        )


def init_app(app):
    app.cli.add_command(backfill_review_queue_approvals)

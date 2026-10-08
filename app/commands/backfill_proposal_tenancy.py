"""Attribute pre-existing proposal-path rows to an organisation.

``ArchitectureInferenceRelationship`` and ``ReviewDecision`` gained
``TenantMixin``, so ``do_orm_execute`` now filters every entity read by
``organization_id``. Rows written before that have it NULL and are therefore
invisible to every tenant.

Attribution is determinable rather than guessed, per table:

``review_decisions``
    Each decision references the queue item it decided on
    (``review_item_id``). ``ReviewQueueItem`` already carries
    ``organization_id`` (its own backfill is ``backfill-review-queue-org``).
    A decision takes its queue item's organisation when the item has one.

``architecture_inference_relationship``
    Every known writer (``ArchitectureGraphFacade.get_or_create_relationship``,
    the two ``JourneyOrchestrator.generate_architecture`` wiring passes, and
    the wizard's ``_sync_capability_realization_links``) stores ``source_id``
    / ``target_id`` as ``archimate_elements.id`` values, never as
    ``architecture_id`` or any other identifier. A row is attributed only
    when BOTH the source and target element resolve to a real
    ``archimate_elements`` row AND those two elements agree on
    ``organization_id``. A row whose source and target elements disagree, or
    where either side does not resolve to an existing, organisation-owned
    element, is reported and left NULL — inventing an owner, or picking one
    side arbitrarily, would be worse than leaving it unattributed.
    ``architecture_id`` is not used for attribution: on the wizard's writer
    it holds a *solution* id, not an ``ArchitectureModel`` id, and neither
    identifier carries an organisation column of its own.

Idempotent (only touches NULLs) and non-destructive. Counts are reported
before and after, and — for the relationship table — broken down by
``source_tag`` so a systematic gap in one writer's rows is visible rather
than averaged away.

    flask --app manage backfill-proposal-tenancy --dry-run
    flask --app manage backfill-proposal-tenancy
"""
import click
from flask.cli import with_appcontext


def init_app(app):
    app.cli.add_command(backfill_proposal_tenancy)


def _scalar(db, sql, params=None):
    return db.session.execute(db.text(sql), params or {}).scalar() or 0


def _review_decision_counts(db):
    total = _scalar(db, "SELECT COUNT(*) FROM review_decisions")
    orphaned = _scalar(
        db, "SELECT COUNT(*) FROM review_decisions WHERE organization_id IS NULL"
    )
    resolvable = _scalar(
        db,
        "SELECT COUNT(*) FROM review_decisions d "
        "JOIN review_queue_items q ON q.id = d.review_item_id "
        "WHERE d.organization_id IS NULL AND q.organization_id IS NOT NULL",
    )
    return total, orphaned, resolvable


def _air_source_tags(db):
    rows = db.session.execute(
        db.text(
            "SELECT COALESCE(source_tag, '(none)'), COUNT(*) "
            "FROM architecture_inference_relationship "
            "GROUP BY COALESCE(source_tag, '(none)') ORDER BY 1"
        )
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def _air_tag_counts(db, tag):
    """(orphaned, agreed, conflicting) for one source_tag among NULL-org AIR rows."""
    tag_predicate = "r.source_tag IS NULL" if tag == "(none)" else "r.source_tag = :tag"
    params = {} if tag == "(none)" else {"tag": tag}

    orphaned = _scalar(
        db,
        f"SELECT COUNT(*) FROM architecture_inference_relationship r "
        f"WHERE r.organization_id IS NULL AND {tag_predicate}",  # nosec B608 -- tag_predicate is one of two fixed literal strings chosen in code; the value itself is a bound parameter
        params,
    )
    agreed = _scalar(
        db,
        "SELECT COUNT(*) FROM architecture_inference_relationship r "
        "JOIN archimate_elements src ON src.id = r.source_id "
        "JOIN archimate_elements tgt ON tgt.id = r.target_id "
        "WHERE r.organization_id IS NULL "
        f"AND {tag_predicate} "  # nosec B608 -- see note above
        "AND src.organization_id IS NOT NULL "
        "AND tgt.organization_id IS NOT NULL "
        "AND src.organization_id = tgt.organization_id",
        params,
    )
    conflicting = _scalar(
        db,
        "SELECT COUNT(*) FROM architecture_inference_relationship r "
        "JOIN archimate_elements src ON src.id = r.source_id "
        "JOIN archimate_elements tgt ON tgt.id = r.target_id "
        "WHERE r.organization_id IS NULL "
        f"AND {tag_predicate} "  # nosec B608 -- see note above
        "AND src.organization_id IS NOT NULL "
        "AND tgt.organization_id IS NOT NULL "
        "AND src.organization_id <> tgt.organization_id",
        params,
    )
    return orphaned, agreed, conflicting


@click.command("backfill-proposal-tenancy")
@click.option("--dry-run", is_flag=True, help="Report what would change, write nothing.")
@with_appcontext
def backfill_proposal_tenancy(dry_run):
    """Set organization_id on review_decisions / architecture_inference_relationship rows that have none."""
    from app.extensions import db

    # ---- review_decisions ----
    rd_total, rd_orphaned, rd_resolvable = _review_decision_counts(db)
    click.echo(f"review_decisions: {rd_total} row(s), {rd_orphaned} unattributed.")
    if rd_orphaned:
        click.echo(f"  resolvable via review_item_id -> review_queue_items.organization_id: {rd_resolvable}")
        rd_unresolvable = rd_orphaned - rd_resolvable
        if rd_unresolvable:
            click.echo(
                f"  NOT resolvable (queue item missing or itself unattributed): {rd_unresolvable} — left NULL"
            )

    # ---- architecture_inference_relationship ----
    air_total = _scalar(db, "SELECT COUNT(*) FROM architecture_inference_relationship")
    air_orphaned = _scalar(
        db, "SELECT COUNT(*) FROM architecture_inference_relationship WHERE organization_id IS NULL"
    )
    click.echo(f"architecture_inference_relationship: {air_total} row(s), {air_orphaned} unattributed.")

    tag_breakdown = []
    air_agreed_total = 0
    air_conflicting_total = 0
    if air_orphaned:
        for tag, _tag_total in _air_source_tags(db):
            orphaned, agreed, conflicting = _air_tag_counts(db, tag)
            if orphaned == 0:
                continue
            tag_breakdown.append((tag, orphaned, agreed, conflicting))
            air_agreed_total += agreed
            air_conflicting_total += conflicting

        click.echo(f"  resolvable (source and target elements agree on organisation): {air_agreed_total}")
        click.echo(f"  conflicting (source and target elements disagree): {air_conflicting_total} — left NULL")
        air_missing_total = air_orphaned - air_agreed_total - air_conflicting_total
        if air_missing_total:
            click.echo(
                f"  unresolvable (source or target element missing or itself unattributed): {air_missing_total} — left NULL"
            )
        click.echo("  by source_tag:")
        for tag, orphaned, agreed, conflicting in tag_breakdown:
            missing = orphaned - agreed - conflicting
            click.echo(
                f"    └─ {tag}: {orphaned} unattributed "
                f"(resolvable={agreed}, conflicting={conflicting}, unresolvable={missing})"
            )

    if dry_run:
        click.echo("dry run — nothing written.")
        return

    updated_rd = 0
    if rd_orphaned:
        result = db.session.execute(
            db.text(
                "UPDATE review_decisions d SET organization_id = q.organization_id "
                "FROM review_queue_items q "
                "WHERE q.id = d.review_item_id "
                "AND d.organization_id IS NULL "
                "AND q.organization_id IS NOT NULL"
            )
        )
        updated_rd = result.rowcount

    updated_air = 0
    if air_orphaned:
        result = db.session.execute(
            db.text(
                "UPDATE architecture_inference_relationship r "
                "SET organization_id = src.organization_id "
                "FROM archimate_elements src, archimate_elements tgt "
                "WHERE src.id = r.source_id AND tgt.id = r.target_id "
                "AND r.organization_id IS NULL "
                "AND src.organization_id IS NOT NULL "
                "AND tgt.organization_id IS NOT NULL "
                "AND src.organization_id = tgt.organization_id"
            )
        )
        updated_air = result.rowcount

    db.session.commit()

    click.echo(f"attributed {updated_rd} review_decisions row(s).")
    click.echo(f"attributed {updated_air} architecture_inference_relationship row(s).")

    # ---- after counts ----
    _rd_total_after, rd_orphaned_after, _ = _review_decision_counts(db)
    air_orphaned_after = _scalar(
        db, "SELECT COUNT(*) FROM architecture_inference_relationship WHERE organization_id IS NULL"
    )
    click.echo(f"review_decisions: {rd_orphaned_after} unattributed after.")
    click.echo(f"architecture_inference_relationship: {air_orphaned_after} unattributed after.")

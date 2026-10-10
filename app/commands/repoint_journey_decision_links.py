"""One-off remap: `architecture_journey_links` rows of type 'decision' currently
store an `ArchitectureDecisionRecord.id` in `entity_id`. R1-02 repoints
`JOURNEY_LINK_RESOLVERS["decision"]` to read `ArchitectureDecision` (the
register ARB already uses) instead, so a raw
resolver change alone would make every existing link silently resolve to whatever
unrelated `ArchitectureDecision` row happens to share that numeric id -- a wrong
decision shown with full apparent authority, not "no longer available".

This command must run, per organisation, BEFORE the resolver change reaches
production, if any journey-link row of type 'decision' exists there (sdd.md 9.2;
security.md 5.4). It never deletes a row of either table:

1. Back up every affected `architecture_journey_links` row (before state) to a
   JSON file, printed on completion.
2. For each row, find or create a same-org `ArchitectureDecision` with
   `source_table='architecture_decision_records' AND source_id=<old entity_id>`
   (ADR 0008 rule 2: a copy declares itself). Title and status are transcribed
   from the record being replaced; nothing else.
3. Repoint the link's `entity_id` to that `ArchitectureDecision.id`.

Idempotent: a link already pointing at a row with the right provenance is left
alone, and re-running finds nothing left to do.

    flask --app manage repoint-journey-decision-links --dry-run
    flask --app manage repoint-journey-decision-links --apply
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import click
from flask.cli import with_appcontext
from sqlalchemy import select

from app import db


def _affected_links(session, organization_id: int | None = None):
    from app.models.architecture_journey_link import ArchitectureJourneyLink

    stmt = select(ArchitectureJourneyLink).where(
        ArchitectureJourneyLink.entity_type == "decision"
    )
    if organization_id is not None:
        stmt = stmt.where(ArchitectureJourneyLink.organization_id == organization_id)
    return session.scalars(stmt.order_by(ArchitectureJourneyLink.id)).all()


def _write_backup(rows) -> Path:
    payload = [
        {
            "id": row.id,
            "organization_id": row.organization_id,
            "journey_id": row.journey_id,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "relation": row.relation,
        }
        for row in rows
    ]
    backup_dir = Path(tempfile.gettempdir()) / "archie-decision-link-repoint-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"journey-decision-links-{int(time.time())}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _run(*, dry_run: bool, organization_id: int | None) -> dict:
    from app.models.adr import ArchitectureDecisionRecord
    from app.models.architecture_decision import ArchitectureDecision

    session = db.session
    before = len(_affected_links(session, organization_id))

    rows = _affected_links(session, organization_id)
    if not rows:
        click.echo("No architecture_journey_links rows of type 'decision' found.")
        return {"before": before, "after": before, "backup": None, "created": 0, "repointed": 0, "unresolved": 0}

    backup_path = _write_backup(rows)
    click.echo(f"Backed up {len(rows)} affected link row(s) to {backup_path}")

    if dry_run:
        click.echo(f"dry-run: would inspect {len(rows)} link(s); no changes committed.")
        session.rollback()
        return {"before": before, "after": before, "backup": str(backup_path), "created": 0, "repointed": 0, "unresolved": 0}

    created = 0
    repointed = 0
    unresolved = 0
    for link in rows:
        org_id = link.organization_id
        old_record_id = link.entity_id

        # Idempotent: if entity_id already names an ArchitectureDecision with the
        # right provenance (or any ArchitectureDecision at all, meaning a prior
        # run already repointed this link), leave it alone.
        already = session.execute(
            select(ArchitectureDecision.id).where(
                ArchitectureDecision.id == old_record_id,
                ArchitectureDecision.organization_id == org_id,
            )
        ).scalar_one_or_none()
        if already is not None:
            continue

        target_id = session.execute(
            select(ArchitectureDecision.id).where(
                ArchitectureDecision.organization_id == org_id,
                ArchitectureDecision.source_table == "architecture_decision_records",
                ArchitectureDecision.source_id == old_record_id,
            )
        ).scalar_one_or_none()

        if target_id is None:
            source = session.execute(
                select(ArchitectureDecisionRecord).where(
                    ArchitectureDecisionRecord.id == old_record_id,
                    ArchitectureDecisionRecord.organization_id == org_id,
                )
            ).scalar_one_or_none()
            if source is None:
                # The old record itself is gone; there is nothing to transcribe.
                # Leave the link as-is -- the resolver already renders a missing
                # target as "Record no longer available".
                unresolved += 1
                continue
            projection = ArchitectureDecision(
                organization_id=org_id,
                title=source.title,
                status=source.status or "proposed",
                source_table="architecture_decision_records",
                source_id=old_record_id,
            )
            session.add(projection)
            session.flush()
            target_id = projection.id
            created += 1

        link.entity_id = target_id
        repointed += 1

    session.commit()
    after = len(_affected_links(session, organization_id))
    click.echo(
        f"repoint-journey-decision-links: {repointed} link(s) repointed, "
        f"{created} projection row(s) created, {unresolved} unresolved "
        f"(source record gone). before={before} after={after}"
    )
    return {
        "before": before,
        "after": after,
        "backup": str(backup_path),
        "created": created,
        "repointed": repointed,
        "unresolved": unresolved,
    }


@click.command("repoint-journey-decision-links")
@click.option("--dry-run", is_flag=True, help="Back up and report without writing.")
@click.option("--apply", "apply_changes", is_flag=True, help="Apply the remap.")
@click.option("--org", "organization_id", type=int, default=None, help="Limit to one organisation.")
@with_appcontext
def repoint_journey_decision_links(dry_run, apply_changes, organization_id):
    """Remap 'decision' journey links from ArchitectureDecisionRecord ids to
    ArchitectureDecision (the register) ids, per sdd.md 9.2."""
    if dry_run == apply_changes:
        raise click.UsageError("choose exactly one of --dry-run or --apply")
    _run(dry_run=dry_run, organization_id=organization_id)


def init_app(app):
    """Register the repoint-journey-decision-links CLI command."""
    app.cli.add_command(repoint_journey_decision_links)

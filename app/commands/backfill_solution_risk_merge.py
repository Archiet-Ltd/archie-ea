"""Copy each unmerged solution risk into the one risk register.

``risks`` (Risk, app/models/risk.py) plus its link table ``risk_entity_links``
(RiskEntityLink) is the canonical risk register -- app/services/risk_service.py
is its one writer. ``solution_risks`` (SolutionRisk,
app/models/solution_lifecycle_models.py) raised its own, separate risks
against a Solution, with no shared score history and no reuse of the
Application/Solution/Programme entity picker the canonical register uses.

This command copies each row not yet copied:

* the destination Risk's ``solution_id`` keeps the direct pointer the
  existing heat map and risk-list filters already read (``Risk.solution_id``),
  and a ``RiskEntityLink`` of ``entity_type="solution"`` is added too, so the
  row also shows up through the canonical register's own link table, exactly
  as a risk created there directly would;
* impact/probability ("very_low".."critical") convert to the 1-5 scale
  risk_service already uses, reusing the exact mapping the risk heat map
  route already applies to these same strings
  (app/modules/solutions_strategic/v2/routes/solution_routes.py's
  ``_level_to_int``) rather than a second, possibly-inconsistent one. The
  converted pair is stored both on the legacy likelihood/impact columns (so
  the existing heat map keeps working unchanged) and as the risk's inherent
  score -- residual is left unset, since nothing in the source data
  represents a post-mitigation assessment; a human sets it later;
* one RiskScoreHistory row records that initial inherent score, written via
  ``risk_service.set_risk_score`` -- the one writer -- so this backfill does
  not become a second write path around it;
* a row whose organisation cannot be determined is never guessed and never
  copied into shared scope. It is recorded in ``ErrorEvent`` (the
  platform-wide, nullable-organisation surface every platform admin already
  sees at /admin/errors and in the error digest email), reusing the exact
  pattern the approval-queue consolidation's own quarantine adopted, rather
  than a second bespoke quarantine table;
* nothing is deleted, and ``solution_risks`` rows are otherwise untouched --
  ``retired_into_risk_id`` is the only column this command ever sets on them.

Idempotent: a row whose ``retired_into_risk_id`` is set, or a quarantined row
whose organisation is still unknown, is skipped on every re-run. Prints
before/after counts and exits non-zero only while some row is neither merged
nor quarantined.

    flask --app manage backfill-solution-risk-merge --dry-run
    flask --app manage backfill-solution-risk-merge
    flask --app manage backfill-solution-risk-merge --organization-id 7
"""
from collections import defaultdict
from datetime import datetime

import click
from flask.cli import with_appcontext


def init_app(app):
    app.cli.add_command(backfill_solution_risk_merge)


def _level_to_int(value):
    """Reuse the exact probability/impact scale the risk heat map route
    already applies to these same SolutionRisk string values, rather than a
    second, possibly-inconsistent mapping."""
    from app.modules.solutions_strategic.v2.routes.solution_routes import (
        _level_to_int as _shared_level_to_int,
    )
    return _shared_level_to_int(value)


def _status_for(value):
    from app.models.risk import RiskStatus

    if value:
        try:
            return RiskStatus(str(value).strip().lower())
        except ValueError:
            pass
    return RiskStatus.OPEN


def _title_for(solution_risk):
    if solution_risk.risk_name:
        return solution_risk.risk_name[:255]
    return (solution_risk.risk_description or f"Risk {solution_risk.id}")[:255]


def _record_quarantine(source_id):
    """Persist one unattributable row where a platform administrator can see it.

    Reuses ``ErrorEvent`` (app/models/error_event.py) rather than a new
    store -- the same choice the approval-queue consolidation's backfill made:
    it is already the platform-wide, nullable-organisation,
    dedup-by-fingerprint surface with its own admin page
    (app/modules/monitoring/routes/error_events_routes.py) and digest email.
    ``resolved=False`` until someone gives the row an organisation and
    re-runs the backfill; idempotent across runs via the fingerprint.
    """
    from app import db
    from app.models.error_event import ErrorEvent

    fingerprint = f"backfill-quarantine:solution_risks:{source_id}"[:64]
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
            f"backfill-solution-risk-merge: solution_risks #{source_id} has no "
            "organisation and cannot be copied into the one risk register. "
            "Attribute it an organisation, then re-run the backfill."
        ),
        location="app.commands.backfill_solution_risk_merge",
        organization_id=None,
        occurrence_count=1,
        first_seen_at=now,
        last_seen_at=now,
        resolved=False,
    ))


def _counts():
    from sqlalchemy import nullsfirst

    from app import db
    from app.models.solution_lifecycle_models import SolutionRisk

    rows = (
        db.session.query(
            SolutionRisk.organization_id,
            db.func.count(),
            db.func.count(SolutionRisk.retired_into_risk_id),
        )
        .group_by(SolutionRisk.organization_id)
        .order_by(nullsfirst(SolutionRisk.organization_id))
        .all()
    )
    return {org: (total, merged) for org, total, merged in rows}


def _print_counts(label, counts):
    click.echo(f"{label}:")
    for org, (total, merged) in counts.items():
        owner = "unattributed" if org is None else f"organisation {org}"
        click.echo(f"  solution_risks {owner:22} rows={total:<7} merged={merged}")


def _merge_one(solution_risk):
    """Create the canonical Risk + link + inherent-score history for one row."""
    from app import db
    from app.models.risk import Risk
    from app.services import risk_service
    from app.services.archimate_backbone import sync_archimate_element

    likelihood = _level_to_int(solution_risk.probability)
    impact = _level_to_int(solution_risk.impact)
    risk = Risk(
        organization_id=solution_risk.organization_id,
        solution_id=solution_risk.solution_id,
        title=_title_for(solution_risk),
        description=solution_risk.risk_description,
        likelihood=likelihood,
        impact=impact,
        status=_status_for(solution_risk.status),
        owner=(solution_risk.owner or "")[:128] or None,
        mitigation_plan=solution_risk.mitigation,
        created_at=solution_risk.created_at,
    )
    db.session.add(risk)
    db.session.flush()  # need risk.id for the link and the history row below
    sync_archimate_element(risk)
    risk_service.add_risk_link(risk.id, "solution", solution_risk.solution_id)
    risk_service.set_risk_score(risk.id, "inherent", likelihood, impact)
    solution_risk.retired_into_risk_id = risk.id
    return risk


@click.command("backfill-solution-risk-merge")
@click.option("--dry-run", is_flag=True, help="Report what would be merged; write nothing.")
@click.option("--organization-id", type=int, default=None, help="Only this organisation.")
@with_appcontext
def backfill_solution_risk_merge(dry_run, organization_id):
    """Copy every unmerged solution risk into the one risk register."""
    from flask import current_app, g

    from app import db
    from app.models.solution_lifecycle_models import SolutionRisk

    before = _counts()
    _print_counts("Before", before)

    rows = (
        SolutionRisk.query.filter(SolutionRisk.retired_into_risk_id.is_(None))
        .order_by(SolutionRisk.id)
        .all()
    )
    plan = defaultdict(list)
    quarantine = []
    for row in rows:
        if row.organization_id is None:
            quarantine.append(row.id)
        elif organization_id is None or row.organization_id == organization_id:
            plan[row.organization_id].append(row.id)

    if dry_run:
        for org_id in sorted(plan):
            click.echo(f"organisation {org_id}: solution_risks {len(plan[org_id])} rows to merge")
        click.echo(f"Quarantined (organisation cannot be determined, not merged): {len(quarantine)}")
        for source_id in quarantine:
            click.echo(f"  solution_risks #{source_id}")
        return

    merged = 0
    for org_id in sorted(plan):
        ids = plan[org_id]
        click.echo(f"organisation {org_id}: solution_risks {len(ids)} rows to merge")
        # A request context with g.current_org_id set, one per organisation:
        # risk_service.add_risk_link and .set_risk_score construct
        # RiskEntityLink/RiskScoreHistory rows without an explicit
        # organization_id, relying on the tenant before_flush listener
        # (app/middleware/tenant_isolation.py) to fill it in from
        # g.current_org_id -- without this, TenantMixin's column default
        # cannot safely guess an organisation once more than one exists and
        # the insert would violate NOT NULL. The same technique
        # scripts/check_store_agreement.py's observe_tenant uses to run
        # tenant-scoped code outside a real request. No db.session.remove()
        # between organisations: this command's own scoping comes from
        # g.current_org_id and each row's explicit organization_id, not from
        # a clean session, and removing the session here has been observed to
        # discard an already-committed row when this command's session is
        # shared with a caller's own transaction (as in a test harness).
        with current_app.test_request_context("/"):
            g.current_org_id = org_id
            for row_id in ids:
                row = db.session.get(SolutionRisk, row_id)
                if row is None or row.retired_into_risk_id is not None:
                    continue  # already handled (idempotent re-run, or done earlier this run)
                try:
                    _merge_one(row)
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    raise
                merged += 1
        # g.current_org_id lives on the app context, which test_request_context
        # only implicitly pushes when none is already active -- inside
        # with_appcontext (and, in a test, inside the caller's own app
        # context) it is the SAME app context throughout, so the value set
        # above survives this block's exit. Clear it explicitly so it cannot
        # leak into the next organisation's iteration, the "After" counts
        # below (which must see every organisation, not just the last one
        # processed), or the caller once this command returns.
        if hasattr(g, "current_org_id"):
            delattr(g, "current_org_id")

    # Quarantine cannot run scoped to one organisation -- an unattributed row
    # has none -- so it always covers every unattributed row, independent of
    # --organization-id.
    for source_id in quarantine:
        _record_quarantine(source_id)
    if quarantine:
        db.session.commit()

    click.echo(f"Merged {merged}.")
    click.echo(f"Quarantined (organisation cannot be determined, not merged): {len(quarantine)}")
    for source_id in quarantine:
        click.echo(f"  solution_risks #{source_id}")

    after = _counts()
    _print_counts("After", after)
    # Reconcile: every attributed row in scope is now merged. Quarantined
    # (organisation-less) rows are accounted for above and never count here.
    unreconciled = sum(
        total - done for org, (total, done) in after.items()
        if org is not None and (organization_id is None or org == organization_id)
    )
    if unreconciled:
        raise click.ClickException(
            f"{unreconciled} attributed solution_risks row(s) are not yet merged into "
            "risks; see warnings above."
        )
    click.echo("Reconciled: every attributed solution_risks row is merged into risks.")

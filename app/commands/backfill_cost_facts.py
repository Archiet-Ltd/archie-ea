"""flask backfill-cost-facts — copy existing cost records into the cost fact store.

One organisation at a time it reads three sources and writes one typed cost
fact per record, recording where each came from (source, source table and
source record id):

1. the cost columns on ``application_components`` (written through the same
   function the application cost accessor uses, so a later accessor write finds
   and updates the very same fact rather than adding a second);
2. ``application_costs`` rows (actual per fiscal period, and budget when set);
3. ``capability_cost_allocations`` rows.

A count of every source is taken per organisation before anything is written.
Idempotent: a second run reports every record as unchanged. Non-fatal: a
record that cannot be copied is counted and skipped, the rest continue. No
source value is ever changed or deleted.

Usage:
    flask --app manage backfill-cost-facts
    flask --app manage backfill-cost-facts --dry-run
    flask --app manage backfill-cost-facts --org-ids 1,2
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Dict, List, Optional

import click
from flask.cli import with_appcontext

from app import db

logger = logging.getLogger(__name__)

SOURCE = "backfill"
SOURCE_APP_COLUMNS = "application_components"
SOURCE_APP_COSTS = "application_costs"
SOURCE_CAPABILITY = "capability_cost_allocations"


def _new_counter() -> Dict[str, int]:
    return {"read": 0, "created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0}


def _fiscal_period(year: int, quarter: Optional[int], start: Optional[date], end: Optional[date]):
    """(period, start, end) for a fiscal year or quarter, honouring recorded dates."""
    if quarter in (1, 2, 3, 4):
        q_start = date(year, 3 * quarter - 2, 1)
        q_end = date(year, 3 * quarter, 31 if quarter in (1, 4) else 30)
        return "fiscal_quarter", start or q_start, end or q_end
    return "fiscal_year", start or date(year, 1, 1), end or date(year, 12, 31)


def source_counts(organization_id: int) -> Dict[str, int]:
    """How many records each source holds for the organisation."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.cost_intelligence import CapabilityCostAllocation
    from app.models.enterprise_intelligence import ApplicationCost
    from app.services.application_cost_accessor import FACT_COLUMNS

    app_rows = db.session.execute(
        db.select(ApplicationComponent).where(ApplicationComponent.organization_id == organization_id)
    ).scalars().all()
    columns = sum(1 for a in app_rows for c in FACT_COLUMNS if getattr(a, c, None) is not None)
    app_costs = db.session.execute(
        db.select(db.func.count(ApplicationCost.id))
        .join(ApplicationComponent, ApplicationCost.application_id == ApplicationComponent.id)
        .where(ApplicationComponent.organization_id == organization_id)
    ).scalar_one()
    allocations = db.session.execute(
        db.select(db.func.count(CapabilityCostAllocation.id))
        .where(CapabilityCostAllocation.organization_id == organization_id)
    ).scalar_one()
    return {SOURCE_APP_COLUMNS: columns, SOURCE_APP_COSTS: app_costs, SOURCE_CAPABILITY: allocations}


def _guarded(counter: Dict[str, int], dry_run: bool, label: str, fn) -> None:
    """Run one record's copy in its own savepoint; count the outcome, never raise."""
    try:
        with db.session.begin_nested():
            outcome = fn()
        if outcome is None:
            counter["skipped"] += 1
        else:
            counter[outcome] += 1
    except Exception:  # noqa: BLE001 -- non-fatal by design; counted and logged
        logger.exception("cost fact backfill: %s could not be copied", label)
        counter["errors"] += 1


def backfill_organisation(organization_id: int, dry_run: bool = False) -> Dict:
    """Backfill one organisation; returns its before-counts and per-source results."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.cost_fact import (
        ELEMENT_APPLICATION,
        ELEMENT_CAPABILITY,
        KIND_ACTUAL,
        KIND_BUDGET,
        KINDS,
    )
    from app.models.cost_intelligence import CapabilityCostAllocation
    from app.models.enterprise_intelligence import ApplicationCost
    from app.services.application_cost_accessor import (
        FACT_COLUMNS,
        get_reporting_currency,
        sync_cost_fact,
    )
    from app.services.cost_fact_store import upsert_fact

    before = source_counts(organization_id)
    results = {
        SOURCE_APP_COLUMNS: _new_counter(),
        SOURCE_APP_COSTS: _new_counter(),
        SOURCE_CAPABILITY: _new_counter(),
    }
    results[SOURCE_APP_COLUMNS]["read"] = before[SOURCE_APP_COLUMNS]
    results[SOURCE_APP_COSTS]["read"] = before[SOURCE_APP_COSTS]
    results[SOURCE_CAPABILITY]["read"] = before[SOURCE_CAPABILITY]
    reporting = get_reporting_currency(organization_id)

    apps = db.session.execute(
        db.select(ApplicationComponent).where(ApplicationComponent.organization_id == organization_id)
    ).scalars().all()
    for app_obj in apps:
        for column in FACT_COLUMNS:
            if getattr(app_obj, column, None) is None:
                continue
            if dry_run:
                continue
            _guarded(results[SOURCE_APP_COLUMNS], dry_run, f"{app_obj.id}:{column}",
                     lambda a=app_obj, c=column: sync_cost_fact(a, c))

    cost_rows = db.session.execute(
        db.select(ApplicationCost)
        .join(ApplicationComponent, ApplicationCost.application_id == ApplicationComponent.id)
        .where(ApplicationComponent.organization_id == organization_id)
        .order_by(ApplicationCost.id)
    ).scalars().all()
    for row in cost_rows:
        if dry_run:
            continue

        def _copy(r=row):
            period, start, end = _fiscal_period(
                r.fiscal_year, r.fiscal_quarter, r.cost_period_start, r.cost_period_end)
            outcomes = []
            for kind, amount, suffix in ((KIND_ACTUAL, r.total_cost, ""), (KIND_BUDGET, r.total_budget, ":budget")):
                if amount is None:
                    continue
                _, outcome = upsert_fact(
                    organization_id, ELEMENT_APPLICATION, r.application_id, amount, reporting, SOURCE,
                    kind=kind, period=period, period_start=start, period_end=end,
                    source_table=SOURCE_APP_COSTS, source_id=f"{r.id}{suffix}")
                outcomes.append(outcome)
            if not outcomes:
                return None
            for rank in ("created", "updated"):
                if rank in outcomes:
                    return rank
            return "unchanged"

        _guarded(results[SOURCE_APP_COSTS], dry_run, f"application_costs.{row.id}", _copy)

    allocations = db.session.execute(
        db.select(CapabilityCostAllocation)
        .where(CapabilityCostAllocation.organization_id == organization_id)
        .order_by(CapabilityCostAllocation.id)
    ).scalars().all()
    for alloc in allocations:
        if dry_run:
            continue

        def _copy_alloc(a=alloc):
            total = a.calculate_total_cost()
            if not total:
                return None  # nothing recorded on the row; a zero is not a cost
            kind = a.cost_type if a.cost_type in KINDS else KIND_ACTUAL
            period, start, end = _fiscal_period(
                a.fiscal_year, a.fiscal_quarter, a.period_start_date, a.period_end_date)
            _, outcome = upsert_fact(
                organization_id, ELEMENT_CAPABILITY, a.capability_id, total,
                a.currency or reporting, SOURCE, kind=kind, period=period,
                period_start=start, period_end=end,
                source_table=SOURCE_CAPABILITY, source_id=str(a.id))
            return outcome

        _guarded(results[SOURCE_CAPABILITY], dry_run, f"capability_cost_allocations.{alloc.id}", _copy_alloc)

    return {"before": before, "results": results}


def backfill_cost_facts(dry_run: bool = False, organization_ids: Optional[List[int]] = None) -> Dict:
    """Run the backfill for every organisation (or those given), one at a time."""
    from app.models.organization import Organization

    if organization_ids:
        org_ids = sorted(organization_ids)
    else:
        org_ids = [r[0] for r in db.session.execute(
            db.select(Organization.id).order_by(Organization.id)).all()]

    report: Dict[str, Dict] = {}
    for org_id in org_ids:
        try:
            report[str(org_id)] = backfill_organisation(org_id, dry_run=dry_run)
            if dry_run:
                db.session.rollback()
            else:
                db.session.commit()
        except Exception:  # noqa: BLE001 -- one organisation never stops the others
            logger.exception("cost fact backfill: organisation %s failed", org_id)
            db.session.rollback()
            report[str(org_id)] = {"error": "organisation could not be backfilled"}
        # Next organisation re-reads from the database; every query here carries
        # an explicit organisation predicate, so no cached row is ever reused.
        db.session.expire_all()
    return report


@click.command("backfill-cost-facts")
@click.option("--dry-run", is_flag=True, help="Count the sources only; write nothing.")
@click.option("--org-ids", default=None, help="Comma-separated organisation ids.")
@click.option("--json", "as_json", is_flag=True, help="Print the report as JSON.")
@with_appcontext
def backfill_cost_facts_command(dry_run, org_ids, as_json):
    ids = [int(x) for x in org_ids.split(",") if x.strip()] if org_ids else None
    report = backfill_cost_facts(dry_run=dry_run, organization_ids=ids)
    if as_json:
        click.echo(json.dumps(report, indent=2, sort_keys=True))
        return
    for org_id, entry in report.items():
        click.echo(f"\n  Organisation {org_id}:")
        if "error" in entry:
            click.echo(f"    {entry['error']}")
            continue
        for source, counts in entry["results"].items():
            click.echo(f"    {source}: " + ", ".join(f"{k}={v}" for k, v in counts.items()))


def init_app(app) -> None:
    app.cli.add_command(backfill_cost_facts_command)

"""backfill-capability-mappings: carry every older application-to-capability mapping into the one store.

``unified_application_capability_mapping`` is the store every screen reads. Two older tables held the same
fact and are frozen: ``application_capability_mapping`` (keyed on the business capability, with an
organisation column) and ``application_capability_coverage`` (keyed on the business capability, organisation
taken from the capability). A mapping that only they hold is invisible on the record and the mapping page.

This command copies those rows into the store, resolving each business capability to its unified capability
through the projection link (``unified_capabilities.source_table`` / ``source_id``) and keeping every row in
the organisation that owns it:

    flask --app manage backfill-capability-mappings                 # dry run (the default); writes nothing
    flask --app manage backfill-capability-mappings --dry-run
    flask --app manage backfill-capability-mappings --apply
    flask --app manage backfill-capability-mappings --apply --org-id 7

Per organisation it prints rows found, rows already in the store, rows to insert and rows it cannot resolve,
each with its reason. Apply is idempotent (a second run inserts 0 rows) and never deletes or changes a
legacy row or an existing store row. It runs under the platform scope, because the rows span organisations.
"""
from __future__ import annotations

from collections import OrderedDict, defaultdict
from typing import Any, Dict, List, Optional

import click
from flask.cli import with_appcontext

LEGACY = "application_capability_mapping"
COVERAGE = "application_capability_coverage"

R_NO_BC = "the business capability no longer exists"
R_ORG_MISMATCH = "the mapping row names a different organisation than its capability"
R_NO_UNIFIED = "the business capability has no unified capability to map to"
R_NO_APP = "the application does not exist"
R_FOREIGN_APP = "the application belongs to another organisation"


def _rows(db, sql: str) -> List[Any]:
    return list(db.session.execute(db.text(sql)).mappings())


def plan(org_id: Optional[int] = None) -> Dict[Any, Dict[str, Any]]:
    """Work out, per organisation, what the backfill would do. Reads only."""
    from app import db

    legacy = _rows(db, """SELECT id, organization_id, business_capability_id AS bc_id, application_component_id AS app_id,
                                 support_level, coverage_percentage, relationship_type, gap_status, is_active,
                                 NULL AS is_strategic, NULL AS notes FROM application_capability_mapping""")
    coverage = _rows(db, """SELECT c.id, NULL AS organization_id, c.capability_id AS bc_id,
                                   c.application_component_id AS app_id, c.support_level, c.coverage_percentage,
                                   NULL AS relationship_type, NULL AS gap_status, TRUE AS is_active,
                                   c.is_strategic, c.notes FROM application_capability_coverage c""")
    bc_org = {r["id"]: r["organization_id"] for r in _rows(db, "SELECT id, organization_id FROM business_capability")}
    unified = {(int(r["source_id"]), r["organization_id"]): r["id"] for r in _rows(
        db, """SELECT id, source_id, organization_id FROM unified_capabilities
                WHERE source_table = 'business_capability' AND source_id ~ '^[0-9]+$'""")}
    app_org = {r["id"]: r["organization_id"] for r in _rows(db, "SELECT id, organization_id FROM application_components")}
    in_store = {(r["unified_capability_id"], r["application_component_id"]) for r in _rows(
        db, "SELECT unified_capability_id, application_component_id FROM unified_application_capability_mapping")}

    report: Dict[Any, Dict[str, Any]] = {}

    def bucket(org):
        return report.setdefault(org, {"found": {LEGACY: 0, COVERAGE: 0}, "already": 0, "same_pair": 0,
                                       "insert": OrderedDict(), "unresolved": defaultdict(int)})

    for table, rows in ((LEGACY, legacy), (COVERAGE, coverage)):
        for r in rows:
            owner = bc_org.get(r["bc_id"]) if r["bc_id"] in bc_org else r["organization_id"]
            if org_id is not None and owner != org_id:
                continue
            b = bucket(owner)
            b["found"][table] += 1
            if r["bc_id"] not in bc_org:
                b["unresolved"][R_NO_BC] += 1
            elif table == LEGACY and r["organization_id"] != owner:
                b["unresolved"][R_ORG_MISMATCH] += 1
            elif (r["bc_id"], owner) not in unified:
                b["unresolved"][R_NO_UNIFIED] += 1
            elif r["app_id"] not in app_org:
                b["unresolved"][R_NO_APP] += 1
            elif app_org[r["app_id"]] != owner:
                b["unresolved"][R_FOREIGN_APP] += 1
            else:
                pair = (unified[(r["bc_id"], owner)], r["app_id"])
                if pair in in_store:
                    b["already"] += 1
                elif pair in b["insert"]:
                    b["same_pair"] += 1
                else:
                    b["insert"][pair] = dict(r)
    return report


def apply(report: Dict[Any, Dict[str, Any]]) -> int:
    """Insert the planned rows. Idempotent: a pair already present is left as it is."""
    from sqlalchemy.dialects.postgresql import insert

    from app import db
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping as M

    inserted = 0
    for org, b in report.items():
        for (ucap, app_id), r in b["insert"].items():
            relationship = r["relationship_type"] if r["relationship_type"] in (
                "supports", "enables", "governs", "measures") else "supports"
            stmt = insert(M.__table__).values(
                unified_capability_id=ucap, application_component_id=app_id,
                support_level=r["support_level"] or "partial", coverage_percentage=int(round(r["coverage_percentage"] or 0)),
                support_quality=3, relationship_type=relationship, relationship_strength=3,
                dependency_level="medium", gap_status=r["gap_status"] or "unknown",
                is_active=True if r["is_active"] is None else bool(r["is_active"]),
                is_strategic=bool(r["is_strategic"]), notes=r["notes"],
            ).on_conflict_do_nothing(index_elements=["unified_capability_id", "application_component_id"])
            inserted += db.session.execute(stmt).rowcount or 0
    return inserted


def _print(report: Dict[Any, Dict[str, Any]], apply_mode: bool, inserted: Optional[int]) -> None:
    verb = "inserted" if apply_mode else "to insert"
    click.echo(f"backfill-capability-mappings: {'APPLY' if apply_mode else 'DRY RUN (nothing is written)'}")
    totals = defaultdict(int)
    for org in sorted(report, key=lambda o: (o is None, o)):
        b = report[org]
        found = sum(b["found"].values())
        unresolved = sum(b["unresolved"].values())
        click.echo(f"organisation {org if org is not None else '(none)'}: rows found {found} "
                   f"({LEGACY} {b['found'][LEGACY]}, {COVERAGE} {b['found'][COVERAGE]}); "
                   f"already in the store {b['already']}; {verb} {len(b['insert'])}; "
                   f"same pair as another row {b['same_pair']}; cannot resolve {unresolved}")
        for reason, n in sorted(b["unresolved"].items()):
            click.echo(f"    cannot resolve {n}: {reason}")
        for key, val in (("found", found), ("already", b["already"]), ("insert", len(b["insert"])),
                         ("same_pair", b["same_pair"]), ("unresolved", unresolved)):
            totals[key] += val
    click.echo(f"total: rows found {totals['found']}; already in the store {totals['already']}; {verb} "
               f"{totals['insert']}; same pair as another row {totals['same_pair']}; cannot resolve {totals['unresolved']}")
    if apply_mode:
        click.echo(f"rows written: {inserted}")


@click.command("backfill-capability-mappings")
@click.option("--dry-run/--apply", "dry_run", default=True, help="Dry run (the default) writes nothing; --apply writes.")
@click.option("--org-id", type=int, default=None, help="Only this organisation.")
@with_appcontext
def backfill_capability_mappings(dry_run, org_id):
    from app import db
    from app.jobs.tenant_safe_job import platform_scope

    with platform_scope("capability mapping backfill: copy every organisation's older mappings into the store"):
        report = plan(org_id)
        if dry_run:
            _print(report, False, None)
            return
        inserted = apply(report)
        db.session.commit()
    _print(report, True, inserted)


def init_app(app):
    app.cli.add_command(backfill_capability_mappings)

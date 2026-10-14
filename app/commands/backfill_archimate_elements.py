"""
Flask CLI command for backfilling ArchiMateElement records.
Run with: flask backfill-archimate-elements [--dry-run] [--motivation-only]

Covers:
  - ApplicationComponent  (Application layer)
  - BusinessCapability    (Strategy layer)
  - SolutionDriver        → Driver        (Motivation)
  - SolutionGoal          → Goal          (Motivation)
  - SolutionConstraint    → Constraint    (Motivation)
  - SolutionRequirement   → Requirement   (Motivation)
  - SolutionRisk          → Assessment    (Motivation)
  - SolutionMetric        → Outcome       (Motivation)
  - SolutionPlateau       → Plateau       (Implementation)
"""

import click
from flask.cli import with_appcontext

# SQL to fetch (entity_id, name, solution_id, organization_id) for each motivation
# entity type.
# All queries avoid ORM to prevent column-drift errors from unapplied migrations.
#
# organization_id is carried through from the owning solution because this command
# runs with no request context: the before_flush listener in
# app/middleware/tenant_isolation.py only stamps organization_id when
# g.current_org_id is set, so a CLI-created ArchiMateElement would otherwise be
# written with a NULL organization_id and be invisible to every tenant's ORM
# queries afterwards. The three formerly solution_id-only queries now join
# solutions for the same reason.
_ENTITY_QUERIES = {
    # via solution.analysis_session_id chain
    "Driver": (
        "Driver", "Motivation",
        """
        SELECT d.id, d.name, s.id AS solution_id, s.organization_id AS organization_id
        FROM solution_drivers d
        JOIN solution_problem_definitions spd ON d.problem_id = spd.id
        JOIN solution_analysis_sessions sas ON spd.session_id = sas.id
        JOIN solutions s ON s.analysis_session_id = sas.id
        """,
    ),
    "Goal": (
        "Goal", "Motivation",
        """
        SELECT g.id, g.name, s.id AS solution_id, s.organization_id AS organization_id
        FROM solution_goals g
        JOIN solution_problem_definitions spd ON g.problem_id = spd.id
        JOIN solution_analysis_sessions sas ON spd.session_id = sas.id
        JOIN solutions s ON s.analysis_session_id = sas.id
        """,
    ),
    "Constraint": (
        "Constraint", "Motivation",
        """
        SELECT c.id, c.name, s.id AS solution_id, s.organization_id AS organization_id
        FROM solution_constraints c
        JOIN solution_problem_definitions spd ON c.problem_id = spd.id
        JOIN solution_analysis_sessions sas ON spd.session_id = sas.id
        JOIN solutions s ON s.analysis_session_id = sas.id
        """,
    ),
    "Requirement": (
        "Requirement", "Motivation",
        """
        SELECT r.id, r.name, s.id AS solution_id, s.organization_id AS organization_id
        FROM solution_requirements r
        JOIN solutions s ON (
            r.solution_id = s.id
            OR (r.solution_id IS NULL AND r.problem_id IN (
                SELECT spd.id FROM solution_problem_definitions spd
                JOIN solution_analysis_sessions sas ON spd.session_id = sas.id
                WHERE s.analysis_session_id = sas.id
            ))
        )
        WHERE r.name IS NOT NULL AND r.name != ''
        """,
    ),
    # direct solution_id
    "Assessment": (
        "Assessment", "Motivation",
        """
        SELECT x.id, x.risk_description AS name, x.solution_id,
               s.organization_id AS organization_id
        FROM solution_risks x
        JOIN solutions s ON x.solution_id = s.id
        """,
    ),
    "Outcome": (
        "Outcome", "Motivation",
        """
        SELECT x.id, x.name, x.solution_id, s.organization_id AS organization_id
        FROM solution_metrics x
        JOIN solutions s ON x.solution_id = s.id
        """,
    ),
    "Plateau": (
        "Plateau", "Implementation",
        """
        SELECT x.id, x.name, x.solution_id, s.organization_id AS organization_id
        FROM solution_plateaus x
        JOIN solutions s ON x.solution_id = s.id
        """,
    ),
}


def _already_linked_sql(db, solution_id, ae_type, name):
    """True if (solution_id, ae_type, name) already has a join record."""
    # tenancy-ok: CLI backfill, deliberately global across organisations; no
    # request context exists. The lookup is keyed on solution_id, which came from
    # the solutions row driving this iteration, so it cannot reach past that
    # solution's own elements even though nothing filters by organisation.
    row = db.session.execute(db.text(
    """
        SELECT 1 FROM solution_elements se
        JOIN archimate_elements ae ON se.archimate_element_id = ae.id
        WHERE se.solution_id = :sol AND ae.type = :t AND ae.name = :n
        LIMIT 1
    """), {"sol": solution_id, "t": ae_type, "n": name}).fetchone()
    return row is not None


@click.command("backfill-archimate-elements")
@click.option("--dry-run", is_flag=True, help="Show counts without modifying data")
@click.option("--motivation-only", is_flag=True, help="Skip ApplicationComponent/Capability")
@with_appcontext
def backfill_archimate_elements_command(dry_run, motivation_only):
    """Backfill ArchiMateElement for all solution entity types (ARCH-LINK-4)."""
    from app import db
    from app.models.archimate_core import ArchiMateElement
    from app.models.solution_element import SolutionElement

    if dry_run:
        click.echo("DRY RUN — no data will be modified.\n")

    total_created = 0

    # ── Legacy: ApplicationComponent and BusinessCapability ──────────────────
    if not motivation_only:
        from app.models.application_portfolio import ApplicationComponent
        from app.models.business_capabilities import BusinessCapability
        from app.services.archimate_backbone import sync_archimate_element

        apps_missing = ApplicationComponent.query.filter(
            ApplicationComponent.archimate_element_id.is_(None)
        ).count()
        caps_missing = BusinessCapability.query.filter(
            BusinessCapability.archimate_element_id.is_(None)
        ).count()
        click.echo(f"ApplicationComponents missing element: {apps_missing}")
        click.echo(f"BusinessCapabilities missing element:  {caps_missing}")

        if not dry_run:
            batch_size = 100
            app_created = 0
            app_failed = set()  # rows the writer refused: never re-select them
            while True:
                batch = ApplicationComponent.query.filter(
                    ApplicationComponent.archimate_element_id.is_(None),
                    ApplicationComponent.id.notin_(app_failed),
                ).limit(batch_size).all()
                if not batch:
                    break
                for app_comp in batch:
                    try:
                        element = sync_archimate_element(app_comp)
                        if element is not None:
                            app_created += 1
                    except Exception:
                        app_failed.add(app_comp.id)
                db.session.commit()
            click.echo(f"  → Created {app_created} ApplicationComponent elements")
            total_created += app_created

            cap_created = 0
            cap_failed = set()  # rows the writer refused: never re-select them
            while True:
                batch = BusinessCapability.query.filter(
                    BusinessCapability.archimate_element_id.is_(None),
                    BusinessCapability.id.notin_(cap_failed),
                ).limit(batch_size).all()
                if not batch:
                    break
                for cap in batch:
                    try:
                        element = sync_archimate_element(cap)
                        if element is not None:
                            cap_created += 1
                    except Exception:
                        cap_failed.add(cap.id)
                db.session.commit()
            click.echo(f"  → Created {cap_created} Capability elements")
            total_created += cap_created

    # ── Motivation + Implementation layer entities ───────────────────────────
    click.echo("\nMotivation / Implementation entity backfill:")
    for key, (ae_type, ae_layer, sql) in _ENTITY_QUERIES.items():
        try:
            # tenancy-ok: CLI backfill, deliberately global across organisations;
            # no request context exists. Each row carries its solution's
            # organization_id so the element created from it is attributed.
            rows = db.session.execute(db.text(sql)).fetchall()
        except Exception as exc:
            click.echo(f"  {key:16s}: QUERY ERROR — {exc}", err=True)
            db.session.rollback()
            continue

        created = skipped = errors = 0
        for row in rows:
            name = ((row.name or "").strip() or f"{ae_type}-{row.id}")[:100]
            sol_id = row.solution_id
            if not sol_id:
                skipped += 1
                continue
            if _already_linked_sql(db, sol_id, ae_type, name):
                skipped += 1
                continue
            if not dry_run:
                try:
                    ae = ArchiMateElement(
                        name=name, type=ae_type, layer=ae_layer,
                        description=f"{ae_type}: {name}",
                        organization_id=row.organization_id,
                    )
                    db.session.add(ae)
                    db.session.flush()
                    db.session.add(SolutionElement(
                        solution_id=sol_id,
                        archimate_element_id=ae.id,
                        layer=ae_layer,
                    ))
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    errors += 1
                    continue
            created += 1

        action = "Would create" if dry_run else "Created    "
        click.echo(
            f"  {key:16s} ({ae_type:14s}): {action} {created:3d}  "
            f"skip {skipped:3d}  err {errors:2d}"
        )
        total_created += created

    action = "Would create" if dry_run else "Total created"
    click.echo(f"\n{action}: {total_created} ArchiMate elements")


def init_app(app):
    """Register CLI command with app."""
    app.cli.add_command(backfill_archimate_elements_command)

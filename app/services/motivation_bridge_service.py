"""
Motivation Bridge Service

Bridges the two disconnected motivation-layer worlds in Entelim:

  1. Enterprise motivation entities (Driver, Goal, Outcome, Principle) that
     live in app/models/motivation.py + app/models/models.py, auto-feed the
     enterprise ArchiMate traceability matrix.
  2. Solution-scoped "journey" twins created by the AI solution-design
     journey (SolutionDriver, SolutionGoal in solution_architect_models.py;
     SolutionOutcome in solution_outcomes.py; SolutionPrinciple), which never
     roll up to the enterprise layer today.

promote_solution_motivation() copies (never moves, never deletes) each
Solution* motivation element for a solution into a corresponding enterprise
entity — reusing an existing enterprise row by name where one already exists,
otherwise creating a new one (plus its ArchiMateElement, since Driver/Goal/
Outcome/Principle do NOT auto-create one via an ORM event listener the way
Meaning/Value/Assessment do — callers are expected to create it explicitly,
same as app/modules/architecture/services/*_service.py already do). Every
promotion is recorded as a MotivationBridgeLink row so that:

  - Re-running the bridge is a no-op for already-bridged elements (idempotent).
  - The enterprise traceability service can look up which enterprise element
    a given solution's journey motivation was promoted to.

This module NEVER mutates or deletes Solution* rows, and never alters an
existing enterprise Driver/Goal/Outcome/Principle row that it matches by name
(match-only, no overwrite) — strictly additive.
"""

from app.services.archimate_backbone import sync_archimate_element
import json
import logging

from app import db
from app.models.archimate_core import ArchiMateElement
from app.models.models import Outcome, Principle
from app.models.motivation import Driver, Goal, MotivationBridgeLink
from app.models.solution_architect_models import SolutionProblemDefinition
from app.models.solution_models import Solution
from app.models.solution_outcomes import SolutionOutcome

logger = logging.getLogger(__name__)

# SolutionOutcome.tracking_status -> enterprise Outcome.realization_status vocab
_TRACKING_TO_REALIZATION = {
    "not_started": "not_started",
    "in_progress": "in_progress",
    "achieved": "achieved",
    "exceeded": "achieved",
    "missed": "failed",
}


def _get_problem_definition(solution):
    """Return the SolutionProblemDefinition for this solution's analysis session, if any."""
    if not getattr(solution, "analysis_session_id", None):
        return None
    return SolutionProblemDefinition.query.filter_by(
        session_id=solution.analysis_session_id
    ).first()


def _create_archimate_element(name, el_type, description=None, organization_id=None):
    """Create the ArchiMateElement a newly-promoted enterprise entity needs.

    Driver/Goal/Outcome/Principle have no after_insert listener auto-creating
    this (unlike Meaning/Value/Assessment) — callers must create it explicitly,
    matching the pattern used in app/modules/architecture/services/*_service.py.

    ArchiMateElement is tenant-scoped (organization_id NOT NULL). This service
    runs under the `flask bridge-motivation` CLI — no request context — so
    _default_org_id() can only fall back to a single-org guess and returns None
    on a multi-org install, which would violate the NOT NULL constraint. Pass the
    source solution's organization_id explicitly so the promoted element lands in
    the right tenant.
    """
    ae = ArchiMateElement(
        name=name,
        type=el_type,
        layer="Motivation",
        description=description or f"{el_type}: {name}",
        organization_id=organization_id,
    )
    db.session.add(ae)
    db.session.flush()
    return ae


def _find_or_create_driver(sd, name, org_id=None):
    existing = Driver.query.filter_by(name=name).first()
    if existing:
        return existing, False

    ae = _create_archimate_element(name, "Driver", sd.description, organization_id=org_id)
    driver_type = sd.driver_type.value if getattr(sd, "driver_type", None) is not None else None
    driver = Driver(
        name=name,
        description=sd.description,
        archimate_element_id=ae.id,
        driver_type=driver_type,
        source=sd.source,
        status="active",
    )
    db.session.add(driver)
    sync_archimate_element(driver)
    db.session.flush()
    return driver, True


def _find_or_create_goal(sg, name, org_id=None):
    existing = Goal.query.filter_by(name=name).first()
    if existing:
        return existing, False

    # Preserve the Driver -> Goal chain if the driver this goal belongs to has
    # already been bridged in this same run (or a previous one).
    driver_id = None
    if getattr(sg, "driver_id", None):
        driver_link = MotivationBridgeLink.query.filter_by(
            solution_element_type="SolutionDriver", solution_element_id=sg.driver_id
        ).first()
        if driver_link and driver_link.enterprise_element_type == "Driver":
            driver_id = driver_link.enterprise_element_id

    target_date = None
    if getattr(sg, "target_date", None) is not None:
        target_date = (
            sg.target_date.date() if hasattr(sg.target_date, "date") else sg.target_date
        )

    ae = _create_archimate_element(name, "Goal", sg.description, organization_id=org_id)
    goal = Goal(
        name=name,
        description=sg.description,
        archimate_element_id=ae.id,
        driver_id=driver_id,
        target_date=target_date,
        strategic_priority=sg.priority,
        measurable_metrics=json.dumps(sg.kpis) if getattr(sg, "kpis", None) else None,
        notes=sg.measurement_criteria,
        status="active",
    )
    db.session.add(goal)
    sync_archimate_element(goal)
    db.session.flush()
    return goal, True


def _find_or_create_outcome(so, name, org_id=None):
    existing = Outcome.query.filter_by(name=name).first()
    if existing:
        return existing, False

    tracking_value = (
        so.tracking_status.value if getattr(so, "tracking_status", None) is not None else None
    )
    ae = _create_archimate_element(name, "Outcome", so.description, organization_id=org_id)
    outcome = Outcome(
        name=name,
        description=so.description,
        archimate_element_id=ae.id,
        kpi_metric=so.outcome_type.value if getattr(so, "outcome_type", None) is not None else None,
        target_value=str(so.predicted_value) if so.predicted_value is not None else None,
        current_value=str(so.actual_value) if so.actual_value is not None else None,
        measurement_unit=so.predicted_unit,
        target_date=so.predicted_date,
        realization_status=_TRACKING_TO_REALIZATION.get(tracking_value, "not_started"),
        # Outcome is tenant-scoped (TenantMixin). This service runs under the
        # `flask bridge-motivation` CLI -- no request context -- so the
        # column's declared_attr default can only guess and returns None on a
        # multi-org install, silently writing an org-less, permanently
        # invisible row (see app/commands/backfill_outcome_org.py, which
        # exists to clean up exactly this). Pass it explicitly, matching
        # _create_archimate_element above.
        organization_id=org_id,
    )
    db.session.add(outcome)
    db.session.flush()
    return outcome, True


def _find_or_create_principle(sp, name, org_id=None):
    existing = Principle.query.filter_by(name=name).first()
    if existing:
        return existing, False

    statement = sp.statement or name
    ae = _create_archimate_element(name, "Principle", statement, organization_id=org_id)
    principle = Principle(
        name=name,
        statement=statement,
        rationale=sp.rationale,
        implications=sp.implications,
        archimate_element_id=ae.id,
        status="draft",
        # Principle is tenant-scoped (TenantMixin, organization_id nullable).
        # Same no-request-context hazard as Outcome above -- pass explicitly
        # or a multi-org install silently writes an org-less row.
        organization_id=org_id,
    )
    db.session.add(principle)
    db.session.flush()
    return principle, True


_FIND_OR_CREATE = {
    "Driver": _find_or_create_driver,
    "Goal": _find_or_create_goal,
    "Outcome": _find_or_create_outcome,
    "Principle": _find_or_create_principle,
}


def _promote_one(element, sol_type, ent_type, solution, summary):
    """Promote a single Solution* element, recording a MotivationBridgeLink.

    Idempotent: if a link already exists for (sol_type, element.id) this is a
    no-op (counted as 'skipped'). Raises on failure so the caller's savepoint
    can roll back just this element without losing prior progress.
    """
    existing_link = MotivationBridgeLink.query.filter_by(
        solution_element_type=sol_type, solution_element_id=element.id
    ).first()
    if existing_link:
        summary["skipped"] += 1
        return

    name = (getattr(element, "name", None) or "").strip()
    if not name:
        raise ValueError(f"{sol_type} {element.id} has no name; cannot bridge")

    find_or_create = _FIND_OR_CREATE.get(ent_type)
    if find_or_create is None:
        raise ValueError(f"Unsupported enterprise_element_type: {ent_type}")

    enterprise_obj, created = find_or_create(element, name, solution.organization_id)

    link = MotivationBridgeLink(
        solution_id=solution.id,
        solution_element_type=sol_type,
        solution_element_id=element.id,
        enterprise_element_type=ent_type,
        enterprise_element_id=enterprise_obj.id,
        archimate_element_id=getattr(enterprise_obj, "archimate_element_id", None),
    )
    db.session.add(link)
    db.session.flush()

    if created:
        summary["created"] += 1
    else:
        summary["linked"] += 1


def _collect_candidates(solution):
    """Return [(element, solution_element_type, enterprise_element_type), ...]
    for every Solution* motivation element belonging to this solution.

    Drivers are ordered before goals so a goal's driver_id can be resolved to
    an already-bridged enterprise Driver within the same promotion run.
    """
    candidates = []
    problem = _get_problem_definition(solution)
    if problem:
        candidates.extend((d, "SolutionDriver", "Driver") for d in problem.drivers)
        candidates.extend((g, "SolutionGoal", "Goal") for g in problem.goals)
        candidates.extend((p, "SolutionPrinciple", "Principle") for p in problem.principles)

    outcomes = SolutionOutcome.query.filter_by(solution_id=solution.id).all()
    candidates.extend((o, "SolutionOutcome", "Outcome") for o in outcomes)
    return candidates


def promote_solution_motivation(solution_id: int, dry_run: bool = False) -> dict:
    """Promote all Solution* motivation elements of one solution to the enterprise layer.

    Non-destructive & idempotent: only ever INSERTs new enterprise rows and
    MotivationBridgeLink rows. Never touches Solution* rows. A bad element
    is caught and recorded in summary['errors'] without aborting the batch.

    With dry_run=True nothing is committed — all work is rolled back at the
    end so the database is left exactly as it was.

    Returns: {"created": int, "linked": int, "skipped": int, "errors": [...]}
    """
    summary = {"created": 0, "linked": 0, "skipped": 0, "errors": []}

    solution = db.session.get(Solution, solution_id)
    if not solution:
        summary["errors"].append(
            {"element_type": "Solution", "element_id": solution_id, "error": "Solution not found"}
        )
        return summary

    for element, sol_type, ent_type in _collect_candidates(solution):
        try:
            with db.session.begin_nested():
                _promote_one(element, sol_type, ent_type, solution, summary)
        except Exception as exc:  # noqa: BLE001 — one bad element must not abort the batch
            logger.warning(
                "motivation_bridge: failed to promote %s %s: %s",
                sol_type, getattr(element, "id", None), exc,
            )
            summary["errors"].append(
                {
                    "element_type": sol_type,
                    "element_id": getattr(element, "id", None),
                    "error": str(exc),
                }
            )

    if dry_run:
        db.session.rollback()
    else:
        try:
            db.session.commit()
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            logger.error("motivation_bridge: commit failed for solution %s: %s", solution_id, exc)
            summary["errors"].append(
                {"element_type": "commit", "element_id": solution_id, "error": str(exc)}
            )

    return summary


def promote_all(dry_run: bool = False) -> dict:
    """Run promote_solution_motivation() for every Solution.

    Returns an aggregated summary plus 'solutions_processed'.
    """
    overall = {"created": 0, "linked": 0, "skipped": 0, "errors": [], "solutions_processed": 0}

    for solution in Solution.query.all():
        result = promote_solution_motivation(solution.id, dry_run=dry_run)
        overall["created"] += result["created"]
        overall["linked"] += result["linked"]
        overall["skipped"] += result["skipped"]
        overall["errors"].extend(result["errors"])
        overall["solutions_processed"] += 1

    return overall

"""SA-004: Solution Architecture Document (SAD) narrative generation service.

Generates a structured SAD dict from real DB data, and renders it to HTML.
All 10 sections degrade gracefully to empty_state strings if no data exists.
"""

import logging
from html import escape

from app import db
from app.utils.tenant_sql import org_scope

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_EMPTY = "No data available."


def _safe(fn, default=None):
    """Execute fn; return default on any exception without crashing."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        # WARNING, not DEBUG. This swallows a failed read and substitutes an
        # empty result, so the only trace that a section is empty because the
        # query broke -- rather than because there is nothing to show -- is
        # this line. At DEBUG it was invisible in production for the entire
        # life of the broken risk query.
        logger.warning("SAD safe-query failed, section will render empty: %s", exc)
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            logger.debug("SAD safe-query rollback also failed")  # fabricated-ok: log-only on rollback failure; returns the caller-supplied default, invents nothing
        return default


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_sad(solution_id: int) -> dict:
    """Return structured dict with all 10 SAD sections.

    Each section has:
      - title (str)
      - content (list[dict] | str)
      - empty_state (str)
    Pulls REAL data from DB — no fabricated values.
    Returns gracefully for empty sections.
    """
    from app.models.solution_models import Solution
    from app.models.solution_architect_models import (
        SolutionProblemDefinition,
        SolutionDriver,
        SolutionGoal,
        SolutionRequirement,
        SolutionConstraint,
    )
    from app.services.archimate_traceability_service import get_traceability_chain

    solution = _safe(lambda: db.session.get(Solution, solution_id))
    if not solution:
        return _empty_sad(solution_id)

    # ── §1 Executive Summary ─────────────────────────────────────────────
    exec_summary = {
        "title": "1. Executive Summary",
        "empty_state": _EMPTY,
        "content": {
            "name": solution.name or "",
            "description": solution.description or "",
            "status": solution.status or "",
            "deployment_status": solution.deployment_status or "",
            "governance_status": solution.governance_status or "",
            "solution_type": solution.solution_type or "",
            "business_domain": solution.business_domain or "",
            "complexity_level": solution.complexity_level or "",
            "solution_owner": solution.solution_owner or "",
            "business_sponsor": solution.business_sponsor or "",
            "technical_lead": solution.technical_lead or "",
            "adm_phase": solution.adm_phase or "",
        },
    }

    # ── §2 Business Context ──────────────────────────────────────────────
    prob_def = _safe(lambda: SolutionProblemDefinition.query.filter_by(
        session_id=solution.analysis_session_id
    ).first() if solution.analysis_session_id else None)

    drivers = _safe(lambda: SolutionDriver.query.filter_by(
        problem_id=prob_def.id
    ).limit(50).all() if prob_def else [], default=[])

    goals = _safe(lambda: SolutionGoal.query.filter_by(
        problem_id=prob_def.id
    ).limit(50).all() if prob_def else [], default=[])

    reqs = _safe(lambda: SolutionRequirement.query.filter_by(
        solution_id=solution_id, deleted_at=None
    ).order_by(SolutionRequirement.priority).limit(100).all(), default=[])

    business_context = {
        "title": "2. Business Context",
        "empty_state": _EMPTY,
        "drivers": [
            {
                "name": d.name,
                "description": d.description or "",
                "type": d.driver_type.value if d.driver_type else "",
                "impact": d.impact_level,
            }
            for d in (drivers or [])
        ],
        "goals": [
            {
                "name": g.name,
                "description": g.description or "",
                "priority": g.priority,
            }
            for g in (goals or [])
        ],
        "requirements": [
            {
                "name": r.name,
                "description": r.description or "",
                "type": r.requirement_type.value if r.requirement_type else "",
                "priority": r.priority,
                "mandatory": r.is_mandatory,
            }
            for r in (reqs or [])
        ],
        "problem_description": prob_def.problem_description if prob_def else "",
        "business_context_text": prob_def.business_context if prob_def else "",
    }

    # ── §3 Architecture Vision (TOGAF Phase A) ───────────────────────────
    arch_vision = {
        "title": "3. Architecture Vision",
        "empty_state": _EMPTY,
        "content": {
            "adm_phase": solution.adm_phase or "A",
            "scope_description": solution.scope_description or "",
            "scope_in": solution.scope_in or "",
            "scope_out": solution.scope_out or "",
            "business_value": solution.business_value or "",
            "target_outcomes": solution.target_outcomes or [],
            "success_metrics": solution.success_metrics or [],
            "planned_start_date": solution.planned_start_date.isoformat() if solution.planned_start_date else "",
            "planned_end_date": solution.planned_end_date.isoformat() if solution.planned_end_date else "",
            "affected_systems": solution.affected_systems or "",
        },
    }

    # ── §4 Business Architecture ─────────────────────────────────────────
    biz_elements = _safe(lambda: _query_archimate_by_layer(solution_id, "Business"), default=[])
    business_arch = {
        "title": "4. Business Architecture",
        "empty_state": _EMPTY,
        "elements": biz_elements,
    }

    # ── §5 Application Architecture ──────────────────────────────────────
    app_elements = _safe(lambda: _query_archimate_by_layer(solution_id, "Application"), default=[])
    application_arch = {
        "title": "5. Application Architecture",
        "empty_state": _EMPTY,
        "elements": app_elements,
    }

    # ── §6 Technology Architecture ───────────────────────────────────────
    tech_elements = _safe(lambda: _query_archimate_by_layer(solution_id, "Technology"), default=[])
    technology_arch = {
        "title": "6. Technology Architecture",
        "empty_state": _EMPTY,
        "elements": tech_elements,
    }

    # ── §7 Cross-layer Traceability ──────────────────────────────────────
    chain = _safe(lambda: get_traceability_chain(solution_id), default={})
    traceability = {
        "title": "7. Cross-layer Traceability",
        "empty_state": _EMPTY,
        "layers": {
            "stakeholders": (chain or {}).get("stakeholders", []),
            "drivers": (chain or {}).get("drivers", []),
            "goals": (chain or {}).get("goals", []),
            "requirements": (chain or {}).get("requirements", []),
            "capabilities": (chain or {}).get("capabilities", []),
            "processes": (chain or {}).get("processes", []),
            "applications": (chain or {}).get("applications", []),
            "technology": (chain or {}).get("technology", []),
        },
    }

    # ── §8 Gap Analysis ──────────────────────────────────────────────────
    constraints = _safe(lambda: SolutionConstraint.query.filter_by(
        problem_id=prob_def.id
    ).limit(50).all() if prob_def else [], default=[])

    gap_analysis = {
        "title": "8. Gap Analysis",
        "empty_state": _EMPTY,
        "constraints": [
            {
                "name": c.name,
                "description": c.description or "",
                "type": c.constraint_type.value if c.constraint_type else "",
                "severity": c.severity,
                "value": c.value or "",
            }
            for c in (constraints or [])
        ],
        "scope_in": solution.scope_in or "",
        "scope_out": solution.scope_out or "",
    }

    # ── §9 Implementation Roadmap ────────────────────────────────────────
    roadmap = _safe(lambda: _query_roadmap(solution_id), default=[])
    implementation_roadmap = {
        "title": "9. Implementation Roadmap",
        "empty_state": _EMPTY,
        "work_packages": roadmap or [],
    }

    # ── §10 Risks and Constraints ────────────────────────────────────────
    risks = _safe(lambda: _query_risks(solution_id), default=[])
    risks_constraints = {
        "title": "10. Risks and Constraints",
        "empty_state": _EMPTY,
        "risks": risks or [],
    }

    return {
        "solution": {
            "id": solution.id,
            "name": solution.name,
            "status": solution.status,
        },
        "sections": [
            exec_summary,
            business_context,
            arch_vision,
            business_arch,
            application_arch,
            technology_arch,
            traceability,
            gap_analysis,
            implementation_roadmap,
            risks_constraints,
        ],
    }


def get_sad_html(solution_id: int) -> str:
    """Render the SAD dict to an HTML string using Jinja2 render_template_string."""
    sad = generate_sad(solution_id)
    template_path = "solutions/sad_document.html"
    try:
        from flask import render_template
        return render_template(template_path, sad=sad, standalone=True)
    except Exception as exc:  # noqa: BLE001
        logger.error("SAD HTML render failed: %s", exc)
        # A Jinja render failure can echo back template variable content
        # (including solution data) in its exception message -- escape
        # before it reaches this HTML error page.
        return f"<html><body><h1>SAD generation error</h1><pre>{escape(str(exc))}</pre></body></html>"


# ---------------------------------------------------------------------------
# Private query helpers
# ---------------------------------------------------------------------------

def _query_archimate_by_layer(solution_id: int, layer: str) -> list:
    """Return ArchiMate elements for a solution scoped to a layer."""
    from sqlalchemy import text
    # solution_elements has no organization_id, so the join proves nothing about
    # ownership. archimate_elements does (populated on every row) — scope there.
    _org_clause, _org_params = org_scope(prefix="ae.")
    rows = db.session.execute(text(
        "SELECT ae.id, ae.name, ae.type, ae.layer, ae.description "
        "FROM archimate_elements ae "
        "JOIN solution_elements se ON se.archimate_element_id = ae.id "
        "WHERE se.solution_id = :sid AND LOWER(ae.layer) = LOWER(:layer)"
        + _org_clause +
        " ORDER BY ae.name LIMIT 200"
    ), {"sid": solution_id, "layer": layer, **_org_params}).fetchall()
    return [
        {"id": r[0], "name": r[1], "type": r[2] or "", "layer": r[3] or "", "description": r[4] or ""}
        for r in rows
    ]


def _query_roadmap(solution_id: int) -> list:
    """Return work packages / kanban cards linked to the solution."""
    from sqlalchemy import text
    # kanban_cards has an organization_id column but it is NULL on every row
    # (added nullable by reconcile-schema, never backfilled), so a predicate
    # there would return nothing. Scope through solutions, which is populated.
    _org_clause, _org_params = org_scope(prefix="s.")
    rows = _safe(lambda: db.session.execute(text(
        "SELECT kc.id, kc.title, kc.status, kc.priority "
        "FROM kanban_cards kc "
        "JOIN solutions s ON s.id = kc.solution_id "
        "WHERE kc.solution_id = :sid" + _org_clause +
        " ORDER BY kc.priority, kc.id LIMIT 100"
    ), {"sid": solution_id, **_org_params}).fetchall(), default=[])
    return [
        {"id": r[0], "title": r[1], "status": r[2] or "", "priority": r[3] or ""}
        for r in (rows or [])
    ]


def _query_risks(solution_id: int) -> list:
    """Return risk snapshots linked to the solution."""
    from sqlalchemy import text
    _org_clause, _org_params = org_scope()
    rows = _safe(lambda: db.session.execute(text(
        # Column names checked against the live table, not guessed: this query
        # asked for risk_description/likelihood/status, none of which exist here
        # (they are notes/probability/mitigation_status). All three raised
        # UndefinedColumn, _safe swallowed it, and every narrative rendered an
        # empty risk register.
        "SELECT id, risk_name, notes, probability, impact, mitigation_status "
        "FROM solution_risk_snapshots WHERE solution_id = :sid" + _org_clause +
        " ORDER BY id LIMIT 100"
    ), {"sid": solution_id, **_org_params}).fetchall(), default=[])
    return [
        {
            "id": r[0], "name": r[1], "description": r[2] or "",
            "likelihood": r[3] or "", "impact": r[4] or "", "status": r[5] or "",
        }
        for r in (rows or [])
    ]


def _empty_sad(solution_id: int) -> dict:
    """Return an empty SAD structure when the solution is not found."""
    return {
        "solution": {"id": solution_id, "name": "Unknown", "status": ""},
        "sections": [
            {
                "title": f"{i}. Section",
                "empty_state": _EMPTY,
                "content": {},
            }
            for i in range(1, 11)
        ],
    }

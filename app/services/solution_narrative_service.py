"""SA-004: Solution Architecture Document (SAD) narrative generation service.

Generates a structured SAD dict from real DB data, and renders it to HTML.
All 10 sections degrade gracefully to empty_state strings if no data exists.
"""

import logging
from html import escape

from app import db

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

    # Motivation modelled on the design itself (the common path: no analysis
    # session) is read from the solution's linked elements.
    motivation = _safe(lambda: _linked_motivation(solution_id), default={}) or {}

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
        ] + motivation.get("drivers", []),
        "goals": [
            {
                "name": g.name,
                "description": g.description or "",
                "priority": g.priority,
            }
            for g in (goals or [])
        ] + motivation.get("goals", []),
        "requirements": [
            {
                "name": r.name,
                "description": r.description or "",
                "type": r.requirement_type.value if r.requirement_type else "",
                "priority": r.priority,
                "mandatory": r.is_mandatory,
            }
            for r in (reqs or [])
        ] + motivation.get("requirements", []),
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

    from app.services.solution_design_score_service import compute as design_score

    return {
        "solution": {
            "id": solution.id,
            "name": solution.name,
            "status": solution.status,
        },
        "design_score": design_score(solution).to_dict(),
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
    """The solution's linked design elements in one layer.

    Reads solution_archimate_elements, the link every writer uses, through the
    design score service. It used to read solution_elements, which nothing
    writes, so every architecture section of the document was empty.
    """
    from app.services.solution_design_score_service import linked_elements

    wanted = layer.lower()
    return sorted(
        (
            {"id": e.element_id, "name": e.name, "type": e.type, "layer": e.layer, "description": ""}
            for e in linked_elements(solution_id) if e.layer == wanted
        ),
        key=lambda d: d["name"].lower(),
    )


def _linked_motivation(solution_id: int) -> dict:
    """Drivers, goals and requirements linked to the solution's design."""
    from app.services.solution_design_score_service import linked_elements

    out = {"drivers": [], "goals": [], "requirements": []}
    for e in linked_elements(solution_id):
        if e.type == "Driver":
            out["drivers"].append({"name": e.name, "description": "", "type": "", "impact": ""})
        elif e.type in ("Goal", "Outcome"):
            out["goals"].append({"name": e.name, "description": "", "priority": ""})
        elif e.type in ("Requirement", "Constraint"):
            out["requirements"].append({"name": e.name, "description": "", "type": e.type,
                                        "priority": "", "mandatory": e.type == "Constraint"})
    return out


_ROADMAP_TYPES = ("WorkPackage", "Plateau", "Deliverable", "ImplementationEvent")


def _query_roadmap(solution_id: int) -> list:
    """The work packages, plateaus and events linked to the solution's design.

    Read from the design's links, not from kanban cards: the roadmap of a
    solution is its implementation and migration elements.
    """
    from app.services.solution_design_score_service import linked_elements

    rows = [e for e in linked_elements(solution_id) if e.type in _ROADMAP_TYPES]
    return [
        {"id": e.element_id, "title": e.name, "status": e.type, "priority": ""}
        for e in sorted(rows, key=lambda e: (_ROADMAP_TYPES.index(e.type), e.name.lower()))
    ]


def _query_risks(solution_id: int) -> list:
    """The solution's risks from the risk register (and any not yet merged)."""
    from app.services.solution_design_score_service import solution_risks

    return [
        {
            "id": r["id"], "name": r["title"], "description": r["owner"] and ("Owner: " + r["owner"]) or "",
            "likelihood": r["likelihood"] or "", "impact": r["impact"] or "", "status": r["status"] or "",
        }
        for r in solution_risks(solution_id)
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

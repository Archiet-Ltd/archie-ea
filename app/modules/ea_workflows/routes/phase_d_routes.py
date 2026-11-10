"""TD-005: Phase D Technology Architecture API Routes.

Endpoints exposing Phase D analysis data: technology stack audit, per-application
technology debt scores, infrastructure complexity matrix, and workflow orchestration
for the ADM_PHASE_D_TECH workflow.
"""

import logging

from flask import Blueprint, jsonify
from flask_login import current_user, login_required

logger = logging.getLogger(__name__)

phase_d_bp = Blueprint("phase_d", __name__)

_PHASE_D_WORKFLOW_CODE = "ADM_PHASE_D_TECH"


# ---------------------------------------------------------------------------
# GET /api/ea/phase-d/stack-audit
# ---------------------------------------------------------------------------


@phase_d_bp.route("/api/ea/phase-d/stack-audit", methods=["GET"])
@login_required
def stack_audit():
    """Return a full technology stack audit of the application portfolio.

    Response 200::

        {"audit": {hosting_breakdown, apps_with_node_assignment, ...}}
    """
    try:
        from app.services.technology_stack_audit_service import TechnologyStackAuditService

        audit = TechnologyStackAuditService().audit_portfolio()
        return jsonify({"audit": audit}), 200
    except Exception as exc:
        logger.error("stack-audit error: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to compute stack audit", "detail": str(exc)}), 500


# ---------------------------------------------------------------------------
# GET /api/ea/phase-d/debt-scores
# ---------------------------------------------------------------------------


def _risk_tier(score: float) -> str:
    if score >= 75:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 25:
        return "medium"
    return "low"


_DEBT_INPUTS = ("end-of-life date", "version", "support status")


def _debt_factors(result):
    """Plain names for the inputs that raised an application's debt score."""
    factors = []
    eol = result.get("eol_score", 0)
    if eol >= 100:
        factors.append("End of life has passed")
    elif eol >= 75:
        factors.append("End of life within 12 months")
    elif eol >= 50:
        factors.append("End of life within 24 months")
    if result.get("version_score", 0) > 0:
        factors.append("Old major version")
    support = result.get("support_status_score", 0)
    if support >= 80:
        factors.append("Unsupported by the vendor")
    elif support > 0:
        factors.append("Limited vendor support")
    return factors


@phase_d_bp.route("/api/ea/phase-d/debt-scores", methods=["GET"])
@login_required
def debt_scores():
    """Return technology debt scores for every ApplicationComponent.

    Response 200::

        {
            "scores": [{"app_id": int, "app_name": str, "debt_score": float, "risk_tier": str}],
            "summary": {"critical": int, "high": int, "medium": int, "low": int}
        }
    """
    try:
        from app.models.application_portfolio import ApplicationComponent
        from app.services.technology_roadmap_service import TechnologyRoadmapService

        apps = ApplicationComponent.query.all()
        svc = TechnologyRoadmapService()
        scores = []
        summary = {"critical": 0, "high": 0, "medium": 0, "low": 0, "not_rated": 0}
        for app in apps:
            result = svc.score_technology_debt(app.id) or {}
            missing = result.get("missing_data")
            if not result or missing is None or len(missing) == len(_DEBT_INPUTS):
                # Nothing to measure: not rated, never a confident 0.
                summary["not_rated"] += 1
                scores.append(
                    {
                        "app_id": app.id,
                        "app_name": app.name,
                        "debt_score": None,
                        "risk_tier": None,
                        "factors": [],
                        "reason": "Not rated: no " + ", ".join(_DEBT_INPUTS) + " recorded",
                    }
                )
                continue
            debt_score = result.get("debt_score", 0)
            tier = _risk_tier(float(debt_score))
            summary[tier] += 1
            scores.append(
                {
                    "app_id": app.id,
                    "app_name": app.name,
                    "debt_score": debt_score,
                    "risk_tier": tier,
                    "factors": _debt_factors(result),
                    "reason": None,
                }
            )
        return jsonify({"scores": scores, "summary": summary}), 200
    except Exception as exc:
        logger.error("debt-scores error: %s", exc, exc_info=True)
        return jsonify({"error": "Could not compute the debt scores. Try again in a moment."}), 500


# ---------------------------------------------------------------------------
# GET /api/ea/phase-d/complexity-matrix
# ---------------------------------------------------------------------------


@phase_d_bp.route("/api/ea/phase-d/complexity-matrix", methods=["GET"])
@login_required
def complexity_matrix():
    """Return infrastructure complexity matrix for the application portfolio.

    Response 200::

        {"matrix": [...]}
    """
    try:
        from app.services.infrastructure_complexity_service import (
            InfrastructureComplexityService,
        )

        matrix = InfrastructureComplexityService().compute_complexity_matrix()
        return jsonify({"matrix": matrix}), 200
    except Exception as exc:
        logger.error("complexity-matrix error: %s", exc, exc_info=True)
        # 500, not 200. An empty matrix at 200 reads as "no infrastructure
        # complexity anywhere", which on a Phase D screen is a finding rather
        # than an outage, and the client could not tell the difference.
        return jsonify({"error": "Could not compute the complexity matrix"}), 500


# ---------------------------------------------------------------------------
# POST /api/ea/phase-d/run-phase-d
# ---------------------------------------------------------------------------


@phase_d_bp.route("/api/ea/phase-d/run-phase-d", methods=["POST"])
@login_required
def run_phase_d():
    """Create and queue a new Phase D Technology Architecture workflow instance.

    Response 201::

        {"instance_id": int, "status": "queued"}
    """
    try:
        from app.services.ea_workflow_engine import EAWorkflowEngine

        engine = EAWorkflowEngine()
        user_id = current_user.id if current_user.is_authenticated else None
        instance = engine.start_workflow(
            workflow_code=_PHASE_D_WORKFLOW_CODE,
            context={},
            triggered_by="api",
            user_id=user_id,
        )
        return jsonify({"instance_id": instance.id, "status": "queued"}), 201
    except ValueError as exc:
        logger.warning("run-phase-d: %s", exc)
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        logger.error("run-phase-d unexpected error: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to start Phase D workflow", "detail": str(exc)}), 500


@phase_d_bp.route("/api/ea/phase-d/viewpoint", methods=["GET"])
@login_required
def get_phase_d_viewpoint():
    """Return live ArchiMate viewpoint for Phase D (Technology Architecture).

    Response 200: {"phase": "D", "viewpoint": {elements_by_layer, relationships,
                   element_count, relationship_count, layer_summary}}
    """
    from app.services.phase_viewpoint_binding_service import PhaseViewpointBindingService
    from app.services.workflow_archimate_context_service import WorkflowArchiMateContextService
    from app.services.archimate_viewpoint_render_service import ArchimateViewpointRenderService
    phase_code = "ADM_PHASE_D_TECH"
    elements = WorkflowArchiMateContextService().get_phase_elements(phase_code)
    element_ids = [e["id"] for e in elements]
    viewpoint = ArchimateViewpointRenderService().render_viewpoint(phase_code, element_ids)
    viewpoint["viewpoint_name"] = PhaseViewpointBindingService().get_viewpoint_name(phase_code)
    return jsonify({"phase": "D", "viewpoint": viewpoint}), 200

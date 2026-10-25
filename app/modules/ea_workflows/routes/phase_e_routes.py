"""OA-005: Phase E Opportunities & Solutions API Routes.

Endpoints for TOGAF ADM Phase E — Opportunities and Solutions.
All endpoints require authentication and are read-only (GET) except
POST /run-phase-e which creates a workflow instance.
"""
import logging

from flask import Blueprint, jsonify  # dead-code-ok
from flask_login import current_user, login_required
from sqlalchemy import desc

from app import db

logger = logging.getLogger(__name__)

phase_e_bp = Blueprint("phase_e", __name__, url_prefix="/api/ea/phase-e")

# ---------------------------------------------------------------------------
# GET /api/ea/phase-e/gap-register
# ---------------------------------------------------------------------------


@phase_e_bp.route("/gap-register", methods=["GET"])
@login_required
def gap_register():
    """Return the unified gap register across all TOGAF ADM source tables.

    Response 200::

        {
            "gaps": [...],
            "total": int,
            "by_severity": {"critical": int, "high": int, "medium": int, "low": int}
        }
    """
    try:
        from app.services.gap_register_service import get_unified_gap_register

        gaps = get_unified_gap_register()
        by_severity = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for gap in gaps:
            sev = (gap.get("severity") or "").lower()
            if sev in by_severity:
                by_severity[sev] += 1
        return jsonify({"gaps": gaps, "total": len(gaps), "by_severity": by_severity}), 200
    except Exception as exc:
        logger.error("gap-register error: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to load gap register", "detail": str(exc)}), 500


# ---------------------------------------------------------------------------
# GET /api/ea/phase-e/solution-options/<int:gap_analysis_id>
# ---------------------------------------------------------------------------


@phase_e_bp.route("/solution-options/<int:gap_analysis_id>", methods=["GET"])
@login_required
def solution_options(gap_analysis_id: int):
    """Return scored solution options for the given gap analysis.

    Response 200::

        {"options": [...], "gap_analysis_id": int}

    Response 404 when gap_analysis_id does not match any analysis.
    """
    try:
        from app.models.capability_gap_analysis import CapabilityGapAnalysis
        from app.services.solution_options_scoring_service import SolutionOptionsScoringService

        analysis = db.session.get(CapabilityGapAnalysis, gap_analysis_id)
        if analysis is None:
            return jsonify({"error": "Gap analysis not found", "gap_analysis_id": gap_analysis_id}), 404

        svc = SolutionOptionsScoringService()
        options = svc.score_options(gap_analysis_id)
        return jsonify({"options": options, "gap_analysis_id": gap_analysis_id}), 200
    except Exception as exc:
        logger.error("solution-options error: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to score solution options", "detail": str(exc)}), 500


# ---------------------------------------------------------------------------
# GET /api/ea/phase-e/initiative-shortlist
# ---------------------------------------------------------------------------


@phase_e_bp.route("/initiative-shortlist", methods=["GET"])
@login_required
def initiative_shortlist():
    """Return the top-10 solution options ranked by prioritisation score.

    Response 200::

        {"initiatives": [{id, name, prioritisation_score, feasibility_score}], "count": int}
    """
    try:
        from app.middleware.tenant_context import current_org_id
        from app.models.capability_gap_analysis import (
            CapabilityGapAnalysis,
            CapabilityGapDetail,
            GapSolutionOption,
        )

        # GapSolutionOption has no tenant column; it belongs to an organisation
        # through its gap detail's analysis.
        rows = (
            GapSolutionOption.query
            .join(CapabilityGapDetail, GapSolutionOption.gap_detail_id == CapabilityGapDetail.id)
            .join(CapabilityGapAnalysis, CapabilityGapDetail.analysis_id == CapabilityGapAnalysis.id)
            .filter(CapabilityGapAnalysis.organization_id == current_org_id())
            .order_by(desc(GapSolutionOption.prioritisation_score))
            .limit(10)
            .all()
        )
        initiatives = [
            {
                "id": row.id,
                "name": row.solution_name,
                "prioritisation_score": row.prioritisation_score,
                "feasibility_score": row.feasibility_score,
            }
            for row in rows
        ]
        return jsonify({"initiatives": initiatives, "count": len(initiatives)}), 200
    except Exception as exc:
        logger.error("initiative-shortlist error: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to load initiative shortlist", "detail": str(exc)}), 500


# ---------------------------------------------------------------------------
# POST /api/ea/phase-e/run-phase-e
# ---------------------------------------------------------------------------

_PHASE_E_WORKFLOW_CODE = "ADM_PHASE_E_OPPORTUNITIES"


@phase_e_bp.route("/run-phase-e", methods=["POST"])
@login_required
def run_phase_e():
    """Create and queue a new Phase E Opportunities & Solutions workflow instance.

    Response 201::

        {"instance_id": int, "status": "queued"}
    """
    try:
        from app.services.ea_workflow_engine import EAWorkflowEngine

        engine = EAWorkflowEngine()
        user_id = current_user.id if current_user.is_authenticated else None
        instance = engine.start_workflow(
            workflow_code=_PHASE_E_WORKFLOW_CODE,
            context={},
            triggered_by="api",
            user_id=user_id,
        )
        return jsonify({"instance_id": instance.id, "status": "queued"}), 201
    except ValueError as exc:
        logger.warning("run-phase-e: %s", exc)
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        logger.error("run-phase-e unexpected error: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to start Phase E workflow", "detail": str(exc)}), 500


@phase_e_bp.route("/viewpoint", methods=["GET"])
@login_required
def get_phase_e_viewpoint():
    """Return live ArchiMate viewpoint for Phase E (Opportunities & Solutions)."""
    from app.services.phase_viewpoint_binding_service import PhaseViewpointBindingService
    from app.services.workflow_archimate_context_service import WorkflowArchiMateContextService
    from app.services.archimate_viewpoint_render_service import ArchimateViewpointRenderService
    phase_code = "ADM_PHASE_E_OPPORTUNITIES"
    elements = WorkflowArchiMateContextService().get_phase_elements(phase_code)
    element_ids = [e["id"] for e in elements]
    viewpoint = ArchimateViewpointRenderService().render_viewpoint(phase_code, element_ids)
    viewpoint["viewpoint_name"] = PhaseViewpointBindingService().get_viewpoint_name(phase_code)
    return jsonify({"phase": "E", "viewpoint": viewpoint}), 200

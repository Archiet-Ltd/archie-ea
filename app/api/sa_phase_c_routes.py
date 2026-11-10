"""
SA Phase C — Information Systems Architecture API Routes

Exposes Phase C analysis endpoints (disposition matrix, application patterns,
integration topology) and workflow orchestration for the
INFORMATION_SYSTEMS_ARCHITECTURE_PHASE_C workflow.
"""
import logging

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app import db
from app.models.workflow_models import EAWorkflowInstance

logger = logging.getLogger(__name__)

sa_phase_c_bp = Blueprint("sa_phase_c", __name__, url_prefix="/api/ea-workflows/sa")

# ---------------------------------------------------------------------------
# GET /api/ea-workflows/sa/disposition-matrix
# ---------------------------------------------------------------------------


@sa_phase_c_bp.route("/disposition-matrix", methods=["GET"])
@login_required
def get_disposition_matrix():
    """Return application disposition matrix from RationalizationScoringService."""
    try:
        from app.services.rationalization_scoring_service import (
            RationalizationScoringService,
        )

        svc = RationalizationScoringService()
        matrix = svc.compute_disposition_matrix(scope_app_ids=[])
        return jsonify({"matrix": matrix}), 200
    except Exception as exc:
        logger.error("disposition-matrix error: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to compute disposition matrix", "detail": str(exc)}), 500


# ---------------------------------------------------------------------------
# GET /api/ea-workflows/sa/application-patterns
# ---------------------------------------------------------------------------


# A person waiting on this address gets an answer or a plain error within this
# many seconds; the model call is bounded by LLMService's own timeout parameter.
PATTERNS_LLM_TIMEOUT_SECONDS = 4


@sa_phase_c_bp.route("/application-patterns", methods=["GET"])
@login_required
def get_application_patterns():
    """Return application pattern classifications from ApplicationPatternClassifierService."""
    try:
        from app.services.application_pattern_classifier_service import (
            ApplicationPatternClassifierService,
            LLMClassificationTimeoutError,
        )

        svc = ApplicationPatternClassifierService()
        # A large portfolio is ~19 sequential LLM calls at batch_size=50, which
        # stalled this request for 10+ minutes. Bound the wall clock; apps not
        # reached in time get the deterministic rule engine, and the response
        # reports the split (patterns.by_source / patterns.llm_truncated).
        patterns = svc.classify_portfolio(
            time_budget_seconds=60, llm_timeout_seconds=PATTERNS_LLM_TIMEOUT_SECONDS
        )
        return jsonify({"patterns": patterns}), 200
    except LLMClassificationTimeoutError as exc:
        logger.error("application-patterns timed out: %s", exc)
        return jsonify({
            "error": "The AI provider did not answer in time. Try again in a moment.",
        }), 504
    except Exception as exc:
        logger.error("application-patterns error: %s", exc, exc_info=True)
        return jsonify({"error": "Could not classify the portfolio. Try again in a moment."}), 500


# ---------------------------------------------------------------------------
# GET /api/ea-workflows/sa/integration-topology
# ---------------------------------------------------------------------------


@sa_phase_c_bp.route("/integration-topology", methods=["GET"])
@login_required
def get_integration_topology():
    """Return integration topology analysis from IntegrationPatternRecommenderService."""
    try:
        from app.services.integration_pattern_recommender_service import (
            IntegrationPatternRecommenderService,
        )

        svc = IntegrationPatternRecommenderService()
        topology = svc.analyse_integration_topology()
        return jsonify({"topology": topology}), 200
    except Exception as exc:
        logger.error("integration-topology error: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to analyse integration topology", "detail": str(exc)}), 500


# ---------------------------------------------------------------------------
# GET /api/ea-workflows/sa/phase-d-requirements/<int:phase_c_instance_id>
# ---------------------------------------------------------------------------

_PHASE_C_WORKFLOW_CODE = "INFORMATION_SYSTEMS_ARCHITECTURE_PHASE_C"


@sa_phase_c_bp.route("/phase-d-requirements/<int:phase_c_instance_id>", methods=["GET"])
@login_required
def get_phase_d_requirements(phase_c_instance_id: int):
    """
    Return Phase D technology requirements derived from a completed Phase C instance.

    Returns 404 when the instance does not exist or is not a Phase C workflow.
    """
    instance = db.session.get(EAWorkflowInstance, phase_c_instance_id)
    if instance is None:
        return jsonify({"error": "Phase C workflow instance not found", "instance_id": phase_c_instance_id}), 404

    defn = instance.definition
    is_phase_c = (
        defn is not None
        and (
            (defn.workflow_code == _PHASE_C_WORKFLOW_CODE)
            or (defn.adm_phase == "C")
        )
    )
    if not is_phase_c:
        return jsonify({
            "error": "Workflow instance is not a Phase C Information Systems Architecture workflow",
            "instance_id": phase_c_instance_id,
        }), 404

    context = instance.context or {}
    requirements = {
        "instance_id": instance.id,
        "instance_code": instance.instance_code,
        "status": instance.status,
        "phase_c_outputs": context.get("phase_c_outputs", {}),
        "application_patterns": context.get("application_patterns", {}),
        "integration_topology": context.get("integration_topology", {}),
        "disposition_matrix": context.get("disposition_matrix", []),
        "technology_requirements": context.get("technology_requirements", {}),
        "derived_at": instance.completed_at.isoformat() if instance.completed_at else None,
    }
    return jsonify(requirements), 200


# ---------------------------------------------------------------------------
# POST /api/ea-workflows/sa/run-phase-c
# ---------------------------------------------------------------------------


@sa_phase_c_bp.route("/run-phase-c", methods=["POST"])
@login_required
def run_phase_c():
    """
    Create and queue a new Phase C Information Systems Architecture workflow instance.

    Request JSON::

        {
            "instance_name": str,
            "linked_phase_b_instance_id": int   # optional
        }

    Returns::

        {"instance_id": int, "status": "queued"}  — 201
    """
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400

    body = request.get_json(silent=True) or {}
    instance_name = body.get("instance_name")
    if not instance_name or not str(instance_name).strip():
        return jsonify({"error": "instance_name is required"}), 400

    linked_phase_b_instance_id = body.get("linked_phase_b_instance_id")

    try:
        from app.services.ea_workflow_engine import EAWorkflowEngine

        engine = EAWorkflowEngine()

        context = {
            "instance_name": str(instance_name).strip(),
        }
        if linked_phase_b_instance_id is not None:
            context["linked_phase_b_instance_id"] = linked_phase_b_instance_id

        user_id = current_user.id if current_user.is_authenticated else None

        instance = engine.start_workflow(
            workflow_code=_PHASE_C_WORKFLOW_CODE,
            context=context,
            triggered_by="api",
            user_id=user_id,
        )

        return jsonify({"instance_id": instance.id, "status": "queued"}), 201

    except ValueError as exc:
        # Workflow definition not found in engine
        logger.warning("run-phase-c: %s", exc)
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        logger.error("run-phase-c unexpected error: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to start Phase C workflow", "detail": str(exc)}), 500


@sa_phase_c_bp.route("/viewpoint", methods=["GET"])
@login_required
def get_phase_c_viewpoint():
    """Return live ArchiMate viewpoint for Phase C (IS Architecture).

    Response 200: {"phase": "C", "viewpoint": {elements_by_layer, relationships,
                   element_count, relationship_count, layer_summary}}
    """
    from app.services.phase_viewpoint_binding_service import PhaseViewpointBindingService
    from app.services.workflow_archimate_context_service import WorkflowArchiMateContextService
    from app.services.archimate_viewpoint_render_service import ArchimateViewpointRenderService
    phase_code = "ADM_PHASE_C_IS"
    elements = WorkflowArchiMateContextService().get_phase_elements(phase_code)
    element_ids = [e["id"] for e in elements]
    viewpoint = ArchimateViewpointRenderService().render_viewpoint(phase_code, element_ids)
    viewpoint["viewpoint_name"] = PhaseViewpointBindingService().get_viewpoint_name(phase_code)
    return jsonify({"phase": "C", "viewpoint": viewpoint}), 200

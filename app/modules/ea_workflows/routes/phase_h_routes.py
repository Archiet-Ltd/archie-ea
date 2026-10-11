"""CM-005: Phase H Architecture Change Management API Routes.

Endpoints for Phase H data consumption: change request registry,
ArchiMate graph impact assessment, and ADM cycle trigger classification.

All endpoints require authentication (login_required).
ORM only — zero raw SQL.
"""

import logging
import uuid

from flask import Blueprint, jsonify, request
from flask_login import login_required

from app import db
from app.models.architecture_review_board import ChangeRequest
from app.services.architecture_change_impact_service import (
    ArchitectureChangeImpactService,
)
from app.utils.route_guards import load_entity

logger = logging.getLogger(__name__)

phase_h_bp = Blueprint("phase_h", __name__, url_prefix="/api/ea/phase-h")

# Allowed values for change_type / trigger_type
_ALLOWED_CHANGE_TYPES = {
    "simplification",
    "exception",
    "business_change",
    "technology_change",
    "correction",
    "governance_change",
    "major",
}


@phase_h_bp.route("/change-requests", methods=["GET"])
@login_required
def list_change_requests():
    """Return all ChangeRequest rows.

    Response:
    {
        "change_requests": [
            {"id": 1, "title": "...", "status": "...", "trigger_type": "...", "scope_app_count": 0},
            ...
        ],
        "total": <int>
    }
    """
    try:
        rows = ChangeRequest.query.all()
        return (
            jsonify(
                {
                    "change_requests": [
                        {
                            "id": cr.id,
                            "title": cr.title,
                            "status": cr.status,
                            "trigger_type": cr.change_type,
                            # scope_app_ids not stored in current model schema
                            "scope_app_count": 0,
                        }
                        for cr in rows
                    ],
                    "total": len(rows),
                }
            ),
            200,
        )
    except Exception as exc:
        logger.error("list_change_requests error: %s", exc, exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@phase_h_bp.route("/change-requests", methods=["POST"])
@login_required
def create_change_request():
    """Create a new ChangeRequest.

    Request JSON:
    {
        "title": str,
        "description": str,
        "trigger_type": str,   -- mapped to change_type
        "scope_app_ids": []    -- informational; not persisted (no column in model)
    }

    Response: {"id": int, "title": str, "status": "pending"}, 201
    """
    data = request.get_json(silent=True) or {}

    title = data.get("title", "").strip()
    description = data.get("description", "").strip()
    trigger_type = data.get("trigger_type", "").strip()

    # Validation
    if not title:
        return jsonify({"error": "title is required"}), 400
    if not description:
        return jsonify({"error": "description is required"}), 400
    if not trigger_type:
        return jsonify({"error": "trigger_type is required"}), 400
    if trigger_type not in _ALLOWED_CHANGE_TYPES:
        return (
            jsonify(
                {
                    "error": f"trigger_type must be one of: {sorted(_ALLOWED_CHANGE_TYPES)}",
                }
            ),
            400,
        )

    try:
        cr_number = f"CR-{uuid.uuid4().hex[:8].upper()}"
        cr = ChangeRequest(
            change_request_number=cr_number,
            title=title,
            description=description,
            change_type=trigger_type,
            status="pending",
        )
        db.session.add(cr)
        db.session.commit()
        return jsonify({"id": cr.id, "title": cr.title, "status": cr.status}), 201
    except Exception as exc:
        db.session.rollback()
        logger.error("create_change_request error: %s", exc, exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@phase_h_bp.route("/impact-assessment/<int:change_request_id>", methods=["GET"])
@login_required
def impact_assessment(change_request_id):
    """Assess the blast radius of a ChangeRequest via ArchiMate graph traversal.

    Returns the impact dict from ArchitectureChangeImpactService.
    404 if change_request_id does not exist.
    """
    # Not db.session.get() -- it does not reliably run the tenant
    # with_loader_criteria filter (see load_entity's own docstring).
    cr = load_entity(ChangeRequest, change_request_id)
    if cr is None:
        return jsonify({"error": "ChangeRequest not found"}), 404

    try:
        impact = ArchitectureChangeImpactService().assess_change_impact(
            change_request_id
        )
        return jsonify(impact), 200
    except Exception as exc:
        logger.error(
            "impact_assessment(%s) error: %s", change_request_id, exc, exc_info=True
        )
        return jsonify({"error": "Internal server error"}), 500


@phase_h_bp.route("/adm-triggers", methods=["GET"])
@login_required
def adm_triggers():
    """Classify recent changes as new ADM cycle triggers vs. continuations.

    Analyses the last 30 ChangeRequest rows.
    Criteria for new_cycle: change_type == 'major'.

    Response:
    {
        "new_cycle_triggers": int,
        "continuation_changes": int,
        "total_analyzed": int
    }
    """
    try:
        recent = (
            ChangeRequest.query.order_by(ChangeRequest.created_at.desc()).limit(30).all()
        )
        new_cycle = sum(1 for cr in recent if cr.change_type == "major")
        continuation = len(recent) - new_cycle
        return (
            jsonify(
                {
                    "new_cycle_triggers": new_cycle,
                    "continuation_changes": continuation,
                    "total_analyzed": len(recent),
                }
            ),
            200,
        )
    except Exception as exc:
        logger.error("adm_triggers error: %s", exc, exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@phase_h_bp.route("/viewpoint", methods=["GET"])
@login_required
def get_phase_h_viewpoint():
    """Return live ArchiMate viewpoint for Phase H (Architecture Change Management)."""
    from app.services.phase_viewpoint_binding_service import PhaseViewpointBindingService
    from app.services.workflow_archimate_context_service import WorkflowArchiMateContextService
    from app.services.archimate_viewpoint_render_service import ArchimateViewpointRenderService
    phase_code = "ADM_PHASE_H_CHANGE"
    elements = WorkflowArchiMateContextService().get_phase_elements(phase_code)
    element_ids = [e["id"] for e in elements]
    viewpoint = ArchimateViewpointRenderService().render_viewpoint(phase_code, element_ids)
    viewpoint["viewpoint_name"] = PhaseViewpointBindingService().get_viewpoint_name(phase_code)
    return jsonify({"phase": "H", "viewpoint": viewpoint}), 200

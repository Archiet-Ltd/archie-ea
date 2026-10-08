"""
DEPRECATED: This file is migrated to app/modules/architecture/.
Registration is now centralized via app.modules.architecture.register().
Do NOT modify -- kept as fallback until Phase 6 cleanup.

ARB Workflow API Routes

Provides REST API endpoints for enhanced ARB workflow capabilities
including compliance checking, conditions management, and decision workflow.

Endpoints:
- POST /api/arb-workflow/<id>/compliance-check - Run compliance checks
- POST /api/arb-workflow/<id>/conditional-approval - Create conditional approval
- POST /api/arb-workflow/conditions/<id>/fulfill - Fulfill a condition
- POST /api/arb-workflow/conditions/<id>/waive - Waive a condition
- GET /api/arb-workflow/conditions/summary - Get conditions summary
- GET /api/arb-workflow/pending-by-phase - Get pending reviews by TOGAF phase
- GET /api/arb-workflow/compliance-dashboard - Get compliance dashboard

Solution Lifecycle Transition Endpoints (ENH-020):
- POST /api/arb-workflow/solutions/<id>/begin-review  - submitted/arb_review → under_review
- POST /api/arb-workflow/solutions/<id>/approve       - under_review → approved
- POST /api/arb-workflow/solutions/<id>/reject        - under_review → rejected
- POST /api/arb-workflow/solutions/<id>/withdraw      - any → withdrawn
- GET  /api/arb-workflow/solutions/<id>/lifecycle     - get current lifecycle state
"""

from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app.decorators import audit_log, require_roles
from app.extensions import db
from app.middleware.tenant_decorators import is_active_org_admin
from app.models.user import Permission
from app.modules.transformation_room.arb_decision_adapter import (
    TypedARBDecisionAdapter,
)
from app.services.arb_workflow_service import ARBWorkflowService

arb_workflow_bp = Blueprint("arb_workflow", __name__, url_prefix="/api/arb-workflow")


def _enforce_legacy_roles(*roles):
    """Apply a historical role gate only after a request is proven legacy."""

    @require_roles(*roles)
    def allowed():
        return None

    return allowed()


def _arb_write_floor_denial():
    """Retain the authenticated ARB write floor before canonical authority."""
    if current_user.can(Permission.GENERAL):
        return None
    return jsonify({
        "success": False,
        "error": "Your role does not have write access to ARB governance.",
        "code": "PERMISSION_DENIED",
    }), 403


@arb_workflow_bp.route("/<int:review_item_id>/compliance-check", methods=["POST"])
@login_required
@require_roles("admin", "enterprise_architect", "architect")
@audit_log("arb_compliance_check")
def run_compliance_check(review_item_id: int):
    """
    Run automated compliance checks on a review item.

    Request Body (optional):
        {
            "check_types": ["archimate", "pattern", "standards", "security"]
        }

    Returns:
        JSON with compliance check results
    """
    data = request.get_json() or {}
    check_types = data.get("check_types")

    try:
        service = ARBWorkflowService()
        results = service.run_compliance_checks(
            review_item_id=review_item_id, check_types=check_types
        )

        if "error" in results:
            return jsonify({"success": False, "error": results["error"]}), 404

        return jsonify({"success": True, "data": results})
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_workflow_bp.route("/<int:review_item_id>/conditional-approval", methods=["POST"])
@login_required
@audit_log("arb_conditional_approval")
def create_conditional_approval(review_item_id: int):
    """
    Create a conditional approval for a review item.

    Request Body:
        {
            "conditions": [
                {
                    "description": "Complete security review",
                    "category": "security",
                    "due_date": "2026 - 03 - 01"
                },
                ...
            ],
            "approval_notes": "Optional approval notes"
        }

    Returns:
        JSON with approval result and condition details
    """
    data = request.get_json(silent=True) or {}
    typed_result = TypedARBDecisionAdapter.decide_review_from_request(
        review_item_id=review_item_id,
        payload=data,
        outcome="approved_with_conditions",
    )
    if typed_result.typed:
        if not typed_result.success:
            return jsonify({
                "success": False,
                "reason_codes": typed_result.reason_codes,
            }), typed_result.http_status
        return jsonify({
            "success": True,
            "data": {
                "review_cycle_id": typed_result.review_cycle_id,
                "review_item_id": typed_result.review_item_id,
                "decision_event_id": typed_result.decision_event_id,
                "condition_ids": typed_result.condition_ids,
                "status": typed_result.status,
                "outcome": typed_result.outcome,
                "conditions": typed_result.conditions,
                "idempotent": typed_result.idempotent,
            },
        })

    _enforce_legacy_roles("admin", "enterprise_architect", "architect")

    if not data or "conditions" not in data:
        return jsonify({"success": False, "error": "conditions array is required"}), 400

    conditions = data["conditions"]
    if not conditions:
        return jsonify({"success": False, "error": "At least one condition is required"}), 400

    # Validate each condition has required fields
    for idx, cond in enumerate(conditions):
        if "description" not in cond:
            return (
                jsonify({"success": False, "error": f"Condition {idx + 1} missing description"}),
                400,
            )
        if "due_date" not in cond:
            return (
                jsonify({"success": False, "error": f"Condition {idx + 1} missing due_date"}),
                400,
            )

    try:
        service = ARBWorkflowService()
        result = service.create_conditional_approval(
            review_item_id=review_item_id,
            conditions=conditions,
            approved_by_id=current_user.id,
            approval_notes=data.get("approval_notes"),
        )

        if "error" in result:
            return jsonify({"success": False, "error": result["error"]}), 404

        return jsonify({"success": True, "data": result})
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_workflow_bp.route("/conditions/<int:condition_id>/fulfill", methods=["POST"])
@login_required
@audit_log("arb_condition_fulfill")
def fulfill_condition(condition_id: int):
    """
    Mark a condition as fulfilled with evidence.

    Request Body:
        {
            "evidence": "Description of how condition was met",
            "evidence_url": "https://link-to-evidence-document.com" (optional)
        }

    Returns:
        JSON with fulfillment result
    """
    data = request.get_json(silent=True) or {}
    typed_result = TypedARBDecisionAdapter.fulfill_condition_from_request(
        condition_id=condition_id,
        payload=data,
    )
    if typed_result.typed:
        if not typed_result.success:
            return jsonify({
                "success": False,
                "reason_codes": typed_result.reason_codes,
            }), typed_result.http_status
        return jsonify({"success": True, "data": typed_result.data})

    _enforce_legacy_roles("admin", "enterprise_architect", "architect")

    if not data or "evidence" not in data:
        return jsonify({"success": False, "error": "evidence is required"}), 400
    if not TypedARBDecisionAdapter.legacy_condition_matches_request(
        condition_id=condition_id
    ):
        return jsonify({
            "success": False,
            "error": f"Condition {condition_id} not found",
        }), 404

    try:
        service = ARBWorkflowService()
        result = service.fulfill_condition(
            condition_id=condition_id,
            fulfilled_by_id=current_user.id,
            evidence=data["evidence"],
            evidence_url=data.get("evidence_url"),
        )

        if "error" in result:
            return jsonify({"success": False, "error": result["error"]}), 404

        return jsonify({"success": True, "data": result})
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_workflow_bp.route("/conditions/<int:condition_id>/waive", methods=["POST"])
@login_required
@audit_log("arb_condition_waive")
def waive_condition(condition_id: int):
    """
    Waive a condition with justification.

    Request Body:
        {
            "reason": "Justification for waiving the condition"
        }

    Returns:
        JSON with waiver result
    """
    data = request.get_json(silent=True) or {}
    typed_result = TypedARBDecisionAdapter.waive_condition_from_request(
        condition_id=condition_id,
        payload=data,
    )
    if typed_result.typed:
        if not typed_result.success:
            return jsonify({
                "success": False,
                "reason_codes": typed_result.reason_codes,
            }), typed_result.http_status
        return jsonify({"success": True, "data": typed_result.data})

    # Keep the legacy endpoint's historical role gate after typed resolution.
    # Typed authority is checked by the canonical service against locked rows.
    _enforce_legacy_roles("admin", "enterprise_architect")

    if not data or "reason" not in data:
        return jsonify({"success": False, "error": "reason is required"}), 400
    if not TypedARBDecisionAdapter.legacy_condition_matches_request(
        condition_id=condition_id
    ):
        return jsonify({
            "success": False,
            "error": f"Condition {condition_id} not found",
        }), 404

    try:
        service = ARBWorkflowService()
        result = service.waive_condition(
            condition_id=condition_id, waived_by_id=current_user.id, reason=data["reason"]
        )

        if "error" in result:
            return jsonify({"success": False, "error": result["error"]}), 404

        return jsonify({"success": True, "data": result})
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_workflow_bp.route("/conditions/summary", methods=["GET"])
@login_required
def get_conditions_summary():
    """
    Get summary of conditions and their status.

    Query Parameters:
        review_item_id (int): Optional review item ID to filter

    Returns:
        JSON with conditions summary including overdue and due soon
    """
    review_item_id = request.args.get("review_item_id", type=int)

    try:
        service = ARBWorkflowService()
        summary = service.get_conditions_summary(review_item_id=review_item_id)

        return jsonify({"success": True, "data": summary})
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_workflow_bp.route("/pending-by-phase", methods=["GET"])
@login_required
def get_pending_by_phase():
    """
    Get pending review items grouped by TOGAF ADM phase.

    Returns:
        JSON with pending reviews organized by phase
    """
    try:
        service = ARBWorkflowService()
        by_phase = service.get_pending_reviews_by_phase()

        return jsonify({"success": True, "data": by_phase})
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_workflow_bp.route("/compliance-dashboard", methods=["GET"])
@login_required
def get_compliance_dashboard():
    """
    Get compliance dashboard metrics.

    Returns:
        JSON with compliance check statistics
    """
    try:
        service = ARBWorkflowService()
        dashboard = service.get_compliance_dashboard()

        return jsonify({"success": True, "data": dashboard})
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_workflow_bp.route("/health", methods=["GET"])
@login_required
def health_check():
    """
    Health check endpoint for the ARB Workflow service.

    Returns:
        JSON with service status
    """
    return jsonify(
        {
            "success": True,
            "service": "arb-workflow",
            "status": "healthy",
            "endpoints": [
                "POST /api/arb-workflow/<id>/compliance-check",
                "POST /api/arb-workflow/<id>/conditional-approval",
                "POST /api/arb-workflow/conditions/<id>/fulfill",
                "POST /api/arb-workflow/conditions/<id>/waive",
                "GET /api/arb-workflow/conditions/summary",
                "GET /api/arb-workflow/pending-by-phase",
                "GET /api/arb-workflow/compliance-dashboard",
                # Stage management endpoints
                "GET /api/arb-workflow/stages",
                "POST /api/arb-workflow/stages",
                "GET /api/arb-workflow/stages/<id>",
                "PUT /api/arb-workflow/stages/<id>",
                "DELETE /api/arb-workflow/stages/<id>",
                "POST /api/arb-workflow/stages/init",
                "PUT /api/arb-workflow/stages/reorder",
                # Stage transition endpoints
                "GET /api/arb-workflow/<id>/available-transitions",
                "POST /api/arb-workflow/<id>/transition",
                "POST /api/arb-workflow/<id>/validate-transition",
                # Kanban board endpoints
                "GET /api/arb-workflow/kanban",
                "GET /api/arb-workflow/stages/analytics",
            ],
        }
    )


# =============================================================================
# WORKFLOW STAGE MANAGEMENT ENDPOINTS (P5 Enhancement)
# =============================================================================


@arb_workflow_bp.route("/stages", methods=["GET"])
@login_required
def get_stages():
    """
    Get all workflow stages.

    Query Parameters:
        include_inactive (bool): Include inactive stages (default: false)

    Returns:
        JSON with list of stages
    """
    include_inactive = request.args.get("include_inactive", "false").lower() == "true"

    try:
        service = ARBWorkflowService()
        stages = service.get_all_stages(include_inactive=include_inactive)

        return jsonify({"success": True, "data": stages})
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_workflow_bp.route("/stages", methods=["POST"])
@login_required
@require_roles("admin")
@audit_log("arb_stage_create")
def create_stage():
    """
    Create a new workflow stage.

    Request Body:
        {
            "name": "Stage Name",
            "code": "stage_code",
            "order": 5,
            "description": "Stage description",
            "is_initial": false,
            "is_terminal": false,
            "color": "#3B82F6",
            "icon": "clock",
            "required_approvers": 1,
            "approver_roles": ["enterprise_architect"],
            "gate_conditions": [...],
            "allowed_transitions": ["next_stage"],
            "sla_hours": 48
        }

    Returns:
        JSON with created stage
    """
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "Request body required"}), 400

    required_fields = ["name", "code", "order"]
    for field in required_fields:
        if field not in data:
            return jsonify({"success": False, "error": f"{field} is required"}), 400

    try:
        service = ARBWorkflowService()
        result = service.create_stage(
            name=data["name"],
            code=data["code"],
            order=data["order"],
            created_by_id=current_user.id,
            description=data.get("description"),
            is_initial=data.get("is_initial", False),
            is_terminal=data.get("is_terminal", False),
            color=data.get("color", "#6B7280"),
            icon=data.get("icon"),
            required_approvers=data.get("required_approvers", 0),
            approver_roles=data.get("approver_roles"),
            gate_conditions=data.get("gate_conditions"),
            allowed_transitions=data.get("allowed_transitions"),
            sla_hours=data.get("sla_hours"),
            notify_on_enter=data.get("notify_on_enter"),
        )

        if "error" in result:
            return jsonify({"success": False, "error": result["error"]}), 400

        return jsonify({"success": True, "data": result["stage"]}), 201
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_workflow_bp.route("/stages/<int:stage_id>", methods=["GET"])
@login_required
def get_stage(stage_id: int):
    """
    Get a specific workflow stage.

    Returns:
        JSON with stage details
    """
    try:
        service = ARBWorkflowService()
        stage = service.get_stage(stage_id)

        if not stage:
            return jsonify({"success": False, "error": f"Stage {stage_id} not found"}), 404

        return jsonify({"success": True, "data": stage})
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_workflow_bp.route("/stages/<int:stage_id>", methods=["PUT"])
@login_required
@require_roles("admin")
@audit_log("arb_stage_update")
def update_stage(stage_id: int):
    """
    Update a workflow stage.

    Request Body:
        Any stage fields to update

    Returns:
        JSON with updated stage
    """
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "Request body required"}), 400

    try:
        service = ARBWorkflowService()
        result = service.update_stage(stage_id, **data)

        if "error" in result:
            return (
                jsonify({"success": False, "error": result["error"]}),
                404 if "not found" in result["error"] else 400,
            )

        return jsonify({"success": True, "data": result["stage"]})
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_workflow_bp.route("/stages/<int:stage_id>", methods=["DELETE"])
@login_required
@require_roles("admin")
@audit_log("arb_stage_delete")
def delete_stage(stage_id: int):
    """
    Delete (deactivate) a workflow stage.

    Returns:
        JSON with deletion result
    """
    try:
        service = ARBWorkflowService()
        result = service.delete_stage(stage_id)

        if "error" in result:
            return (
                jsonify({"success": False, "error": result["error"]}),
                404 if "not found" in result["error"] else 400,
            )

        return jsonify({"success": True, "message": result["message"]})
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_workflow_bp.route("/stages/init", methods=["POST"])
@login_required
@require_roles("admin")
@audit_log("arb_stages_init")
def init_stages():
    """
    Initialize default workflow stages.

    Returns:
        JSON with initialization result
    """
    try:
        service = ARBWorkflowService()
        result = service.initialize_workflow_stages()

        return jsonify({"success": True, "data": result})
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_workflow_bp.route("/stages/reorder", methods=["PUT"])
@login_required
@require_roles("admin")
@audit_log("arb_stages_reorder")
def reorder_stages():
    """
    Reorder workflow stages.

    Request Body:
        {
            "stage_order": [1, 3, 2, 4, 5]  // List of stage IDs in new order
        }

    Returns:
        JSON with reordered stages
    """
    data = request.get_json()
    if not data or "stage_order" not in data:
        return jsonify({"success": False, "error": "stage_order array is required"}), 400

    try:
        service = ARBWorkflowService()
        result = service.reorder_stages(data["stage_order"])

        return jsonify({"success": True, "data": result})
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# STAGE TRANSITION ENDPOINTS
# =============================================================================


@arb_workflow_bp.route("/<int:review_item_id>/available-transitions", methods=["GET"])
@login_required
def get_available_transitions(review_item_id: int):
    """
    Get available stage transitions for a review item.

    Returns:
        JSON with list of available transitions
    """
    try:
        service = ARBWorkflowService()
        result = service.get_available_transitions(review_item_id)

        if "error" in result:
            return jsonify({"success": False, "error": result["error"]}), 404

        return jsonify({"success": True, "data": result})
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_workflow_bp.route("/<int:review_item_id>/validate-transition", methods=["POST"])
@login_required
def validate_transition(review_item_id: int):
    """
    Validate if a stage transition is allowed.

    Request Body:
        {
            "target_stage": "under_review"
        }

    Returns:
        JSON with validation result
    """
    data = request.get_json()
    if not data or "target_stage" not in data:
        return jsonify({"success": False, "error": "target_stage is required"}), 400

    try:
        service = ARBWorkflowService()
        result = service.validate_stage_transition(
            review_item_id=review_item_id, target_stage_code=data["target_stage"]
        )

        return jsonify({"success": True, "data": result})
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_workflow_bp.route("/<int:review_item_id>/transition", methods=["POST"])
@login_required
@audit_log("arb_stage_transition")
def transition_stage(review_item_id: int):
    """
    Transition a review item to a new stage.

    Request Body:
        {
            "target_stage": "under_review",
            "notes": "Optional transition notes",
            "force": false  // Skip validation (admin only)
        }

    Returns:
        JSON with transition result
    """
    data = request.get_json(silent=True) or {}
    if not data or "target_stage" not in data:
        return jsonify({"success": False, "error": "target_stage is required"}), 400

    typed_result = TypedARBDecisionAdapter.transition_review_from_request(
        review_item_id=review_item_id,
        payload=data,
    )
    if typed_result.typed:
        if not typed_result.success:
            return jsonify({
                "success": False,
                "reason_codes": typed_result.reason_codes,
            }), typed_result.http_status
        return jsonify({
            "success": True,
            "data": {
                "review_cycle_id": typed_result.review_cycle_id,
                "review_item_id": typed_result.review_item_id,
                "decision_event_id": typed_result.decision_event_id,
                "condition_ids": typed_result.condition_ids,
                "status": typed_result.status,
                "outcome": typed_result.outcome,
                "conditions": typed_result.conditions,
                "idempotent": typed_result.idempotent,
            },
        })

    # Force transition requires admin (in the active organisation)
    force = data.get("force", False)
    if force and not is_active_org_admin():
        return (
            jsonify({"success": False, "error": "Force transition requires admin privileges"}),
            403,
        )

    try:
        service = ARBWorkflowService()
        result = service.transition_stage(
            review_item_id=review_item_id,
            target_stage_code=data["target_stage"],
            transitioned_by_id=current_user.id,
            notes=data.get("notes"),
            force=force,
        )

        if not result.get("success"):
            return (
                jsonify(
                    {
                        "success": False,
                        "error": result.get("error"),
                        "validation": result.get("validation"),
                    }
                ),
                400,
            )

        return jsonify({"success": True, "data": result})
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# KANBAN BOARD ENDPOINTS
# =============================================================================


@arb_workflow_bp.route("/kanban", methods=["GET"])
@login_required
def get_kanban_board():
    """
    Get Kanban board data for ARB workflow visualization.

    Query Parameters:
        arb_session_id (int): Filter by ARB session
        include_all (bool): Include items from all sessions/states

    Returns:
        JSON with Kanban board data structure
    """
    arb_session_id = request.args.get("arb_session_id", type=int)
    include_all = request.args.get("include_all", "false").lower() == "true"

    try:
        service = ARBWorkflowService()
        board_data = service.get_kanban_board_data(
            arb_session_id=arb_session_id, include_all=include_all
        )

        return jsonify({"success": True, "data": board_data})
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_workflow_bp.route("/stages/analytics", methods=["GET"])
@login_required
def get_stage_analytics():
    """
    Get analytics for workflow stages.

    Returns:
        JSON with stage analytics
    """
    try:
        service = ARBWorkflowService()
        analytics = service.get_stage_analytics()

        return jsonify({"success": True, "data": analytics})
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# SOLUTION ARB LIFECYCLE TRANSITION ENDPOINTS  (ENH-020)
# These operate on Solution.governance_status directly.
# =============================================================================

# Valid transitions: from_status → set of allowed next statuses
_LIFECYCLE_TRANSITIONS = {
    "draft":        {"arb_review"},
    "proposed":     {"arb_review"},
    "arb_submitted": {"under_review"},
    "arb_review":   {"under_review"},
    "under_review": {"approved", "rejected"},
    # withdrawn is available from any non-terminal status (enforced in route)
}
_TERMINAL_STATUSES = {"approved", "rejected", "withdrawn"}
_REVIEW_ROLES = ("admin", "enterprise_architect", "architect")


def _get_solution_or_404(solution_id: int):
    """Resolve a solution with an explicit (id, organization_id) predicate.

    ``Query.get()`` is tenant-scoped only on an identity-map miss, so it is
    not a tenancy boundary (AGENTS.md). A solution in another tenant must be
    indistinguishable from a missing one.
    """
    from flask import abort, g

    from app.models.solution_models import Solution

    organization_id = getattr(g, "current_org_id", None)
    if not isinstance(organization_id, int) or organization_id <= 0:
        abort(404)
    solution = db.session.execute(
        db.select(Solution).where(
            Solution.id == solution_id,
            Solution.organization_id == organization_id,
        )
    ).scalar_one_or_none()
    if solution is None:
        abort(404)
    return solution


def _typed_cycle_for_solution(solution_id: int, *, open_only: bool):
    """Return the typed ARB cycle governing this solution, if any."""
    from app.modules.architecture.routes.arb_routes import TypedARBDecisionAdapter

    if open_only:
        return TypedARBDecisionAdapter.open_typed_cycle_for_solution(solution_id)
    cycles = TypedARBDecisionAdapter.typed_cycles_for_solution(solution_id)
    return cycles[0] if cycles else None


def _typed_lifecycle_blocked(reason_code: str, message: str):
    """Refuse a lifecycle write that no typed command exposes."""
    import uuid

    return jsonify({
        "success": False,
        "error": message,
        "reason_codes": [reason_code],
        "missing_evidence": [],
        "request_id": str(uuid.uuid4()),
    }), 409


def _typed_decision_response(result, solution_id: int, *, legacy_fields=None):
    """Typed decision result in this endpoint's established envelope."""
    if not result.success:
        return jsonify(result.failure_payload()), result.http_status
    payload = {
        "success": True,
        # governance_status stays a projection of the recorded decision event;
        # this route no longer writes Solution.governance_status for a typed
        # cycle.
        "governance_status": result.status,
        "solution_id": solution_id,
        **(legacy_fields or {}),
        **result.success_fields(),
    }
    return jsonify(payload), 200


@arb_workflow_bp.route("/solutions/<int:solution_id>/lifecycle", methods=["GET"])
@login_required
def get_solution_lifecycle(solution_id: int):
    """
    Return the current lifecycle state and available transitions for a solution.
    """
    typed_result = TypedARBDecisionAdapter.current_solution_lifecycle_from_request(
        solution_id=solution_id
    )
    if typed_result.typed:
        if not typed_result.success:
            return jsonify({
                "success": False,
                "reason_codes": typed_result.reason_codes,
            }), typed_result.http_status
        return jsonify({"success": True, **typed_result.data})

    solution = _get_solution_or_404(solution_id)
    status = solution.governance_status or "draft"
    allowed_next = list(_LIFECYCLE_TRANSITIONS.get(status, set()))
    can_withdraw = status not in _TERMINAL_STATUSES

    return jsonify({
        "success": True,
        "governance_status": status,
        "allowed_transitions": allowed_next,
        "can_withdraw": can_withdraw,
        "arb_submission_date": solution.arb_submission_date.isoformat() if solution.arb_submission_date else None,
        "arb_approval_date": solution.arb_approval_date.isoformat() if solution.arb_approval_date else None,
        "arb_rejection_reason": solution.arb_rejection_reason,
    })


@arb_workflow_bp.route("/solutions/<int:solution_id>/begin-review", methods=["POST"])
@login_required
@audit_log("arb_begin_review")
def begin_arb_review(solution_id: int):
    """
    Transition a solution from arb_review/arb_submitted → under_review.

    Request Body (optional):
        { "notes": "Review notes" }
    """
    from app.modules.transformation_room.arb_decision_adapter import (
        TypedARBDecisionAdapter,
    )

    if TypedARBDecisionAdapter.solution_has_typed_cycle(solution_id):
        denial = _arb_write_floor_denial()
        if denial is not None:
            return denial
        typed_result = TypedARBDecisionAdapter.begin_current_solution_from_request(
            solution_id=solution_id
        )
        if not typed_result.success:
            return jsonify({
                "success": False,
                "reason_codes": typed_result.reason_codes,
            }), typed_result.http_status
        return jsonify({
            "success": True,
            "message": "ARB review started",
            "governance_status": typed_result.status,
            "review_cycle_id": typed_result.review_cycle_id,
            "review_item_id": typed_result.review_item_id,
            "idempotent": typed_result.idempotent,
        })

    _enforce_legacy_roles(*_REVIEW_ROLES)
    solution = _get_solution_or_404(solution_id)
    if _typed_cycle_for_solution(solution_id, open_only=True) is not None:
        return _typed_lifecycle_blocked(
            "typed_cycle_status_not_client_mutable",
            "This solution is governed by a typed ARB cycle. Its review state "
            "is derived from recorded commands and cannot be set directly.",
        )
    status = solution.governance_status or "draft"

    if status not in ("arb_review", "arb_submitted", "proposed"):
        return jsonify({
            "success": False,
            "error": f"Cannot begin review from status '{status}'. "
                     "Solution must be in arb_review or arb_submitted state.",
        }), 409

    data = request.get_json() or {}
    try:
        solution.governance_status = "under_review"
        _log_arb_event(solution, "under_review", current_user.id, data.get("notes", ""))
        db.session.commit()
        return jsonify({
            "success": True,
            "message": "ARB review started",
            "governance_status": solution.governance_status,
        })
    except Exception:
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_workflow_bp.route("/solutions/<int:solution_id>/approve", methods=["POST"])
@login_required
@audit_log("arb_approve_solution")
def approve_solution(solution_id: int):
    """
    Approve a solution that is under_review.

    Request Body (optional):
        { "notes": "Approval notes" }
    """
    from app.modules.transformation_room.arb_decision_adapter import (
        TypedARBDecisionAdapter,
    )

    data = request.get_json(silent=True) or {}
    if TypedARBDecisionAdapter.solution_has_typed_cycle(solution_id):
        denial = _arb_write_floor_denial()
        if denial is not None:
            return denial
        typed_result = TypedARBDecisionAdapter.decide_current_solution_from_request(
            solution_id=solution_id,
            payload=data,
            outcome="approved",
        )
        if not typed_result.success:
            return jsonify({
                "success": False,
                "reason_codes": typed_result.reason_codes,
            }), typed_result.http_status
        return jsonify({
            "success": True,
            "message": "Solution approved",
            "governance_status": typed_result.status,
            "review_cycle_id": typed_result.review_cycle_id,
            "review_item_id": typed_result.review_item_id,
            "decision_event_id": typed_result.decision_event_id,
            "idempotent": typed_result.idempotent,
        })

    _enforce_legacy_roles(*_REVIEW_ROLES)
    solution = _get_solution_or_404(solution_id)
    typed_cycle = _typed_cycle_for_solution(solution_id, open_only=True)
    if typed_cycle is not None:
        from app.modules.architecture.routes.arb_routes import (
            TypedARBDecisionAdapter,
        )

        payload = request.get_json(silent=True) or {}
        result = TypedARBDecisionAdapter.decide(
            cycle=typed_cycle,
            outcome="approved",
            rationale=payload.get("notes") or payload.get("rationale"),
            conditions=None,
        )
        return _typed_decision_response(
            result, solution_id, legacy_fields={"message": "Solution approved"}
        )
    status = solution.governance_status or "draft"

    if status != "under_review":
        return jsonify({
            "success": False,
            "error": f"Cannot approve from status '{status}'. Solution must be under_review.",
        }), 409

    data = request.get_json() or {}
    try:
        solution.governance_status = "approved"
        solution.arb_approval_date = datetime.utcnow()
        solution.arb_rejection_reason = None
        _log_arb_event(solution, "approved", current_user.id, data.get("notes", ""))
        db.session.commit()

        # COM-017: send Teams notification after ARB approval
        try:
            from app.services.m365_service import M365Service

            svc = M365Service()
            m365_cfg = svc._get_config()
            if m365_cfg:
                webhook_url = (m365_cfg.config or {}).get("teams_webhook_url", "")
                notes = data.get("notes", "")
                svc.send_teams_notification(
                    org_id=getattr(solution, "org_id", None),
                    channel_webhook_url=webhook_url,
                    title=f"ARB Decision: {solution.name}",
                    message=f"Status: Approved.{' Conditions: ' + notes if notes else ''}",
                    action_url=f"/solutions/{solution_id}",
                )
        except Exception:
            import logging as _log
            _log.getLogger(__name__).exception(
                "COM-017: Teams notification failed for solution %s", solution_id
            )

        return jsonify({
            "success": True,
            "message": "Solution approved",
            "governance_status": solution.governance_status,
            "arb_approval_date": solution.arb_approval_date.isoformat(),
        })
    except Exception:
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_workflow_bp.route("/solutions/<int:solution_id>/reject", methods=["POST"])
@login_required
@audit_log("arb_reject_solution")
def reject_solution(solution_id: int):
    """
    Reject a solution that is under_review.

    Request Body:
        { "reason": "Rejection reason" (required), "notes": "Additional notes" }
    """
    from app.modules.transformation_room.arb_decision_adapter import (
        TypedARBDecisionAdapter,
    )

    data = request.get_json(silent=True) or {}
    if TypedARBDecisionAdapter.solution_has_typed_cycle(solution_id):
        denial = _arb_write_floor_denial()
        if denial is not None:
            return denial
        typed_result = TypedARBDecisionAdapter.decide_current_solution_from_request(
            solution_id=solution_id,
            payload=data,
            outcome="rejected",
        )
        if not typed_result.success:
            return jsonify({
                "success": False,
                "reason_codes": typed_result.reason_codes,
            }), typed_result.http_status
        return jsonify({
            "success": True,
            "message": "Solution rejected",
            "governance_status": typed_result.status,
            "arb_rejection_reason": data.get("reason"),
            "review_cycle_id": typed_result.review_cycle_id,
            "review_item_id": typed_result.review_item_id,
            "decision_event_id": typed_result.decision_event_id,
            "idempotent": typed_result.idempotent,
        })

    _enforce_legacy_roles(*_REVIEW_ROLES)
    solution = _get_solution_or_404(solution_id)
    typed_cycle = _typed_cycle_for_solution(solution_id, open_only=True)
    if typed_cycle is not None:
        from app.modules.architecture.routes.arb_routes import (
            TypedARBDecisionAdapter,
        )

        payload = request.get_json(silent=True) or {}
        rejection_reason = (payload.get("reason") or "").strip()
        result = TypedARBDecisionAdapter.decide(
            cycle=typed_cycle,
            outcome="rejected",
            rationale=rejection_reason or None,
            conditions=None,
        )
        return _typed_decision_response(
            result,
            solution_id,
            legacy_fields={
                "message": "Solution rejected",
                "arb_rejection_reason": rejection_reason,
            },
        )
    status = solution.governance_status or "draft"

    if status != "under_review":
        return jsonify({
            "success": False,
            "error": f"Cannot reject from status '{status}'. Solution must be under_review.",
        }), 409

    data = request.get_json() or {}
    reason = (data.get("reason") or "").strip()
    if not reason:
        return jsonify({"success": False, "error": "reason is required to reject a solution"}), 400

    try:
        solution.governance_status = "rejected"
        solution.arb_rejection_reason = reason
        _log_arb_event(solution, "rejected", current_user.id, reason)
        db.session.commit()
        return jsonify({
            "success": True,
            "message": "Solution rejected",
            "governance_status": solution.governance_status,
            "arb_rejection_reason": solution.arb_rejection_reason,
        })
    except Exception:
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_workflow_bp.route("/solutions/<int:solution_id>/withdraw", methods=["POST"])
@login_required
@audit_log("arb_withdraw_solution")
def withdraw_solution(solution_id: int):
    """
    Withdraw a solution from ARB consideration (owner or admin only).
    Available from any non-terminal status.

    Request Body (optional):
        { "reason": "Withdrawal reason" }
    """
    from app.modules.transformation_room.arb_decision_adapter import (
        TypedARBDecisionAdapter,
    )

    if TypedARBDecisionAdapter.solution_has_typed_cycle(solution_id):
        return jsonify({
            "success": False,
            "reason_codes": ["typed_withdraw_not_supported"],
        }), 409

    solution = _get_solution_or_404(solution_id)
    if _typed_cycle_for_solution(solution_id, open_only=True) is not None:
        # Withdraw has no typed command with defined audit semantics, and a
        # typed decision history must not be silently abandoned.
        return _typed_lifecycle_blocked(
            "typed_cycle_withdraw_not_supported",
            "This solution is governed by an open typed ARB cycle. Withdrawal "
            "is not an available typed command; return the review instead.",
        )
    status = solution.governance_status or "draft"

    if status in _TERMINAL_STATUSES:
        return jsonify({
            "success": False,
            "error": f"Cannot withdraw a solution with status '{status}'.",
        }), 409

    is_owner = (
        solution.created_by_id == current_user.id
        or (solution.solution_owner and solution.solution_owner.lower() == current_user.email.lower())
    )
    if not is_owner and not is_active_org_admin():
        return jsonify({
            "success": False,
            "error": "Only the solution owner or an admin can withdraw a solution.",
        }), 403

    data = request.get_json() or {}
    try:
        solution.governance_status = "withdrawn"
        _log_arb_event(solution, "withdrawn", current_user.id, data.get("reason", ""))
        db.session.commit()
        return jsonify({
            "success": True,
            "message": "Solution withdrawn",
            "governance_status": solution.governance_status,
        })
    except Exception:
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ---------------------------------------------------------------------------
# Internal helper – write to SolutionEvent if the table exists, else no-op
# ---------------------------------------------------------------------------

def _log_arb_event(solution, new_status: str, user_id: int, notes: str) -> None:
    """
    Append a governance status-change event to the solution event log if
    the SolutionEvent model is available (graceful degradation).
    """
    try:
        from app.models.solution_events import SolutionEvent  # optional model
        event = SolutionEvent(
            solution_id=solution.id,
            event_type="governance_status_change",
            payload={
                "old_status": solution.governance_status,
                "new_status": new_status,
                "notes": notes,
            },
            created_by_id=user_id,
            created_at=datetime.utcnow(),
        )
        db.session.add(event)
    except (ImportError, Exception):
        # SolutionEvent table may not exist; skip logging silently
        pass

"""
DEPRECATED: This file is migrated to app/modules/solutions_strategic/.
Registration is now centralized via app.modules.solutions_strategic.register().
Do NOT modify -- kept as fallback until Phase 6 cleanup.

Roadmap Builder API Routes

Provides REST API endpoints for managing implementation roadmaps
with work package dependencies and plateau modeling.

Endpoints:
- Work Packages:
  - GET /api/roadmap-builder/work-packages - List work packages
  - POST /api/roadmap-builder/work-packages - Create work package
  - GET /api/roadmap-builder/work-packages/<id> - Get work package
  - PUT /api/roadmap-builder/work-packages/<id> - Update work package
  - DELETE /api/roadmap-builder/work-packages/<id> - Delete work package
  - POST /api/roadmap-builder/work-packages/<id>/dependencies - Add dependency
  - DELETE /api/roadmap-builder/work-packages/<id>/dependencies/<dep_id> - Remove dependency

- Plateaus:
  - GET /api/roadmap-builder/plateaus - List plateaus
  - POST /api/roadmap-builder/plateaus - Create plateau
  - GET /api/roadmap-builder/plateaus/timeline - Get plateau timeline

- Analysis:
  - GET /api/roadmap-builder/dependency-graph - Get dependency graph for ReactFlow
  - GET /api/roadmap-builder/critical-path - Calculate critical path
  - GET /api/roadmap-builder/timeline - Get roadmap timeline
  - GET /api/roadmap-builder/summary - Get roadmap summary
"""

from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from app.decorators import WORK_PACKAGE_WRITE_ROLES, require_roles

from app.decorators import audit_log

from ..services.roadmap_builder_service import RoadmapBuilderService
from app.utils.pagination import safe_int_arg

roadmap_builder_bp = Blueprint("roadmap_builder", __name__, url_prefix="/api/roadmap-builder")


def _get_service() -> RoadmapBuilderService:
    """Get service instance."""
    return RoadmapBuilderService()


# =============================================================================
# Work Package Endpoints
# =============================================================================


@roadmap_builder_bp.route("/work-packages", methods=["GET"])
@login_required
def list_work_packages():
    """
    List work packages with optional filters.

    Query Parameters:
        status (str): Filter by status
        priority (str): Filter by priority
        limit (int): Maximum results (default: 100)
        offset (int): Pagination offset (default: 0)

    Returns:
        JSON list of work packages
    """
    status_filter = request.args.get("status")
    priority_filter = request.args.get("priority")
    limit = safe_int_arg('limit', 100, minimum=1, maximum=500)
    offset = safe_int_arg('offset', 0, minimum=0)

    service = _get_service()
    result = service.list_work_packages(
        status_filter=status_filter, priority_filter=priority_filter, limit=limit, offset=offset
    )

    return jsonify({"success": True, "data": result})


@roadmap_builder_bp.route("/work-packages", methods=["POST"])
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
@audit_log("create_work_package")
def create_work_package():
    """
    Create a new work package.

    Request Body:
        {
            "name": "Work Package Name",
            "description": "Description",
            "start_date": "2026 - 02 - 01",
            "end_date": "2026 - 03 - 01",
            "priority": "high",
            "status": "planned",
            "assigned_to": "Team A",
            "estimated_cost": 50000,
            "dependencies": [1, 2, 3],
            "capability_id": 5
        }

    Returns:
        JSON with created work package
    """
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "Request body is required"}), 400

    if "name" not in data:
        return jsonify({"success": False, "error": "name is required"}), 400

    # Parse dates
    start_date = None
    end_date = None
    try:
        if "start_date" in data and data["start_date"]:
            start_date = datetime.strptime(data["start_date"], "%Y-%m-%d").date()
        if "end_date" in data and data["end_date"]:
            end_date = datetime.strptime(data["end_date"], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "start_date and end_date must be in YYYY-MM-DD format"}), 400

    service = _get_service()
    result = service.create_work_package(
        name=data["name"],
        description=data.get("description"),
        start_date=start_date,
        end_date=end_date,
        priority=data.get("priority", "medium"),
        status=data.get("status", "planned"),
        assigned_to=data.get("assigned_to"),
        estimated_cost=data.get("estimated_cost", 0.0),
        dependencies=data.get("dependencies"),
        capability_id=data.get("capability_id"),
        created_by=current_user.username
        if hasattr(current_user, "username")
        else str(current_user.id),
    )

    if not result.get("success"):
        return jsonify(result), 500

    return jsonify({"success": True, "data": result}), 201


@roadmap_builder_bp.route("/work-packages/<int:work_package_id>", methods=["GET"])
@login_required
def get_work_package(work_package_id: int):
    """
    Get a work package by ID with dependency details.

    Path Parameters:
        work_package_id: ID of work package

    Returns:
        JSON with work package details
    """
    service = _get_service()
    result = service.get_work_package(work_package_id)

    if not result.get("success"):
        return jsonify(result), 404

    return jsonify({"success": True, "data": result})


@roadmap_builder_bp.route("/work-packages/<int:work_package_id>", methods=["PUT"])
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
@audit_log("update_work_package")
def update_work_package(work_package_id: int):
    """
    Update a work package.

    Path Parameters:
        work_package_id: ID of work package to update

    Request Body:
        Dict of fields to update

    Returns:
        JSON with updated work package
    """
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "Request body is required"}), 400

    # Parse dates if present
    try:
        if "start_date" in data and data["start_date"]:
            data["start_date"] = datetime.strptime(data["start_date"], "%Y-%m-%d").date()
        if "end_date" in data and data["end_date"]:
            data["end_date"] = datetime.strptime(data["end_date"], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "start_date and end_date must be in YYYY-MM-DD format"}), 400

    service = _get_service()
    result = service.update_work_package(work_package_id, data)

    if not result.get("success"):
        return jsonify(result), 404 if "not found" in result.get("error", "") else 500

    return jsonify({"success": True, "data": result})


@roadmap_builder_bp.route("/work-packages/<int:work_package_id>", methods=["DELETE"])
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
@audit_log("delete_work_package")
def delete_work_package(work_package_id: int):
    """
    Delete a work package.

    Path Parameters:
        work_package_id: ID of work package to delete

    Returns:
        JSON with deletion result
    """
    service = _get_service()
    result = service.delete_work_package(work_package_id)

    if not result.get("success"):
        return jsonify(result), 404

    return jsonify({"success": True, "data": result})


@roadmap_builder_bp.route("/work-packages/<int:work_package_id>/dependencies", methods=["POST"])
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
@audit_log("add_dependency")
def add_dependency(work_package_id: int):
    """
    Add a dependency to a work package.

    Path Parameters:
        work_package_id: ID of work package

    Request Body:
        {
            "depends_on_id": 5
        }

    Returns:
        JSON with result
    """
    data = request.get_json()
    if not data or "depends_on_id" not in data:
        return jsonify({"success": False, "error": "depends_on_id is required"}), 400

    service = _get_service()
    result = service.add_dependency(work_package_id, data["depends_on_id"])

    if not result.get("success"):
        # Another organisation's work package is indistinguishable from a missing one.
        return jsonify(result), 404 if "not found" in result.get("error", "") else 400

    return jsonify({"success": True, "data": result})


@roadmap_builder_bp.route(
    "/work-packages/<int:work_package_id>/dependencies/<int:dep_id>", methods=["DELETE"]
)
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
def remove_dependency(work_package_id: int, dep_id: int):
    """
    Remove a dependency from a work package.

    Path Parameters:
        work_package_id: ID of work package
        dep_id: ID of dependency to remove

    Returns:
        JSON with result
    """
    service = _get_service()
    result = service.remove_dependency(work_package_id, dep_id)

    if not result.get("success"):
        return jsonify(result), 404

    return jsonify({"success": True, "data": result})


# =============================================================================
# Plateau Endpoints
# =============================================================================


@roadmap_builder_bp.route("/plateaus", methods=["GET"])
@login_required
def list_plateaus():
    """
    List all plateaus in chronological order.

    Returns:
        JSON list of plateaus
    """
    service = _get_service()
    result = service.list_plateaus()

    return jsonify({"success": True, "data": result})


@roadmap_builder_bp.route("/plateaus", methods=["POST"])
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
@audit_log("create_plateau")
def create_plateau():
    """
    Create a new architecture plateau.

    Request Body:
        {
            "name": "Plateau Name",
            "plateau_type": "interim",
            "start_date": "2026 - 06 - 01",
            "end_date": "2026 - 12 - 01",
            "description": "Description",
            "business_value": "Expected value",
            "transition_from_id": 1
        }

    Returns:
        JSON with created plateau
    """
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "Request body is required"}), 400

    if "name" not in data:
        return jsonify({"success": False, "error": "name is required"}), 400

    # Parse dates
    start_date = None
    end_date = None
    try:
        if "start_date" in data and data["start_date"]:
            start_date = datetime.strptime(data["start_date"], "%Y-%m-%d").date()
        if "end_date" in data and data["end_date"]:
            end_date = datetime.strptime(data["end_date"], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "start_date and end_date must be in YYYY-MM-DD format"}), 400

    service = _get_service()
    result = service.create_plateau(
        name=data["name"],
        plateau_type=data.get("plateau_type", "interim"),
        start_date=start_date,
        end_date=end_date,
        description=data.get("description"),
        business_value=data.get("business_value"),
        transition_from_id=data.get("transition_from_id"),
        created_by=current_user.username
        if hasattr(current_user, "username")
        else str(current_user.id),
    )

    if not result.get("success"):
        return jsonify(result), 500

    return jsonify({"success": True, "data": result}), 201


@roadmap_builder_bp.route("/plateaus/timeline", methods=["GET"])
@login_required
def get_plateau_timeline():
    """
    Get plateau timeline for visualization.

    Returns:
        JSON with timeline data
    """
    service = _get_service()
    result = service.get_plateau_timeline()

    return jsonify({"success": True, "data": result})


# =============================================================================
# Analysis Endpoints
# =============================================================================


@roadmap_builder_bp.route("/dependency-graph", methods=["GET"])
@login_required
def get_dependency_graph():
    """
    Get dependency graph data for ReactFlow visualization.

    Query Parameters:
        include_plateaus (bool): Include plateau nodes (default: true)
        include_gaps (bool): Include gap nodes (default: false)
        status (str): Comma-separated status filter

    Returns:
        JSON with nodes and edges for ReactFlow
    """
    include_plateaus = request.args.get("include_plateaus", "true").lower() == "true"
    include_gaps = request.args.get("include_gaps", "false").lower() == "true"
    status_str = request.args.get("status", "")

    status_filter = None
    if status_str:
        status_filter = [s.strip() for s in status_str.split(",")]

    service = _get_service()
    result = service.generate_dependency_graph(
        include_plateaus=include_plateaus, include_gaps=include_gaps, status_filter=status_filter
    )

    return jsonify({"success": True, "data": result})


@roadmap_builder_bp.route("/critical-path", methods=["GET"])
@login_required
def get_critical_path():
    """
    Calculate critical path through work packages.

    Returns:
        JSON with critical path analysis
    """
    service = _get_service()
    result = service.calculate_critical_path()

    return jsonify({"success": True, "data": result})


@roadmap_builder_bp.route("/timeline", methods=["GET"])
@login_required
def get_roadmap_timeline():
    """
    Get roadmap timeline for Gantt-style visualization.

    Query Parameters:
        start_date (str): Optional start date filter (YYYY-MM-DD)
        end_date (str): Optional end date filter (YYYY-MM-DD)
        group_by (str): Grouping key (status, priority, assigned_to)

    Returns:
        JSON with timeline data
    """
    start_date = None
    end_date = None

    start_str = request.args.get("start_date")
    end_str = request.args.get("end_date")
    group_by = request.args.get("group_by", "status")

    if start_str:
        start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
    if end_str:
        end_date = datetime.strptime(end_str, "%Y-%m-%d").date()

    service = _get_service()
    result = service.generate_roadmap_timeline(
        start_date=start_date, end_date=end_date, group_by=group_by
    )

    return jsonify({"success": True, "data": result})


@roadmap_builder_bp.route("/summary", methods=["GET"])
@login_required
def get_roadmap_summary():
    """
    Get overall roadmap summary statistics.

    Returns:
        JSON with summary statistics
    """
    service = _get_service()
    result = service.get_roadmap_summary()

    return jsonify({"success": True, "data": result})


# =============================================================================
# Health Check
# =============================================================================


@roadmap_builder_bp.route("/health", methods=["GET"])
@login_required
def health_check():
    """
    Health check endpoint for the Roadmap Builder service.

    Returns:
        JSON with service status
    """
    return jsonify(
        {
            "success": True,
            "service": "roadmap-builder",
            "status": "healthy",
            "endpoints": [
                "GET /api/roadmap-builder/work-packages",
                "POST /api/roadmap-builder/work-packages",
                "GET /api/roadmap-builder/work-packages/<id>",
                "PUT /api/roadmap-builder/work-packages/<id>",
                "DELETE /api/roadmap-builder/work-packages/<id>",
                "POST /api/roadmap-builder/work-packages/<id>/dependencies",
                "DELETE /api/roadmap-builder/work-packages/<id>/dependencies/<dep_id>",
                "GET /api/roadmap-builder/plateaus",
                "POST /api/roadmap-builder/plateaus",
                "GET /api/roadmap-builder/plateaus/timeline",
                "GET /api/roadmap-builder/dependency-graph",
                "GET /api/roadmap-builder/critical-path",
                "GET /api/roadmap-builder/timeline",
                "GET /api/roadmap-builder/summary",
            ],
        }
    )

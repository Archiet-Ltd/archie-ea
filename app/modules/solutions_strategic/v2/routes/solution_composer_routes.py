"""
DEPRECATED: This file is migrated to app/modules/solutions_strategic/.
Registration is now centralized via app.modules.solutions_strategic.register().
Do NOT modify -- kept as fallback until Phase 6 cleanup.

Solution Composer Canvas API Routes

Provides REST API endpoints for the visual solution composer canvas
with ArchiMate 3.2 relationship validation.

Endpoints:
- POST /api/solution-composer/canvas - Create new canvas
- GET /api/solution-composer/canvas - List all canvases
- GET /api/solution-composer/canvas/<id> - Load canvas
- PUT /api/solution-composer/canvas/<id> - Save canvas
- DELETE /api/solution-composer/canvas/<id> - Delete canvas
- POST /api/solution-composer/nodes - Add node
- DELETE /api/solution-composer/nodes/<id> - Remove node
- PUT /api/solution-composer/nodes/<id>/position - Update node position
- POST /api/solution-composer/connections - Add connection
- DELETE /api/solution-composer/connections/<id> - Remove connection
- POST /api/solution-composer/validate-connection - Validate a connection
- GET /api/solution-composer/suggest-connections/<node_id> - Get connection suggestions
- GET /api/solution-composer/palette - Get palette elements
- GET /api/solution-composer/validate - Validate entire canvas
- GET /api/solution-composer/state - Get current canvas state
"""

import logging
import time

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app.decorators import audit_log
from app.modules.solutions_strategic.v2.services.solution_composer_service import SolutionComposerService
from app.utils.pagination import safe_int_arg
from app.middleware.tenant_decorators import is_active_org_admin

logger = logging.getLogger(__name__)

solution_composer_bp = Blueprint("solution_composer", __name__, url_prefix="/api/solution-composer")

# Store service instances per user with LRU eviction to prevent memory leak
_canvas_services = {}
_canvas_access_times = {}
_MAX_CANVAS_SERVICES = 100


def _get_service() -> SolutionComposerService:
    """Get or create a service instance for the current user with LRU eviction."""
    user_id = current_user.id if current_user.is_authenticated else "anonymous"

    # Evict oldest entries if at capacity
    if user_id not in _canvas_services and len(_canvas_services) >= _MAX_CANVAS_SERVICES:
        oldest_user = min(_canvas_access_times, key=_canvas_access_times.get)
        _canvas_services.pop(oldest_user, None)
        _canvas_access_times.pop(oldest_user, None)
        logger.debug(f"Evicted canvas service for user {oldest_user} (LRU)")

    if user_id not in _canvas_services:
        _canvas_services[user_id] = SolutionComposerService()

    _canvas_access_times[user_id] = time.monotonic()
    return _canvas_services[user_id]


# =============================================================================
# Canvas Management Endpoints
# =============================================================================


@solution_composer_bp.route("/canvas", methods=["POST"])
@login_required
@audit_log("create_canvas")
def create_canvas():
    """
    Create a new solution composer canvas.

    Request Body:
        {
            "name": "My Solution",
            "description": "Optional description"
        }

    Returns:
        JSON with canvas state
    """
    data = request.get_json() or {}
    name = data.get("name", "Untitled Solution")
    description = data.get("description")

    service = _get_service()
    result = service.create_canvas(name=name, description=description)

    return jsonify({"success": True, "data": result})


@solution_composer_bp.route("/canvas", methods=["GET"])
@login_required
def list_canvases():
    """
    List all solution canvases.

    Query Parameters:
        limit (int): Maximum results (default: 50)
        offset (int): Offset for pagination (default: 0)

    Returns:
        JSON list of canvases
    """
    limit = min(max(safe_int_arg('limit', 50, minimum=1, maximum=500), 1), 200)
    offset = max(safe_int_arg('offset', 0, minimum=0), 0)

    service = _get_service()
    result = service.list_canvases(limit=limit, offset=offset)

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 500

    return jsonify({"success": True, "data": result})


@solution_composer_bp.route("/canvas/<int:canvas_id>", methods=["GET"])
@login_required
def load_canvas(canvas_id: int):
    """
    Load a canvas from the database.

    Path Parameters:
        canvas_id: ID of the canvas to load

    Returns:
        JSON with canvas state
    """
    service = _get_service()
    result = service.load_canvas(canvas_id)

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 404

    return jsonify({"success": True, "data": result})


@solution_composer_bp.route("/canvas/<int:canvas_id>", methods=["PUT"])
@login_required
@audit_log("save_canvas")
def save_canvas(canvas_id: int):
    """
    Save the current canvas state.

    Path Parameters:
        canvas_id: ID of the canvas (0 for new canvas)

    Request Body (optional):
        {
            "name": "Updated name",
            "description": "Updated description"
        }

    Returns:
        JSON with save result
    """
    service = _get_service()
    data = request.get_json() or {}

    # Update canvas metadata if provided
    if service.current_canvas:
        if "name" in data:
            service.current_canvas.name = data["name"]
        if "description" in data:
            service.current_canvas.description = data["description"]
        if canvas_id > 0:
            service.current_canvas.canvas_id = canvas_id

    result = service.save_canvas(user_id=current_user.id)

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 500

    return jsonify({"success": True, "data": result})


@solution_composer_bp.route("/canvas/<int:canvas_id>", methods=["DELETE"])
@login_required
@audit_log("delete_canvas")
def delete_canvas(canvas_id: int):
    """
    Delete a canvas.

    Path Parameters:
        canvas_id: ID of the canvas to delete

    Returns:
        JSON with deletion result
    """
    service = _get_service()
    result = service.delete_canvas(canvas_id)

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 404

    return jsonify({"success": True, "data": result})


# =============================================================================
# Node Management Endpoints
# =============================================================================


@solution_composer_bp.route("/nodes", methods=["POST"])
@login_required
@audit_log("add_node")
def add_node():
    """
    Add a node to the canvas.

    Request Body:
        {
            "node_id": "unique-id",
            "element_type": "application_component",
            "name": "Node Name",
            "source_type": "vendor_product",
            "source_id": 123,
            "position_x": 100,
            "position_y": 200,
            "properties": {}
        }

    Returns:
        JSON with node details
    """
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "Request body is required"}), 400

    required_fields = ["node_id", "element_type", "name", "source_type"]
    for field in required_fields:
        if field not in data:
            return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400

    service = _get_service()
    result = service.add_node(
        node_id=data["node_id"],
        element_type=data["element_type"],
        name=data["name"],
        source_type=data["source_type"],
        source_id=data.get("source_id"),
        position_x=data.get("position_x", 0.0),
        position_y=data.get("position_y", 0.0),
        properties=data.get("properties", {}),
    )

    return jsonify({"success": True, "data": result})


@solution_composer_bp.route("/nodes/<node_id>", methods=["DELETE"])
@login_required
@audit_log("remove_node")
def remove_node(node_id: str):
    """
    Remove a node from the canvas.

    Path Parameters:
        node_id: ID of the node to remove

    Returns:
        JSON with removal result
    """
    service = _get_service()
    result = service.remove_node(node_id)

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 404

    return jsonify({"success": True, "data": result})


@solution_composer_bp.route("/nodes/<node_id>", methods=["PUT"])
@login_required
@audit_log("update_node")
def update_node(node_id: str):
    """
    Update node properties (name, element type, custom properties).

    Path Parameters:
        node_id: ID of the node

    Request Body:
        {
            "name": "New Name",
            "element_type": "application_service",
            "properties": {"key": "value"}
        }

    Returns:
        JSON with updated node data
    """
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "Request body is required"}), 400

    service = _get_service()
    result = service.update_node(
        node_id=node_id,
        name=data.get("name"),
        element_type=data.get("element_type"),
        properties=data.get("properties"),
    )

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 400

    return jsonify({"success": True, "data": result})


@solution_composer_bp.route("/nodes/<node_id>/position", methods=["PUT"])
@login_required
@audit_log("update_node_position")
def update_node_position(node_id: str):
    """
    Update a node's position on the canvas.

    Path Parameters:
        node_id: ID of the node

    Request Body:
        {
            "position_x": 150,
            "position_y": 250
        }

    Returns:
        JSON with update result
    """
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "Request body is required"}), 400

    if "position_x" not in data or "position_y" not in data:
        return jsonify({"success": False, "error": "position_x and position_y are required"}), 400

    try:
        pos_x = float(data["position_x"])
        pos_y = float(data["position_y"])
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "position_x and position_y must be valid numbers"}), 400

    service = _get_service()
    result = service.update_node_position(
        node_id=node_id, position_x=pos_x, position_y=pos_y
    )

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 404

    return jsonify({"success": True, "data": result})


# =============================================================================
# Container Operations (Black-Box / White-Box)
# =============================================================================


@solution_composer_bp.route("/nodes/<node_id>/parent", methods=["PUT"])
@login_required
@audit_log("set_node_parent")
def set_node_parent(node_id: str):
    """Set or clear the parent container for a node."""
    data = request.get_json() or {}
    service = _get_service()
    result = service.set_parent(child_id=node_id, parent_id=data.get("parent_id"))
    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 400
    return jsonify({"success": True, "data": result})


@solution_composer_bp.route("/nodes/<node_id>/container", methods=["PUT"])
@login_required
@audit_log("toggle_node_container")
def toggle_node_container(node_id: str):
    """Toggle container status for a node."""
    data = request.get_json() or {}
    service = _get_service()
    result = service.toggle_container(node_id=node_id, is_container=data.get("is_container", False))
    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 400
    return jsonify({"success": True, "data": result})


@solution_composer_bp.route("/nodes/<node_id>/collapse", methods=["PUT"])
@login_required
@audit_log("toggle_node_collapse")
def toggle_node_collapse(node_id: str):
    """Toggle collapse (black-box/white-box) for a container node."""
    service = _get_service()
    result = service.toggle_collapse(node_id=node_id)
    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 400
    return jsonify({"success": True, "data": result})


@solution_composer_bp.route("/nodes/<node_id>/dock", methods=["PUT"])
@login_required
@audit_log("set_node_dock")
def set_node_dock(node_id: str):
    """Set dock edge for an interface element."""
    data = request.get_json() or {}
    service = _get_service()
    result = service.set_dock_edge(node_id=node_id, edge=data.get("dock_edge"))
    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 400
    return jsonify({"success": True, "data": result})


# =============================================================================
# Connection Management Endpoints
# =============================================================================


@solution_composer_bp.route("/connections", methods=["POST"])
@login_required
@audit_log("add_connection")
def add_connection():
    """
    Add a connection between nodes.

    Request Body:
        {
            "connection_id": "unique-id",
            "source_node_id": "source-node",
            "target_node_id": "target-node",
            "relationship_type": "serving",
            "label": "optional label"
        }

    Returns:
        JSON with connection details and validation
    """
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "Request body is required"}), 400

    required_fields = ["connection_id", "source_node_id", "target_node_id", "relationship_type"]
    for field in required_fields:
        if field not in data:
            return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400

    service = _get_service()
    result = service.add_connection(
        connection_id=data["connection_id"],
        source_node_id=data["source_node_id"],
        target_node_id=data["target_node_id"],
        relationship_type=data["relationship_type"],
        label=data.get("label"),
        properties=data.get("properties", {}),
    )

    return jsonify({"success": True, "data": result})


@solution_composer_bp.route("/connections/<connection_id>", methods=["DELETE"])
@login_required
@audit_log("remove_connection")
def remove_connection(connection_id: str):
    """
    Remove a connection from the canvas.

    Path Parameters:
        connection_id: ID of the connection to remove

    Returns:
        JSON with removal result
    """
    service = _get_service()
    result = service.remove_connection(connection_id)

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 404

    return jsonify({"success": True, "data": result})


@solution_composer_bp.route("/connections/<connection_id>", methods=["PUT"])
@login_required
@audit_log("update_connection")
def update_connection(connection_id: str):
    """
    Update a connection's relationship type or label.

    Path Parameters:
        connection_id: ID of the connection

    Request Body:
        {
            "relationship_type": "serving",
            "label": "optional label"
        }

    Returns:
        JSON with updated connection data
    """
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "Request body is required"}), 400

    service = _get_service()
    result = service.update_connection(
        connection_id=connection_id,
        relationship_type=data.get("relationship_type"),
        label=data.get("label"),
    )

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 400

    return jsonify({"success": True, "data": result})


@solution_composer_bp.route("/validate-connection", methods=["POST"])
@login_required
@audit_log("validate_connection")
def validate_connection():
    """
    Validate a connection without adding it.

    Request Body:
        {
            "source_node_id": "source-node",
            "target_node_id": "target-node",
            "relationship_type": "serving"
        }

    Returns:
        JSON with validation result
    """
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "Request body is required"}), 400

    required_fields = ["source_node_id", "target_node_id", "relationship_type"]
    for field in required_fields:
        if field not in data:
            return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400

    service = _get_service()
    result = service.validate_connection(
        source_node_id=data["source_node_id"],
        target_node_id=data["target_node_id"],
        relationship_type=data["relationship_type"],
    )

    return jsonify({"success": True, "data": result})


@solution_composer_bp.route("/suggest-connections/<node_id>", methods=["GET"])
@login_required
def suggest_connections(node_id: str):
    """
    Get valid connection suggestions from a node.

    Path Parameters:
        node_id: Source node ID

    Returns:
        JSON with connection suggestions
    """
    service = _get_service()
    result = service.suggest_valid_connections(node_id)

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 404

    return jsonify({"success": True, "data": result})


# =============================================================================
# Canvas State and Validation Endpoints
# =============================================================================


@solution_composer_bp.route("/state", methods=["GET"])
@login_required
def get_canvas_state():
    """
    Get the current canvas state.

    Returns:
        JSON with current canvas state
    """
    service = _get_service()
    result = service.get_canvas_state()

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 404

    return jsonify({"success": True, "data": result})


@solution_composer_bp.route("/validate", methods=["GET"])
@login_required
def validate_canvas():
    """
    Validate the entire canvas for ArchiMate compliance.

    Returns:
        JSON with validation results
    """
    service = _get_service()
    result = service.validate_canvas()

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 404

    return jsonify({"success": True, "data": result})


@solution_composer_bp.route("/palette", methods=["GET"])
@login_required
def get_palette():
    """
    Get available elements for the canvas palette.

    Query Parameters:
        include_vendors (bool): Include vendor products (default: true)
        include_applications (bool): Include application components (default: true)
        include_capabilities (bool): Include business capabilities (default: true)

    Returns:
        JSON with palette elements
    """
    include_vendors = request.args.get("include_vendors", "true").lower() == "true"
    include_applications = request.args.get("include_applications", "true").lower() == "true"
    include_capabilities = request.args.get("include_capabilities", "true").lower() == "true"

    service = _get_service()
    result = service.get_palette_elements(
        include_vendors=include_vendors,
        include_applications=include_applications,
        include_capabilities=include_capabilities,
    )

    return jsonify({"success": True, "data": result})


# =============================================================================
# Utility Endpoints
# =============================================================================


@solution_composer_bp.route("/relationship-types", methods=["GET"])
@login_required
def get_relationship_types():
    """
    Get available ArchiMate relationship types.

    Returns:
        JSON with relationship types
    """
    relationship_types = [
        {"type": "composition", "label": "Composition", "category": "structural"},
        {"type": "aggregation", "label": "Aggregation", "category": "structural"},
        {"type": "assignment", "label": "Assignment", "category": "structural"},
        {"type": "realization", "label": "Realization", "category": "structural"},
        {"type": "serving", "label": "Serving", "category": "dependency"},
        {"type": "access", "label": "Access", "category": "dependency"},
        {"type": "influence", "label": "Influence", "category": "dependency"},
        {"type": "triggering", "label": "Triggering", "category": "dynamic"},
        {"type": "flow", "label": "Flow", "category": "dynamic"},
        {"type": "specialization", "label": "Specialization", "category": "other"},
        {"type": "association", "label": "Association", "category": "other"},
    ]

    return jsonify({"success": True, "data": {"relationship_types": relationship_types}})


@solution_composer_bp.route("/element-types", methods=["GET"])
@login_required
def get_element_types():
    """
    Get available ArchiMate element types by layer.

    Returns:
        JSON with element types
    """
    service = _get_service()
    element_types = service._get_archimate_element_types()

    # Group by layer
    by_layer = {}
    for et in element_types:
        layer = et["layer"]
        if layer not in by_layer:
            by_layer[layer] = []
        by_layer[layer].append(et)

    return jsonify(
        {"success": True, "data": {"element_types": element_types, "by_layer": by_layer}}
    )


# =============================================================================
# AI Suggestions Endpoint (Task 1)
# =============================================================================


@solution_composer_bp.route("/suggest-ai/<node_id>", methods=["GET"])
@login_required
def get_ai_suggestions(node_id: str):
    """
    Get AI-powered suggestions for a node: related components, connections,
    and pattern hints based on the current canvas context.
    """
    service = _get_service()
    result = service.get_ai_suggestions(node_id)

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 404

    return jsonify({"success": True, "data": result})


# =============================================================================
# Smart Palette Search Endpoint (Task 2)
# =============================================================================


@solution_composer_bp.route("/palette/search", methods=["GET"])
@login_required
def search_palette():
    """
    Search repository entities for the palette.

    Query Parameters:
        q (str): Search query
        categories (str): Comma-separated list of categories to search
        limit (int): Max results per category (default 30)
    """
    query = request.args.get("q", "").strip()
    if not query or len(query) < 2:
        return jsonify({"success": False, "error": "Query must be at least 2 characters"}), 400

    categories = (
        request.args.get("categories", "").split(",") if request.args.get("categories") else None
    )
    limit = min(safe_int_arg('limit', 30, minimum=1, maximum=500), 200)

    service = _get_service()
    result = service.search_palette(query=query, categories=categories, limit=limit)

    return jsonify({"success": True, "data": result})


# =============================================================================
# Pattern Templates Endpoints (Task 4)
# =============================================================================


@solution_composer_bp.route("/patterns", methods=["GET"])
@login_required
def get_patterns():
    """Get available architecture pattern templates."""
    service = _get_service()
    patterns = service.get_pattern_templates()
    return jsonify({"success": True, "data": {"patterns": patterns}})


@solution_composer_bp.route("/patterns/<pattern_id>/apply", methods=["POST"])
@login_required
@audit_log("apply_pattern")
def apply_pattern(pattern_id: str):
    """
    Apply a pattern template to the current canvas.

    Path Parameters:
        pattern_id: ID of the pattern to apply
    """
    service = _get_service()
    result = service.apply_pattern(pattern_id)

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 400

    return jsonify({"success": True, "data": result})


# =============================================================================
# Rich Node Details Endpoint (Task 5)
# =============================================================================


@solution_composer_bp.route("/nodes/<node_id>/details", methods=["GET"])
@login_required
def get_node_details(node_id: str):
    """
    Get enriched details for a node including repository-linked data.
    """
    service = _get_service()
    result = service.get_node_details(node_id)

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 404

    return jsonify({"success": True, "data": result})


# =============================================================================
# Export Endpoints (Task 7)
# =============================================================================


@solution_composer_bp.route("/export/xml", methods=["GET"])
@login_required
def export_archimate_xml():
    """Export the current canvas as ArchiMate Open Exchange Format XML."""
    service = _get_service()
    result = service.export_canvas_archimate_xml()

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 400

    return jsonify({"success": True, "data": result})


@solution_composer_bp.route("/export/json", methods=["GET"])
@login_required
def export_canvas_json():
    """Export the current canvas as portable JSON."""
    service = _get_service()
    result = service.export_canvas_json()

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 400

    return jsonify({"success": True, "data": result})


# =============================================================================
# Confidence Scoring Endpoints (Task 3)
# =============================================================================


@solution_composer_bp.route("/confidence", methods=["GET"])
@login_required
def get_canvas_confidence():
    """
    Score confidence for all nodes and connections on the current canvas.

    Returns per-node scores, per-connection scores, and an overall canvas score
    using multi-factor confidence analysis (name quality, type correctness,
    validation status, database linkage, extraction method).
    """
    service = _get_service()
    result = service.score_canvas_confidence()

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 400

    return jsonify({"success": True, "data": result})


@solution_composer_bp.route("/nodes/<node_id>/confidence", methods=["GET"])
@login_required
def get_node_confidence(node_id: str):
    """Score confidence for a single node."""
    service = _get_service()
    result = service.score_node_confidence(node_id)

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 404

    return jsonify({"success": True, "data": result})


# =============================================================================
# Scenario Comparison Endpoints (Task 6)
# =============================================================================


@solution_composer_bp.route("/canvas/<int:canvas_id>/duplicate", methods=["POST"])
@login_required
@audit_log("duplicate_canvas")
def duplicate_canvas(canvas_id: int):
    """
    Duplicate a canvas as a new scenario for what-if analysis.

    Request Body (optional):
        { "name": "My Scenario Name" }
    """
    data = request.get_json() or {}
    new_name = data.get("name")

    service = _get_service()
    result = service.duplicate_canvas(canvas_id=canvas_id, new_name=new_name)

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 400

    return jsonify({"success": True, "data": result}), 201


@solution_composer_bp.route("/compare", methods=["POST"])
@login_required
@audit_log("compare_canvases")
def compare_canvases():
    """
    Compare two canvases (baseline vs scenario).

    Request Body:
        {
            "canvas_a": <int>,  // baseline canvas ID
            "canvas_b": <int>   // scenario canvas ID
        }
    """
    data = request.get_json() or {}
    canvas_a = data.get("canvas_a")
    canvas_b = data.get("canvas_b")

    if not canvas_a or not canvas_b:
        return jsonify({"success": False, "error": "canvas_a and canvas_b are required"}), 400

    try:
        canvas_id_a = int(canvas_a)
        canvas_id_b = int(canvas_b)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "canvas_a and canvas_b must be valid integers"}), 400

    service = _get_service()
    result = service.compare_canvases(canvas_id_a=canvas_id_a, canvas_id_b=canvas_id_b)

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 400

    return jsonify({"success": True, "data": result})


# =============================================================================
# ARB Submission - VALUE DELIVERY FEATURE
# Submit canvas design to Architecture Review Board for governance approval
# =============================================================================


@solution_composer_bp.route("/submit-to-arb", methods=["POST"])
@login_required
@audit_log("submit_to_arb")
def submit_to_arb():
    """Submit a **persisted** Architecture Model to the Architecture Review Board.

    This endpoint used to construct a raw, unlinked ``ARBReviewItem`` straight
    from browser canvas JSON.  That row had no typed subject, no evidence
    snapshot and no review cycle, so nothing downstream could govern it, and the
    canvas payload itself was trusted as its own evidence.

    A canvas is a drawing surface, not a system of record.  It is therefore no
    longer submittable on its own: the caller must first persist the design as
    an ``ArchitectureModel`` and submit that model's id, which is then routed
    through the typed, evidence-gated submission command.  Nothing is
    manufactured from canvas JSON.

    Request body:
        {"architecture_model_id": 42, "human_reviewed": true}
    """
    from app.modules.transformation_room.arb_submission_adapter import (
        TypedARBSubmissionAdapter,
    )

    data = request.get_json(silent=True) or {}
    model_id = data.get("architecture_model_id")
    if isinstance(model_id, bool) or not isinstance(model_id, int) or model_id <= 0:
        return (
            jsonify(
                {
                    "success": False,
                    "reason_codes": ["architecture_model_required"],
                    "missing_evidence": [
                        {
                            "code": "architecture_model_required",
                            "field": "architecture_model_id",
                        }
                    ],
                    "error": (
                        "Save this canvas as an architecture model first, then "
                        "submit that model for review. A canvas on its own has "
                        "no governed subject or evidence to review."
                    ),
                    "action_url": "/architecture/models",
                }
            ),
            422,
        )

    result = TypedARBSubmissionAdapter.submit_subject_from_request(
        subject_type="architecture_model",
        subject_id=model_id,
        payload=data,
    )
    if not result.success:
        reason_codes = result.reason_codes
        status = result.http_status
        if reason_codes == ["architecture_model_not_found"]:
            reason_codes = ["architecture_model_not_persisted"]
            status = 422
        return jsonify(
            {
                "success": False,
                "reason_codes": reason_codes,
                "missing_evidence": result.missing_evidence,
                "error": "The architecture model could not be submitted for review.",
                "action_url": "/architecture/models",
            }
        ), status

    data = {
        "review_number": result.review_number,
        "review_id": result.review_item_id,
        "review_item_id": result.review_item_id,
        "snapshot_id": result.snapshot_id,
        "review_cycle_id": result.review_cycle_id,
        "canonical_url": result.canonical_url,
        "idempotent": result.idempotent,
        "status": "submitted",
        "message": (
            f"Design submitted to ARB as {result.review_number}. "
            "You will be notified when review begins."
        ),
        "arb_dashboard_url": "/arb/reviews",
        "redirect_url": f"/arb/reviews/{result.review_item_id}",
    }
    # Preserve the established Composer response status while returning the
    # canonical typed identifiers and replay marker.
    return jsonify({"success": True, "data": data}), 200


@solution_composer_bp.route("/strategic-alignment", methods=["GET"])
@login_required
def get_strategic_alignment():
    """
    Calculate strategic alignment score for the current canvas.

    Analyzes how well the solution design aligns with:
    - Business capabilities
    - Strategic goals
    - Investment priorities

    Returns:
        JSON with alignment scores and recommendations
    """
    service = _get_service()

    if not service.current_canvas:
        return jsonify({"success": False, "error": "No canvas loaded"}), 400

    result = service.calculate_strategic_alignment()
    return jsonify({"success": True, "data": result})


@solution_composer_bp.route("/stakeholder-impact", methods=["GET"])
@login_required
def get_stakeholder_impact():
    """
    Analyze stakeholder impact for the current canvas design.

    Identifies stakeholders affected by the solution and their
    level of impact based on connected capabilities and applications.

    Returns:
        JSON with stakeholder analysis
    """
    service = _get_service()

    if not service.current_canvas:
        return jsonify({"success": False, "error": "No canvas loaded"}), 400

    result = service.analyze_stakeholder_impact()
    return jsonify({"success": True, "data": result})


@solution_composer_bp.route("/export/adr", methods=["GET"])
@login_required
def export_adr():
    """
    Generate an Architecture Decision Record (ADR) document from the canvas.

    Creates a markdown ADR documenting the solution design, including:
    - Context and problem statement
    - Decision drivers
    - Considered options (from canvas elements)
    - Decision outcome
    - Consequences

    Returns:
        JSON with markdown ADR content
    """
    service = _get_service()

    if not service.current_canvas:
        return jsonify({"success": False, "error": "No canvas loaded"}), 400

    result = service.generate_adr_document()
    return jsonify({"success": True, "data": result})


# =============================================================================
# ADM Integration Endpoints
# =============================================================================


@solution_composer_bp.route("/canvas/from-adm/<int:workflow_instance_id>", methods=["POST"])
@login_required
@audit_log("create_canvas_from_adm")
def create_canvas_from_adm_workflow(workflow_instance_id: int):
    """
    Create a new Solution Composer canvas from ADM Phase A workflow outputs.

    This endpoint bridges the TOGAF ADM Architecture Vision workflow with the
    Solution Composer, automatically populating a canvas with:
    - Stakeholders as Business Actors/Roles
    - Business Goals as motivational elements
    - Current capabilities with maturity indicators
    - Constraints with severity styling
    - ArchiMate-compliant connections between elements

    Path Parameters:
        workflow_instance_id: ID of the completed ADM Phase A workflow instance

    Request Body:
        {
            "name": "Optional custom canvas name",
            "merge_strategy": "append"  // Optional: "append", "replace", "merge"
        }

    Returns:
        JSON with created canvas state ready for editing
    """
    from ..services.adm_to_composer_bridge import adm_to_composer_bridge
    from ..services.ea_workflow_engine import EAWorkflowInstance

    # Get workflow instance
    workflow_instance = EAWorkflowInstance.query.get(workflow_instance_id)
    if not workflow_instance:
        return jsonify(
            {"success": False, "error": f"Workflow instance {workflow_instance_id} not found"}
        ), 404

    # Verify workflow is completed
    if workflow_instance.status != "completed":
        return jsonify(
            {
                "success": False,
                "error": f"Workflow must be completed. Current status: {workflow_instance.status}",
            }
        ), 400

    # Build ADM outputs from workflow context (EAWorkflowInstance uses "context", not "execution_context")
    # Map engine output keys to bridge-expected format
    ctx = workflow_instance.context or {}
    scope_def = ctx.get("scope_definition") or {}
    if not scope_def.get("project_name") and ctx.get("project_name"):
        scope_def = {**scope_def, "project_name": ctx["project_name"]}
    adm_outputs = {
        "scope": scope_def,
        "stakeholders": ctx.get("stakeholder_map") or {},
        "goals": ctx.get("business_goals") or {},
        "constraints": ctx.get("constraints") or {},
        "capabilities": ctx.get("capability_assessment") or {},
    }
    if not any(adm_outputs.values()):
        return jsonify(
            {"success": False, "error": "Workflow has no step outputs available"}
        ), 400

    data = request.get_json() or {}

    try:
        # Create canvas from ADM outputs
        canvas = adm_to_composer_bridge.create_canvas_from_adm_workflow(
            workflow_instance_id=workflow_instance_id,
            adm_outputs=adm_outputs,
            user_id=current_user.id,
        )

        # Override name if provided
        if data.get("name"):
            canvas.name = data.get("name")

        # Save to database via composer service
        service = _get_service()
        service.current_canvas = canvas
        save_result = service.save_canvas(user_id=current_user.id)

        if "error" in save_result:
            return jsonify({"success": False, "error": save_result["error"]}), 500

        return jsonify(
            {
                "success": True,
                "data": {
                    "canvas_id": save_result.get("canvas_id"),
                    "name": canvas.name,
                    "description": canvas.description,
                    "node_count": len(canvas.nodes),
                    "connection_count": len(canvas.connections),
                    "workflow_instance_id": workflow_instance_id,
                    "message": f"Canvas created from ADM workflow with {len(canvas.nodes)} elements",
                }
            }
        )

    except Exception:
        return jsonify({"success": False, "error": "Failed to create canvas"}), 500


@solution_composer_bp.route("/canvas/<int:canvas_id>/import-adm/<int:workflow_instance_id>", methods=["POST"])
@login_required
@audit_log("import_adm_to_canvas")
def import_adm_to_existing_canvas(canvas_id: int, workflow_instance_id: int):
    """
    Import ADM Phase A elements into an existing Solution Composer canvas.

    Path Parameters:
        canvas_id: ID of the existing canvas to import into
        workflow_instance_id: ID of the completed ADM workflow instance

    Request Body:
        {
            "merge_strategy": "append"  // "append", "replace", or "merge"
        }

    Returns:
        JSON with updated canvas state
    """
    from ..services.adm_to_composer_bridge import adm_to_composer_bridge
    from ..services.ea_workflow_engine import EAWorkflowInstance

    # Get workflow instance
    workflow_instance = EAWorkflowInstance.query.get(workflow_instance_id)
    if not workflow_instance:
        return jsonify(
            {"success": False, "error": f"Workflow instance {workflow_instance_id} not found"}
        ), 404

    if workflow_instance.status != "completed":
        return jsonify(
            {
                "success": False,
                "error": f"Workflow must be completed. Current status: {workflow_instance.status}",
            }
        ), 400

    # Build ADM outputs from workflow context (EAWorkflowInstance uses "context")
    ctx = workflow_instance.context or {}
    scope_def = ctx.get("scope_definition") or {}
    if not scope_def.get("project_name") and ctx.get("project_name"):
        scope_def = {**scope_def, "project_name": ctx["project_name"]}
    adm_outputs = {
        "scope": scope_def,
        "stakeholders": ctx.get("stakeholder_map") or {},
        "goals": ctx.get("business_goals") or {},
        "constraints": ctx.get("constraints") or {},
        "capabilities": ctx.get("capability_assessment") or {},
    }
    if not any(adm_outputs.values()):
        return jsonify(
            {"success": False, "error": "Workflow has no step outputs available"}
        ), 400

    data = request.get_json() or {}
    merge_strategy = data.get("merge_strategy", "append")

    try:
        canvas = adm_to_composer_bridge.import_adm_to_existing_canvas(
            canvas_id=canvas_id,
            adm_outputs=adm_outputs,
            merge_strategy=merge_strategy,
        )

        # Save updated canvas
        service = _get_service()
        service.current_canvas = canvas
        save_result = service.save_canvas(user_id=current_user.id)

        if "error" in save_result:
            return jsonify({"success": False, "error": save_result["error"]}), 500

        return jsonify(
            {
                "success": True,
                "data": {
                    "canvas_id": canvas_id,
                    "node_count": len(canvas.nodes),
                    "connection_count": len(canvas.connections),
                    "merge_strategy": merge_strategy,
                    "message": f"ADM elements imported with {merge_strategy} strategy",
                }
            }
        )

    except ValueError:
        return jsonify({"success": False, "error": "Resource not found"}), 404
    except Exception:
        return jsonify({"success": False, "error": "Failed to import"}), 500


# =============================================================================
# Health Check
# =============================================================================


@solution_composer_bp.route("/health", methods=["GET"])
@login_required
def health_check():
    """
    Health check endpoint for the Solution Composer service.

    Returns:
        JSON with service status
    """
    return jsonify(
        {
            "success": True,
            "service": "solution-composer",
            "status": "healthy",
            "endpoints": [
                "POST /api/solution-composer/canvas",
                "GET /api/solution-composer/canvas",
                "GET /api/solution-composer/canvas/<id>",
                "PUT /api/solution-composer/canvas/<id>",
                "DELETE /api/solution-composer/canvas/<id>",
                "POST /api/solution-composer/nodes",
                "DELETE /api/solution-composer/nodes/<id>",
                "PUT /api/solution-composer/nodes/<id>",
                "PUT /api/solution-composer/nodes/<id>/position",
                "POST /api/solution-composer/connections",
                "PUT /api/solution-composer/connections/<id>",
                "DELETE /api/solution-composer/connections/<id>",
                "POST /api/solution-composer/validate-connection",
                "GET /api/solution-composer/suggest-connections/<node_id>",
                "GET /api/solution-composer/palette",
                "GET /api/solution-composer/validate",
                "GET /api/solution-composer/state",
                "GET /api/solution-composer/relationship-types",
                "GET /api/solution-composer/element-types",
                "POST /api/solution-composer/export-archimate",
                "GET /api/solution-composer/suggest-ai/<node_id>",
                "GET /api/solution-composer/palette/search",
                "GET /api/solution-composer/patterns",
                "POST /api/solution-composer/patterns/<id>/apply",
                "GET /api/solution-composer/nodes/<id>/details",
                "GET /api/solution-composer/export/xml",
                "GET /api/solution-composer/export/json",
                "GET /api/solution-composer/confidence",
                "GET /api/solution-composer/nodes/<id>/confidence",
                "POST /api/solution-composer/canvas/<id>/duplicate",
                "POST /api/solution-composer/compare",
            ],
        }
    )


@solution_composer_bp.route("/export-archimate", methods=["POST"])
@login_required
@audit_log("export_archimate")
def export_archimate():
    """
    Export current canvas to ArchiMate 3.2 XML format.

    Returns:
        JSON with XML string for download
    """
    service = _get_service()
    result = service.export_to_archimate_xml()

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 500

    return jsonify({"success": True, "data": result})


# =============================================================================
# Solution Auto-Populate (G13)
# =============================================================================


@solution_composer_bp.route("/solutions/<int:solution_id>/populate", methods=["POST"])
@login_required
def populate_canvas_from_solution(solution_id: int):
    """G13: Auto-populate canvas with ArchiMate elements linked to a solution.

    Reads SolutionArchiMateElement junction records, fetches each ArchiMateElement,
    and adds them as canvas nodes arranged by ArchiMate layer (motivation →
    strategy → business → application → technology → implementation).

    Clears the current in-memory canvas and creates a fresh one for this solution.
    """
    from app.models.solution_archimate_element import SolutionArchiMateElement
    from app.models.archimate_core import ArchiMateElement
    from app.models.solution_models import Solution

    solution = Solution.query.get_or_404(solution_id)
    if solution.created_by_id != current_user.id and not is_active_org_admin():
        return jsonify({"error": "Access denied"}), 403

    junctions = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()

    if not junctions:
        return jsonify({"success": False, "error": "No ArchiMate elements linked to this solution yet."}), 404

    service = _get_service()
    service.create_canvas(
        name=f"{solution.name} — Architecture Canvas",
        description=f"Auto-populated from solution #{solution_id}",
    )

    _LAYER_ORDER = {
        "motivation": 0, "strategy": 1, "business": 2,
        "application": 3, "technology": 4, "implementation": 5, "physical": 6,
    }
    _LAYER_Y_STEP = 160
    _NODE_X_STEP = 200
    layer_x_counters = {}

    added = []
    for junc in junctions:
        element = ArchiMateElement.query.get(junc.element_id)
        if not element:
            continue
        el_type = element.element_type or "ApplicationComponent"
        el_name = element.name or junc.element_name or "Unnamed"
        layer = (element.layer or "application").lower()
        layer_idx = _LAYER_ORDER.get(layer, 3)
        col = layer_x_counters.get(layer, 0)
        layer_x_counters[layer] = col + 1

        node_id = f"sol{solution_id}-el{element.id}"
        (service.add_node(
            node_id=node_id,
            element_type=el_type,
            name=el_name,
            source_type="archimate_element",
            source_id=element.id,
            position_x=col * _NODE_X_STEP + 50,
            position_y=layer_idx * _LAYER_Y_STEP + 50,
            properties={"element_role": junc.element_role, "layer_type": junc.layer_type},
        ))
        added.append({"node_id": node_id, "name": el_name, "type": el_type, "layer": layer})

    state = service.get_canvas_state()
    return jsonify({
        "success": True,
        "nodes_added": len(added),
        "elements": added,
        "canvas": state.get("data", state),
    })

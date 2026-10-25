"""
Legacy compatibility routes, error handlers, and health endpoint.

Routes: /chat (redirect), /chat/message, /chat/data/<path>,
        /chat/transform/<path>, /chat/entities/<path>, /api/health/llm.
Error handlers: 404, 500, 401, 403.
"""

import logging

from flask import current_app, jsonify, redirect, request, url_for
from flask_login import login_required

from app.decorators import audit_log
from app.middleware.tenant_decorators import _wants_json
from app.services.feature_flag_service import FeatureFlagService
from app.utils.plain_errors import render_plain_error
from . import unified_ai_chat_bp
from .chat_core import send_message
from .workflow_routes import update_capability, update_application_metadata, suggest_entities
from .entity_routes import transform_output, get_available_roles, get_role_info, match_entities_api, get_entity_types

logger = logging.getLogger(__name__)

# ============================================================================
# ERROR HANDLERS
# ============================================================================


@unified_ai_chat_bp.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return jsonify({"error": "Resource not found"}), 404


@unified_ai_chat_bp.errorhandler(500)
def internal_error(error):
    """Handle 500 errors."""
    current_app.logger.error(f"Internal server error: {str(error)}")
    return jsonify({"error": "Internal server error"}), 500


@unified_ai_chat_bp.errorhandler(401)
def unauthorized(error):
    """Handle 401 errors."""
    return jsonify({"error": "Unauthorized access"}), 401


@unified_ai_chat_bp.errorhandler(403)
def forbidden(error):
    """Handle 403 errors — plain refusal page for browsers, JSON for API/AJAX callers.

    A blueprint-level handler beats the app-level handler in errors.py, so a
    browser loading /ai-chat/admin/analytics was receiving raw JSON instead of
    the product's plain refusal page.  Use the same JSON-or-HTML switch the
    app-level handler uses (tenant_decorators._wants_json) and the same
    render_plain_error helper for the HTML path."""
    if _wants_json():
        return jsonify({"error": "Access forbidden"}), 403
    return render_plain_error(403, "errors/403.html")



# ============================================================================
# LEGACY COMPATIBILITY ROUTES
# ============================================================================


@unified_ai_chat_bp.route("/chat", strict_slashes=False)
@login_required
def legacy_chat_index():
    """Legacy redirect to main chat interface"""
    return redirect(url_for("unified_ai_chat.index"))


@unified_ai_chat_bp.route("/chat/message", methods=["POST"])
@login_required
@audit_log("legacy_send_chat_message")
def legacy_send_message():
    """Legacy redirect to message endpoint"""
    return send_message()


@unified_ai_chat_bp.route("/chat/data/<path:path>", strict_slashes=False)
@login_required
def legacy_data_routes(path):
    """Legacy data interaction route handler"""
    # Map legacy routes to new unified routes
    route_mapping = {
        "/create-capability": "unified_ai_chat.create_capability",
        "/update-capability/<int:capability_id>": "unified_ai_chat.update_capability",
        "/add-compliance-requirement": "unified_ai_chat.add_compliance_requirement",
        "/update-application/<int:app_id>": "unified_ai_chat.update_application_metadata",
        "/validate-request": "unified_ai_chat.validate_modification_request",
    }

    if path in route_mapping:
        # Handle routes with parameters
        if "<int:capability_id>" in route_mapping[path]:
            # Extract ID from path
            parts = path.split("/")
            if len(parts) >= 3 and parts[2].isdigit():
                capability_id = int(parts[2])
                if parts[1] == "update-capability":
                    return update_capability(capability_id)
        elif "<int:app_id>" in route_mapping[path]:
            # Extract ID from path
            parts = path.split("/")
            if len(parts) >= 3 and parts[2].isdigit():
                app_id = int(parts[2])
                if parts[1] == "update-application":
                    return update_application_metadata(app_id)
        else:
            return redirect(url_for(route_mapping[path]))
    else:
        return jsonify({"error": "Legacy route not found"}), 404


@unified_ai_chat_bp.route("/chat/transform/<path:path>", strict_slashes=False)
@login_required
def legacy_transform_routes(path):
    """Legacy business output route handler"""
    if path == "transform-output":
        return transform_output()
    elif path == "available-roles":
        return get_available_roles()
    elif path.startswith("role-info/"):
        role_value = path.replace("role-info/", "")
        return get_role_info(role_value)
    else:
        return jsonify({"error": "Legacy route not found"}), 404


# ============================================================================
# FEEDBACK & LEARNING ENDPOINTS
# ============================================================================



# ============================================================================
# LEGACY ENTITY ROUTES AND HEALTH CHECK
# ============================================================================


@unified_ai_chat_bp.route("/chat/entities/<path:path>", strict_slashes=False)
@login_required
def legacy_entity_routes(path):
    """Legacy entity matching route handler"""
    if path == "match":
        return match_entities_api()
    elif path == "types":
        return get_entity_types()
    elif path == "suggest":
        return suggest_entities()
    else:
        return jsonify({"error": "Legacy route not found"}), 404


# ============================================================================
# HEALTH ENDPOINT
# ============================================================================


@unified_ai_chat_bp.route("/api/health/llm", methods=["GET"])
@login_required
def llm_health():
    """Health check endpoint for LLM configuration."""
    status = FeatureFlagService.get_feature_status()

    if status["llm_configured"]:
        return jsonify(
            {
                "status": "healthy",
                "provider": status["provider"],
                "model": status["model"],
                "features": status["features"],
            }
        ), 200
    else:
        return jsonify(
            {
                "status": "unhealthy",
                "error": "LLM provider not configured",
                "features": status["features"],
                "hint": "Configure LLM at /admin/api-settings or set environment variables",
            }
        ), 503


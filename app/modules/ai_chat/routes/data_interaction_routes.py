"""
AI Data Interaction Routes - Secure Data Modification Endpoints
API endpoints for AI-driven data modifications with validation and guardrails.

Blueprint: ai_data_interaction (url_prefix="/ai-chat/data")
Routes: 14
"""

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app.decorators import audit_log
from app.services.ai_data_interaction_service import AIDataInteractionService
from app.services.rate_limiter import rate_limit

ai_data_interaction = Blueprint("ai_data_interaction", __name__, url_prefix="/ai-chat/data")

# NOTE: create-capability, update-capability, add-compliance-requirement routes
# removed — canonical versions live in unified_ai_chat (workflow_routes.py)
# with better input validation (validate_string, validate_enum, sanitize_html).


@ai_data_interaction.route("/bulk-update-capabilities", methods=["POST"])
@login_required
@audit_log("ai_data_bulk_update_capabilities")
@rate_limit(10, "1h")
def bulk_update_capabilities():
    """
    Perform bulk updates on multiple capabilities.

    Expected JSON payload:
    {
        "updates": [
            {
                "capability_id": 123,
                "update_data": {"name": "Updated Name"}
            },
            ...
        ]
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        updates = data.get("updates", [])
        if not updates:
            return jsonify({"error": "No updates provided"}), 400

        service = AIDataInteractionService(user_id=current_user.id)
        result = service.bulk_update_capabilities(updates)

        return jsonify(
            {
                "success": result["success"],
                "summary": result["summary"],
                "results": result["results"],
            }
        )

    except Exception as e:
        return (
            jsonify({"success": False, "error": f"Failed to perform bulk updates: {str(e)}"}),
            500,
        )


@ai_data_interaction.route("/audit-log", methods=["GET"])
@login_required
def get_audit_log():
    """
    Get audit log of AI data interaction operations.

    Query parameters:
    - operation_type: Filter by operation type (optional)
    """
    try:
        operation_type = request.args.get("operation_type")

        service = AIDataInteractionService(user_id=current_user.id)
        audit_log = service.get_audit_log(operation_type)

        return jsonify({"success": True, "audit_log": audit_log, "total_entries": len(audit_log)})

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@ai_data_interaction.route("/validate-operation", methods=["POST"])
@login_required
@audit_log("ai_data_validate_operation")
def validate_operation():
    """
    Validate an operation before execution.

    Expected JSON payload:
    {
        "operation": "create_capability",
        "data": {
            "name": "Test Capability",
            "description": "Test description"
        }
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        operation = data.get("operation")
        operation_data = data.get("data", {})

        if not operation:
            return jsonify({"error": "Operation type is required"}), 400

        service = AIDataInteractionService(user_id=current_user.id)
        validation_result = service.validate_operation(operation, operation_data)

        return jsonify({"success": True, "validation": validation_result})

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@ai_data_interaction.route("/create-application", methods=["POST"])
@login_required
@audit_log("ai_data_create_application")
@rate_limit(20, "1h")
def create_application():
    """
    Create a new application through AI interaction.

    Expected JSON payload:
    {
        "name": "Application Name",
        "description": "Application description",
        "application_type": "enterprise",
        "business_domain": "Sales",
        "deployment_model": "cloud"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        service = AIDataInteractionService(user_id=current_user.id)
        result = service.create_application(data)

        if result["success"]:
            return jsonify(
                {
                    "success": True,
                    "application_id": result["application_id"],
                    "message": result["message"],
                }
            )
        else:
            return jsonify({"success": False, "error": result["error"]}), 400

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@ai_data_interaction.route("/update-application/<int:application_id>", methods=["PUT"])
@login_required
@audit_log("ai_data_update_application")
def update_application(application_id):
    """
    Update an existing application.

    Expected JSON payload:
    {
        "name": "Updated Name",
        "description": "Updated description",
        "business_domain": "Updated domain"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        service = AIDataInteractionService(user_id=current_user.id)
        result = service.update_application(application_id, data)

        if result["success"]:
            return jsonify(
                {
                    "success": True,
                    "application_id": result["application_id"],
                    "message": result["message"],
                }
            )
        else:
            return jsonify({"success": False, "error": result["error"]}), 400

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@ai_data_interaction.route("/create-vendor", methods=["POST"])
@login_required
@audit_log("ai_data_create_vendor")
@rate_limit(20, "1h")
def create_vendor():
    """
    Create a new vendor organization through AI interaction.

    Expected JSON payload:
    {
        "name": "Vendor Name",
        "display_name": "Display Name",
        "vendor_type": "software_vendor",
        "headquarters_location": "Location",
        "website": "https://vendor.com"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        service = AIDataInteractionService(user_id=current_user.id)
        result = service.create_vendor(data)

        if result["success"]:
            return jsonify(
                {"success": True, "vendor_id": result["vendor_id"], "message": result["message"]}
            )
        else:
            return jsonify({"success": False, "error": result["error"]}), 400

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@ai_data_interaction.route("/update-vendor/<int:vendor_id>", methods=["PUT"])
@login_required
@audit_log("ai_data_update_vendor")
def update_vendor(vendor_id):
    """
    Update an existing vendor organization.

    Expected JSON payload:
    {
        "name": "Updated Name",
        "website": "https://updated-vendor.com",
        "strategic_tier": "tier_2_preferred"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        service = AIDataInteractionService(user_id=current_user.id)
        result = service.update_vendor(vendor_id, data)

        if result["success"]:
            return jsonify(
                {"success": True, "vendor_id": result["vendor_id"], "message": result["message"]}
            )
        else:
            return jsonify({"success": False, "error": result["error"]}), 400

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@ai_data_interaction.route("/create-capability-mapping", methods=["POST"])
@login_required
@audit_log("ai_data_create_capability_mapping")
@rate_limit(20, "1h")
def create_capability_mapping():
    """
    Create a new capability-application mapping through AI interaction.

    Expected JSON payload:
    {
        "application_id": 123,
        "capability_id": 456,
        "coverage_type": "core",
        "coverage_percentage": 85,
        "notes": "Mapping notes"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        service = AIDataInteractionService(user_id=current_user.id)
        result = service.create_capability_mapping(data)

        if result["success"]:
            return jsonify(
                {"success": True, "mapping_id": result["mapping_id"], "message": result["message"]}
            )
        else:
            return jsonify({"success": False, "error": result["error"]}), 400

    except Exception as e:
        return (
            jsonify({"success": False, "error": f"Failed to create capability mapping: {str(e)}"}),
            500,
        )


@ai_data_interaction.route("/update-capability-mapping/<int:mapping_id>", methods=["PUT"])
@login_required
@audit_log("ai_data_update_capability_mapping")
def update_capability_mapping(mapping_id):
    """
    Update an existing capability-application mapping.

    Expected JSON payload:
    {
        "coverage_type": "supporting",
        "coverage_percentage": 75,
        "notes": "Updated notes"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        service = AIDataInteractionService(user_id=current_user.id)
        result = service.update_capability_mapping(mapping_id, data)

        if result["success"]:
            return jsonify(
                {"success": True, "mapping_id": result["mapping_id"], "message": result["message"]}
            )
        else:
            return jsonify({"success": False, "error": result["error"]}), 400

    except Exception as e:
        return (
            jsonify({"success": False, "error": f"Failed to update capability mapping: {str(e)}"}),
            500,
        )


@ai_data_interaction.route("/bulk-create-applications", methods=["POST"])
@login_required
@audit_log("ai_data_bulk_create_applications")
@rate_limit(5, "1h")
def bulk_create_applications():
    """
    Create multiple applications in bulk.

    Expected JSON payload:
    {
        "applications": [
            {
                "name": "App 1",
                "description": "Description 1",
                "business_domain": "Sales"
            },
            {
                "name": "App 2",
                "description": "Description 2",
                "business_domain": "Finance"
            }
        ]
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        applications = data.get("applications", [])
        if not applications:
            return jsonify({"error": "No applications provided"}), 400

        service = AIDataInteractionService(user_id=current_user.id)
        result = service.bulk_create_applications(applications)

        return jsonify(
            {
                "success": result["success"],
                "summary": result["summary"],
                "results": result["results"],
            }
        )

    except Exception as e:
        return (
            jsonify({"success": False, "error": f"Failed to bulk create applications: {str(e)}"}),
            500,
        )


@ai_data_interaction.route("/bulk-update-applications", methods=["POST"])
@login_required
@audit_log("ai_data_bulk_update_applications")
@rate_limit(5, "1h")
def bulk_update_applications():
    """
    Update multiple applications in bulk.

    Expected JSON payload:
    {
        "updates": [
            {
                "application_id": 123,
                "update_data": {"business_domain": "Updated Sales"}
            },
            {
                "application_id": 456,
                "update_data": {"lifecycle_status": "deprecated"}
            }
        ]
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        updates = data.get("updates", [])
        if not updates:
            return jsonify({"error": "No updates provided"}), 400

        service = AIDataInteractionService(user_id=current_user.id)
        result = service.bulk_update_applications(updates)

        return jsonify(
            {
                "success": result["success"],
                "summary": result["summary"],
                "results": result["results"],
            }
        )

    except Exception as e:
        return (
            jsonify({"success": False, "error": f"Failed to bulk update applications: {str(e)}"}),
            500,
        )


# ENT-046: AI → SAD section generation ──────────────────────────────────────

# Maps SAD-01…SAD-14 keys to (service_import_path, method, kwargs_key)
# Each entry: (module_path, class_name, method_name, id_arg)
# id_arg is the kwarg name that receives solution_id.
#
# SAD-02 (solution lifecycle forecasting) has no backing implementation —
# no service in this codebase forecasts a solution's lifecycle — so it is
# listed in _SAD_SECTION_UNAVAILABLE below instead of here, and the route
# returns an honest "not available" response rather than attempting a call
# that would fail with AttributeError.
_SAD_SECTION_UNAVAILABLE = {
    "SAD-02": "Lifecycle forecasting is not implemented for this section.",
}

_SAD_SECTION_SERVICE_MAP = {
    "SAD-03": (
        "app.modules.solutions_strategic.v2.services.gap_analysis_service",
        "GapAnalysisService",
        "get_capability_health",
        "solution_id",
    ),
    "SAD-04": (
        "app.services.options_analysis_engine",
        "OptionsAnalysisEngine",
        "get_mcda_matrix",
        "solution_id",
    ),
    "SAD-05": (
        "app.services.advanced_tco_engine",
        "AdvancedTCOEngine",
        "compute_tco",
        "solution_id",
    ),
}


@ai_data_interaction.route("/generate-sad-section", methods=["POST"])
@login_required
@audit_log("ai_data_generate_sad_section")
@rate_limit(10, "1h")  # 10/hr rate-limit config, not displayed data
def generate_sad_section():
    """Generate content for a specific SAD section via the mapped backend service.

    POST body: { "solution_id": <int>, "sad_section": "SAD-01" }

    Returns: { "success": true, "preview": <dict>, "sad_section": "SAD-01" }
    """
    try:
        data = request.get_json() or {}
        solution_id = data.get("solution_id")
        sad_section = data.get("sad_section", "").upper().strip()

        if not solution_id or not isinstance(solution_id, int):
            return jsonify({"success": False, "error": "solution_id (int) is required"}), 400
        if sad_section in _SAD_SECTION_UNAVAILABLE:
            return jsonify({
                "success": False,
                "sad_section": sad_section,
                "error": _SAD_SECTION_UNAVAILABLE[sad_section],
            }), 501
        if sad_section not in _SAD_SECTION_SERVICE_MAP:
            supported = list(_SAD_SECTION_SERVICE_MAP.keys())
            return jsonify({
                "success": False,
                "error": f"sad_section must be one of {supported}",
            }), 400

        module_path, class_name, method_name, id_arg = _SAD_SECTION_SERVICE_MAP[sad_section]

        import importlib
        mod = importlib.import_module(module_path)
        service_cls = getattr(mod, class_name)
        service_instance = service_cls()
        method = getattr(service_instance, method_name)
        preview = method(**{id_arg: solution_id})

        return jsonify({
            "success": True,
            "sad_section": sad_section,
            "preview": preview,
        })

    except Exception as e:
        return jsonify({"success": False, "error": f"SAD section generation failed: {str(e)}"}), 500

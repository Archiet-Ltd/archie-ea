"""
Data modification and architect workflow routes.

Routes:
- Data CRUD: create-capability, update-capability, add-compliance-requirement,
  update-application, validate-request
- Architect workflows: generate-archimate, apply-archimate, map-apqc, apply-apqc,
  bulk-process
- Entity: suggest
"""

import logging

from flask import current_app, jsonify, request
from flask_login import current_user, login_required

from app.core.auth.decorators import require_permission
from app.decorators import audit_log
from app.modules.ai_chat.approval_gate import queue_ai_write, tag_ai_action
from app.modules.architecture.services.architect_workflow_service import ArchitectWorkflowService
from app.services.ai_data_interaction_service import AIDataInteractionService
from app.services.chat_entity_matching_service import ChatEntityMatchingService
from app.utils.validators import (
    sanitize_html,
    validate_enum,
    validate_string,
    validation_error_response,
)
from . import unified_ai_chat_bp

logger = logging.getLogger(__name__)

# ============================================================================
# DATA INTERACTION ROUTES (AI-DRIVEN DATA MODIFICATIONS)
# ============================================================================


@unified_ai_chat_bp.route("/data/create-capability", methods=["POST"])
@login_required
@audit_log("ai_chat_create_capability")
def create_capability():
    """
    Create a new business capability through AI interaction.

    Expected JSON payload:
    {
        "name": "Capability Name",
        "description": "Capability description",
        "level": "Strategic",
        "business_domain": "Domain",
        "maturity_level": "Defined"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return validation_error_response("No JSON data received")

        # Validate required name field
        name = data.get("name")
        is_valid, validated_name, error = validate_string(
            name, max_length=255, min_length=1, field_name="name", required=True
        )
        if not is_valid:
            return validation_error_response(error)
        data["name"] = sanitize_html(validated_name)

        # Validate description
        description = data.get("description")
        if description:
            is_valid, validated_desc, error = validate_string(
                description, max_length=2000, field_name="description"
            )
            if not is_valid:
                return validation_error_response(error)
            data["description"] = sanitize_html(validated_desc)

        # Validate level
        level = data.get("level")
        if level:
            valid_levels = [
                "Strategic",
                "Tactical",
                "Operational",
                "Core",
                "Supporting",
            ]
            is_valid, validated_level, error = validate_enum(
                level, valid_levels, field_name="level", case_insensitive=True
            )
            if not is_valid:
                return validation_error_response(error)
            data["level"] = validated_level

        # Validate business_domain
        business_domain = data.get("business_domain")
        if business_domain:
            is_valid, validated_domain, error = validate_string(
                business_domain, max_length=100, field_name="business_domain"
            )
            if not is_valid:
                return validation_error_response(error)
            data["business_domain"] = sanitize_html(validated_domain)

        # Validate maturity_level
        maturity_level = data.get("maturity_level")
        if maturity_level:
            valid_maturity = [
                "Initial",
                "Managed",
                "Defined",
                "Quantitatively Managed",
                "Optimizing",
            ]
            is_valid, validated_maturity, error = validate_enum(
                maturity_level,
                valid_maturity,
                field_name="maturity_level",
                case_insensitive=True,
            )
            if not is_valid:
                return validation_error_response(error)
            data["maturity_level"] = validated_maturity

        # Queue after validation/sanitization above, so a queued-and-later-
        # approved write runs the exact same sanitized `data` this route
        # would have written itself -- see approval_gate.py's docstring.
        queued = queue_ai_write(
            "create", "capability", data,
            summary=f"Create capability '{data['name']}'",
        )
        if queued is not None:
            return queued

        service = AIDataInteractionService(user_id=current_user.id)
        result = service.create_capability(data)

        if result["success"]:
            return jsonify(
                {
                    "success": True,
                    "capability_id": result["capability_id"],
                    "message": result["message"],
                }
            )
        else:
            return jsonify({"success": False, "error": result["error"]}), 400

    except Exception:
        return jsonify({"success": False, "error": "Failed to create capability"}), 500


@unified_ai_chat_bp.route(
    "/data/update-capability/<int:capability_id>", methods=["PUT"]
)
@login_required
@audit_log("ai_chat_update_capability")
def update_capability(capability_id):
    """
    Update an existing business capability.

    Expected JSON payload:
    {
        "name": "Updated Name",
        "description": "Updated description",
        "level": "Updated level"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        queued = queue_ai_write(
            "update", "capability", data,
            summary=f"Update capability #{capability_id}",
            entity_id=capability_id,
        )
        if queued is not None:
            return queued

        service = AIDataInteractionService(user_id=current_user.id)
        result = service.update_capability(capability_id, data)

        if result["success"]:
            return jsonify(
                {
                    "success": True,
                    "capability_id": result["capability_id"],
                    "message": result["message"],
                }
            )
        else:
            return jsonify({"success": False, "error": result["error"]}), 400

    except Exception:
        return jsonify({"success": False, "error": "Failed to update capability"}), 500


@unified_ai_chat_bp.route("/data/add-compliance-requirement", methods=["POST"])
@login_required
@audit_log("ai_chat_add_compliance_requirement")
def add_compliance_requirement():
    """
    Add a compliance requirement to a capability.

    Expected JSON payload:
    {
        "capability_id": 123,
        "requirement_type": "SOX",
        "description": "Compliance requirement description",
        "priority": "High"
    }

    Not gated by REQUIRE_AI_APPROVAL: AIChatApprovalService.approve_and_execute
    only knows how to execute operation_type "create"/"update"/"link"/"delete"
    against a fixed entity_type vocabulary (capability, application, vendor,
    capability_mapping, work_package, application_capability_mapping -- see
    that file), which has no "compliance_requirement" entry. Queuing this
    write would create an approval row that could never actually be executed
    by clicking Approve -- a fake queue with the same silent-drop problem
    this gate exists to fix. Honest immediate execution (still behind
    @login_required + @audit_log) beats that.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        tag_ai_action(
            action_type=request.endpoint, entity_type="compliance_requirement",
            metadata={'method': request.method, 'path': request.path},
        )
        service = AIDataInteractionService(user_id=current_user.id)
        result = service.add_compliance_requirement(data)

        if result["success"]:
            return jsonify(
                {
                    "success": True,
                    "requirement_id": result["requirement_id"],
                    "message": result["message"],
                }
            )
        else:
            return jsonify({"success": False, "error": result["error"]}), 400

    except Exception:
        return (
            jsonify(
                {"success": False, "error": "Failed to add compliance requirement"}
            ),
            500,
        )


@unified_ai_chat_bp.route("/data/create-requirement", methods=["POST"])
@login_required
@audit_log("ai_chat_create_requirement")
def create_requirement():
    """Create an ArchiMate Requirement element, optionally linked to a capability (AIC-003).

    Expected JSON payload:
    {
        "name": "Requirement statement text",
        "description": "Optional detail",
        "capability_id": 42,
        "adm_phase": "A",
        "source": "ai_chat"
    }

    Not gated by REQUIRE_AI_APPROVAL: AIChatApprovalService.approve_and_execute's
    "create" dispatch has no "requirement" entity_type (only capability,
    application, vendor, capability_mapping, work_package -- see that file).
    Queuing this would create an approval row Approve could never execute.
    Honest immediate execution (still behind @login_required + @audit_log)
    beats a fake queue.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        tag_ai_action(
            action_type=request.endpoint, entity_type="requirement",
            metadata={'method': request.method, 'path': request.path},
        )
        service = AIDataInteractionService(user_id=current_user.id)
        result = service.create_requirement(data)

        if result.get("success"):
            return jsonify(
                {
                    "success": True,
                    "element_id": result["element_id"],
                    "requirement_id": result["requirement_id"],
                    "name": result["name"],
                    "type": "Requirement",
                    "relationship_id": result.get("relationship_id"),
                }
            )
        else:
            return jsonify({"success": False, "error": result.get("error")}), 400

    except Exception:
        logger.exception("create_requirement route error")
        return jsonify({"success": False, "error": "Failed to create requirement"}), 500


@unified_ai_chat_bp.route("/data/update-application/<int:app_id>", methods=["PUT"])
@login_required
@require_permission("edit", "application")
@audit_log("ai_chat_update_application_metadata")
def update_application_metadata(app_id):
    """
    Update application metadata through AI interaction.

    Expected JSON payload:
    {
        "name": "Updated App Name",
        "description": "Updated description",
        "criticality": "High",
        "technology_stack": "Updated stack"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        queued = queue_ai_write(
            "update", "application", data,
            summary=f"Update application #{app_id}",
            entity_id=app_id,
        )
        if queued is not None:
            return queued

        # Was `service.update_application_metadata(app_id, data)` -- no such
        # method exists on AIDataInteractionService (only `update_application`),
        # so this call always raised AttributeError and 500'd regardless of
        # the approval gate. Found while verifying that a queued-and-approved
        # write performs the same operation this route would have: the
        # approval path (operation_type="update", entity_type="application")
        # correctly calls the real `update_application`, which is what
        # exposed the direct path's dead call. Aligned on the real method
        # name rather than reproducing the bug on both paths.
        service = AIDataInteractionService(user_id=current_user.id)
        result = service.update_application(app_id, data)

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
        return jsonify({"success": False, "error": "Failed to update application"}), 500


@unified_ai_chat_bp.route("/data/validate-request", methods=["POST"])
@login_required
@audit_log("ai_chat_validate_modification_request")
def validate_modification_request():
    """
    Validate if a data modification request is safe and allowed.

    Expected JSON payload:
    {
        "request_type": "create_capability",
        "data": {...},
        "context": "Additional context"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        service = AIDataInteractionService(user_id=current_user.id)
        result = service.validate_request(data)

        return jsonify(
            {
                "success": True,
                "is_valid": result["is_valid"],
                "risk_level": result["risk_level"],
                "warnings": result.get("warnings", []),
                "recommendations": result.get("recommendations", []),
            }
        )

    except Exception:
        return jsonify({"success": False, "error": "Validation failed"}), 500



# ============================================================================
# ARCHITECT WORKFLOW ROUTES
# ============================================================================


@unified_ai_chat_bp.route("/generate-archimate", methods=["POST"])
@login_required
@audit_log("ai_chat_generate_archimate")
def generate_archimate():
    """
    Generate ArchiMate elements preview for an application.

    Expected JSON payload:
    {
        "application_id": 123
    }
    """
    try:
        data = request.get_json()
        if not data or "application_id" not in data:
            return jsonify({"error": "application_id is required"}), 400

        service = ArchitectWorkflowService(user_id=current_user.id)
        result = service.generate_archimate_preview(data["application_id"])

        if result["success"]:
            tag_ai_action("generate_archimate", "application", data["application_id"])
            return jsonify(result)
        else:
            return jsonify(result), 400

    except Exception as e:
        current_app.logger.error(f"Error generating ArchiMate preview: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_ai_chat_bp.route("/apply-archimate", methods=["POST"])
@login_required
@require_permission("create", "relationship")
@audit_log("ai_chat_apply_archimate")
def apply_archimate():
    """
    Apply selected ArchiMate elements to an application.

    Expected JSON payload:
    {
        "application_id": 123,
        "elements": [
            {"name": "App Service", "type": "ApplicationService", ...}
        ]
    }

    Not gated by REQUIRE_AI_APPROVAL: this calls ArchitectWorkflowService, not
    AIDataInteractionService, and applies an arbitrary list of ArchiMate
    elements -- there is no "apply_archimate" operation_type in
    AIChatApprovalService.approve_and_execute's dispatch, and no realistic way
    to shoehorn a variable-length element list into the fixed create/update/
    delete/link vocabulary that dispatch executes. Queuing this would produce
    an approval row Approve could never execute. Honest immediate execution
    (still behind @login_required + @audit_log, and already tagged via
    tag_ai_action below on success) beats a fake queue.
    """
    try:
        data = request.get_json()
        if not data or "application_id" not in data or "elements" not in data:
            return jsonify({"error": "application_id and elements are required"}), 400

        service = ArchitectWorkflowService(user_id=current_user.id)
        result = service.apply_archimate_elements(
            data["application_id"], data["elements"]
        )

        if result["success"]:
            # Wire applied elements to junction table when instance_id is present (AIC-002)
            instance_id = data.get("instance_id")
            if instance_id and isinstance(instance_id, int) and instance_id > 0:
                try:
                    from app.services.workflow_archimate_context_service import WorkflowArchiMateContextService
                    wf_svc = WorkflowArchiMateContextService()
                    for el in data.get("elements", []):
                        name = el.get("name") or el.get("type", "ArchiMateElement")
                        el_type = el.get("type", "ApplicationComponent")
                        layer = el.get("layer", "application")
                        wf_svc.persist_derived_element(
                            phase_code=el.get("adm_phase", ""),
                            name=name[:120],
                            element_type=el_type,
                            layer=layer,
                            source_instance_id=instance_id,
                            element_role="output",
                            step_id="ai_chat_apply_archimate",
                        )
                except Exception as exc:
                    current_app.logger.warning("AIC-002: junction write failed: %s", exc)
            tag_ai_action("apply_archimate", "application", data["application_id"])
            return jsonify(result)
        else:
            return jsonify(result), 400

    except Exception as e:
        current_app.logger.error(f"Error applying ArchiMate elements: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_ai_chat_bp.route("/map-apqc", methods=["POST"])
@login_required
@audit_log("ai_chat_map_apqc")
def map_apqc():
    """
    Generate APQC mapping preview for an application.

    Expected JSON payload:
    {
        "application_id": 123
    }
    """
    try:
        data = request.get_json()
        if not data or "application_id" not in data:
            return jsonify({"error": "application_id is required"}), 400

        service = ArchitectWorkflowService(user_id=current_user.id)
        result = service.map_apqc_preview(data["application_id"])

        if result["success"]:
            return jsonify(result)
        else:
            return jsonify(result), 400

    except Exception as e:
        current_app.logger.error(f"Error mapping APQC: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_ai_chat_bp.route("/apply-apqc", methods=["POST"])
@login_required
@require_permission("create", "relationship")
@audit_log("ai_chat_apply_apqc")
def apply_apqc():
    """
    Apply selected APQC mappings to an application.

    Expected JSON payload:
    {
        "application_id": 123,
        "mappings": [
            {"process_id": 456, "confidence": 95, ...}
        ]
    }

    Not gated by REQUIRE_AI_APPROVAL: same reasoning as apply_archimate above
    -- ArchitectWorkflowService.apply_apqc_mappings has no matching
    operation_type/entity_type pair in AIChatApprovalService.approve_and_
    execute's dispatch, so queuing would produce an approval Approve could
    never execute. Honest immediate execution beats a fake queue.
    """
    try:
        data = request.get_json()
        if not data or "application_id" not in data or "mappings" not in data:
            return jsonify({"error": "application_id and mappings are required"}), 400

        service = ArchitectWorkflowService(user_id=current_user.id)
        result = service.apply_apqc_mappings(data["application_id"], data["mappings"])

        if result["success"]:
            tag_ai_action("apply_apqc", "application", data["application_id"])
            return jsonify(result)
        else:
            return jsonify(result), 400

    except Exception as e:
        current_app.logger.error(f"Error applying APQC mappings: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_ai_chat_bp.route("/bulk-process", methods=["POST"])
@login_required
@audit_log("ai_chat_bulk_process")
def bulk_process():
    """
    Bulk process a list of applications using ArchiMate or APQC workflows.

    Expected JSON payload:
    {
        "application_ids": [123, 456, 789],
        "process_type": "archimate" | "apqc"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No JSON data received"}), 400

        application_ids = data.get("application_ids")
        process_type = data.get("process_type", "archimate")

        if not application_ids or not isinstance(application_ids, list):
            return jsonify({"success": False, "error": "application_ids must be a non-empty list"}), 400

        if process_type not in ("archimate", "apqc"):
            return jsonify({"success": False, "error": "process_type must be 'archimate' or 'apqc'"}), 400

        service = ArchitectWorkflowService(user_id=current_user.id)
        results = []

        for app_id in application_ids:
            try:
                if process_type == "archimate":
                    result = service.generate_archimate_preview(int(app_id))
                else:
                    result = service.map_apqc_preview(int(app_id))
                results.append({"application_id": app_id, "success": result.get("success", False), **{
                    k: v for k, v in result.items() if k != "success"
                }})
            except Exception as exc:
                current_app.logger.warning("bulk_process: app_id=%s error: %s", app_id, exc)
                results.append({"application_id": app_id, "success": False, "error": str(exc)})

        succeeded = sum(1 for r in results if r.get("success"))
        return jsonify({
            "success": True,
            "process_type": process_type,
            "total": len(results),
            "succeeded": succeeded,
            "failed": len(results) - succeeded,
            "results": results,
        })

    except Exception as e:
        current_app.logger.error(f"Error in bulk_process: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_ai_chat_bp.route("/entities/suggest", methods=["POST"])
@login_required
@audit_log("ai_chat_suggest_entities")
def suggest_entities():
    """
    Suggest entities based on partial input or context.

    Expected JSON payload:
    {
        "partial_text": "App name starts with...",
        "context": {"domain": "applications"},
        "limit": 5
    }
    """
    try:
        data = request.get_json()
        partial_text = data.get("partial_text", "")
        context = data.get("context", {})
        limit = data.get("limit", 10)

        if not partial_text:
            return jsonify({"error": "Partial text is required"}), 400

        service = ChatEntityMatchingService()
        suggestions = service.suggest_entities(partial_text, context, limit)

        return jsonify(
            {"success": True, "suggestions": suggestions, "partial_text": partial_text}
        )

    except Exception:
        return jsonify(
            {
                "error": "Failed to get suggestions",
                "details": "See server logs for details",
            }
        ), 500



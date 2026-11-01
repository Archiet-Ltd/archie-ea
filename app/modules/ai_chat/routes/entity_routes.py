"""
Entity matching, business output transformation, and architecture generation routes.

Routes:
- Business output: transform-output, available-roles, role-info, batch-transform
- Entity matching: entities/match, entities/types, entities/resolve,
  knowledge-graph/related, api/entities/match, entity-matching (POST)
- Architecture: architecture/generate, architecture/validate
"""

import logging

from flask import current_app, jsonify, request
from flask_login import current_user, login_required

from app.decorators import audit_log
from app.services.ai_architecture_service import CognitiveArchitectureService
from app.services.business_output_service import BusinessOutputService, StakeholderRole
from app.services.chat_entity_matching_service import ChatEntityMatchingService
from app.utils.duplicate_guard import find_similar_entities
from app.modules.ai_chat.services.failure_messages import ai_unavailable_notice
from . import unified_ai_chat_bp

logger = logging.getLogger(__name__)


def _is_llm_not_configured_error(message: str) -> bool:
    """True when an error string names an unconfigured/unavailable LLM
    provider rather than some other failure — used to distinguish an honest
    503 ("this feature needs an LLM you haven't set up") from a genuine 500."""
    if not message:
        return False
    lowered = message.lower()
    return (
        "no enabled llm provider" in lowered
        or "llm provider" in lowered and "configur" in lowered
        or "api key" in lowered and "not" in lowered
    )

# ============================================================================
# BUSINESS OUTPUT TRANSFORMATION ROUTES
# ============================================================================


@unified_ai_chat_bp.route("/transform-output", methods=["POST"])
@login_required
@audit_log("ai_chat_transform_output")
def transform_output():
    """
    Transform AI chat response for specific stakeholder role.

    Expected JSON payload:
    {
        "ai_response": {
            "response": "AI response text",
            "domain": "domain_name",
            "metadata": {}
        },
        "role": "business_analyst"  # or other stakeholder role
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        ai_response = data.get("ai_response")
        role_value = data.get("role")

        if not ai_response:
            return jsonify({"error": "AI response is required"}), 400

        if not role_value:
            return jsonify({"error": "Stakeholder role is required"}), 400

        # Validate role
        try:
            role = StakeholderRole(role_value)
        except ValueError:
            return jsonify({"error": f"Invalid role: {role_value}"}), 400

        # Transform response
        service = BusinessOutputService()
        transformed = service.transform_for_stakeholder(ai_response, role)

        return jsonify({"success": True, "transformed_output": transformed})

    except Exception:
        return jsonify(
            {"error": "Transformation failed", "details": "See server logs for details"}
        ), 500


@unified_ai_chat_bp.route("/available-roles", methods=["GET"])
@login_required
def get_available_roles():
    """Get list of available stakeholder roles."""
    try:
        service = BusinessOutputService()
        roles = service.get_available_roles()

        return jsonify({"success": True, "roles": roles})

    except Exception:
        return jsonify(
            {"error": "Failed to get roles", "details": "See server logs for details"}
        ), 500


@unified_ai_chat_bp.route("/role-info/<role_value>", methods=["GET"])
@login_required
def get_role_info(role_value):
    """Get detailed information about a specific role."""
    try:
        # Validate role
        try:
            StakeholderRole(role_value)
        except ValueError:
            return jsonify({"error": f"Invalid role: {role_value}"}), 400

        service = BusinessOutputService()
        roles = service.get_available_roles()

        # Find the specific role
        role_info = next((r for r in roles if r["value"] == role_value), None)

        if not role_info:
            return jsonify({"error": "Role not found"}), 404

        return jsonify({"success": True, "role_info": role_info})

    except Exception:
        return jsonify(
            {
                "error": "Failed to get role info",
                "details": "See server logs for details",
            }
        ), 500


@unified_ai_chat_bp.route("/batch-transform", methods=["POST"])
@login_required
@audit_log("ai_chat_batch_transform_outputs")
def batch_transform_outputs():
    """
    Transform multiple AI responses for different stakeholder roles.

    Expected JSON payload:
    {
        "transformations": [
            {
                "ai_response": {...},
                "role": "business_analyst"
            },
            {
                "ai_response": {...},
                "role": "executive"
            }
        ]
    }
    """
    try:
        data = request.get_json()
        if not data or "transformations" not in data:
            return jsonify({"error": "No transformations data received"}), 400

        transformations = data["transformations"]
        service = BusinessOutputService()
        results = []

        for transformation in transformations:
            ai_response = transformation.get("ai_response")
            role_value = transformation.get("role")

            if ai_response and role_value:
                try:
                    role = StakeholderRole(role_value)
                    transformed = service.transform_for_stakeholder(ai_response, role)
                    results.append(
                        {
                            "success": True,
                            "role": role_value,
                            "transformed_output": transformed,
                        }
                    )
                except ValueError:
                    results.append(
                        {
                            "success": False,
                            "role": role_value,
                            "error": f"Invalid role: {role_value}",
                        }
                    )
            else:
                results.append(
                    {
                        "success": False,
                        "role": role_value,
                        "error": "Missing ai_response or role",
                    }
                )

        return jsonify({"success": True, "results": results})

    except Exception:
        return jsonify(
            {
                "error": "Batch transformation failed",
                "details": "See server logs for details",
            }
        ), 500


# ============================================================================
# ENTITY MATCHING ROUTES
# ============================================================================


@unified_ai_chat_bp.route("/entities/match", methods=["POST"])
@login_required
@audit_log("ai_chat_match_entities")
def match_entities_ui():
    """Match entities based on input text using AI."""
    try:
        data = request.get_json()
        text = data.get("text", "")
        entity_types = data.get(
            "entity_types", []
        )  # Optional: limit to specific entity types
        context = data.get("context", {})  # Optional: additional context

        if not text:
            return jsonify({"error": "Text input is required"}), 400

        service = ChatEntityMatchingService()
        result = service.match_entities(text, entity_types, context)

        return jsonify(
            {
                "success": result.get("success", True),
                "matches": result.get("matches", []),
                "total": result.get("total", 0),
                "text_processed": text,
                "context_used": bool(context),
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error in match_entities_ui: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_ai_chat_bp.route("/entities/types", methods=["GET"])
@login_required
def get_entity_types():
    """Get available entity types for matching."""
    try:
        service = ChatEntityMatchingService()
        entity_types = service.get_available_entity_types()

        return jsonify({"success": True, "entity_types": entity_types})

    except Exception:
        return jsonify(
            {
                "error": "Failed to get entity types",
                "details": "See server logs for details",
            }
        ), 500


@unified_ai_chat_bp.route("/entity-matching", methods=["POST"])
@login_required
@audit_log("ai_chat_entity_matching_message")
def entity_matching_message():
    """Entity Matching Assistant chat turn (M-03).

    The chat interface (entity_matching_chat_interface.html) POSTs here on
    every message; only a GET view existed (chat_views.py), so every submit
    405'd, ``response.json()`` threw on the HTML error page, and the UI's
    catch block showed a generic "Sorry, I encountered an error" no matter
    what the user typed. This is the route the JS has always expected.

    Extracts entities from the message via the chat-aware matcher, then adds
    an honest near-duplicate pass against the live repository through
    ``find_similar_entities`` (ARCH-030/S-06) so a genuine LLM extraction is
    also compared against what already exists, rather than the two entity
    ingestion paths (this one and document upload) growing separate ad hoc
    duplicate logic.
    """
    data = request.get_json() or {}
    document_text = (data.get("document_text") or data.get("text") or "").strip()
    if not document_text:
        return jsonify({"success": False, "error": "document_text is required"}), 400

    persona = data.get("user_persona", data.get("persona", "enterprise_architect"))
    domain = data.get("domain", "architecture")
    chat_history = data.get("chat_history")

    try:
        matcher = ChatEntityMatchingService()
        result = matcher.analyze_document_with_chat_context(
            document_text=document_text,
            user_persona=persona,
            domain=domain,
            chat_history=chat_history,
        )
    except Exception as e:
        current_app.logger.error(f"[AI_CHAT] Entity matching crashed: {e}", exc_info=True)
        return jsonify(
            {"success": False, "error": f"Entity matching failed: {e}"}
        ), 500

    if result.get("permission_denied"):
        return jsonify(
            {"success": False, "error": result["error"], "code": "PERMISSION_DENIED"}
        ), 403

    if not result.get("success"):
        error_text = str(result.get("error") or "Entity matching failed")
        if _is_llm_not_configured_error(error_text):
            is_admin = bool(getattr(current_user, "is_org_admin", False))
            notice = ai_unavailable_notice("entity_matching", is_admin)
            return jsonify(
                {
                    "success": False,
                    "error": notice["sentence"],
                    "code": "LLM_NOT_CONFIGURED",
                    "cta_text": notice["cta_text"],
                    "cta_href": notice["cta_href"],
                }
            ), 503
        current_app.logger.error(f"[AI_CHAT] Entity matching failed: {error_text}")
        return jsonify({"success": False, "error": error_text}), 500

    # Honest near-duplicate advisory: compare each extracted ArchiMate element
    # name against the live repository rather than trusting the LLM's own
    # notion of "new" vs "duplicate". Advisory only — never blocks the chat
    # response, and never fabricates a match when none is found.
    try:
        from app.models.archimate_core import ArchiMateElement

        extracted = result.get("document_analysis", {}).get("extracted_entities", {})
        elements = extracted.get("archimate_elements") or []
        duplicate_guard_matches = {}
        for elem in elements:
            name = elem.get("name") if isinstance(elem, dict) else None
            if not name:
                continue
            similar = find_similar_entities(ArchiMateElement, name)
            if similar:
                duplicate_guard_matches[name] = similar
        result["duplicate_guard_matches"] = duplicate_guard_matches
    except Exception as e:
        current_app.logger.warning(f"[AI_CHAT] duplicate_guard advisory pass failed: {e}")

    return jsonify(result), 200


# ============================================================================
# ARCHITECTURE GENERATION ROUTES
# ============================================================================


@unified_ai_chat_bp.route("/architecture/generate", methods=["POST"])
@login_required
@audit_log("ai_chat_generate_architecture")
def generate_architecture():
    """
    Generate ArchiMate architecture elements from natural language.

    Expected JSON payload:
    {
        "description": "Generate a business process for customer onboarding",
        "layer": "business",
        "element_type": "BusinessProcess",
        "context": {}
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        description = data.get("description")
        layer = data.get("layer", "business")
        element_type = data.get("element_type", "BusinessProcess")
        context = data.get("context", {})

        if not description:
            return jsonify({"error": "Description is required"}), 400

        service = CognitiveArchitectureService()
        result = service.generate_element(description, layer, element_type, context)

        return jsonify(
            {
                "success": True,
                "element": result.get("element"),
                "metadata": result.get("metadata", {}),
                "visualization": result.get("visualization", {}),
            }
        )

    except Exception:
        return jsonify(
            {
                "error": "Architecture generation failed",
                "details": "See server logs for details",
            }
        ), 500


@unified_ai_chat_bp.route("/architecture/validate", methods=["POST"])
@login_required
@audit_log("ai_chat_validate_architecture")
def validate_architecture():
    """
    Validate ArchiMate architecture elements.

    Expected JSON payload:
    {
        "elements": [...],
        "relationships": [...]
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        service = CognitiveArchitectureService()
        validation_result = service.validate_architecture(data)

        return jsonify(
            {
                "success": True,
                "is_valid": validation_result.get("is_valid", False),
                "issues": validation_result.get("issues", []),
                "recommendations": validation_result.get("recommendations", []),
            }
        )

    except Exception:
        return jsonify(
            {
                "error": "Architecture validation failed",
                "details": "See server logs for details",
            }
        ), 500



# ============================================================================
# ADDITIONAL ENTITY ROUTES
# ============================================================================


@unified_ai_chat_bp.route("/entities/resolve", methods=["POST"])
@login_required
@audit_log("ai_chat_resolve_entity")
def resolve_entity():
    """Resolve entity name (acronym expansion, normalization, etc.)."""
    try:
        from app.services.archimate.entity_resolution_service import (
            EntityResolutionService,
        )

        data = request.json
        entity_name = data.get("entity_name")
        entity_type = data.get("entity_type")
        context = data.get("context")

        if not entity_name:
            return jsonify({"success": False, "error": "Missing entity_name"}), 400

        resolver = EntityResolutionService()
        resolution = resolver.resolve_entity(entity_name, entity_type, context)

        return jsonify({"success": True, "resolution": resolution})

    except Exception as e:
        current_app.logger.error(f"Error resolving entity: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_ai_chat_bp.route("/knowledge-graph/related", methods=["POST"])
@login_required
@audit_log("ai_chat_get_related_entities")
def get_related_entities():
    """Get related entities from knowledge graph."""
    try:
        from app.services.archimate.knowledge_graph_service import KnowledgeGraphService

        data = request.json
        element = data.get("element")

        if not element:
            return jsonify({"success": False, "error": "Missing element"}), 400

        kg_service = KnowledgeGraphService()
        related = kg_service.get_semantic_context(element, max_context=5)

        return jsonify({"success": True, "related_entities": related})

    except Exception as e:
        current_app.logger.error(f"Error getting related entities: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_ai_chat_bp.route("/api/entities/match", methods=["POST"])
@login_required
@audit_log("ai_chat_match_entities_api")
def match_entities_api():
    """Match entities based on input text using chat-aware entity matching."""
    data = request.get_json() or {}
    text = data.get("text", "").strip()

    if not text:
        return jsonify({"success": False, "error": "Text is required"}), 400

    persona = data.get("persona", "enterprise_architect")
    domain = data.get("domain", "architecture")
    chat_history = data.get("chat_history")

    try:
        matcher = ChatEntityMatchingService()
        result = matcher.analyze_document_with_chat_context(
            document_text=text,
            user_persona=persona,
            domain=domain,
            chat_history=chat_history,
        )

        status_code = 200 if result.get("success") else 500
        return jsonify(result), status_code

    except Exception as e:
        current_app.logger.error(f"[AI_CHAT] Entity match error: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


"""
Enterprise API - REST/gRPC API Endpoints for Core Services

Provides comprehensive REST API access to all core enterprise architecture services:
- Knowledge Graph operations
- ArchiMate model management
- Capability analysis and mapping
- Vendor analysis and MDM
- Application portfolio management
- Security and audit APIs
- Options analysis and decision support
- Workflow orchestration

Supports both REST and gRPC protocols for maximum interoperability.
"""

import logging

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app import db
from app.core.auth.decorators import require_permission
from app.decorators import audit_log
from app.services.application_capability_catalog import ApplicationCapabilityCatalogService
from app.services.archimate.archimate_service import ArchiMateService
from app.services.capability_mapping_service import CapabilityMappingService
from app.services.kg.knowledge_graph_service import KnowledgeGraphService
from app.services.unified_vendor_services import UnifiedVendorServices
from app.utils.pagination import safe_int_arg

try:
    from app.services.options_analysis_engine import OptionsAnalysisEngine

    options_service = OptionsAnalysisEngine()
except Exception:
    OptionsAnalysisEngine = None
    options_service = None
    logger = logging.getLogger(__name__)
    logger.warning(
        "OptionsAnalysisEngine not available; continuing without Options Analysis functionality"
    )

from app.security.audit import AuditEventSeverity, AuditEventType, audit_logger
from app.services.arb_governance_service import ARBGovernanceService

# Initialize services
kg_service = KnowledgeGraphService()
archimate_service = ArchiMateService()
capability_service = CapabilityMappingService()
vendor_service = UnifiedVendorServices()
application_service = ApplicationCapabilityCatalogService()
if OptionsAnalysisEngine is not None and options_service is None:
    options_service = OptionsAnalysisEngine()
# The one creator the architecture review pages use (app/modules/architecture/routes/arb_routes.py).
arb_service = ARBGovernanceService()

logger = logging.getLogger(__name__)

enterprise_api_bp = Blueprint("enterprise_api", __name__, url_prefix="/api/v2/enterprise")


# =============================================================================
# KNOWLEDGE GRAPH API
# =============================================================================


@enterprise_api_bp.route("/kg/elements", methods=["GET"])
@login_required
@require_permission("read", "enterprise_record")
def get_kg_elements():
    """
    Get ArchiMate elements from knowledge graph

    Query Parameters:
    - type: Element type filter (BusinessActor, BusinessProcess, etc.)
    - domain: Business domain filter
    - limit: Maximum results (default: 100)
    - offset: Pagination offset (default: 0)
    """
    try:
        element_type = request.args.get("type")
        domain = request.args.get("domain")
        limit = safe_int_arg('limit', 100, minimum=1, maximum=500)
        offset = safe_int_arg('offset', 0, minimum=0)

        elements = kg_service.get_elements(
            element_type=element_type, domain=domain, limit=limit, offset=offset
        )

        audit_logger.log_event(
            event_type=AuditEventType.DATA_ACCESS,
            severity=AuditEventSeverity.LOW,
            action="kg_elements_retrieved",
            resource_type="knowledge_graph",
            details={"count": len(elements), "filters": {"type": element_type, "domain": domain}},
        )

        return jsonify({"success": True, "data": elements, "count": len(elements)})

    except Exception as e:
        logger.error(f"KG elements retrieval failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@enterprise_api_bp.route("/kg/relationships", methods=["GET"])
@login_required
@require_permission("read", "enterprise_record")
def get_kg_relationships():
    """
    Get ArchiMate relationships from knowledge graph

    Query Parameters:
    - type: Relationship type filter
    - source_id: Source element ID
    - target_id: Target element ID
    """
    try:
        rel_type = request.args.get("type")
        source_id = request.args.get("source_id")
        target_id = request.args.get("target_id")

        relationships = kg_service.get_relationships(
            relationship_type=rel_type, source_id=source_id, target_id=target_id
        )

        return jsonify({"success": True, "data": relationships, "count": len(relationships)})

    except Exception as e:
        logger.error(f"KG relationships retrieval failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@enterprise_api_bp.route("/kg/search", methods=["GET"])
@login_required
@require_permission("read", "enterprise_record")
def search_kg():
    """
    Search knowledge graph elements

    Query Parameters:
    - q: Search query
    - type: Element type filter
    - limit: Maximum results (default: 50)
    """
    try:
        query = request.args.get("q", "").strip()
        element_type = request.args.get("type")
        limit = safe_int_arg('limit', 50, minimum=1, maximum=500)

        if not query:
            return jsonify({"success": False, "error": "Search query required"}), 400

        results = kg_service.search_elements(query=query, element_type=element_type, limit=limit)

        audit_logger.log_event(
            event_type=AuditEventType.DATA_ACCESS,
            severity=AuditEventSeverity.LOW,
            action="kg_search_performed",
            resource_type="knowledge_graph",
            details={"query": query, "results_count": len(results)},
        )

        return jsonify({"success": True, "data": results, "count": len(results)})

    except Exception as e:
        logger.error(f"KG search failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# ARCHIMATE MODEL API
# =============================================================================


@enterprise_api_bp.route("/archimate/models", methods=["GET"])
@login_required
@require_permission("read", "enterprise_record")
def get_archimate_models():
    """
    Get available ArchiMate models

    Query Parameters:
    - domain: Business domain filter
    - status: Model status filter (draft, published, archived)
    """
    try:
        domain = request.args.get("domain")
        status = request.args.get("status")

        models = archimate_service.get_models(domain=domain, status=status)

        return jsonify({"success": True, "data": models, "count": len(models)})

    except Exception as e:
        logger.error(f"ArchiMate models retrieval failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@enterprise_api_bp.route("/archimate/models/<model_id>", methods=["GET"])
@login_required
@require_permission("read", "enterprise_record")
def get_archimate_model(model_id):
    """
    Get specific ArchiMate model with full details
    """
    try:
        model = archimate_service.get_architecture_model(model_id)
        if not model:
            return jsonify({"success": False, "error": "Model not found"}), 404

        return jsonify({"success": True, "data": model.to_dict()})

    except Exception as e:
        logger.error(f"ArchiMate model retrieval failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@enterprise_api_bp.route("/archimate/export/<model_id>", methods=["GET"])
@login_required
@require_permission("read", "enterprise_record")
def export_archimate_model(model_id):
    """
    Export ArchiMate model in ArchiMate exchange format

    Query Parameters:
    - format: Export format (xml, json) - default: xml
    """
    try:
        # ArchiMate exchange-format export is not implemented in ArchiMateService yet.
        # Return an honest 501 rather than crashing on a missing method.
        return jsonify({
            "success": False,
            "error": "ArchiMate model export is not implemented yet",
        }), 501

    except Exception as e:
        logger.error(f"ArchiMate model export failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# CAPABILITY ANALYSIS API
# =============================================================================


@enterprise_api_bp.route("/capabilities/analysis", methods=["POST"])
@login_required
@require_permission("read", "enterprise_record")
@audit_log("enterprise_capabilities_analyze")
def analyze_capabilities():
    """
    Perform capability gap analysis

    Request Body:
    {
        "current_capabilities": ["capability_id_1", "capability_id_2"],
        "target_capabilities": ["capability_id_3", "capability_id_4"],
        "domain": "business_domain"  // optional
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body required"}), 400

        current_caps = data.get("current_capabilities", [])
        target_caps = data.get("target_capabilities", [])
        domain = data.get("domain")

        if not current_caps or not target_caps:
            return (
                jsonify({"success": False, "error": "Current and target capabilities required"}),
                400,
            )

        analysis_result = capability_service.analyze_gaps(
            current_capabilities=current_caps, target_capabilities=target_caps, domain=domain
        )

        audit_logger.log_event(
            event_type=AuditEventType.DATA_ACCESS,
            severity=AuditEventSeverity.LOW,
            action="capability_analysis_performed",
            resource_type="capability_analysis",
            details={"current_count": len(current_caps), "target_count": len(target_caps)},
        )

        return jsonify({"success": True, "data": analysis_result})

    except Exception as e:
        logger.error(f"Capability analysis failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@enterprise_api_bp.route("/capabilities/mapping", methods=["GET"])
@login_required
@require_permission("read", "enterprise_record")
def get_capability_mappings():
    """
    Get capability mappings for applications

    Query Parameters:
    - application_id: Filter by application
    - domain: Business domain filter
    - maturity_level: Filter by maturity level
    """
    try:
        application_id = request.args.get("application_id")
        domain = request.args.get("domain")
        maturity_level = request.args.get("maturity_level")

        mappings = capability_service.get_mappings(
            application_id=application_id, domain=domain, maturity_level=maturity_level
        )

        return jsonify({"success": True, "data": mappings, "count": len(mappings)})

    except Exception as e:
        logger.error(f"Capability mappings retrieval failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# VENDOR ANALYSIS API
# =============================================================================


@enterprise_api_bp.route("/vendors/analysis", methods=["POST"])
@login_required
@require_permission("read", "enterprise_record")
@audit_log("enterprise_vendors_analyze")
def analyze_vendors():
    """
    Perform vendor analysis for requirements

    Request Body:
    {
        "requirements": ["req_1", "req_2"],
        "criteria": {
            "cost_weight": 0.3,
            "capability_weight": 0.4,
            "risk_weight": 0.3
        },
        "vendor_ids": ["vendor_1", "vendor_2"]  // optional filter
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body required"}), 400

        requirements = data.get("requirements", [])
        criteria = data.get("criteria", {})
        vendor_ids = data.get("vendor_ids")

        if not requirements:
            return jsonify({"success": False, "error": "Requirements required"}), 400

        analysis_result = vendor_service.analyze_vendors(
            requirements=requirements, criteria=criteria, vendor_filter=vendor_ids
        )

        audit_logger.log_event(
            event_type=AuditEventType.DATA_ACCESS,
            severity=AuditEventSeverity.LOW,
            action="vendor_analysis_performed",
            resource_type="vendor_analysis",
            details={
                "requirements_count": len(requirements),
                "vendors_count": len(analysis_result.get("vendors", [])),
            },
        )

        return jsonify({"success": True, "data": analysis_result})

    except Exception as e:
        logger.error(f"Vendor analysis failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@enterprise_api_bp.route("/vendors/products", methods=["GET"])
@login_required
@require_permission("read", "enterprise_record")
def get_vendor_products():
    """
    Get vendor products with capabilities

    Query Parameters:
    - vendor_id: Filter by vendor
    - category: Product category filter
    - capability: Capability filter
    """
    try:
        vendor_id = request.args.get("vendor_id")
        category = request.args.get("category")
        capability = request.args.get("capability")

        products = vendor_service.get_products(
            vendor_id=vendor_id, category=category, capability=capability
        )

        return jsonify({"success": True, "data": products, "count": len(products)})

    except Exception as e:
        logger.error(f"Vendor products retrieval failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# APPLICATION PORTFOLIO API
# =============================================================================


@enterprise_api_bp.route("/applications/portfolio", methods=["GET"])
@login_required
@require_permission("read", "application")
def get_application_portfolio():
    """
    Get application portfolio with capabilities

    Query Parameters:
    - domain: Business domain filter
    - criticality: Criticality level filter
    - status: Application status filter
    """
    try:
        domain = request.args.get("domain")
        criticality = request.args.get("criticality")
        status = request.args.get("status")

        portfolio = application_service.get_portfolio(
            domain=domain, criticality=criticality, status=status
        )

        return jsonify({"success": True, "data": portfolio, "count": len(portfolio)})

    except Exception as e:
        logger.error(f"Application portfolio retrieval failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@enterprise_api_bp.route("/applications/capabilities/<app_id>", methods=["GET"])
@login_required
@require_permission("read", "application")
def get_application_capabilities(app_id):
    """
    Get capabilities mapped to specific application
    """
    try:
        capabilities = application_service.get_application_capabilities(app_id)

        return jsonify({"success": True, "data": capabilities, "count": len(capabilities)})

    except Exception as e:
        logger.error(f"Application capabilities retrieval failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# OPTIONS ANALYSIS API
# =============================================================================


@enterprise_api_bp.route("/options/analysis", methods=["POST"])
@login_required
@require_permission("read", "enterprise_record")
@audit_log("enterprise_options_analyze")
def analyze_options():
    """
    Perform multi-criteria options analysis

    Request Body:
    {
        "scenario": "digital_transformation",
        "options": [
            {"id": "option_1", "name": "Cloud Migration", "costs": {...}, "benefits": {...}},
            {"id": "option_2", "name": "On-Premise Upgrade", "costs": {...}, "benefits": {...}}
        ],
        "criteria": {
            "cost": {"weight": 0.4, "direction": "minimize"},
            "benefit": {"weight": 0.4, "direction": "maximize"},
            "risk": {"weight": 0.2, "direction": "minimize"}
        }
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body required"}), 400

        scenario = data.get("scenario")
        options = data.get("options", [])
        criteria = data.get("criteria", {})

        if not scenario or not options or not criteria:
            return (
                jsonify({"success": False, "error": "Scenario, options, and criteria required"}),
                400,
            )

        analysis_result = options_service.analyze_options(
            scenario=scenario, options=options, criteria=criteria
        )

        audit_logger.log_event(
            event_type=AuditEventType.DATA_ACCESS,
            severity=AuditEventSeverity.LOW,
            action="options_analysis_performed",
            resource_type="options_analysis",
            details={"scenario": scenario, "options_count": len(options)},
        )

        return jsonify({"success": True, "data": analysis_result})

    except Exception as e:
        logger.error(f"Options analysis failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# WORKFLOW ORCHESTRATION API
# =============================================================================


@enterprise_api_bp.route("/workflows/arb", methods=["POST"])
@login_required
@require_permission("create", "review_workflow")
@audit_log("enterprise_arb_workflow_create")
def create_arb_workflow():
    """
    Create ARB (Architecture Review Board) workflow

    Request Body:
    {
        "title": "Q1 Architecture Review",
        "description": "Review of digital transformation initiatives",
        "artifacts": ["model_id_1", "document_id_2"],
        "reviewers": ["user_id_1", "user_id_2"],
        "deadline": "2026 - 02 - 15"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body required"}), 400

        title = data.get("title")
        description = data.get("description")
        reviewers = data.get("reviewers", [])

        if not title or not reviewers:
            return jsonify({"success": False, "error": "Title and reviewers required"}), 400

        review = arb_service.submit_for_review(
            title=title,
            description=description,
            review_type="architecture_change",
            submitter_id=current_user.id,
            decision_sought=description or title,
        )
        workflow = review.to_dict(include_details=False)

        audit_logger.log_event(
            event_type=AuditEventType.DATA_MODIFICATION,
            severity=AuditEventSeverity.LOW,
            action="arb_workflow_created",
            resource_type="arb_workflow",
            resource_id=workflow.get("id"),
            details={"title": title, "reviewers_count": len(reviewers)},
        )

        return jsonify({"success": True, "data": workflow}), 201

    except Exception as e:
        logger.error(f"ARB workflow creation failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@enterprise_api_bp.route("/workflows/arb/<workflow_id>", methods=["GET"])
@login_required
@require_permission("read", "review_workflow")
def get_arb_workflow(workflow_id):
    """
    Get ARB workflow details
    """
    try:
        # No get-workflow-by-id service method exists; return an honest 501
        # rather than crashing on a missing method.
        return jsonify({
            "success": False,
            "error": "ARB workflow retrieval by id is not implemented yet",
        }), 501

    except Exception as e:
        logger.error(f"ARB workflow retrieval failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# HEALTH CHECK API
# =============================================================================


@enterprise_api_bp.route("/health", methods=["GET"])
@login_required
def health_check():
    """
    Enterprise API health check endpoint
    """
    return jsonify(
        {
            "success": True,
            "service": "Enterprise API v2",
            "status": "healthy",
            "version": "2.0.0",
            "endpoints": [
                "/api/v2/enterprise/kg/*",
                "/api/v2/enterprise/archimate/*",
                "/api/v2/enterprise/capabilities/*",
                "/api/v2/enterprise/vendors/*",
                "/api/v2/enterprise/applications/*",
                "/api/v2/enterprise/options/*",
                "/api/v2/enterprise/workflows/*",
            ],
        }
    )


# =============================================================================
# API DOCUMENTATION ENDPOINT
# =============================================================================


@enterprise_api_bp.route("/docs", methods=["GET"])
@login_required
def api_documentation():
    """
    Get API documentation and OpenAPI specification
    """
    docs = {
        "openapi": "3.0.0",
        "info": {
            "title": "Enterprise Architecture API",
            "version": "2.0.0",
            "description": "REST API for Enterprise Architecture Platform",
        },
        "servers": [{"url": "/api/v2/enterprise", "description": "Enterprise API v2"}],
        "paths": {
            "/kg/elements": {
                "get": {
                    "summary": "Get ArchiMate elements",
                    "parameters": [
                        {"name": "type", "in": "query", "schema": {"type": "string"}},
                        {"name": "domain", "in": "query", "schema": {"type": "string"}},
                        {"name": "limit", "in": "query", "schema": {"type": "integer"}},
                    ],
                }
            },
            "/archimate/models": {"get": {"summary": "Get ArchiMate models"}},
            "/capabilities/analysis": {
                "post": {
                    "summary": "Perform capability gap analysis",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "current_capabilities": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                        "target_capabilities": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                    },
                                }
                            }
                        },
                    },
                }
            },
        },
    }

    return jsonify(docs)


@enterprise_api_bp.errorhandler(404)
def enterprise_not_found(error):
    """Handle blueprint-scoped 404 errors."""
    logger.warning(
        "Enterprise API 404 route=%s method=%s: %s",
        request.path,
        request.method,
        error,
    )
    return jsonify({"success": False, "error": "Endpoint not found"}), 404


@enterprise_api_bp.errorhandler(500)
def enterprise_internal_error(error):
    """Handle blueprint-scoped 500 errors."""
    logger.error(
        "Enterprise API 500 route=%s method=%s: %s",
        request.path,
        request.method,
        error,
        exc_info=True,
    )
    db.session.rollback()
    return jsonify({"success": False, "error": "An internal error occurred"}), 500

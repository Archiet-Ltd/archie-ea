"""
Migration: Copied from app/api/acm_routes.py -> app/modules/capabilities/api/
Date: 2026-02-14 | Relative imports fixed for new location.

ACM Technical Capability API Routes

REST API for Application Capability Model (ACM) technical capabilities.
Provides endpoints for CRUD operations, mappings, and gap analysis.
"""

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app.decorators import audit_log
from app.middleware.tenant_decorators import platform_admin_required

from app.models.application_portfolio import ApplicationComponent
from app.models.technical_capability import TechnicalCapability
from app.services.acm_technical_capability_service import ACMTechnicalCapabilityService
from app.utils.pagination import safe_int_arg
from app.utils.route_guards import require_entity

acm_bp = Blueprint("acm", __name__, url_prefix="/api/acm")


@acm_bp.route("/domains", methods=["GET"])
@login_required
def get_domains():
    """Get all ACM domains with descriptions."""
    domains = ACMTechnicalCapabilityService.get_domains()
    return jsonify(
        {
            "success": True,
            "domains": domains,
            "count": len(domains),
        }
    )


@acm_bp.route("/capabilities", methods=["GET"])
@login_required
def get_capabilities():
    """
    Get all technical capabilities with optional filtering.
    Query params: domain, level, search, page, per_page
    """
    domain = request.args.get("domain")
    level = request.args.get("level")
    search = request.args.get("search")
    page = safe_int_arg('page', 1, minimum=1)
    per_page = safe_int_arg('per_page', 50, minimum=1, maximum=500)

    capabilities, total = ACMTechnicalCapabilityService.get_all_capabilities(
        domain=domain,
        level=level,
        search=search,
        page=page,
        per_page=per_page,
    )

    return jsonify(
        {
            "success": True,
            "capabilities": [c.to_dict() for c in capabilities],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
        }
    )


@acm_bp.route("/capabilities/<int:capability_id>", methods=["GET"])
@login_required
def get_capability(capability_id):
    """Get a single technical capability by ID."""
    capability = ACMTechnicalCapabilityService.get_capability_by_id(capability_id)
    if not capability:
        return jsonify({"success": False, "error": "Capability not found"}), 404

    return jsonify(
        {
            "success": True,
            "capability": capability.to_dict(),
            "full_path": capability.get_full_path(),
            "domain_description": capability.get_domain_description(),
            "related_apqc_categories": capability.get_related_apqc_categories(),
        }
    )


@acm_bp.route("/hierarchy", methods=["GET"])
@login_required
def get_hierarchy():
    """Get hierarchical structure of capabilities for tree view."""
    domain = request.args.get("domain")
    hierarchy = ACMTechnicalCapabilityService.get_hierarchy(domain)
    return jsonify(
        {
            "success": True,
            "hierarchy": hierarchy,
        }
    )


@acm_bp.route("/summary", methods=["GET"])
@login_required
def get_domain_summary():
    """Get summary statistics for each ACM domain."""
    summary = ACMTechnicalCapabilityService.get_domain_summary()
    return jsonify(
        {
            "success": True,
            "summary": summary,
        }
    )


@acm_bp.route("/seed", methods=["POST"])
@login_required
@audit_log("acm_capabilities_seed")
@platform_admin_required
def seed_capabilities():
    """Seed the database with ACM technical capabilities."""
    result = ACMTechnicalCapabilityService.seed_capabilities()
    return jsonify(
        {
            "success": True,
            "message": "ACM capabilities seeded successfully",
            **result,
        }
    )


# Business Capability Mapping
@acm_bp.route("/capabilities/<int:capability_id>/business-mappings", methods=["GET"])
@login_required
def get_business_mappings(capability_id):
    """Get business capabilities mapped to a technical capability."""
    require_entity(TechnicalCapability, capability_id, description="Technical capability not found")
    mappings = ACMTechnicalCapabilityService.get_business_capability_mappings(capability_id)
    return jsonify(
        {
            "success": True,
            "mappings": mappings,
            "count": len(mappings),
        }
    )


@acm_bp.route("/capabilities/<int:capability_id>/business-mappings", methods=["POST"])
@login_required
@audit_log("acm_business_mapping_create")
def create_business_mapping(capability_id):
    """Map a technical capability to a business capability."""
    data = request.get_json()
    business_capability_id = data.get("business_capability_id")
    relationship_type = data.get("relationship_type", "supports")
    strength = data.get("strength", "medium")

    if not business_capability_id:
        return jsonify({"success": False, "error": "business_capability_id is required"}), 400

    success = ACMTechnicalCapabilityService.map_to_business_capability(
        capability_id, business_capability_id, relationship_type, strength
    )

    if success:
        return jsonify({"success": True, "message": "Mapping created successfully"})
    else:
        return jsonify({"success": False, "error": "Failed to create mapping"}), 500


# Application Mapping
@acm_bp.route("/capabilities/<int:capability_id>/application-mappings", methods=["GET"])
@login_required
def get_application_mappings(capability_id):
    """Get applications mapped to a technical capability."""
    require_entity(TechnicalCapability, capability_id, description="Technical capability not found")
    mappings = ACMTechnicalCapabilityService.get_application_mappings(capability_id)
    return jsonify(
        {
            "success": True,
            "mappings": mappings,
            "count": len(mappings),
        }
    )


@acm_bp.route("/capabilities/<int:capability_id>/application-mappings", methods=["POST"])
@login_required
@audit_log("acm_application_mapping_create")
def create_application_mapping(capability_id):
    """Map a technical capability to an application."""
    data = request.get_json()
    application_id = data.get("application_id")
    capability_coverage = data.get("capability_coverage", "partial")
    maturity_level = data.get("maturity_level")
    notes = data.get("notes")

    if not application_id:
        return jsonify({"success": False, "error": "application_id is required"}), 400

    success = ACMTechnicalCapabilityService.map_to_application(
        capability_id, application_id, capability_coverage, maturity_level, notes
    )

    if success:
        return jsonify({"success": True, "message": "Mapping created successfully"})
    else:
        return jsonify({"success": False, "error": "Failed to create mapping"}), 500


@acm_bp.route("/applications/<int:application_id>/capabilities", methods=["GET"])
@login_required
def get_application_capabilities(application_id):
    """Get all technical capabilities for an application."""
    require_entity(ApplicationComponent, application_id, description="Application not found")
    capabilities = ACMTechnicalCapabilityService.get_capabilities_for_application(application_id)
    return jsonify(
        {
            "success": True,
            "capabilities": capabilities,
            "count": len(capabilities),
        }
    )


@acm_bp.route("/applications/<int:application_id>/auto-classify", methods=["POST"])
@login_required
@audit_log("acm_auto_classify")
def auto_classify_application(application_id):
    """Auto-classify an application's ACM domains based on its technology stack."""
    suggested_domains = ACMTechnicalCapabilityService.classify_application_domains(application_id)
    return jsonify(
        {
            "success": True,
            "application_id": application_id,
            "suggested_domains": suggested_domains,
        }
    )


@acm_bp.route("/applications/<int:application_id>/auto-map", methods=["POST"])
@login_required
@audit_log("acm_auto_map")
def auto_map_application(application_id):
    """
    DEPRECATED: Use /applications/api/comprehensive-auto-map instead.

    This endpoint is kept for backward compatibility but now routes to the
    AI-powered mapping system for better results with confidence scoring.
    """
    from flask import current_app

    # Log deprecation warning
    current_app.logger.warning(
        f"DEPRECATED: /applications/{application_id}/auto-map called by {current_user.email}. "
        "Please migrate to /applications/api/comprehensive-auto-map"
    )

    # Route to AI-powered comprehensive mapping for better results
    from app.services.ai_import_service import get_ai_import_service

    ai_service = get_ai_import_service()

    try:
        # Analyze single application with AI
        # Takes application_id only - confidence_threshold and created_by were
        # never declared on it, so this raised TypeError on every call.
        analysis = ai_service.analyze_application_for_ai_mapping(
            application_id=application_id,
        )

        # Convert AI result to legacy format for backward compatibility
        result = {
            "application_id": application_id,
            "mappings_created": analysis.get("mappings_created", 0),
            "capabilities_mapped": len(analysis.get("capability_mappings", [])),
            "confidence_score": analysis.get("confidence_score", 0.0),
            "ai_analysis": analysis.get("ai_analysis", {}),
            "vendor_detected": analysis.get("vendor_detected", False),
            "deprecated_notice": "This endpoint is deprecated. Use /applications/api/comprehensive-auto-map",
        }

        return jsonify({"success": True, **result})

    except Exception as e:
        current_app.logger.exception(f"Error in deprecated auto-map (routed to AI): {e}")
        # Fallback to legacy service if AI fails
        result = ACMTechnicalCapabilityService.auto_map_application_capabilities(application_id)
        if result.get("error"):
            # The legacy service signals a missing application in-band; that is not a
            # successful mapping and must not be reported as one.
            return jsonify({"success": False, "error": result["error"]}), 404
        # error-signalling-ok: the legacy fallback ran and produced real mappings, so the request did succeed; the fallback flag tells the caller which path produced them
        return jsonify(
            {
                "success": True,
                **result,
                "deprecated_notice": "This endpoint is deprecated. Use /applications/api/comprehensive-auto-map",
                "fallback": "legacy_service_used",
            }
        )


# APQC Process Mapping
@acm_bp.route("/capabilities/<int:capability_id>/apqc-mappings", methods=["GET"])
@login_required
def get_apqc_mappings(capability_id):
    """Get APQC processes mapped to a technical capability."""
    require_entity(TechnicalCapability, capability_id, description="Technical capability not found")
    mappings = ACMTechnicalCapabilityService.get_apqc_mappings(capability_id)
    return jsonify(
        {
            "success": True,
            "mappings": mappings,
            "count": len(mappings),
        }
    )


@acm_bp.route("/capabilities/<int:capability_id>/apqc-mappings", methods=["POST"])
@login_required
@audit_log("acm_apqc_mapping_create")
def create_apqc_mapping(capability_id):
    """Map a technical capability to an APQC process."""
    data = request.get_json()
    apqc_process_id = data.get("apqc_process_id")
    relationship_type = data.get("relationship_type", "implements")

    if not apqc_process_id:
        return jsonify({"success": False, "error": "apqc_process_id is required"}), 400

    success = ACMTechnicalCapabilityService.map_to_apqc_process(
        capability_id, apqc_process_id, relationship_type
    )

    if success:
        return jsonify({"success": True, "message": "Mapping created successfully"})
    else:
        return jsonify({"success": False, "error": "Failed to create mapping"}), 500


# Vendor Product Mapping
@acm_bp.route("/capabilities/<int:capability_id>/vendor-mappings", methods=["GET"])
@login_required
def get_vendor_mappings(capability_id):
    """Get vendor products mapped to a technical capability."""
    require_entity(TechnicalCapability, capability_id, description="Technical capability not found")
    mappings = ACMTechnicalCapabilityService.get_vendor_mappings(capability_id)
    return jsonify(
        {
            "success": True,
            "mappings": mappings,
            "count": len(mappings),
        }
    )


@acm_bp.route("/capabilities/<int:capability_id>/vendor-mappings", methods=["POST"])
@login_required
@audit_log("acm_vendor_mapping_create")
def create_vendor_mapping(capability_id):
    """Map a technical capability to a vendor product."""
    data = request.get_json()
    vendor_product_id = data.get("vendor_product_id")
    capability_coverage = data.get("capability_coverage", "partial")

    if not vendor_product_id:
        return jsonify({"success": False, "error": "vendor_product_id is required"}), 400

    success = ACMTechnicalCapabilityService.map_to_vendor_product(
        capability_id, vendor_product_id, capability_coverage
    )

    if success:
        return jsonify({"success": True, "message": "Mapping created successfully"})
    else:
        return jsonify({"success": False, "error": "Failed to create mapping"}), 500


# Gap Analysis
@acm_bp.route("/gap-analysis", methods=["GET"])
@login_required
def get_gap_analysis():
    """
    Analyze technical capability gaps.
    Query params: domain (optional)
    """
    domain = request.args.get("domain")
    analysis = ACMTechnicalCapabilityService.analyze_capability_gaps(domain)
    return jsonify(
        {
            "success": True,
            "analysis": analysis,
        }
    )

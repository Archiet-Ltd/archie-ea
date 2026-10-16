"""
API v1 Mapping Endpoints

Comprehensive REST API for the 4 mapping model types:
1. TechnicalCapabilityVendorMapping
2. UnifiedCapabilityApplicationMapping
3. UnifiedCapabilityVendorOrganizationMapping
4. ApplicationVendorProductMapping

All endpoints follow PRD - 003 standardization with consistent response format.
Supports full CRUD operations with filtering, pagination, and analytics.
"""

from flask import Blueprint, request
from flask_login import login_required
from sqlalchemy import func, or_

from app.decorators import audit_log

from app import db
from app.models.capability_to_vendor_mapping import (
    ApplicationVendorProductMapping,
    TechnicalCapabilityVendorMapping,
    UnifiedCapabilityApplicationMapping,
    UnifiedCapabilityVendorOrganizationMapping,
)
from app.models.technical_capability import TechnicalCapability
from app.utils.api_response import error_response, not_found_response, success_response
from app.utils.pagination import safe_int_arg

mappings_bp = Blueprint("mappings_v1", __name__, url_prefix="/api/v1/mappings")


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def _owned_query(model):
    """Query ``model`` limited to rows the caller's active organisation owns.

    The mapping tables carry no organisation column, so ownership is the owner of the
    parent: the application (application mappings) or the business capability (vendor
    strategy mappings). No active organisation means no rows. A shared (organisation-less)
    capability is not owned by anyone, so a tenant cannot change its mappings.
    """
    from sqlalchemy import false

    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_capability import UnifiedCapability
    from app.utils.tenant import current_organization_id

    query = db.session.query(model)
    org_id = current_organization_id()
    if org_id is None:
        return query.filter(false())
    if model is UnifiedCapabilityVendorOrganizationMapping:
        return query.join(
            UnifiedCapability, UnifiedCapability.id == model.unified_capability_id
        ).filter(UnifiedCapability.organization_id == org_id)
    return query.join(
        ApplicationComponent, ApplicationComponent.id == model.application_component_id
    ).filter(ApplicationComponent.organization_id == org_id)


def _owned_get(model, mapping_id):
    return _owned_query(model).filter(model.id == mapping_id).first()


def _as_id(value):
    """An integer row id from a JSON value, or None.

    Integer-like strings are accepted; booleans (which are ints in Python), floats, blanks
    and out-of-range numbers are not ids.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value.isascii() or not value.isdigit():
            return None
        value = int(value)
    if not isinstance(value, int) or not 0 < value <= 2147483647:
        return None
    return value


def _owns_application(application_id):
    """True when the application belongs to the caller's active organisation."""
    from app.models.application_portfolio import ApplicationComponent
    from app.utils.route_guards import entity_exists
    from app.utils.tenant import current_organization_id

    # The automatic tenant filter is off without an organisation, so refuse explicitly.
    app_id = _as_id(application_id)
    if current_organization_id() is None or app_id is None:
        return False
    return entity_exists(ApplicationComponent, app_id)


def _owns_capability(capability_id):
    """True when the capability is owned by the caller's active organisation.

    Strict owner semantics: a shared reference capability is not owned by anyone.
    """
    from app.models.unified_capability import UnifiedCapability
    from app.utils.tenant import current_organization_id

    org_id = current_organization_id()
    cap_id = _as_id(capability_id)
    if org_id is None or cap_id is None:
        return False
    return (
        db.session.query(UnifiedCapability.id)
        .filter(UnifiedCapability.id == cap_id,
                UnifiedCapability.organization_id == org_id)
        .first()
        is not None
    )


def _sees_capability(capability_id):
    """True when the capability is the caller's own or a shared reference capability."""
    from app.models.unified_capability import UnifiedCapability
    from app.utils.tenant import current_organization_id

    org_id = current_organization_id()
    cap_id = _as_id(capability_id)
    if org_id is None or cap_id is None:
        return False
    return UnifiedCapability.visible_to_organization(cap_id, org_id) is not None


def _build_filter_query(query, filters):
    """Build query with common filters."""
    if filters.get("search"):
        # Search across name fields if available
        query = query.filter(
            or_(
                # Add searchable fields based on model type
            )
        )
    return query


def _apply_pagination(query, page=1, per_page=50):
    """Apply pagination to query."""
    page = max(1, page)
    per_page = min(per_page, 100)  # Cap at 100
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return pagination.items, {
        "page": page,
        "per_page": per_page,
        "total": pagination.total,
        "pages": pagination.pages,
    }


# ============================================================================
# MAPPING 1: TechnicalCapabilityVendorMapping
# ============================================================================


@mappings_bp.route("/technical-to-vendor", methods=["GET"])
@login_required
def get_technical_to_vendor_mappings():
    """
    Get TechnicalCapability → VendorProduct mappings.

    Query Parameters:
    - technical_capability_id: Filter by technical capability
    - vendor_product_id: Filter by vendor product
    - capability_name: Search by capability name
    - product_name: Search by product name
    - min_fit_score: Filter by minimum fit score (0 - 100)
    - maturity_level: Filter by maturity level (1 - 5)
    - page: Page number (default: 1)
    - per_page: Items per page (default: 50, max: 100)

    Returns:
    {
        "success": true,
        "data": {
            "mappings": [
                {
                    "id": 1,
                    "technical_capability": {...},
                    "vendor_product": {...},
                    "coverage_percentage": 85,
                    "fit_score": 90,
                    "maturity_level": 4,
                    "implementation_effort": "medium",
                    ...
                }
            ],
            "pagination": {...},
            "summary": {
                "total_mappings": 120,
                "avg_fit_score": 82.5,
                "coverage_summary": {...}
            }
        }
    }
    """
    try:
        page = safe_int_arg('page', 1, minimum=1)
        per_page = safe_int_arg('per_page', 50, minimum=1, maximum=500)

        # Build query
        query = db.session.query(TechnicalCapabilityVendorMapping)

        # Filters
        if request.args.get("technical_capability_id"):
            query = query.filter(
                TechnicalCapabilityVendorMapping.technical_capability_id
                == request.args.get("technical_capability_id", type=int)
            )

        if request.args.get("vendor_product_id"):
            query = query.filter(
                TechnicalCapabilityVendorMapping.vendor_product_id
                == request.args.get("vendor_product_id", type=int)
            )

        if request.args.get("min_fit_score"):
            query = query.filter(
                TechnicalCapabilityVendorMapping.fit_score
                >= request.args.get("min_fit_score", type=float)
            )

        if request.args.get("maturity_level"):
            query = query.filter(
                TechnicalCapabilityVendorMapping.maturity_level
                == request.args.get("maturity_level", type=int)
            )

        # Pagination
        mappings, pagination_info = _apply_pagination(query, page, per_page)

        # Format response
        data = []
        for mapping in mappings:
            data.append(
                {
                    "id": mapping.id,
                    "technical_capability_id": mapping.technical_capability_id,
                    "technical_capability_name": mapping.technical_capability.name
                    if mapping.technical_capability
                    else None,
                    "vendor_product_id": mapping.vendor_product_id,
                    "vendor_product_name": mapping.vendor_product.name
                    if mapping.vendor_product
                    else None,
                    "vendor_name": mapping.vendor_product.vendor_organization.name
                    if mapping.vendor_product and mapping.vendor_product.vendor_organization
                    else None,
                    "coverage_percentage": mapping.coverage_percentage,
                    "fit_score": mapping.fit_score,
                    "maturity_level": mapping.maturity_level,
                    "implementation_effort": mapping.implementation_effort,
                    "time_to_value_days": mapping.time_to_value_days,
                    "risk_level": mapping.risk_level,
                    "performance_rating": mapping.performance_rating,
                    "usability_rating": mapping.usability_rating,
                    "reliability_rating": mapping.reliability_rating,
                    "support_rating": mapping.support_rating,
                    "mapping_notes": mapping.mapping_notes,
                    "created_at": mapping.created_at.isoformat()
                    if hasattr(mapping, "created_at")
                    else None,
                    "updated_at": mapping.updated_at.isoformat()
                    if hasattr(mapping, "updated_at")
                    else None,
                }
            )

        # Calculate summary stats
        avg_fit = (
            db.session.query(func.avg(TechnicalCapabilityVendorMapping.fit_score)).scalar() or 0
        )

        return success_response(
            {
                "mappings": data,
                "pagination": pagination_info,
                "summary": {
                    "total_mappings": pagination_info["total"],
                    "average_fit_score": round(float(avg_fit), 2) if avg_fit else 0,
                    "count_this_page": len(data),
                },
            }
        )

    except Exception as e:
        return error_response(f"Error fetching technical-to-vendor mappings: {str(e)}", 500)


@mappings_bp.route("/technical-to-vendor", methods=["POST"])
@login_required
@audit_log("api_technical_mapping_create")
def create_technical_to_vendor_mapping():
    """Create a new TechnicalCapability → VendorProduct mapping."""
    try:
        data = request.get_json()

        # Validate required fields
        required = ["technical_capability_id", "vendor_product_id"]
        if not all(field in data for field in required):
            return error_response(f"Missing required fields: {required}", 400)

        # Check for duplicates
        existing = TechnicalCapabilityVendorMapping.query.filter_by(
            technical_capability_id=data["technical_capability_id"],
            vendor_product_id=data["vendor_product_id"],
        ).first()

        if existing:
            return error_response("Mapping already exists", 409)

        # Create mapping
        mapping = TechnicalCapabilityVendorMapping(
            technical_capability_id=data["technical_capability_id"],
            vendor_product_id=data["vendor_product_id"],
            coverage_percentage=data.get("coverage_percentage", 75.0),
            maturity_level=data.get("maturity_level", 3),
            fit_score=data.get("fit_score", 75.0),
            implementation_effort=data.get("implementation_effort"),
            time_to_value_days=data.get("time_to_value_days"),
            customization_required=data.get("customization_required", False),
            risk_level=data.get("risk_level", "medium"),
            performance_rating=data.get("performance_rating"),
            usability_rating=data.get("usability_rating"),
            reliability_rating=data.get("reliability_rating"),
            support_rating=data.get("support_rating"),
            mapping_notes=data.get("mapping_notes"),
        )

        db.session.add(mapping)
        db.session.commit()

        return success_response({"id": mapping.id, "message": "Mapping created successfully"}, 201)

    except Exception as e:
        db.session.rollback()
        return error_response(f"Error creating mapping: {str(e)}", 500)


@mappings_bp.route("/technical-to-vendor/<int:mapping_id>", methods=["GET"])
@login_required
def get_technical_to_vendor_mapping(mapping_id):
    """Get a specific TechnicalCapability → VendorProduct mapping."""
    try:
        mapping = TechnicalCapabilityVendorMapping.query.get(mapping_id)

        if not mapping:
            return not_found_response("Mapping not found")

        return success_response(
            {
                "id": mapping.id,
                "technical_capability_id": mapping.technical_capability_id,
                "vendor_product_id": mapping.vendor_product_id,
                "coverage_percentage": mapping.coverage_percentage,
                "fit_score": mapping.fit_score,
                "maturity_level": mapping.maturity_level,
                "implementation_effort": mapping.implementation_effort,
                "time_to_value_days": mapping.time_to_value_days,
                "customization_required": mapping.customization_required,
                "risk_level": mapping.risk_level,
                "performance_rating": mapping.performance_rating,
                "usability_rating": mapping.usability_rating,
                "reliability_rating": mapping.reliability_rating,
                "support_rating": mapping.support_rating,
                "mapping_notes": mapping.mapping_notes,
            }
        )

    except Exception as e:
        return error_response(f"Error fetching mapping: {str(e)}", 500)


@mappings_bp.route("/technical-to-vendor/<int:mapping_id>", methods=["PUT"])
@login_required
@audit_log("api_technical_mapping_update")
def update_technical_to_vendor_mapping(mapping_id):
    """Update a TechnicalCapability → VendorProduct mapping."""
    try:
        mapping = TechnicalCapabilityVendorMapping.query.get(mapping_id)

        if not mapping:
            return not_found_response("Mapping not found")

        data = request.get_json()

        # Update allowed fields
        allowed_fields = [
            "coverage_percentage",
            "maturity_level",
            "fit_score",
            "implementation_effort",
            "time_to_value_days",
            "customization_required",
            "risk_level",
            "performance_rating",
            "usability_rating",
            "reliability_rating",
            "support_rating",
            "mapping_notes",
        ]

        for field in allowed_fields:
            if field in data:
                setattr(mapping, field, data[field])

        db.session.commit()

        return success_response({"message": "Mapping updated successfully"})

    except Exception as e:
        db.session.rollback()
        return error_response(f"Error updating mapping: {str(e)}", 500)


@mappings_bp.route("/technical-to-vendor/<int:mapping_id>", methods=["DELETE"])
@login_required
@audit_log("api_technical_mapping_delete")
def delete_technical_to_vendor_mapping(mapping_id):
    """Delete a TechnicalCapability → VendorProduct mapping."""
    try:
        mapping = TechnicalCapabilityVendorMapping.query.get(mapping_id)

        if not mapping:
            return not_found_response("Mapping not found")

        db.session.delete(mapping)
        db.session.commit()

        return success_response({"message": "Mapping deleted successfully"})

    except Exception as e:
        db.session.rollback()
        return error_response(f"Error deleting mapping: {str(e)}", 500)


# ============================================================================
# MAPPING 2: UnifiedCapabilityApplicationMapping
# ============================================================================


@mappings_bp.route("/unified-to-application", methods=["GET"])
@login_required
def get_unified_to_application_mappings():
    """
    Get UnifiedCapability (BUSINESS) → ApplicationComponent mappings.

    Query Parameters:
    - unified_capability_id: Filter by business capability
    - application_id: Filter by application
    - capability_name: Search by capability name
    - app_name: Search by application name
    - coverage_type: Filter by coverage type (supported, partial, planned, deprecated)
    - page: Page number
    - per_page: Items per page
    """
    try:
        page = safe_int_arg('page', 1, minimum=1)
        per_page = safe_int_arg('per_page', 50, minimum=1, maximum=500)

        query = _owned_query(UnifiedCapabilityApplicationMapping)

        # Filters
        if request.args.get("unified_capability_id"):
            query = query.filter(
                UnifiedCapabilityApplicationMapping.unified_capability_id
                == request.args.get("unified_capability_id", type=int)
            )

        if request.args.get("application_id"):
            query = query.filter(
                UnifiedCapabilityApplicationMapping.application_component_id
                == request.args.get("application_id", type=int)
            )

        if request.args.get("coverage_type"):
            query = query.filter(
                UnifiedCapabilityApplicationMapping.coverage_type
                == request.args.get("coverage_type")
            )

        # Pagination
        mappings, pagination_info = _apply_pagination(query, page, per_page)

        # Format response
        data = []
        for mapping in mappings:
            data.append(
                {
                    "id": mapping.id,
                    "unified_capability_id": mapping.unified_capability_id,
                    "capability_name": mapping.unified_capability.name
                    if mapping.unified_capability
                    else None,
                    "application_id": mapping.application_id,
                    "application_name": mapping.application_component.name
                    if mapping.application_component
                    else None,
                    "coverage_type": mapping.coverage_type,
                    "coverage_percentage": mapping.coverage_percentage,
                    "gap_description": mapping.gap_description,
                    "roadmap_priority": mapping.roadmap_priority,
                    "implementation_status": mapping.implementation_status,
                    "notes": mapping.notes,
                    "created_at": mapping.created_at.isoformat()
                    if hasattr(mapping, "created_at")
                    else None,
                }
            )

        return success_response({"mappings": data, "pagination": pagination_info})

    except Exception as e:
        return error_response(f"Error fetching unified-to-application mappings: {str(e)}", 500)


@mappings_bp.route("/unified-to-application", methods=["POST"])
@login_required
@audit_log("api_capability_app_mapping_create")
def create_unified_to_application_mapping():
    """Create a new UnifiedCapability → ApplicationComponent mapping."""
    try:
        data = request.get_json()

        required = ["unified_capability_id", "application_id"]
        if not all(field in data for field in required):
            return error_response(f"Missing required fields: {required}", 400)

        # The row is owned through the application, so a shared reference capability is
        # allowed here; the strict owner rule stays on the vendor-organisation family.
        if not (_owns_application(data["application_id"])
                and _sees_capability(data["unified_capability_id"])):
            return not_found_response("Application or capability not found")
        data["application_id"] = _as_id(data["application_id"])
        data["unified_capability_id"] = _as_id(data["unified_capability_id"])

        # Check for duplicates
        existing = UnifiedCapabilityApplicationMapping.query.filter_by(
            unified_capability_id=data["unified_capability_id"],
            application_component_id=data["application_id"],
        ).first()

        if existing:
            return error_response("Mapping already exists", 409)

        mapping = UnifiedCapabilityApplicationMapping(
            unified_capability_id=data["unified_capability_id"],
            application_component_id=data["application_id"],
            support_level=data.get("coverage_type", "supported"),
            coverage_percentage=data.get("coverage_percentage", 80.0),
            gaps_description=data.get("gap_description"),
            modernization_priority=data.get("roadmap_priority"),
            health_status=data.get("implementation_status", "active"),
            mapping_notes=data.get("notes"),
        )

        db.session.add(mapping)
        db.session.commit()

        return success_response({"id": mapping.id, "message": "Mapping created successfully"}, 201)

    except Exception as e:
        db.session.rollback()
        return error_response(f"Error creating mapping: {str(e)}", 500)


@mappings_bp.route("/unified-to-application/<int:mapping_id>", methods=["GET"])
@login_required
def get_unified_to_application_mapping(mapping_id):
    """Get a specific UnifiedCapability → ApplicationComponent mapping."""
    try:
        mapping = _owned_get(UnifiedCapabilityApplicationMapping, mapping_id)

        if not mapping:
            return not_found_response("Mapping not found")

        return success_response(
            {
                "id": mapping.id,
                "unified_capability_id": mapping.unified_capability_id,
                "application_id": mapping.application_id,
                "coverage_type": mapping.coverage_type,
                "coverage_percentage": mapping.coverage_percentage,
                "gap_description": mapping.gap_description,
                "roadmap_priority": mapping.roadmap_priority,
                "implementation_status": mapping.implementation_status,
                "notes": mapping.notes,
            }
        )

    except Exception as e:
        return error_response(f"Error fetching mapping: {str(e)}", 500)


@mappings_bp.route("/unified-to-application/<int:mapping_id>", methods=["PUT"])
@login_required
@audit_log("api_capability_app_mapping_update")
def update_unified_to_application_mapping(mapping_id):
    """Update a UnifiedCapability → ApplicationComponent mapping."""
    try:
        mapping = _owned_get(UnifiedCapabilityApplicationMapping, mapping_id)

        if not mapping:
            return not_found_response("Mapping not found")

        data = request.get_json()

        allowed_fields = [
            "coverage_type",
            "coverage_percentage",
            "gap_description",
            "roadmap_priority",
            "implementation_status",
            "notes",
        ]

        for field in allowed_fields:
            if field in data:
                setattr(mapping, field, data[field])

        db.session.commit()

        return success_response({"message": "Mapping updated successfully"})

    except Exception as e:
        db.session.rollback()
        return error_response(f"Error updating mapping: {str(e)}", 500)


@mappings_bp.route("/unified-to-application/<int:mapping_id>", methods=["DELETE"])
@login_required
@audit_log("api_capability_app_mapping_delete")
def delete_unified_to_application_mapping(mapping_id):
    """Delete a UnifiedCapability → ApplicationComponent mapping."""
    try:
        mapping = _owned_get(UnifiedCapabilityApplicationMapping, mapping_id)

        if not mapping:
            return not_found_response("Mapping not found")

        db.session.delete(mapping)
        db.session.commit()

        return success_response({"message": "Mapping deleted successfully"})

    except Exception as e:
        db.session.rollback()
        return error_response(f"Error deleting mapping: {str(e)}", 500)


# ============================================================================
# MAPPING 3: UnifiedCapabilityVendorOrganizationMapping
# ============================================================================


@mappings_bp.route("/unified-to-vendor-org", methods=["GET"])
@login_required
def get_unified_to_vendor_org_mappings():
    """
    Get UnifiedCapability (BUSINESS) → VendorOrganization mappings (Strategic).

    Query Parameters:
    - unified_capability_id: Filter by business capability
    - vendor_id: Filter by vendor organization
    - strategic_alignment: Filter by alignment (strategic, tactical, emerging)
    - annual_spend_min: Minimum annual spend
    - annual_spend_max: Maximum annual spend
    - page: Page number
    """
    try:
        page = safe_int_arg('page', 1, minimum=1)
        per_page = safe_int_arg('per_page', 50, minimum=1, maximum=500)

        query = _owned_query(UnifiedCapabilityVendorOrganizationMapping)

        # Filters
        if request.args.get("unified_capability_id"):
            query = query.filter(
                UnifiedCapabilityVendorOrganizationMapping.unified_capability_id
                == request.args.get("unified_capability_id", type=int)
            )

        if request.args.get("vendor_id"):
            query = query.filter(
                UnifiedCapabilityVendorOrganizationMapping.vendor_organization_id
                == request.args.get("vendor_id", type=int)
            )

        if request.args.get("strategic_alignment"):
            query = query.filter(
                UnifiedCapabilityVendorOrganizationMapping.strategic_alignment
                == request.args.get("strategic_alignment")
            )

        if request.args.get("annual_spend_min"):
            query = query.filter(
                UnifiedCapabilityVendorOrganizationMapping.annual_spend
                >= request.args.get("annual_spend_min", type=float)
            )

        # Pagination
        mappings, pagination_info = _apply_pagination(query, page, per_page)

        # Format response
        data = []
        for mapping in mappings:
            data.append(
                {
                    "id": mapping.id,
                    "unified_capability_id": mapping.unified_capability_id,
                    "capability_name": mapping.unified_capability.name
                    if mapping.unified_capability
                    else None,
                    "vendor_organization_id": mapping.vendor_organization_id,
                    "vendor_name": mapping.vendor_organization.name
                    if mapping.vendor_organization
                    else None,
                    "strategic_alignment": mapping.strategic_alignment,
                    "annual_spend": float(mapping.annual_spend) if mapping.annual_spend else 0,
                    "contract_start_date": mapping.contract_start_date.isoformat()
                    if mapping.contract_start_date
                    else None,
                    "contract_end_date": mapping.contract_end_date.isoformat()
                    if mapping.contract_end_date
                    else None,
                    "primary_contact": mapping.primary_contact,
                    "vendor_score": mapping.vendor_score,
                    "relationship_status": mapping.relationship_status,
                    "notes": mapping.notes,
                    "created_at": mapping.created_at.isoformat()
                    if hasattr(mapping, "created_at")
                    else None,
                }
            )

        return success_response({"mappings": data, "pagination": pagination_info})

    except Exception as e:
        return error_response(f"Error fetching unified-to-vendor-org mappings: {str(e)}", 500)


@mappings_bp.route("/unified-to-vendor-org", methods=["POST"])
@login_required
@audit_log("api_capability_vendor_mapping_create")
def create_unified_to_vendor_org_mapping():
    """Create a new UnifiedCapability → VendorOrganization mapping."""
    try:
        data = request.get_json()

        required = ["unified_capability_id", "vendor_organization_id"]
        if not all(field in data for field in required):
            return error_response(f"Missing required fields: {required}", 400)

        if not _owns_capability(data["unified_capability_id"]):
            return not_found_response("Capability not found")
        data["unified_capability_id"] = _as_id(data["unified_capability_id"])

        # Check for duplicates
        existing = UnifiedCapabilityVendorOrganizationMapping.query.filter_by(
            unified_capability_id=data["unified_capability_id"],
            vendor_organization_id=data["vendor_organization_id"],
        ).first()

        if existing:
            return error_response("Mapping already exists", 409)

        mapping = UnifiedCapabilityVendorOrganizationMapping(
            unified_capability_id=data["unified_capability_id"],
            vendor_organization_id=data["vendor_organization_id"],
            strategic_importance=data.get("strategic_alignment", "tactical"),
            annual_spend=data.get("annual_spend"),
            relationship_type=data.get("relationship_status", "active"),
            strategic_notes=data.get("notes"),
        )

        db.session.add(mapping)
        db.session.commit()

        return success_response({"id": mapping.id, "message": "Mapping created successfully"}, 201)

    except Exception as e:
        db.session.rollback()
        return error_response(f"Error creating mapping: {str(e)}", 500)


@mappings_bp.route("/unified-to-vendor-org/<int:mapping_id>", methods=["GET"])
@login_required
def get_unified_to_vendor_org_mapping(mapping_id):
    """Get a specific UnifiedCapability → VendorOrganization mapping."""
    try:
        mapping = _owned_get(UnifiedCapabilityVendorOrganizationMapping, mapping_id)

        if not mapping:
            return not_found_response("Mapping not found")

        return success_response(
            {
                "id": mapping.id,
                "unified_capability_id": mapping.unified_capability_id,
                "vendor_organization_id": mapping.vendor_organization_id,
                "strategic_alignment": mapping.strategic_alignment,
                "annual_spend": float(mapping.annual_spend) if mapping.annual_spend else 0,
                "primary_contact": mapping.primary_contact,
                "vendor_score": mapping.vendor_score,
                "relationship_status": mapping.relationship_status,
                "notes": mapping.notes,
            }
        )

    except Exception as e:
        return error_response(f"Error fetching mapping: {str(e)}", 500)


@mappings_bp.route("/unified-to-vendor-org/<int:mapping_id>", methods=["PUT"])
@login_required
@audit_log("api_capability_vendor_mapping_update")
def update_unified_to_vendor_org_mapping(mapping_id):
    """Update a UnifiedCapability → VendorOrganization mapping."""
    try:
        mapping = _owned_get(UnifiedCapabilityVendorOrganizationMapping, mapping_id)

        if not mapping:
            return not_found_response("Mapping not found")

        data = request.get_json()

        allowed_fields = [
            "strategic_alignment",
            "annual_spend",
            "primary_contact",
            "vendor_score",
            "relationship_status",
            "notes",
        ]

        for field in allowed_fields:
            if field in data:
                setattr(mapping, field, data[field])

        db.session.commit()

        return success_response({"message": "Mapping updated successfully"})

    except Exception as e:
        db.session.rollback()
        return error_response(f"Error updating mapping: {str(e)}", 500)


@mappings_bp.route("/unified-to-vendor-org/<int:mapping_id>", methods=["DELETE"])
@login_required
@audit_log("api_capability_vendor_mapping_delete")
def delete_unified_to_vendor_org_mapping(mapping_id):
    """Delete a UnifiedCapability → VendorOrganization mapping."""
    try:
        mapping = _owned_get(UnifiedCapabilityVendorOrganizationMapping, mapping_id)

        if not mapping:
            return not_found_response("Mapping not found")

        db.session.delete(mapping)
        db.session.commit()

        return success_response({"message": "Mapping deleted successfully"})

    except Exception as e:
        db.session.rollback()
        return error_response(f"Error deleting mapping: {str(e)}", 500)


# ============================================================================
# MAPPING 4: ApplicationVendorProductMapping
# ============================================================================


@mappings_bp.route("/application-to-vendor", methods=["GET"])
@login_required
def get_application_to_vendor_mappings():
    """
    Get ApplicationComponent → VendorProduct mappings.

    Query Parameters:
    - application_id: Filter by application
    - vendor_product_id: Filter by vendor product
    - tech_stack_category: Filter by category (database, middleware, ui, etc.)
    - page: Page number
    - per_page: Items per page
    """
    try:
        page = safe_int_arg('page', 1, minimum=1)
        per_page = safe_int_arg('per_page', 50, minimum=1, maximum=500)

        query = _owned_query(ApplicationVendorProductMapping)

        # Filters
        if request.args.get("application_id"):
            query = query.filter(
                ApplicationVendorProductMapping.application_component_id
                == request.args.get("application_id", type=int)
            )

        if request.args.get("vendor_product_id"):
            query = query.filter(
                ApplicationVendorProductMapping.vendor_product_id
                == request.args.get("vendor_product_id", type=int)
            )

        if request.args.get("tech_stack_category"):
            query = query.filter(
                ApplicationVendorProductMapping.tech_stack_category
                == request.args.get("tech_stack_category")
            )

        # Pagination
        mappings, pagination_info = _apply_pagination(query, page, per_page)

        # Format response
        data = []
        for mapping in mappings:
            data.append(
                {
                    "id": mapping.id,
                    "application_id": mapping.application_id,
                    "application_name": mapping.application_component.name
                    if mapping.application_component
                    else None,
                    "vendor_product_id": mapping.vendor_product_id,
                    "product_name": mapping.vendor_product.name if mapping.vendor_product else None,
                    "vendor_name": mapping.vendor_product.vendor_organization.name
                    if mapping.vendor_product and mapping.vendor_product.vendor_organization
                    else None,
                    "tech_stack_category": mapping.tech_stack_category,
                    "integration_type": mapping.integration_type,
                    "integration_status": mapping.integration_status,
                    "performance_impact": mapping.performance_impact,
                    "security_alignment": mapping.security_alignment,
                    "support_level": mapping.support_level,
                    "version": mapping.version,
                    "deployment_model": mapping.deployment_model,
                    "licensing_model": mapping.licensing_model,
                    "annual_license_cost": float(mapping.annual_license_cost)
                    if mapping.annual_license_cost
                    else 0,
                    "notes": mapping.notes,
                    "created_at": mapping.created_at.isoformat()
                    if hasattr(mapping, "created_at")
                    else None,
                }
            )

        return success_response({"mappings": data, "pagination": pagination_info})

    except Exception as e:
        return error_response(f"Error fetching application-to-vendor mappings: {str(e)}", 500)


@mappings_bp.route("/application-to-vendor", methods=["POST"])
@login_required
@audit_log("api_app_vendor_mapping_create")
def create_application_to_vendor_mapping():
    """Create a new ApplicationComponent → VendorProduct mapping."""
    try:
        data = request.get_json()

        required = ["application_id", "vendor_product_id"]
        if not all(field in data for field in required):
            return error_response(f"Missing required fields: {required}", 400)

        if not _owns_application(data["application_id"]):
            return not_found_response("Application not found")
        data["application_id"] = _as_id(data["application_id"])

        # Check for duplicates
        existing = ApplicationVendorProductMapping.query.filter_by(
            application_component_id=data["application_id"],
            vendor_product_id=data["vendor_product_id"],
        ).first()

        if existing:
            return error_response("Mapping already exists", 409)

        mapping = ApplicationVendorProductMapping(
            application_component_id=data["application_id"],
            vendor_product_id=data["vendor_product_id"],
            product_version=data.get("version"),
            deployment_model=data.get("deployment_model", "cloud"),
            license_cost_annual=data.get("annual_license_cost"),
            mapping_notes=data.get("notes"),
        )

        db.session.add(mapping)
        db.session.commit()

        return success_response({"id": mapping.id, "message": "Mapping created successfully"}, 201)

    except Exception as e:
        db.session.rollback()
        return error_response(f"Error creating mapping: {str(e)}", 500)


@mappings_bp.route("/application-to-vendor/<int:mapping_id>", methods=["GET"])
@login_required
def get_application_to_vendor_mapping(mapping_id):
    """Get a specific ApplicationComponent → VendorProduct mapping."""
    try:
        mapping = _owned_get(ApplicationVendorProductMapping, mapping_id)

        if not mapping:
            return not_found_response("Mapping not found")

        return success_response(
            {
                "id": mapping.id,
                "application_id": mapping.application_id,
                "vendor_product_id": mapping.vendor_product_id,
                "tech_stack_category": mapping.tech_stack_category,
                "integration_type": mapping.integration_type,
                "integration_status": mapping.integration_status,
                "performance_impact": mapping.performance_impact,
                "security_alignment": mapping.security_alignment,
                "support_level": mapping.support_level,
                "version": mapping.version,
                "deployment_model": mapping.deployment_model,
                "licensing_model": mapping.licensing_model,
                "annual_license_cost": float(mapping.annual_license_cost)
                if mapping.annual_license_cost
                else 0,
                "notes": mapping.notes,
            }
        )

    except Exception as e:
        return error_response(f"Error fetching mapping: {str(e)}", 500)


@mappings_bp.route("/application-to-vendor/<int:mapping_id>", methods=["PUT"])
@login_required
@audit_log("api_app_vendor_mapping_update")
def update_application_to_vendor_mapping(mapping_id):
    """Update an ApplicationComponent → VendorProduct mapping."""
    try:
        mapping = _owned_get(ApplicationVendorProductMapping, mapping_id)

        if not mapping:
            return not_found_response("Mapping not found")

        data = request.get_json()

        allowed_fields = [
            "tech_stack_category",
            "integration_type",
            "integration_status",
            "performance_impact",
            "security_alignment",
            "support_level",
            "version",
            "deployment_model",
            "licensing_model",
            "annual_license_cost",
            "notes",
        ]

        for field in allowed_fields:
            if field in data:
                setattr(mapping, field, data[field])

        db.session.commit()

        return success_response({"message": "Mapping updated successfully"})

    except Exception as e:
        db.session.rollback()
        return error_response(f"Error updating mapping: {str(e)}", 500)


@mappings_bp.route("/application-to-vendor/<int:mapping_id>", methods=["DELETE"])
@login_required
@audit_log("api_app_vendor_mapping_delete")
def delete_application_to_vendor_mapping(mapping_id):
    """Delete an ApplicationComponent → VendorProduct mapping."""
    try:
        mapping = _owned_get(ApplicationVendorProductMapping, mapping_id)

        if not mapping:
            return not_found_response("Mapping not found")

        db.session.delete(mapping)
        db.session.commit()

        return success_response({"message": "Mapping deleted successfully"})

    except Exception as e:
        db.session.rollback()
        return error_response(f"Error deleting mapping: {str(e)}", 500)


# ============================================================================
# ANALYTICS & SUMMARY ENDPOINTS
# ============================================================================


@mappings_bp.route("/summary", methods=["GET"])
@login_required
def get_mappings_summary():
    """Get summary statistics across all mapping types."""
    try:
        tech_to_vendor = (
            db.session.query(func.count(TechnicalCapabilityVendorMapping.id)).scalar() or 0
        )
        unified_to_app = (
            _owned_query(UnifiedCapabilityApplicationMapping).count() or 0
        )
        unified_to_vendor_org = (
            _owned_query(UnifiedCapabilityVendorOrganizationMapping).count()
            or 0
        )
        app_to_vendor = (
            _owned_query(ApplicationVendorProductMapping).count() or 0
        )

        total_mappings = tech_to_vendor + unified_to_app + unified_to_vendor_org + app_to_vendor

        return success_response(
            {
                "summary": {
                    "total_mappings": total_mappings,
                    "technical_to_vendor": tech_to_vendor,
                    "unified_to_application": unified_to_app,
                    "unified_to_vendor_org": unified_to_vendor_org,
                    "application_to_vendor": app_to_vendor,
                }
            }
        )

    except Exception as e:
        return error_response(f"Error generating summary: {str(e)}", 500)


@mappings_bp.route("/coverage/technical-capabilities", methods=["GET"])
@login_required
def get_technical_capability_coverage():
    """Get coverage analysis for technical capabilities."""
    try:
        capabilities = (
            db.session.query(
                TechnicalCapability.id,
                TechnicalCapability.name,
                func.count(TechnicalCapabilityVendorMapping.id).label("vendor_product_count"),
            )
            .outerjoin(
                TechnicalCapabilityVendorMapping,
                TechnicalCapability.id == TechnicalCapabilityVendorMapping.technical_capability_id,
            )
            .group_by(TechnicalCapability.id)
            .all()
        )

        data = []
        for cap_id, cap_name, count in capabilities:
            data.append(
                {
                    "capability_id": cap_id,
                    "capability_name": cap_name,
                    "vendor_products_count": count or 0,
                    "covered": count > 0,
                }
            )

        return success_response(
            {
                "capabilities": data,
                "total_capabilities": len(data),
                "fully_covered": sum(1 for c in data if c["covered"]),
                "coverage_percentage": round(
                    (sum(1 for c in data if c["covered"]) / len(data) * 100) if data else 0, 2
                ),
            }
        )

    except Exception as e:
        return error_response(f"Error generating coverage analysis: {str(e)}", 500)

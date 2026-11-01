"""
API v1 Capabilities Endpoints

Standardized capability management API endpoints following PRD - 003.
"""

from flask import Blueprint, g, request
from flask_login import login_required
from sqlalchemy import or_

from app import db
from app.models.manufacturing_capability import ManufacturingCapability
from app.models.unified_capability import UnifiedCapability
from app.utils.api_response import (
    error_response,
    not_found_response,
    success_response,
)
from app.utils.pagination import safe_int_arg

capabilities_bp = Blueprint("capabilities_v1", __name__)


@capabilities_bp.route("/", methods=["GET"])
@login_required
def get_capabilities():
    """
    Get all capabilities with pagination and filtering
    ---
    tags:
      - Capabilities
    summary: Get paginated list of capabilities
    description: Returns a paginated list of capabilities with optional filtering
    parameters:
      - name: page
        in: query
        type: integer
        default: 1
        description: Page number
      - name: per_page
        in: query
        type: integer
        default: 50
        description: Items per page
      - name: search
        in: query
        type: string
        description: Search term for capability names
      - name: domain
        in: query
        type: string
        description: Filter by domain
      - name: level
        in: query
        type: string
        description: Filter by level
    responses:
      200:
        description: List of capabilities
    """
    try:
        page = safe_int_arg('page', 1, minimum=1)
        per_page = min(safe_int_arg('per_page', 50, minimum=1, maximum=500), 100)
        search = request.args.get("search", "", type=str)
        domain = request.args.get("domain", "", type=str)
        level = request.args.get("level", "", type=str)

        # Build query
        # A capability retired into another is not offered (ADR 0008 rule 4).
        query = UnifiedCapability.active()

        # Apply filters
        if search:
            query = query.filter(
                or_(
                    UnifiedCapability.name.ilike(f"%{search}%"),
                    UnifiedCapability.description.ilike(f"%{search}%"),
                )
            )

        if domain:
            query = query.filter(UnifiedCapability.domain_id == domain)

        if level:
            query = query.filter(UnifiedCapability.level == level)

        # Paginate
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)

        capabilities = []
        for cap in pagination.items:
            capabilities.append(
                {
                    "id": str(cap.id),
                    "name": cap.name,
                    "description": cap.description or "",
                    "domain": cap.domain.name if cap.domain else None,
                    "level": cap.level,
                    "business_owner": cap.business_owner,
                    "business_impact": getattr(cap, "business_criticality", None),
                    "priority": getattr(cap, "roadmap_priority", None),
                    "coverage": getattr(cap, "process_coverage", None),
                    "status": cap.status,
                }
            )

        return success_response(
            {
                "capabilities": capabilities,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": pagination.total,
                    "pages": pagination.pages,
                    "has_prev": pagination.has_prev,
                    "has_next": pagination.has_next,
                },
            }
        )

    except Exception:
        return error_response(
            message="Failed to retrieve capabilities",
            code="CAPABILITIES_RETRIEVAL_ERROR",
            details={"error": "See server logs for details"},
            status_code=500,
        )


@capabilities_bp.route("/<capability_id>", methods=["GET"])
@login_required
def get_capability(capability_id):
    """
    Get specific capability by ID
    ---
    tags:
      - Capabilities
    summary: Get capability details
    description: Returns detailed information about a specific capability
    parameters:
      - name: capability_id
        in: path
        required: true
        type: string
        description: Capability ID (can be string or integer)
    responses:
      200:
        description: Capability details
      404:
        description: Capability not found
    """
    try:
        # Handle both string and integer IDs
        capability = None

        # Try to find by string ID first (for large IDs)
        all_caps = UnifiedCapability.query.limit(2000).all()
        for cap in all_caps:
            if str(cap.id) == str(capability_id):
                capability = cap
                break

        # If not found, try integer lookup
        if not capability:
            try:
                capability_id_int = int(capability_id)
                capability = UnifiedCapability.visible_to_organization(
                    capability_id_int, g.current_org_id
                )
            except ValueError:
                pass

        if not capability:
            return not_found_response("Capability")

        capability_data = {
            "id": str(capability.id),
            "name": capability.name,
            "description": capability.description or "",
            "domain": capability.domain,
            "level": capability.level,
            "business_owner": capability.business_owner,
            "application": capability.application,
            "business_impact": capability.business_impact,
            "priority": capability.priority,
            "coverage": capability.coverage,
            "status": capability.status,
            "created_at": capability.created_at.isoformat() if capability.created_at else None,
            "updated_at": capability.updated_at.isoformat() if capability.updated_at else None,
        }

        return success_response(capability_data)

    except Exception:
        return error_response(
            message="Failed to retrieve capability",
            code="CAPABILITY_RETRIEVAL_ERROR",
            details={"error": "See server logs for details"},
            status_code=500,
        )


@capabilities_bp.route("/manufacturing", methods=["GET"])
@login_required
def get_manufacturing_capabilities():
    """
    Get manufacturing capabilities
    ---
    tags:
      - Capabilities
    summary: Get manufacturing capabilities
    description: Returns a list of manufacturing-specific capabilities
    parameters:
      - name: page
        in: query
        type: integer
        default: 1
        description: Page number
      - name: per_page
        in: query
        type: integer
        default: 50
        description: Items per page
    responses:
      200:
        description: List of manufacturing capabilities
    """
    try:
        page = safe_int_arg('page', 1, minimum=1)
        per_page = min(safe_int_arg('per_page', 50, minimum=1, maximum=500), 100)

        # Build query
        query = ManufacturingCapability.query

        # Paginate
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)

        capabilities = []
        for cap in pagination.items:
            # ManufacturingCapability only carries manufacturing-specific KPIs (OEE,
            # FPY, etc.) — identity/ownership fields (name, description, domain,
            # level, owner, status, priority, coverage) live on the UnifiedCapability
            # it specializes, reached via the unified_capability relationship.
            uc = cap.unified_capability
            capabilities.append(
                {
                    "id": str(cap.id),
                    "name": uc.name if uc else None,
                    "description": (uc.description if uc else None) or "",
                    "domain": uc.domain.name if uc and uc.domain else None,
                    "level": uc.level if uc else None,
                    "business_owner": uc.business_owner if uc else None,
                    "business_impact": getattr(uc, "business_criticality", None) if uc else None,
                    "priority": getattr(uc, "roadmap_priority", None) if uc else None,
                    "coverage": getattr(uc, "process_coverage", None) if uc else None,
                    "status": uc.status if uc else None,
                    "unified_capability_id": str(cap.unified_capability_id)
                    if cap.unified_capability_id
                    else None,
                }
            )

        return success_response(
            {
                "manufacturing_capabilities": capabilities,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": pagination.total,
                    "pages": pagination.pages,
                    "has_prev": pagination.has_prev,
                    "has_next": pagination.has_next,
                },
            }
        )

    except Exception:
        return error_response(
            message="Failed to retrieve manufacturing capabilities",
            code="MANUFACTURING_CAPABILITIES_RETRIEVAL_ERROR",
            details={"error": "See server logs for details"},
            status_code=500,
        )


@capabilities_bp.route("/domains", methods=["GET"])
@login_required
def get_capability_domains():
    """
    Get all capability domains
    ---
    tags:
      - Capabilities
    summary: Get capability domains
    description: Returns a list of all unique capability domains
    responses:
      200:
        description: List of capability domains
    """
    try:
        domains = db.session.query(UnifiedCapability.domain).distinct().all()
        domain_list = [domain[0] for domain in domains if domain[0]]

        return success_response({"domains": sorted(domain_list)})

    except Exception:
        return error_response(
            message="Failed to retrieve capability domains",
            code="DOMAINS_RETRIEVAL_ERROR",
            details={"error": "See server logs for details"},
            status_code=500,
        )


@capabilities_bp.route("/levels", methods=["GET"])
@login_required
def get_capability_levels():
    """
    Get all capability levels
    ---
    tags:
      - Capabilities
    summary: Get capability levels
    description: Returns a list of all unique capability levels
    responses:
      200:
        description: List of capability levels
    """
    try:
        levels = db.session.query(UnifiedCapability.level).distinct().all()
        level_list = [level[0] for level in levels if level[0]]

        return success_response({"levels": sorted(level_list)})

    except Exception:
        return error_response(
            message="Failed to retrieve capability levels",
            code="LEVELS_RETRIEVAL_ERROR",
            details={"error": "See server logs for details"},
            status_code=500,
        )

"""
API v1 Applications Endpoints

Standardized application management API endpoints following PRD - 003.
"""

from werkzeug.exceptions import HTTPException
from flask import Blueprint, request
from flask_login import login_required

from app.decorators import audit_log
from sqlalchemy import or_

from app import db
from app.models.application_layer import ApplicationProcess, ApplicationService
from app.models.application_portfolio import ApplicationComponent
from app.models.technical_capability import (
    ACMDomain,
    TechnicalCapability,
    application_technical_capability_mapping,
)
from app.utils.api_response import (
    error_response,
    not_found_response,
    success_response,
    validation_error_response,
)
from app.utils.duplicate_guard import (
    allow_duplicate_requested,
    duplicate_conflict_response,
    find_duplicate_by_name,
    find_similar_entities,
    lock_name_for_write,
)
from app.utils.pagination import safe_int_arg

applications_bp = Blueprint("applications_v1", __name__)


@applications_bp.route("/", methods=["GET"])
@login_required
def get_applications():
    """
    Get all applications with pagination and filtering
    ---
    tags:
      - Applications
    summary: Get paginated list of applications
    description: Returns a paginated list of applications with optional filtering
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
        description: Search term for application names
      - name: status
        in: query
        type: string
        description: Filter by status
    responses:
      200:
        description: List of applications
        schema:
          type: object
          properties:
            success:
              type: boolean
              example: true
            data:
              type: object
              properties:
                applications:
                  type: array
                  items:
                    type: object
                pagination:
                  type: object
                  properties:
                    page:
                      type: integer
                    per_page:
                      type: integer
                    total:
                      type: integer
                    pages:
                      type: integer
    """
    try:
        page = safe_int_arg('page', 1, minimum=1)
        per_page = min(safe_int_arg('per_page', 50, minimum=1, maximum=500), 100)
        search = request.args.get("search", "", type=str)
        status = request.args.get("status", "", type=str)

        # Build query
        query = ApplicationComponent.query

        # Apply filters
        if search:
            query = query.filter(
                or_(
                    ApplicationComponent.name.ilike(f"%{search}%"),
                    ApplicationComponent.description.ilike(f"%{search}%"),
                )
            )

        if status:
            query = query.filter(ApplicationComponent.lifecycle_status == status)

        # Paginate
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)

        applications = []
        for app in pagination.items:
            applications.append(
                {
                    "id": str(app.id),
                    "name": app.name,
                    "description": app.description or "",
                    "status": app.lifecycle_status,
                    "business_owner": app.business_owner,
                    "technical_owner": app.technical_owner,
                    "created_at": app.created_at.isoformat()
                    if app.created_at
                    else None,
                    "updated_at": app.updated_at.isoformat()
                    if app.updated_at
                    else None,
                }
            )

        return success_response(
            {
                "applications": applications,
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

    except HTTPException:

        raise

    except Exception:
        return error_response(
            message="Failed to retrieve applications",
            code="APPLICATIONS_RETRIEVAL_ERROR",
            details={"error": "See server logs for details"},
            status_code=500,
        )


@applications_bp.route("/<int:application_id>", methods=["GET"])
@login_required
def get_application(application_id):
    """
    Get specific application by ID
    ---
    tags:
      - Applications
    summary: Get application details
    description: Returns detailed information about a specific application
    parameters:
      - name: application_id
        in: path
        required: true
        type: integer
        description: Application ID
    responses:
      200:
        description: Application details
      404:
        description: Application not found
    """
    try:
        application = ApplicationComponent.query.get(application_id)

        if not application:
            return not_found_response("Application")

        # Get related services and processes
        services = ApplicationService.query.filter_by(
            application_component_id=application_id
        ).all()
        processes = ApplicationProcess.query.filter_by(
            application_component_id=application_id
        ).all()

        app_data = {
            "id": str(application.id),
            "name": application.name,
            "description": application.description or "",
            "status": application.lifecycle_status,
            "business_owner": application.business_owner,
            "technical_owner": application.technical_owner,
            "created_at": application.created_at.isoformat()
            if application.created_at
            else None,
            "updated_at": application.updated_at.isoformat()
            if application.updated_at
            else None,
            "services": [
                {
                    "id": str(service.id),
                    "name": service.name,
                    "description": service.description or "",
                }
                for service in services
            ],
            "processes": [
                {
                    "id": str(process.id),
                    "name": process.name,
                    "description": process.description or "",
                }
                for process in processes
            ],
        }

        return success_response(app_data)

    except HTTPException:

        raise

    except Exception:
        return error_response(
            message="Failed to retrieve application",
            code="APPLICATION_RETRIEVAL_ERROR",
            details={"error": "See server logs for details"},
            status_code=500,
        )


@applications_bp.route("/", methods=["POST"])
@login_required
@audit_log("api_application_create")
def create_application():
    """
    Create a new application
    ---
    tags:
      - Applications
    summary: Create new application
    description: Creates a new application with the provided data
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - name
          properties:
            name:
              type: string
              example: "New Application"
            description:
              type: string
              example: "Application description"
            business_owner:
              type: string
              example: "John Doe"
            technical_owner:
              type: string
              example: "Jane Smith"
            status:
              type: string
              example: "active"
    responses:
      201:
        description: Application created successfully
      400:
        description: Validation error
      409:
        description: >
          An application with the same normalised name already exists in this
          organisation. The body carries ``duplicate_of: {id, name}``. Resend
          with ``allow_duplicate=true`` (body field, ``?allow_duplicate=true``,
          or the ``X-Allow-Duplicate`` header) to create it anyway.
    """
    try:
        data = request.get_json()

        if not data:
            return validation_error_response({"error": "Request body is required"})

        # Validate required fields
        if not data.get("name"):
            return validation_error_response({"name": "Name is required"})

        # ARCH-030: match case-insensitively and whitespace-normalised — an
        # exact filter_by() let "HxGN EAM" and "hxgn  eam" both through.
        # ApplicationComponent inherits TenantMixin, so the organisation
        # predicate is injected; do not add one here.
        if not allow_duplicate_requested(data):
            # Serialise the check-then-insert against a concurrent identical
            # request (see lock_name_for_write).
            lock_name_for_write(ApplicationComponent, data["name"])
            existing = find_duplicate_by_name(ApplicationComponent, data["name"])
            if existing is not None:
                return duplicate_conflict_response("An application", existing)

        # S-06: near-duplicate advisory -- surfaced BEFORE the write commits,
        # not just in the post-hoc rationalization sweep. Never blocks.
        similar = find_similar_entities(ApplicationComponent, data["name"])

        # Create new application
        application = ApplicationComponent(
            name=data["name"],
            description=data.get("description", ""),
            business_owner=data.get("business_owner"),
            technical_owner=data.get("technical_owner"),
            # ARCH-031: no fabricated "operational" default -- an application
            # created with only a name has an unassessed lifecycle, not a live one.
            lifecycle_status=data.get("status"),
        )

        db.session.add(application)
        db.session.commit()

        return success_response(
            {
                "id": str(application.id),
                "name": application.name,
                "description": application.description,
                "status": application.lifecycle_status,
                "created_at": application.created_at.isoformat(),
                "similar_entities": similar,
                "similar_entities_count": len(similar),
            },
            status_code=201,
        )

    except HTTPException:

        raise

    except Exception:
        db.session.rollback()
        return error_response(
            message="Failed to create application",
            code="APPLICATION_CREATION_ERROR",
            details={"error": "See server logs for details"},
            status_code=500,
        )


@applications_bp.route("/<int:application_id>", methods=["PUT"])
@login_required
@audit_log("api_application_update")
def update_application(application_id):
    """
    Update an existing application
    ---
    tags:
      - Applications
    summary: Update application
    description: Updates an existing application with the provided data
    parameters:
      - name: application_id
        in: path
        required: true
        type: integer
        description: Application ID
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            name:
              type: string
            description:
              type: string
            business_owner:
              type: string
            technical_owner:
              type: string
            status:
              type: string
    responses:
      200:
        description: Application updated successfully
      404:
        description: Application not found
      400:
        description: Validation error
    """
    try:
        application = ApplicationComponent.query.get(application_id)

        if not application:
            return not_found_response("Application")

        data = request.get_json()

        if not data:
            return validation_error_response({"error": "Request body is required"})

        # Update fields
        if "name" in data:
            # Check for duplicate name (excluding current application)
            existing = ApplicationComponent.query.filter(
                ApplicationComponent.name == data["name"],
                ApplicationComponent.id != application_id,
            ).first()
            if existing:
                return error_response(
                    message="Application with this name already exists",
                    code="DUPLICATE_APPLICATION",
                    status_code=409,
                )
            application.name = data["name"]

        if "description" in data:
            application.description = data["description"]

        if "business_owner" in data:
            application.business_owner = data["business_owner"]

        if "technical_owner" in data:
            application.technical_owner = data["technical_owner"]

        if "status" in data:
            application.lifecycle_status = data["status"]

        db.session.commit()

        return success_response(
            {
                "id": str(application.id),
                "name": application.name,
                "description": application.description,
                "status": application.lifecycle_status,
                "updated_at": application.updated_at.isoformat(),
            }
        )

    except HTTPException:

        raise

    except Exception:
        db.session.rollback()
        return error_response(
            message="Failed to update application",
            code="APPLICATION_UPDATE_ERROR",
            details={"error": "See server logs for details"},
            status_code=500,
        )


@applications_bp.route("/<int:application_id>", methods=["DELETE"])
@login_required
@audit_log("api_application_delete")
def delete_application(application_id):
    """
    Delete an application
    ---
    tags:
      - Applications
    summary: Delete application
    description: Deletes an application and all its related data
    parameters:
      - name: application_id
        in: path
        required: true
        type: integer
        description: Application ID
    responses:
      200:
        description: Application deleted successfully
      404:
        description: Application not found
    """
    try:
        application = ApplicationComponent.query.get(application_id)

        if not application:
            return not_found_response("Application")

        # Delete related services and processes first (bulk delete to avoid ORM cascade issues)
        ApplicationService.query.filter_by(
            application_component_id=application_id
        ).delete(synchronize_session=False)
        ApplicationProcess.query.filter_by(
            application_component_id=application_id
        ).delete(synchronize_session=False)

        element_id = application.archimate_element_id

        # Use bulk delete to avoid ORM cascade loading related objects
        # (some related tables have schema drift that breaks ORM SELECT during delete)
        ApplicationComponent.query.filter_by(id=application_id).delete(
            synchronize_session=False
        )
        db.session.flush()

        # The mirror ArchiMate element goes with the application (finding C-02):
        # deleting the application alone left a permanent orphan in the repository.
        from app.modules.applications.routes._helpers import (
            _delete_mirror_archimate_element,
        )

        mirror = _delete_mirror_archimate_element(element_id)
        db.session.commit()

        message = "Application deleted successfully"
        if mirror["errors"]:
            message += "; " + "; ".join(mirror["errors"])
        return success_response(
            {
                "message": message,
                "id": str(application_id),
                "deleted": 1,
                "elements_deleted": mirror["elements_deleted"],
                "relationships_deleted": mirror["relationships_deleted"],
                "errors": mirror["errors"],
            }
        )

    except HTTPException:

        raise

    except Exception:
        db.session.rollback()
        return error_response(
            message="Failed to delete application",
            code="APPLICATION_DELETION_ERROR",
            details={"error": "See server logs for details"},
            status_code=500,
        )


# =============================================================================
# ACM Technical Capability Endpoints
# =============================================================================


@applications_bp.route("/<int:application_id>/acm", methods=["GET"])
@login_required
def get_application_acm(application_id):
    """
    Get ACM technical capabilities for an application
    ---
    tags:
      - Applications
      - ACM
    summary: Get application's ACM technical capability mappings
    description: Returns ACM domain coverage and mapped technical capabilities
    parameters:
      - name: application_id
        in: path
        required: true
        type: integer
        description: Application ID
    responses:
      200:
        description: ACM data for application
      404:
        description: Application not found
    """
    try:
        application = ApplicationComponent.query.get(application_id)

        if not application:
            return not_found_response("Application")

        # Get mapped technical capabilities. Querying the association *table*
        # object expands it into all its columns (8 per row), so select the
        # specific mapping columns (labeled) alongside the TechnicalCapability entity.
        _m = application_technical_capability_mapping
        mappings = (
            db.session.query(
                _m.c.id.label("m_id"),
                _m.c.capability_coverage.label("m_coverage"),
                _m.c.maturity_level.label("m_maturity"),
                _m.c.notes.label("m_notes"),
                TechnicalCapability,
            )
            .join(
                TechnicalCapability,
                _m.c.technical_capability_id == TechnicalCapability.id,
            )
            .filter(_m.c.application_id == application_id)
            .all()
        )

        capabilities = []
        domain_counts = {}

        for row in mappings:
            cap = row.TechnicalCapability
            capabilities.append(
                {
                    "id": cap.id,
                    "mapping_id": row.m_id,
                    "name": cap.name,
                    "code": cap.code,
                    "acm_domain": cap.acm_domain,
                    "level": cap.level,
                    "description": cap.description,
                    "coverage": row.m_coverage,
                    "maturity": row.m_maturity,
                    "notes": row.m_notes,
                }
            )

            # Count by domain
            domain = cap.acm_domain
            if domain not in domain_counts:
                domain_counts[domain] = {"count": 0, "capabilities": []}
            domain_counts[domain]["count"] += 1
            domain_counts[domain]["capabilities"].append(cap.name)

        # Build domain summary with full details
        domain_names = {
            "USER-EXPERIENCE": "User Experience",
            "APPLICATION-SERVICES": "Application Services",
            "DATA-STORAGE": "Data Storage",
            "SECURITY-IDENTITY": "Security Identity",
            "DEVOPS-PLATFORM": "DevOps Platform",
            "AI-ANALYTICS": "AI Analytics",
            "COMMUNICATION": "Communication",
        }

        domains = []
        for domain in ACMDomain.ALL_DOMAINS:
            domain_data = domain_counts.get(domain, {"count": 0, "capabilities": []})
            # Get total capabilities in this domain
            total_in_domain = TechnicalCapability.query.filter_by(
                acm_domain=domain
            ).count()
            coverage = round(
                (domain_data["count"] / total_in_domain * 100)
                if total_in_domain > 0
                else 0,
                1,
            )

            # Get level breakdown for this domain
            level_counts = (
                db.session.query(
                    TechnicalCapability.level, db.func.count(TechnicalCapability.id)
                )
                .filter(TechnicalCapability.acm_domain == domain)
                .group_by(TechnicalCapability.level)
                .all()
            )

            by_level = {"L1": 0, "L2": 0, "L3": 0, "L4": 0}
            for level, count in level_counts:
                if level in by_level:
                    by_level[level] = count

            domains.append(
                {
                    "domain": domain,
                    "name": domain_names.get(domain, domain),
                    "description": ACMDomain.DOMAIN_DESCRIPTIONS.get(domain, ""),
                    "count": domain_data["count"],
                    "total": total_in_domain,
                    "coverage": coverage,
                    "by_level": by_level,
                }
            )

        # Calculate overall coverage
        total_capabilities = TechnicalCapability.query.count()
        overall_coverage = round(
            (len(capabilities) / total_capabilities * 100)
            if total_capabilities > 0
            else 0,
            1,
        )

        return success_response(
            {
                "success": True,
                "application_id": application_id,
                "capabilities": capabilities,
                "domains": domains,
                "mapped_count": len(capabilities),
                "coverage": overall_coverage,
                "primary_domain": application.acm_primary_domain,
                "capability_level": application.acm_capability_level,
            }
        )

    except HTTPException:

        raise

    except Exception:
        return error_response(
            message="Failed to retrieve ACM data",
            code="ACM_RETRIEVAL_ERROR",
            details={"error": "See server logs for details"},
            status_code=500,
        )


@applications_bp.route("/<int:application_id>/acm/<int:mapping_id>", methods=["DELETE"])
@login_required
@audit_log("api_acm_mapping_delete")
def delete_application_acm_mapping(application_id, mapping_id):
    """
    Delete an ACM technical capability mapping from an application
    ---
    tags:
      - Applications
      - ACM
    summary: Remove ACM capability mapping
    description: Removes a technical capability mapping from the application
    parameters:
      - name: application_id
        in: path
        required: true
        type: integer
        description: Application ID
      - name: mapping_id
        in: path
        required: true
        type: integer
        description: Mapping ID
    responses:
      200:
        description: Mapping deleted successfully
      404:
        description: Mapping not found
    """
    try:
        # Find and delete the mapping
        result = db.session.execute(  # tenant-filtered: scoped via parent FK
            application_technical_capability_mapping.delete().where(
                db.and_(
                    application_technical_capability_mapping.c.id == mapping_id,
                    application_technical_capability_mapping.c.application_id
                    == application_id,
                )
            )
        )

        if result.rowcount == 0:
            return not_found_response("ACM Mapping")

        db.session.commit()

        return success_response(
            {
                "success": True,
                "message": "ACM mapping deleted successfully",
                "mapping_id": mapping_id,
            }
        )

    except HTTPException:

        raise

    except Exception:
        db.session.rollback()
        return error_response(
            message="Failed to delete ACM mapping",
            code="ACM_DELETION_ERROR",
            details={"error": "See server logs for details"},
            status_code=500,
        )


@applications_bp.route("/<int:application_id>/acm", methods=["POST"])
@login_required
@audit_log("api_acm_mapping_create")
def add_application_acm_mapping(application_id):
    """
    Add ACM technical capability mapping to an application
    ---
    tags:
      - Applications
      - ACM
    summary: Add ACM capability mapping
    description: Maps a technical capability to the application
    parameters:
      - name: application_id
        in: path
        required: true
        type: integer
        description: Application ID
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - technical_capability_id
          properties:
            technical_capability_id:
              type: integer
            capability_coverage:
              type: string
              enum: [full, partial, minimal]
              default: partial
            maturity_level:
              type: string
              enum: [initial, developing, defined, managed, optimized]
            notes:
              type: string
    responses:
      201:
        description: Mapping created successfully
      400:
        description: Validation error
      404:
        description: Application or capability not found
    """
    try:
        data = request.get_json()

        if not data or "technical_capability_id" not in data:
            return validation_error_response({"technical_capability_id": "Required"})

        application = ApplicationComponent.query.get(application_id)
        if not application:
            return not_found_response("Application")

        capability = TechnicalCapability.query.get(data["technical_capability_id"])
        if not capability:
            return not_found_response("Technical Capability")

        # Check if mapping already exists
        existing = db.session.execute(  # tenant-filtered: scoped via parent FK
            application_technical_capability_mapping.select().where(
                db.and_(
                    application_technical_capability_mapping.c.application_id
                    == application_id,
                    application_technical_capability_mapping.c.technical_capability_id
                    == data["technical_capability_id"],
                )
            )
        ).fetchone()

        if existing:
            return validation_error_response(
                {"technical_capability_id": "Mapping already exists"}
            )

        # Create mapping
        from datetime import datetime

        db.session.execute(  # tenant-filtered: scoped via parent FK
            application_technical_capability_mapping.insert().values(
                application_id=application_id,
                technical_capability_id=data["technical_capability_id"],
                capability_coverage=data.get("capability_coverage", "partial"),
                maturity_level=data.get("maturity_level"),
                notes=data.get("notes"),
                created_at=datetime.utcnow(),
            )
        )
        db.session.commit()

        return success_response(
            {
                "success": True,
                "message": "ACM mapping created successfully",
                "application_id": application_id,
                "technical_capability_id": data["technical_capability_id"],
            },
            status_code=201,
        )

    except HTTPException:

        raise

    except Exception:
        db.session.rollback()
        return error_response(
            message="Failed to create ACM mapping",
            code="ACM_CREATION_ERROR",
            details={"error": "See server logs for details"},
            status_code=500,
        )


# ===================================================================
# FRAG-038: TCO calculation route
# ===================================================================

@applications_bp.route("/<int:app_id>/tco")
@login_required
def api_application_tco(app_id):
    """FRAG-038: Get TCO breakdown for an application."""
    try:
        from app.services.tco_calculation_service import TCOCalculationService
        app_obj = ApplicationComponent.query.get_or_404(app_id)
        service = TCOCalculationService()
        breakdown = service.calculate_tco_breakdown(app_obj)
        roi = service.calculate_roi_score(app_obj)
        return success_response(data={"tco": breakdown, "roi": roi})
    except HTTPException:
        raise
    except Exception as e:
        return error_response(message=str(e), code="TCO_ERROR", status_code=500)

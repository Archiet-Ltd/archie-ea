"""
DEPRECATED: This file is migrated to app/modules/solutions_strategic/.
Registration is now centralized via app.modules.solutions_strategic.register().
Do NOT modify -- kept as fallback until Phase 6 cleanup.

Roadmap API Blueprint
Complete CRUD operations for roadmap entities with automation support
"""

from werkzeug.exceptions import HTTPException
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import Blueprint, abort, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import text

from app import db
from app.decorators import audit_log
from app.utils.api_helpers import api_error
from app.models.implementation_migration import (
    Deliverable,
    Gap as ImplementationGap,
    Plateau as ImplementationPlateau,
)
from app.models.unified_work_package import UnifiedWorkPackage as ImplementationWorkPackage
from app.services import work_package_service
from app.utils.tenant import current_organization_id
from app.modules.solutions_strategic.v2.services.roadmap_automation import (
    RoadmapAutomationEngine,
)
from app.modules.solutions_strategic.v2.services.roadmap_sync import RoadmapDataSync
from app.modules.solutions_strategic.v2.services.roadmap_validator import (
    RoadmapValidator,
)
from app.utils.pagination import safe_int_arg

logger = logging.getLogger(__name__)


def _require(work_package_id):
    """This organisation's work package, else a 404 (another organisation's id
    is indistinguishable from a missing one)."""
    work_package = work_package_service.get_work_package(
        work_package_id, current_organization_id()
    )
    if work_package is None:
        abort(404)
    return work_package


def _legacy_id(work_package):
    """Deliverables still key on the retired work_packages table; a row merged
    from it carries that id in source_id."""
    if work_package.source_table == "work_packages":
        return work_package.source_id
    return None

# Create blueprint
roadmap_bp = Blueprint("roadmap_api", __name__, url_prefix="/api/roadmap")

# Initialize services
automation_engine = RoadmapAutomationEngine()
validator = RoadmapValidator()
sync_service = RoadmapDataSync()


def handle_error(error: Exception, context: str) -> Dict[str, Any]:
    """Standard error handling for API endpoints"""
    logger.error(f"Error in {context}: {str(error)}")
    return {
        "error": str(error),
        "context": context,
        "timestamp": datetime.utcnow().isoformat(),
    }, 500


def validate_json_data(required_fields: List[str]) -> Optional[Dict[str, Any]]:
    """Validate JSON request data"""
    if not request.is_json:
        return {"error": "Request must be JSON"}, 400

    data = request.get_json()
    if not data:
        return {"error": "No data provided"}, 400

    missing_fields = [field for field in required_fields if field not in data]
    if missing_fields:
        return {"error": f'Missing required fields: {", ".join(missing_fields)}'}, 400

    return None


# ==================== WORK PACKAGE CRUD ====================


@roadmap_bp.route("/work-packages", methods=["GET"])
@login_required
def get_work_packages():
    """
    Get Work Packages
    ---
    tags:
      - Roadmap
    summary: List all work packages
    description: Retrieves work packages with optional filtering and pagination
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
        description: Items per page (max 100)
      - name: status
        in: query
        type: string
        description: Filter by status
      - name: business_capability
        in: query
        type: string
        description: Filter by business capability
      - name: search
        in: query
        type: string
        description: Search term
    responses:
      200:
        description: List of work packages
      401:
        description: Authentication required
    security:
      - Bearer: []
    """
    try:
        # Query parameters
        page = safe_int_arg('page', 1, minimum=1)
        per_page = min(safe_int_arg('per_page', 50, minimum=1, maximum=500), 100)
        status = request.args.get("status")
        business_capability = request.args.get("business_capability")
        assigned_to = request.args.get("assigned_to")
        search = request.args.get("search", "").strip()

        # Build base query
        query = work_package_service.query_for(current_organization_id())

        # Apply filters
        if status:
            query = query.filter(ImplementationWorkPackage.status == status)
        if business_capability:
            query = query.filter(
                ImplementationWorkPackage.business_capability == business_capability
            )
        if assigned_to:
            query = query.filter(ImplementationWorkPackage.assigned_to.ilike(f"%{assigned_to}%"))
        if search:
            query = query.filter(
                ImplementationWorkPackage.name.ilike(f"%{search}%")
                | ImplementationWorkPackage.description.ilike(f"%{search}%")
            )

        # Paginate results
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)

        # Format response
        work_packages = []
        for wp in pagination.items:
            work_packages.append(
                {
                    "id": wp.id,
                    "name": wp.name,
                    "description": wp.description,
                    "status": wp.status,
                    "business_capability": wp.business_capability,
                    "assigned_to": wp.assigned_to,
                    "start_date": wp.start_date.isoformat() if wp.start_date else None,
                    "end_date": wp.end_date.isoformat() if wp.end_date else None,
                    "progress_percentage": wp.progress_percentage,
                    "estimated_cost": float(wp.estimated_cost) if wp.estimated_cost else None,
                    "priority": wp.priority,
                    "created_at": wp.created_at.isoformat() if wp.created_at else None,
                    "updated_at": wp.updated_at.isoformat() if wp.updated_at else None,
                    "auto_generated": getattr(wp, "auto_generated", False),
                    "source_data": getattr(wp, "source_data", None),
                    "confidence_score": getattr(wp, "confidence_score", None),
                }
            )

        return jsonify(
            {
                "work_packages": work_packages,
                "pagination": {
                    "page": pagination.page,
                    "pages": pagination.pages,
                    "per_page": pagination.per_page,
                    "total": pagination.total,
                    "has_next": pagination.has_next,
                    "has_prev": pagination.has_prev,
                },
                "filters": {
                    "status": status,
                    "business_capability": business_capability,
                    "assigned_to": assigned_to,
                    "search": search,
                },
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        return handle_error(e, "get_work_packages")


@roadmap_bp.route("/work-packages", methods=["POST"])
@login_required
@audit_log("create_work_package")
def create_work_package():
    """
    Create new work package with automation support
    ---
    tags:
      - Roadmap
    summary: Create work package
    description: Create a new work package manually or auto-generate from capabilities/gaps
    security:
      - cookieAuth: []
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - name
            - business_capability
          properties:
            name:
              type: string
            description:
              type: string
            business_capability:
              type: string
            assigned_to:
              type: string
            status:
              type: string
              default: planned
            start_date:
              type: string
              format: date
            end_date:
              type: string
              format: date
            priority:
              type: string
              enum: [low, medium, high, critical]
            auto_generate:
              type: boolean
              description: Auto-generate from capability or gap
    responses:
      201:
        description: Work package created
      400:
        description: Validation error
      401:
        description: Unauthorized
    """
    try:
        # Validate required fields
        validation_error = validate_json_data(["name", "business_capability"])
        if validation_error:
            return validation_error

        data = request.get_json()

        # Validate business rules
        validation_result = validator.validate_work_package_data(data)
        if not validation_result["valid"]:
            return api_error(
                "Validation failed",
                "VALIDATION_ERROR",
                errors=validation_result["errors"],
            )

        # Check for automation request
        auto_generate = data.get("auto_generate", False)
        if auto_generate:
            # Auto-generate from capability or gap
            generated_packages = automation_engine.generate_work_packages(
                source_type=data.get("source_type", "capability"),
                source_id=data.get("source_id"),
                options=data.get("generation_options", {}),
            )
            return (
                jsonify(
                    {
                        "message": "Work packages auto-generated",
                        "work_packages": generated_packages,
                        "generated_count": len(generated_packages),
                    }
                ),
                201,
            )

        # Create manual work package (the one writer)
        work_package = work_package_service.create_work_package(
            organization_id=current_organization_id(),
            user_id=current_user.id,
            name=data["name"],
            description=data.get("description", ""),
            business_capability=data["business_capability"],
            assigned_to=data.get("assigned_to"),
            status=data.get("status", "planned"),
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            progress_percentage=data.get("progress_percentage", 0),
            estimated_cost=data.get("estimated_cost"),
            priority=data.get("priority", "medium"),
            source_data=data.get("source_data"),
            confidence_score=data.get("confidence_score", 1.0),
        )
        db.session.commit()

        return (
            jsonify(
                {
                    "message": "Work package created successfully",
                    "work_package": {
                        "id": work_package.id,
                        "name": work_package.name,
                        "business_capability": work_package.business_capability,
                        "status": work_package.status,
                        "created_at": work_package.created_at.isoformat(),
                    },
                }
            ),
            201,
        )

    except HTTPException:

        raise

    except work_package_service.WorkPackageError as e:
        db.session.rollback()
        return api_error(str(e), "VALIDATION_ERROR")

    except Exception as e:
        db.session.rollback()
        return handle_error(e, "create_work_package")


@roadmap_bp.route("/work-packages/<int:work_package_id>", methods=["GET"])
@login_required
def get_work_package(work_package_id: int):
    """
    Get specific work package with full details
    ---
    tags:
      - Roadmap
    summary: Get work package by ID
    description: Retrieve a specific work package with deliverables and dependencies
    security:
      - cookieAuth: []
    parameters:
      - name: work_package_id
        in: path
        type: integer
        required: true
        description: Work package ID
    responses:
      200:
        description: Work package details
      401:
        description: Unauthorized
      404:
        description: Not found
    """
    try:
        work_package = _require(work_package_id)

        # Get related deliverables
        legacy_id = _legacy_id(work_package)
        deliverables = (
            Deliverable.query.filter_by(work_package_id=legacy_id).all() if legacy_id else []
        )

        # Get dependencies (ids held on the work package itself)
        dep_ids = work_package_service.dependency_ids(work_package)
        dependencies = []
        if dep_ids:
            dependencies = [
                (d.id, d.name, d.status)
                for d in work_package_service.query_for(current_organization_id())
                .filter(ImplementationWorkPackage.id.in_(dep_ids))
                .all()
            ]

        return jsonify(
            {
                "work_package": {
                    "id": work_package.id,
                    "name": work_package.name,
                    "description": work_package.description,
                    "status": work_package.status,
                    "business_capability": work_package.business_capability,
                    "assigned_to": work_package.assigned_to,
                    "start_date": work_package.start_date.isoformat()
                    if work_package.start_date
                    else None,
                    "end_date": work_package.end_date.isoformat()
                    if work_package.end_date
                    else None,
                    "progress_percentage": work_package.progress_percentage,
                    "estimated_cost": float(work_package.estimated_cost)
                    if work_package.estimated_cost
                    else None,
                    "priority": work_package.priority,
                    "created_at": work_package.created_at.isoformat()
                    if work_package.created_at
                    else None,
                    "updated_at": work_package.updated_at.isoformat()
                    if work_package.updated_at
                    else None,
                    "auto_generated": getattr(work_package, "auto_generated", False),
                    "source_data": getattr(work_package, "source_data", None),
                    "confidence_score": getattr(work_package, "confidence_score", None),
                },
                "deliverables": [
                    {
                        "id": d.id,
                        "name": d.name,
                        "status": d.status,
                        "due_date": d.due_date.isoformat() if d.due_date else None,
                    }
                    for d in deliverables
                ],
                "dependencies": [
                    {"id": dep[0], "name": dep[1], "status": dep[2]} for dep in dependencies
                ],
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        return handle_error(e, "get_work_package")


@roadmap_bp.route("/work-packages/<int:work_package_id>", methods=["PUT"])
@login_required
@audit_log("update_work_package")
def update_work_package(work_package_id: int):
    """
    Update work package with validation and sync
    ---
    tags:
      - Roadmap
    summary: Update work package
    description: Update an existing work package
    security:
      - cookieAuth: []
    parameters:
      - name: work_package_id
        in: path
        type: integer
        required: true
        description: Work package ID
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
            status:
              type: string
            priority:
              type: string
    responses:
      200:
        description: Work package updated
      400:
        description: Validation error
      401:
        description: Unauthorized
      404:
        description: Not found
    """
    try:
        work_package = _require(work_package_id)

        # Validate JSON data
        if not request.is_json:
            return {"error": "Request must be JSON"}, 400

        data = request.get_json()
        if not data:
            return {"error": "No data provided"}, 400

        # Validate business rules
        validation_result = validator.validate_work_package_update(work_package, data)
        if not validation_result["valid"]:
            return api_error(
                "Validation failed",
                "VALIDATION_ERROR",
                errors=validation_result["errors"],
            )

        # Update fields
        updatable_fields = [
            "name",
            "description",
            "status",
            "business_capability",
            "assigned_to",
            "start_date",
            "end_date",
            "progress_percentage",
            "estimated_cost",
            "priority",
        ]

        work_package = work_package_service.update_work_package(
            work_package_id,
            organization_id=current_organization_id(),
            user_id=current_user.id,
            **{f: data[f] for f in updatable_fields if f in data},
        )
        db.session.commit()

        return jsonify(
            {
                "message": "Work package updated successfully",
                "work_package": {
                    "id": work_package.id,
                    "name": work_package.name,
                    "status": work_package.status,
                    "updated_at": work_package.updated_at.isoformat(),
                },
            }
        )

    except HTTPException:

        raise

    except work_package_service.WorkPackageError as e:
        db.session.rollback()
        return api_error(str(e), "VALIDATION_ERROR")

    except Exception as e:
        db.session.rollback()
        return handle_error(e, "update_work_package")


@roadmap_bp.route("/work-packages/<int:work_package_id>", methods=["DELETE"])
@login_required
@audit_log("delete_work_package")
def delete_work_package(work_package_id: int):
    """
    Delete work package with dependency checks
    ---
    tags:
      - Roadmap
    summary: Delete work package
    description: Delete a work package (fails if has dependencies or deliverables)
    security:
      - cookieAuth: []
    parameters:
      - name: work_package_id
        in: path
        type: integer
        required: true
        description: Work package ID
    responses:
      200:
        description: Work package deleted
      400:
        description: Cannot delete - has dependencies
      401:
        description: Unauthorized
      404:
        description: Not found
    """
    try:
        work_package = _require(work_package_id)

        # Check for dependencies
        dependent_count = len(
            work_package_service.dependents_of(work_package_id, current_organization_id())
        )

        if dependent_count > 0:
            return (
                jsonify(
                    {
                        "error": "Cannot delete work package with dependencies",
                        "dependent_count": dependent_count,
                    }
                ),
                400,
            )

        # Check for deliverables
        legacy_id = _legacy_id(work_package)
        deliverable_count = (
            Deliverable.query.filter_by(work_package_id=legacy_id).count() if legacy_id else 0
        )
        if deliverable_count > 0:
            return (
                jsonify(
                    {
                        "error": "Cannot delete work package with deliverables",
                        "deliverable_count": deliverable_count,
                    }
                ),
                400,
            )

        # Delete the work package
        work_package_service.delete_work_package(
            work_package_id, organization_id=current_organization_id()
        )
        db.session.commit()

        return jsonify({"message": "Work package deleted successfully"})

    except HTTPException:

        raise

    except work_package_service.WorkPackageError as e:
        db.session.rollback()
        return api_error(str(e), "VALIDATION_ERROR")

    except Exception as e:
        db.session.rollback()
        return handle_error(e, "delete_work_package")


# ==================== DELIVERABLE CRUD ====================


@roadmap_bp.route("/deliverables", methods=["GET"])
@login_required
def get_deliverables():
    """
    Get all deliverables with filtering
    ---
    tags:
      - Roadmap
    summary: List deliverables
    description: Get all deliverables with optional filtering
    security:
      - cookieAuth: []
    parameters:
      - name: work_package_id
        in: query
        type: integer
        required: false
        description: Filter by work package ID
      - name: status
        in: query
        type: string
        required: false
        description: Filter by status
    responses:
      200:
        description: List of deliverables
      401:
        description: Unauthorized
    """
    try:
        work_package_id = request.args.get("work_package_id", type=int)
        status = request.args.get("status")

        query = Deliverable.query
        if work_package_id:
            query = query.filter(Deliverable.work_package_id == work_package_id)
        if status:
            query = query.filter(Deliverable.delivery_status == status)

        deliverables = query.all()

        return jsonify(
            {
                "deliverables": [
                    {
                        "id": d.id,
                        "name": d.name,
                        "description": d.description,
                        "work_package_id": d.work_package_id,
                        "status": d.delivery_status,
                        "due_date": d.target_date.isoformat() if d.target_date else None,
                        "delivered_date": d.delivered_date.isoformat()
                        if d.delivered_date
                        else None,
                        "approval_criteria": getattr(d, "artifact_references", None),
                    }
                    for d in deliverables
                ]
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        return handle_error(e, "get_deliverables")


@roadmap_bp.route("/deliverables", methods=["POST"])
@login_required
@audit_log("create_deliverable")
def create_deliverable():
    """
    Create new deliverable
    ---
    tags:
      - Roadmap
    summary: Create deliverable
    description: Create a new deliverable for a work package
    security:
      - cookieAuth: []
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - name
            - work_package_id
          properties:
            name:
              type: string
            description:
              type: string
            work_package_id:
              type: integer
            status:
              type: string
              default: planned
            due_date:
              type: string
              format: date
    responses:
      201:
        description: Deliverable created
      400:
        description: Validation error
      401:
        description: Unauthorized
    """
    try:
        validation_error = validate_json_data(["name", "work_package_id"])
        if validation_error:
            return validation_error

        data = request.get_json()

        # Validate work package exists
        legacy_id = _legacy_id(_require(data["work_package_id"]))
        if not legacy_id:
            return api_error(
                "Deliverables can only be added to work packages carried over from the earlier work package list.",
                "VALIDATION_ERROR",
            )

        # Deliverable columns are delivery_status/target_date/assigned_user_id — the
        # old code used status/due_date/approval_criteria/created_by (none exist).
        deliverable = Deliverable(
            name=data["name"],
            description=data.get("description", ""),
            work_package_id=legacy_id,
            delivery_status=data.get("status", "planned"),
            target_date=datetime.fromisoformat(data["due_date"]) if data.get("due_date") else None,
        )

        db.session.add(deliverable)
        db.session.commit()

        return (
            jsonify(
                {
                    "message": "Deliverable created successfully",
                    "deliverable": {
                        "id": deliverable.id,
                        "name": deliverable.name,
                        "work_package_id": deliverable.work_package_id,
                        "status": deliverable.delivery_status,
                    },
                }
            ),
            201,
        )

    except HTTPException:

        raise

    except Exception as e:
        db.session.rollback()
        return handle_error(e, "create_deliverable")


@roadmap_bp.route("/deliverables/<int:deliverable_id>", methods=["PUT"])
@login_required
@audit_log("update_deliverable")
def update_deliverable(deliverable_id: int):
    """
    Update deliverable
    ---
    tags:
      - Roadmap
    summary: Update deliverable
    description: Update an existing deliverable
    security:
      - cookieAuth: []
    parameters:
      - name: deliverable_id
        in: path
        type: integer
        required: true
        description: Deliverable ID
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            name:
              type: string
            status:
              type: string
            due_date:
              type: string
              format: date
    responses:
      200:
        description: Deliverable updated
      401:
        description: Unauthorized
      404:
        description: Not found
    """
    try:
        deliverable = Deliverable.query.get_or_404(deliverable_id)

        if not request.is_json:
            return {"error": "Request must be JSON"}, 400

        data = request.get_json()

        updatable_fields = [
            "name",
            "description",
            "status",
            "due_date",
            "delivered_date",
            "approval_criteria",
        ]

        for field in updatable_fields:
            if field in data:
                if field in ["due_date", "delivered_date"] and data[field]:
                    setattr(deliverable, field, datetime.fromisoformat(data[field]))
                else:
                    setattr(deliverable, field, data[field])

        deliverable.updated_by = current_user.id
        deliverable.updated_at = datetime.utcnow()

        db.session.commit()

        return jsonify(
            {
                "message": "Deliverable updated successfully",
                "deliverable": {
                    "id": deliverable.id,
                    "name": deliverable.name,
                    "status": deliverable.status,
                },
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        db.session.rollback()
        return handle_error(e, "update_deliverable")


@roadmap_bp.route("/deliverables/<int:deliverable_id>", methods=["DELETE"])
@login_required
@audit_log("delete_deliverable")
def delete_deliverable(deliverable_id: int):
    """
    Delete deliverable
    ---
    tags:
      - Roadmap
    summary: Delete deliverable
    description: Delete a deliverable by ID
    security:
      - cookieAuth: []
    parameters:
      - name: deliverable_id
        in: path
        type: integer
        required: true
        description: Deliverable ID
    responses:
      200:
        description: Deliverable deleted
      401:
        description: Unauthorized
      404:
        description: Not found
    """
    try:
        deliverable = Deliverable.query.get_or_404(deliverable_id)

        db.session.delete(deliverable)
        db.session.commit()

        return jsonify({"message": "Deliverable deleted successfully"})

    except HTTPException:

        raise

    except Exception as e:
        db.session.rollback()
        return handle_error(e, "delete_deliverable")


# ==================== GAP CRUD ====================


@roadmap_bp.route("/gaps", methods=["GET"])
@login_required
def get_gaps():
    """
    Get all implementation gaps
    ---
    tags:
      - Roadmap
    summary: List implementation gaps
    description: Get all implementation gaps with optional filtering
    security:
      - cookieAuth: []
    parameters:
      - name: priority
        in: query
        type: string
        required: false
        description: Filter by priority
      - name: gap_type
        in: query
        type: string
        required: false
        description: Filter by gap type
    responses:
      200:
        description: List of gaps
      401:
        description: Unauthorized
    """
    try:
        priority = request.args.get("priority")
        gap_type = request.args.get("gap_type")

        query = ImplementationGap.query
        if priority:
            query = query.filter(ImplementationGap.priority == priority)
        if gap_type:
            query = query.filter(ImplementationGap.gap_type == gap_type)

        gaps = query.all()

        return jsonify(
            {
                "gaps": [
                    {
                        "id": g.id,
                        "name": g.name,
                        "description": g.description,
                        "gap_type": g.gap_type,
                        "priority": g.priority,
                        "current_state": g.current_state_ref,
                        "target_state": g.target_state_ref,
                        "impact_assessment": getattr(g, "impact_assessment", None),
                        "created_at": g.created_at.isoformat() if g.created_at else None,
                    }
                    for g in gaps
                ]
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        return handle_error(e, "get_gaps")


@roadmap_bp.route("/gaps", methods=["POST"])
@login_required
@audit_log("create_gap")
def create_gap():
    """
    Create new implementation gap
    ---
    tags:
      - Roadmap
    summary: Create implementation gap
    description: Create a new implementation gap
    security:
      - cookieAuth: []
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - name
            - gap_type
          properties:
            name:
              type: string
            description:
              type: string
            gap_type:
              type: string
            priority:
              type: string
              default: medium
            current_state:
              type: string
            target_state:
              type: string
    responses:
      201:
        description: Gap created
      400:
        description: Validation error
      401:
        description: Unauthorized
    """
    try:
        validation_error = validate_json_data(["name", "gap_type"])
        if validation_error:
            return validation_error

        data = request.get_json()

        # Gap has no 'impact_assessment' (only a short 'impact' enum) and no
        # 'created_by' — the old kwargs crashed every call. Fold the free-text
        # impact narrative into description so it isn't lost.
        _impact = (data.get("impact_assessment") or "").strip()
        _desc = data.get("description", "")
        if _impact:
            _desc = (f"{_desc}\n\nImpact: {_impact}" if _desc else f"Impact: {_impact}")
        gap = ImplementationGap(
            name=data["name"],
            description=_desc,
            gap_type=data["gap_type"],
            priority=data.get("priority", "medium"),
            current_state_ref=data.get("current_state", ""),
            target_state_ref=data.get("target_state", ""),
        )

        db.session.add(gap)
        db.session.commit()

        return (
            jsonify(
                {
                    "message": "Gap created successfully",
                    "gap": {
                        "id": gap.id,
                        "name": gap.name,
                        "gap_type": gap.gap_type,
                        "priority": gap.priority,
                    },
                }
            ),
            201,
        )

    except HTTPException:

        raise

    except Exception as e:
        db.session.rollback()
        return handle_error(e, "create_gap")


# ==================== PLATEAU CRUD ====================


@roadmap_bp.route("/plateaus", methods=["GET"])
@login_required
def get_plateaus():
    """
    Get all implementation plateaus
    ---
    tags:
      - Roadmap
    summary: List implementation plateaus
    description: Get all implementation plateaus
    security:
      - cookieAuth: []
    responses:
      200:
        description: List of plateaus
      401:
        description: Unauthorized
    """
    try:
        plateaus = ImplementationPlateau.query.limit(500).all()

        return jsonify(
            {
                "plateaus": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "description": getattr(p, "description", None),
                        "target_date": p.target_date.isoformat() if getattr(p, "target_date", None) else None,
                        "sequence_order": getattr(p, "sequence_order", None),
                        "state_summary": getattr(p, "state_summary", None),
                        "created_at": p.created_at.isoformat() if getattr(p, "created_at", None) else None,
                    }
                    for p in plateaus
                ]
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        return handle_error(e, "get_plateaus")


@roadmap_bp.route("/plateaus", methods=["POST"])
@login_required
@audit_log("create_plateau")
def create_plateau():
    """
    Create new implementation plateau
    ---
    tags:
      - Roadmap
    summary: Create implementation plateau
    description: Create a new implementation plateau
    security:
      - cookieAuth: []
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
            description:
              type: string
            start_date:
              type: string
              format: date
            end_date:
              type: string
              format: date
            stability_period:
              type: string
            transition_state:
              type: string
    responses:
      201:
        description: Plateau created
      400:
        description: Validation error
      401:
        description: Unauthorized
    """
    try:
        validation_error = validate_json_data(["name"])
        if validation_error:
            return validation_error

        data = request.get_json()

        # Plateau is milestone-based: it has target_date/sequence_order, not
        # start_date/end_date/stability_period/transition_state/created_by. Map the
        # target milestone to end_date; the other legacy kwargs have no columns.
        plateau = ImplementationPlateau(
            name=data["name"],
            description=data.get("description", ""),
            target_date=datetime.fromisoformat(data["end_date"]) if data.get("end_date") else None,
        )

        db.session.add(plateau)
        db.session.commit()

        return (
            jsonify(
                {
                    "message": "Plateau created successfully",
                    "plateau": {
                        "id": plateau.id,
                        "name": plateau.name,
                        "target_date": plateau.target_date.isoformat() if plateau.target_date else None,
                    },
                }
            ),
            201,
        )

    except HTTPException:

        raise

    except Exception as e:
        db.session.rollback()
        return handle_error(e, "create_plateau")


# ==================== AUTOMATION ENDPOINTS ====================


@roadmap_bp.route("/automation/generate-work-packages", methods=["POST"])
@login_required
@audit_log("generate_work_packages")
def generate_work_packages():
    """
    Auto-generate work packages from capabilities or gaps
    ---
    tags:
      - Roadmap
      - Automation
    summary: Generate work packages
    description: Auto-generate work packages from capabilities or gaps
    security:
      - cookieAuth: []
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - source_type
            - source_id
          properties:
            source_type:
              type: string
              enum: [capability, gap]
            source_id:
              type: integer
            options:
              type: object
    responses:
      200:
        description: Work packages generated
      400:
        description: Validation error
      401:
        description: Unauthorized
    """
    try:
        validation_error = validate_json_data(["source_type", "source_id"])
        if validation_error:
            return validation_error

        data = request.get_json()

        generated_packages = automation_engine.generate_work_packages(
            source_type=data["source_type"],
            source_id=data["source_id"],
            options=data.get("options", {}),
        )

        return jsonify(
            {
                "message": "Work packages generated successfully",
                "generated_packages": generated_packages,
                "count": len(generated_packages),
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        return handle_error(e, "generate_work_packages")


@roadmap_bp.route("/automation/optimize-timeline", methods=["POST"])
@login_required
@audit_log("optimize_timeline")
def optimize_timeline():
    """
    Optimize timeline based on dependencies and resources
    ---
    tags:
      - Roadmap
      - Automation
    summary: Optimize timeline
    description: Optimize timeline based on dependencies and resources
    security:
      - cookieAuth: []
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - work_package_ids
          properties:
            work_package_ids:
              type: array
              items:
                type: integer
            constraints:
              type: object
    responses:
      200:
        description: Timeline optimized
      400:
        description: Validation error
      401:
        description: Unauthorized
    """
    try:
        validation_error = validate_json_data(["work_package_ids"])
        if validation_error:
            return validation_error

        data = request.get_json()

        optimized_timeline = automation_engine.optimize_timeline(
            work_package_ids=data["work_package_ids"], constraints=data.get("constraints", {})
        )

        return jsonify(
            {"message": "Timeline optimized successfully", "optimized_timeline": optimized_timeline}
        )

    except HTTPException:

        raise

    except Exception as e:
        return handle_error(e, "optimize_timeline")


@roadmap_bp.route("/automation/detect-conflicts", methods=["POST"])
@login_required
@audit_log("detect_conflicts")
def detect_conflicts():
    """
    Detect scheduling and resource conflicts
    ---
    tags:
      - Roadmap
      - Automation
    summary: Detect conflicts
    description: Detect scheduling and resource conflicts
    security:
      - cookieAuth: []
    parameters:
      - name: body
        in: body
        required: false
        schema:
          type: object
          properties:
            work_package_ids:
              type: array
              items:
                type: integer
            date_range:
              type: object
    responses:
      200:
        description: Conflicts detected
      401:
        description: Unauthorized
    """
    try:
        data = request.get_json() or {}

        conflicts = automation_engine.detect_conflicts(
            work_package_ids=data.get("work_package_ids", []), date_range=data.get("date_range", {})
        )

        return jsonify({"conflicts": conflicts, "conflict_count": len(conflicts)})

    except HTTPException:

        raise

    except Exception as e:
        return handle_error(e, "detect_conflicts")


# ==================== SYNC ENDPOINTS ====================


@roadmap_bp.route("/sync/capabilities", methods=["POST"])
@login_required
@audit_log("sync_capabilities")
def sync_capabilities():
    """
    Sync capabilities to work packages
    ---
    tags:
      - Roadmap
      - Sync
    summary: Sync capabilities
    description: Sync capabilities to work packages
    security:
      - cookieAuth: []
    responses:
      200:
        description: Sync completed
      401:
        description: Unauthorized
    """
    try:
        sync_result = sync_service.sync_capabilities_to_work_packages()

        return jsonify({"message": "Capabilities synced successfully", "sync_result": sync_result})

    except HTTPException:

        raise

    except Exception as e:
        return handle_error(e, "sync_capabilities")


@roadmap_bp.route("/sync/applications", methods=["POST"])
@login_required
@audit_log("sync_applications")
def sync_applications():
    """
    Sync applications to deliverables
    ---
    tags:
      - Roadmap
      - Sync
    summary: Sync applications
    description: Sync applications to deliverables
    security:
      - cookieAuth: []
    responses:
      200:
        description: Sync completed
      401:
        description: Unauthorized
    """
    try:
        sync_result = sync_service.sync_applications_to_deliverables()

        return jsonify({"message": "Applications synced successfully", "sync_result": sync_result})

    except HTTPException:

        raise

    except Exception as e:
        return handle_error(e, "sync_applications")


# ==================== STATISTICS ENDPOINTS ====================


@roadmap_bp.route("/statistics", methods=["GET"])
@login_required
def get_statistics():
    """
    Get comprehensive roadmap statistics
    ---
    tags:
      - Roadmap
    summary: Get roadmap statistics
    description: Get comprehensive statistics about work packages, deliverables, gaps, and plateaus
    security:
      - cookieAuth: []
    responses:
      200:
        description: Roadmap statistics
      401:
        description: Unauthorized
    """
    try:
        # These are per-user roadmap statistics, not a system-wide aggregate:
        # the "tenant-exempt: aggregate stats" note they carried was wrong, and
        # work_packages / gaps are both tenant tables. Raw SQL bypasses the ORM
        # listener, so the predicate has to be written out.
        from flask import g as _g
        _org = getattr(_g, "current_org_id", None)
        _org_where = " WHERE organization_id = :org" if _org is not None else ""
        _org_and = " AND organization_id = :org" if _org is not None else ""
        _org_params = {"org": _org} if _org is not None else {}
        _gap_kind_clause = (
            " AND gap_kind != 'plateau_transition'"
            if _org_where
            else " WHERE gap_kind != 'plateau_transition'"
        )

        stats = {
            "work_packages": {
                "total": ImplementationWorkPackage.query.count(),
                "by_status": dict(
                    db.session.execute(
                        text(
                            f"""
                    SELECT status, COUNT(*)
                    FROM work_packages{_org_where}
                    GROUP BY status
                """
                        ), _org_params
                    ).fetchall()
                ),
                "by_priority": dict(
                    db.session.execute(
                        text(
                            f"""
                    SELECT COALESCE(priority, 'unset'), COUNT(*)
                    FROM work_packages{_org_where}
                    GROUP BY priority
                """
                        ), _org_params
                    ).fetchall()
                ),
                "total_cost": db.session.execute(
                    text(
                        f"""
                    SELECT COALESCE(SUM(estimated_cost), 0)
                    FROM work_packages
                    WHERE estimated_cost IS NOT NULL{_org_and}
                """
                    ), _org_params
                ).fetchone()[0]
                or 0,
            },
            "deliverables": {
                "total": Deliverable.query.count(),
                "by_status": dict(
                    db.session.execute(  # tenant-exempt: system table (aggregate stats)
                        text(
                            """
                    SELECT COALESCE(delivery_status, 'unknown'), COUNT(*)
                    FROM deliverables
                    GROUP BY delivery_status
                """
                        )
                    ).fetchall()
                ),
            },
            "gaps": {
                "total": ImplementationGap.query.filter(ImplementationGap.gap_kind != "plateau_transition").count(),
                "by_priority": dict(
                    db.session.execute(
                        # _org_where/_gap_kind_clause are each one of a fixed
                        # set of hardcoded literal clauses; the only dynamic
                        # value (_org) is always bound via _org_params, never
                        # interpolated -- no request input reaches this text.
                        text(
                            f"SELECT COALESCE(priority, 'unset'), COUNT(*) "
                            f"FROM gaps{_org_where}{_gap_kind_clause} "
                            f"GROUP BY priority"  # nosec B608
                        ), _org_params
                    ).fetchall()
                ),
                "by_type": dict(
                    db.session.execute(
                        # Same shape as by_priority above.
                        text(
                            f"SELECT COALESCE(gap_type, 'unset'), COUNT(*) "
                            f"FROM gaps{_org_where}{_gap_kind_clause} "
                            f"GROUP BY gap_type"  # nosec B608
                        ), _org_params
                    ).fetchall()
                ),
            },
            "plateaus": {"total": ImplementationPlateau.query.count()},
            "automation_metrics": {
                "auto_generated_count": 0,
                "manual_count": ImplementationWorkPackage.query.count(),
                "average_confidence": 0,
            },
        }

        return jsonify(stats)

    except HTTPException:

        raise

    except Exception as e:
        return handle_error(e, "get_statistics")


# ==================== ARCHIMATE ROADMAP GENERATION ====================


@roadmap_bp.route("/archimate/generate-roadmap", methods=["POST"])
@login_required
@audit_log("generate_archimate_roadmap")
def generate_archimate_roadmap():
    """
    Generate complete ArchiMate Implementation & Migration roadmap from gaps
    ---
    tags:
      - Roadmap
      - ArchiMate
    summary: Generate ArchiMate roadmap
    description: Generate complete roadmap from gaps with work packages, plateaus, and timeline
    security:
      - cookieAuth: []
    parameters:
      - name: body
        in: body
        required: false
        schema:
          type: object
          properties:
            gap_ids:
              type: array
              items:
                type: integer
              description: Specific gaps to include
            architecture_id:
              type: integer
              description: Filter by architecture
            priority_filter:
              type: string
              description: Filter by priority
            include_plateaus:
              type: boolean
              default: true
            timeline_months:
              type: integer
              default: 18
    responses:
      200:
        description: Roadmap generated
      400:
        description: Generation failed
      401:
        description: Unauthorized
    """
    try:
        from app.modules.solutions_strategic.v2.services.archimate.roadmap_generator import (
            RoadmapGenerator,
        )

        data = request.get_json() or {}

        generator = RoadmapGenerator()
        result = generator.generate_roadmap_from_gaps(
            gap_ids=data.get("gap_ids"),
            architecture_id=data.get("architecture_id"),
            priority_filter=data.get("priority_filter"),
            include_plateaus=data.get("include_plateaus", True),
            timeline_months=data.get("timeline_months", 18),
        )

        if result["success"]:
            return jsonify(result), 200
        else:
            return jsonify(result), 400

    except HTTPException:

        raise

    except Exception as e:
        return handle_error(e, "generate_archimate_roadmap")


@roadmap_bp.route("/archimate/preview-roadmap", methods=["POST"])
@login_required
@audit_log("preview_archimate_roadmap")
def preview_archimate_roadmap():
    """
    Preview roadmap without creating work packages
    ---
    tags:
      - Roadmap
      - ArchiMate
    summary: Preview ArchiMate roadmap
    description: Preview roadmap structure and statistics without creating work packages
    security:
      - cookieAuth: []
    parameters:
      - name: body
        in: body
        required: false
        schema:
          type: object
          properties:
            gap_ids:
              type: array
              items:
                type: integer
            architecture_id:
              type: integer
    responses:
      200:
        description: Preview generated
      400:
        description: Preview failed
      401:
        description: Unauthorized
    """
    try:
        from app.modules.solutions_strategic.v2.services.archimate.roadmap_generator import (
            RoadmapGenerator,
        )

        data = request.get_json() or {}

        generator = RoadmapGenerator()
        result = generator.preview_roadmap(
            gap_ids=data.get("gap_ids"), architecture_id=data.get("architecture_id")
        )

        if result["success"]:
            return jsonify(result), 200
        else:
            return jsonify(result), 400

    except HTTPException:

        raise

    except Exception as e:
        return handle_error(e, "preview_archimate_roadmap")


@roadmap_bp.route("/archimate/generate-single-gap/<int:gap_id>", methods=["POST"])
@login_required
@audit_log("generate_single_gap_roadmap")
def generate_single_gap_roadmap(gap_id: int):
    """
    Generate mini-roadmap for a single gap
    ---
    tags:
      - Roadmap
      - ArchiMate
    summary: Generate single gap roadmap
    description: Generate mini-roadmap for a single gap for preview or quick planning
    security:
      - cookieAuth: []
    parameters:
      - name: gap_id
        in: path
        type: integer
        required: true
        description: Gap ID
    responses:
      200:
        description: Roadmap generated
      400:
        description: Generation failed
      401:
        description: Unauthorized
    """
    try:
        from app.modules.solutions_strategic.v2.services.archimate.roadmap_generator import (
            RoadmapGenerator,
        )

        generator = RoadmapGenerator()
        result = generator.generate_single_gap_roadmap(gap_id)

        if result["success"]:
            return jsonify(result), 200
        else:
            return jsonify(result), 400

    except HTTPException:

        raise

    except Exception as e:
        return handle_error(e, "generate_single_gap_roadmap")

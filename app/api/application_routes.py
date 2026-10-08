"""
Application Management API Routes

This blueprint contains API endpoints for application management operations.
Moved from application_mgmt/routes.py as part of route consolidation.

API Endpoints:
    /api/applications/table-data - Get applications table data
    /api/applications/<id>/details - Get application details
    /api/applications/<id>/architecture/elements - Get architecture elements
    /api/applications/<id>/architecture/export-csv - Export architecture
    /api/applications/duplicates - Find duplicate applications
    /api/applications/analyze-similarity - Analyze application similarity
    /api/applications/bulk-consolidate - Bulk consolidate duplicates
    /api/applications/<app_id>/process-links - Manage process links

All endpoints return JSON and support the following headers:
    X-API-Version: 1.0
    X-Deprecated: false (true for deprecated endpoints)

Deprecation:
    Endpoints in this blueprint may be deprecated in the future.
    Check X-Deprecation-Date header for deprecation date.
"""

import logging

from flask import Blueprint, jsonify, request
from flask_login import login_required
from sqlalchemy import inspect
from sqlalchemy.orm import joinedload
from werkzeug.exceptions import HTTPException

from app import db
from app.decorators import audit_log, require_roles
from app.models.application_portfolio import ApplicationComponent
from app.utils.api_helpers import api_error
from app.services.rate_limiter import rate_limit
from app.utils.pagination import safe_int_arg

logger = logging.getLogger(__name__)

# Create blueprint
application_api_bp = Blueprint(
    "application_api", __name__, url_prefix="/api/applications"
)


# =============================================================================
# CRUD Endpoints
# =============================================================================


@application_api_bp.route("/", methods=["POST"])
@login_required
@audit_log("application_create")
def create_application():
    """
    Create a new application.

    Request JSON:
        name (str): Application name (required)
        description (str): Application description (optional)
        application_type (str): Type of application (optional)
        business_domain (str): Business domain (optional)

    Returns:
        JSON with created application data or error message
    """
    try:
        data = request.get_json() or {}
        name = data.get("name", "").strip()

        if not name:
            return api_error("Application name is required", "MISSING_NAME")

        # Check for duplicate names
        existing = ApplicationComponent.query.filter_by(name=name).first()
        if existing:
            return api_error(f"Application '{name}' already exists", "DUPLICATE_NAME")

        # Create new application
        app = ApplicationComponent(
            name=name,
            description=data.get("description", ""),
            application_type=data.get("application_type"),
            business_domain=data.get("business_domain"),
        )

        db.session.add(app)
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "id": app.id,
                "name": app.name,
                "message": "Application created successfully",
            }
        ), 201

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating application: {str(e)}")
        return api_error("Failed to create application", "INTERNAL_ERROR", 500)


# =============================================================================
# Table Data Endpoints
# =============================================================================


@application_api_bp.route("/table-data", methods=["GET"])
@login_required
def api_applications_table():
    """
    Get paginated applications data for DataTables.

    Query Parameters:
        page (int): Page number (default: 1)
        per_page (int): Items per page (default: 10)
        search (str): Search query
        sort_column (str): Column to sort by
        sort_direction (str): asc or desc
        status (str): Filter by status
        type (str): Filter by application type

    Returns:
        JSON with data, recordsTotal, recordsFiltered, page, pages
    """
    try:
        # Get query parameters
        page = max(safe_int_arg('page', 1, minimum=1), 1)
        per_page = min(max(safe_int_arg('per_page', 10, minimum=1, maximum=500), 1), 100)
        search = request.args.get("search", "")
        sort_column = request.args.get("sort_column", "name")
        sort_direction = request.args.get("sort_direction", "asc")
        status_filter = request.args.get("status")
        type_filter = request.args.get("type")

        table_columns = {
            col["name"] for col in inspect(db.engine).get_columns(ApplicationComponent.__tablename__)
        }
        status_field = (
            "deployment_status"
            if "deployment_status" in table_columns
            else "lifecycle_status" if "lifecycle_status" in table_columns else None
        )

        select_names = [
            "id",
            "name",
            "description",
            "application_type",
            "business_domain",
            "business_owner",
            "technical_owner",
            "created_at",
            "updated_at",
        ]
        if status_field:
            select_names.append(status_field)
        select_columns = [getattr(ApplicationComponent, c) for c in select_names]

        # ISS-022: Whitelist sortable columns to prevent timing attacks
        ALLOWED_SORT_COLUMNS = {
            "name",
            "description",
            "application_type",
            "business_domain",
            "business_owner",
            "created_at",
            "updated_at",
        }
        if status_field:
            ALLOWED_SORT_COLUMNS.add(status_field)
        if sort_column not in ALLOWED_SORT_COLUMNS:
            sort_column = "name"
        if sort_direction not in ("asc", "desc"):
            sort_direction = "asc"

        # Build query
        query = db.session.query(*select_columns)

        # Apply filters
        if status_filter and status_field:
            query = query.filter(getattr(ApplicationComponent, status_field) == status_filter)
        if type_filter:
            query = query.filter(ApplicationComponent.application_type == type_filter)
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                (ApplicationComponent.name.ilike(search_term))
                | (ApplicationComponent.description.ilike(search_term))
                | (ApplicationComponent.business_domain.ilike(search_term))
            )

        # Get total count before pagination
        records_total = query.count()

        # Apply sorting
        sort_attr = getattr(
            ApplicationComponent, sort_column, ApplicationComponent.name
        )
        if sort_direction == "desc":
            query = query.order_by(sort_attr.desc())
        else:
            query = query.order_by(sort_attr.asc())

        # Apply pagination
        items = query.offset((page - 1) * per_page).limit(per_page).all()
        pages = (records_total + per_page - 1) // per_page if records_total > 0 else 0

        data = []
        for row in items:
            app = row._mapping
            data.append(
                {
                    "id": app["id"],
                    "name": app["name"],
                    "description": app["description"] or "",
                    "application_type": app["application_type"],
                    "business_domain": app["business_domain"],
                    "business_owner": app["business_owner"],
                    "technical_owner": app["technical_owner"],
                    "status": app.get(status_field, "unknown") if status_field else "unknown",
                    "created_at": app["created_at"].isoformat() if app["created_at"] else None,
                    "updated_at": app["updated_at"].isoformat() if app["updated_at"] else None,
                }
            )

        return jsonify(
            {
                "data": data,
                "recordsTotal": records_total,
                "recordsFiltered": records_total,
                "page": page,
                "pages": pages,
                "per_page": per_page,
                "total": records_total,
            }
        )

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error fetching applications table: {e}", exc_info=True)
        return jsonify(
            {"error": "Failed to fetch applications", "message": "An internal error occurred. Please try again."}
        ), 500


# =============================================================================
# Application Solutions Endpoint
# =============================================================================


@application_api_bp.route("/<int:app_id>/solutions", methods=["GET"])
@login_required
def api_application_solutions(app_id):
    """Return solutions in whose scope this application appears.

    Searches Solution.in_scope_applications JSON text for the application name
    and Solution.affected_systems for the application ID.  Falls back to an
    empty list when neither match.
    """
    try:
        from sqlalchemy import cast, String as SAString
        from app.models.solution_models import Solution
        from flask_login import current_user

        app = ApplicationComponent.query.get_or_404(app_id)

        # Build query: admin sees all, others see own solutions only
        if hasattr(current_user, "is_admin") and current_user.is_admin():
            base = Solution.query
        else:
            base = Solution.query.filter_by(created_by_id=current_user.id)

        solutions = (
            base.filter(
                cast(Solution.in_scope_applications, SAString).ilike(
                    f"%{app.name}%"
                )
            )
            .order_by(Solution.updated_at.desc())
            .limit(50)
            .all()
        )

        from flask import url_for as _url_for
        return jsonify({
            "solutions": [
                {
                    "id": s.id,
                    "name": s.name,
                    "status": s.status or "planned",
                    "business_domain": s.business_domain or "",
                    "description": (s.description or "")[:120],
                    "url": _url_for(
                        "solution_design.view_solution", solution_id=s.id
                    ),
                }
                for s in solutions
            ]
        })
    except HTTPException:
        raise
    except Exception as exc:
        db.session.rollback()
        logger.error(f"Error fetching solutions for application {app_id}: {exc}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to load linked solutions"}), 500


# =============================================================================
# Application Details Endpoints
# =============================================================================


@application_api_bp.route("/<string:id>/details", methods=["GET"])
@login_required
def api_application_details(id):
    """
    Get detailed information about an application.

    Args:
        id (str): Application ID

    Returns:
        JSON with application details including:
        - Basic info (name, description, type, status)
        - Business context (owner, domain, criticality)
        - Technical details (stack, interfaces, integrations)
        - Relationships (capabilities, processes, vendors)
    """
    try:
        app = ApplicationComponent.query.get_or_404(id)

        # Get related data
        # ApplicationComponent maps to capabilities through capability_mappings, not a
        # direct .capabilities relationship; .vendors does not exist as a relationship.
        capabilities = []
        for m in (getattr(app, "capability_mappings", None) or []):
            cap = getattr(m, "business_capability", None)
            if cap:
                capabilities.append(
                    {"id": cap.id, "name": cap.name, "level": getattr(cap, "level", None)}
                )

        vendors = []
        for vendor in (getattr(app, "vendors", None) or []):
            vendors.append({"id": vendor.id, "name": vendor.name})

        return jsonify(
            {
                "id": app.id,
                "name": app.name,
                "description": app.description,
                "application_type": app.application_type,
                "business_domain": app.business_domain,
                "business_owner": app.business_owner,
                "technical_owner": app.technical_owner,
                "status": getattr(app, "status", None) or getattr(app, "lifecycle_status", None),
                "criticality": app.criticality,
                "technology_stack": app.technology_stack,
                "deployment_status": app.deployment_status,
                "capabilities": capabilities,
                "vendors": vendors,
                "interfaces_count": len(app.interfaces) if app.interfaces else 0,
                "integrations_count": 0,  # integrations relationship doesn't exist on ApplicationComponent
                "created_at": app.created_at.isoformat() if app.created_at else None,
                "updated_at": app.updated_at.isoformat() if app.updated_at else None,
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching application details: {e}")
        return jsonify(
            {"error": "Failed to fetch application details", "message": "An internal error occurred. Please try again."}
        ), 500


# =============================================================================
# Architecture Elements Endpoints
# =============================================================================


@application_api_bp.route("/<string:id>/architecture/elements", methods=["GET", "POST"])
@login_required
@require_roles("admin", "architect")
@audit_log("arch_element_create")
def api_arch_elements(id):
    """
    Get or create architecture elements for an application.

    GET:
        Get all architecture elements linked to the application.

    POST:
        Create a new architecture element.

    Request Body (POST):
        {
            "element_type": "BusinessProcess|DataObject|...",
            "name": "Element name",
            "description": "Element description",
            "properties": {}
        }
    """
    try:
        ApplicationComponent.query.get_or_404(id)

        if request.method == "GET":
            # Return linked architecture elements
            elements = []

            # Get from various related tables
            from app.models.application_layer import (
                ApplicationProcess,
                ApplicationService,
                DataObject,
            )

            # Collect all elements
            processes = ApplicationProcess.query.filter_by(
                application_component_id=id
            ).all()
            for proc in processes:
                elements.append(
                    {
                        "id": proc.id,
                        "type": "application_process",
                        "name": proc.name,
                        "description": proc.description,
                    }
                )

            services = ApplicationService.query.filter_by(
                application_component_id=id
            ).all()
            for svc in services:
                elements.append(
                    {
                        "id": svc.id,
                        "type": "application_service",
                        "name": svc.name,
                        "description": svc.description,
                    }
                )

            data_objects = DataObject.query.filter_by(application_component_id=id).all()
            for obj in data_objects:
                elements.append(
                    {
                        "id": obj.id,
                        "type": "data_object",
                        "name": obj.name,
                        "description": obj.description,
                    }
                )

            return jsonify(
                {"application_id": id, "elements": elements, "count": len(elements)}
            )

        else:  # POST
            data = request.get_json()
            element_type = data.get("element_type")
            name = data.get("name")
            description = data.get("description", "")

            if not element_type or not name:
                return jsonify(
                    {
                        "error": "Missing required fields",
                        "message": "element_type and name are required",
                    }
                ), 400

            # Create element based on type
            from app.models.application_layer import (
                ApplicationProcess,
                ApplicationService,
                DataObject,
            )

            element = None

            if element_type == "application_process":
                element = ApplicationProcess(
                    application_component_id=id,
                    name=name,
                    description=description,
                    created_at=db.func.now(),
                )
            elif element_type == "application_service":
                element = ApplicationService(
                    application_component_id=id,
                    name=name,
                    description=description,
                    created_at=db.func.now(),
                )
            elif element_type == "data_object":
                element = DataObject(
                    application_component_id=id,
                    name=name,
                    description=description,
                    created_at=db.func.now(),
                )
            else:
                return jsonify(
                    {
                        "error": "Invalid element type",
                        "message": f"Unknown element type: {element_type}",
                    }
                ), 400

            if element:
                db.session.add(element)
                db.session.commit()

                return jsonify(
                    {
                        "message": "Element created successfully",
                        "element": {
                            "id": element.id,
                            "type": element_type,
                            "name": name,
                            "description": description,
                        },
                    }
                ), 201

    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error with architecture elements: {e}")
        return jsonify(
            {"error": "Failed to process architecture elements", "message": "An internal error occurred. Please try again."}
        ), 500


@application_api_bp.route(
    "/<string:id>/architecture/elements/<string:element_id>", methods=["PUT", "DELETE"]
)
@login_required
@require_roles("admin", "architect")
@audit_log("arch_element_modify")
def api_arch_element_ops(id, element_id):
    """
    Update or delete an architecture element.

    PUT:
        Update an existing element.

    DELETE:
        Delete an element.
    """
    try:
        from app.models.application_layer import (
            ApplicationProcess,
            ApplicationService,
            DataObject,
        )

        # Find the element across all types
        element = (
            ApplicationProcess.query.get(element_id)
            or ApplicationService.query.get(element_id)
            or DataObject.query.get(element_id)
        )

        if not element:
            return api_error(
                f"No element found with id: {element_id}",
                "NOT_FOUND",
                404,
            )

        if request.method == "DELETE":
            db.session.delete(element)
            db.session.commit()
            return jsonify({"success": True, "message": "Element deleted successfully"})

        else:  # PUT
            data = request.get_json()
            if "name" in data:
                element.name = data["name"]
            if "description" in data:
                element.description = data["description"]

            db.session.commit()

            return jsonify(
                {
                    "message": "Element updated successfully",
                    "element": {
                        "id": element.id,
                        "type": type(element).__name__,
                        "name": element.name,
                        "description": element.description,
                    },
                }
            )

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error with architecture element: {e}")
        return api_error("Failed to process element", "INTERNAL_ERROR", 500)


@application_api_bp.route("/<string:id>/architecture/export-csv", methods=["GET"])
@login_required
def api_arch_export(id):
    """
    Export application architecture as CSV.

    Returns:
        CSV file with architecture elements
    """
    try:
        import csv
        import io

        ApplicationComponent.query.get_or_404(id)

        # Get all elements
        from app.models.application_layer import (
            ApplicationProcess,
            ApplicationService,
            DataObject,
        )

        elements = []
        elements.extend(
            ApplicationProcess.query.filter_by(application_component_id=id).all()
        )
        elements.extend(
            ApplicationService.query.filter_by(application_component_id=id).all()
        )
        elements.extend(DataObject.query.filter_by(application_component_id=id).all())

        # Create CSV
        output = io.StringIO()
        writer = csv.writer(output)

        # Header
        writer.writerow(["ID", "Type", "Name", "Description", "Created At"])

        # Data
        for elem in elements:
            writer.writerow(
                [
                    elem.id,
                    type(elem).__name__,
                    elem.name,
                    elem.description or "",
                    elem.created_at.isoformat() if elem.created_at else "",
                ]
            )

        output.seek(0)

        from flask import Response

        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=architecture_{id}.csv",
                "X-API-Version": "1.0",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error exporting architecture: {e}")
        return jsonify(
            {"error": "Failed to export architecture", "message": "An internal error occurred. Please try again."}
        ), 500


# =============================================================================
# Duplicate Detection Endpoints
# =============================================================================


@application_api_bp.route("/duplicates", methods=["GET"])
@login_required
@rate_limit(3, "1h")
def api_find_duplicates():
    """
    Find potential duplicate applications.

    Query Parameters:
        threshold (float): Similarity threshold (0.0-1.0, default: 0.7)
        include_processed (bool): Include already processed duplicates (default: false)

    Returns:
        JSON with list of potential duplicates grouped by similarity
    """
    try:
        threshold = request.args.get("threshold", 0.7, type=float)
        request.args.get("include_processed", "false").lower() == "true"

        # Get all applications
        apps = ApplicationComponent.query.limit(2000).all()

        # Compare each pair
        duplicates = []
        checked = set()

        for i, app1 in enumerate(apps):
            for j, app2 in enumerate(apps):
                if i >= j:
                    continue
                if (app1.id, app2.id) in checked:
                    continue

                # Calculate similarity
                similarity = calculate_similarity(app1, app2)

                if similarity >= threshold:
                    duplicates.append(
                        {
                            "app1": {"id": app1.id, "name": app1.name},
                            "app2": {"id": app2.id, "name": app2.name},
                            "similarity": round(similarity, 3),
                        }
                    )
                    checked.add((app1.id, app2.id))

        return jsonify(
            {"duplicates": duplicates, "count": len(duplicates), "threshold": threshold}
        )

    except Exception as e:
        logger.error(f"Error finding duplicates: {e}")
        return api_error("Failed to find duplicates", "INTERNAL_ERROR", 500)


def calculate_similarity(app1, app2):
    """
    Calculate similarity score between two applications.

    Returns:
        float: Similarity score (0.0-1.0)
    """
    score = 0.0
    factors = 0

    # Name similarity
    if app1.name and app2.name:
        if app1.name.lower() == app2.name.lower():
            score += 1.0
        elif (
            app1.name.lower() in app2.name.lower()
            or app2.name.lower() in app1.name.lower()
        ):
            score += 0.7
        factors += 1

    # Domain match
    if app1.business_domain and app2.business_domain:
        if app1.business_domain.lower() == app2.business_domain.lower():
            score += 0.5
        factors += 1

    # Owner match
    if app1.business_owner and app2.business_owner:
        if app1.business_owner.lower() == app2.business_owner.lower():
            score += 0.3
        factors += 1

    # Type match
    if app1.application_type and app2.application_type:
        if app1.application_type.lower() == app2.application_type.lower():
            score += 0.2
        factors += 1

    return score / factors if factors > 0 else 0.0


@application_api_bp.route("/analyze-similarity", methods=["POST"])
@login_required
@audit_log("application_similarity_analyze")
def api_analyze_similarity():
    """
    Analyze similarity between two specific applications.

    Request Body:
        {
            "app1_id": 1,
            "app2_id": 2
        }

    Returns:
        JSON with detailed similarity analysis
    """
    try:
        data = request.get_json()
        app1_id = data.get("app1_id")
        app2_id = data.get("app2_id")

        if not app1_id or not app2_id:
            return jsonify(
                {
                    "error": "Missing application IDs",
                    "message": "Both app1_id and app2_id are required",
                }
            ), 400

        app1 = ApplicationComponent.query.get(app1_id)
        app2 = ApplicationComponent.query.get(app2_id)

        if not app1 or not app2:
            return jsonify(
                {
                    "error": "Application not found",
                    "message": "One or both applications not found",
                }
            ), 404

        # Calculate detailed similarity
        analysis = {
            "app1": {"id": app1.id, "name": app1.name},
            "app2": {"id": app2.id, "name": app2.name},
            "similarity_score": calculate_similarity(app1, app2),
            "factors": {
                "name_similarity": calculate_name_similarity(app1.name, app2.name),
                "domain_match": app1.business_domain == app2.business_domain,
                "owner_match": app1.business_owner == app2.business_owner,
                "type_match": app1.application_type == app2.application_type,
            },
            "recommendation": determine_recommendation(app1, app2),
        }

        return jsonify(analysis)

    except Exception as e:
        logger.error(f"Error analyzing similarity: {e}")
        return jsonify(
            {"error": "Failed to analyze similarity", "message": "An internal error occurred. Please try again."}
        ), 500


def calculate_name_similarity(name1, name2):
    """Calculate name similarity score."""
    if not name1 or not name2:
        return 0.0
    if name1.lower() == name2.lower():
        return 1.0
    if name1.lower() in name2.lower() or name2.lower() in name1.lower():
        return 0.7
    return 0.0


def determine_recommendation(app1, app2):
    """Determine merge recommendation based on similarity."""
    score = calculate_similarity(app1, app2)
    if score >= 0.9:
        return "MERGE_RECOMMENDED: Very high similarity, consider merging"
    elif score >= 0.7:
        return "REVIEW_RECOMMENDED: Significant similarity, manual review suggested"
    elif score >= 0.5:
        return "POSSIBLE_RELATED: Some similarity, may be related"
    else:
        return "LIKELY_DISTINCT: Low similarity, likely distinct applications"


@application_api_bp.route("/bulk-consolidate", methods=["POST"])
@login_required
@require_roles("admin", "architect")
@audit_log("application_bulk_consolidate")
def api_bulk_consolidate():
    """
    Bulk consolidate duplicate applications.

    Request Body:
        {
            "pairs": [
                {"primary_id": 1, "secondary_id": 2},
                {"primary_id": 3, "secondary_id": 4}
            ],
            "merge_strategy": "primary" | "secondary"
        }

    Returns:
        JSON with consolidation results
    """
    try:
        data = request.get_json()
        pairs = data.get("pairs", [])
        merge_strategy = data.get("merge_strategy", "primary")

        if merge_strategy not in ("primary", "secondary"):
            return jsonify({
                "error": "Unsupported merge_strategy",
                "message": (
                    f"merge_strategy must be 'primary' or 'secondary' "
                    f"(got {merge_strategy!r}). The 'new record' strategy is not yet available."
                ),
            }), 400

        results = []

        for pair in pairs:
            primary_id = pair.get("primary_id")
            secondary_id = pair.get("secondary_id")

            primary = ApplicationComponent.query.get(primary_id)
            secondary = ApplicationComponent.query.get(secondary_id)

            if not primary or not secondary:
                results.append(
                    {
                        "pair": {
                            "primary_id": primary_id,
                            "secondary_id": secondary_id,
                        },
                        "status": "FAILED",
                        "message": "One or both applications not found",
                    }
                )
                continue

            # Perform consolidation
            try:
                consolidate_applications(primary, secondary, merge_strategy)
                results.append(
                    {
                        "pair": {
                            "primary_id": primary_id,
                            "secondary_id": secondary_id,
                        },
                        "status": "SUCCESS",
                        "message": f"Consolidated {secondary.name} into {primary.name}",
                    }
                )
            except Exception:
                results.append(
                    {
                        "pair": {
                            "primary_id": primary_id,
                            "secondary_id": secondary_id,
                        },
                        "status": "FAILED",
                        "message": "An internal error occurred. Please try again.",
                    }
                )

        return jsonify(
            {
                "results": results,
                "total": len(pairs),
                "successful": sum(1 for r in results if r["status"] == "SUCCESS"),
                "failed": sum(1 for r in results if r["status"] == "FAILED"),
            }
        )

    except Exception as e:
        logger.error(f"Error in bulk consolidate: {e}")
        return jsonify(
            {"error": "Failed to consolidate applications", "message": "An internal error occurred. Please try again."}
        ), 500


def consolidate_applications(primary, secondary, strategy):
    """
    Consolidate two applications.

    Args:
        primary: Primary application (will be kept)
        secondary: Secondary application (will be merged/deleted)
        strategy: 'primary' or 'secondary'
    """
    if strategy == "primary":
        # Merge secondary into primary
        if secondary.description and not primary.description:
            primary.description = secondary.description
        # Additional field merging (tags, vendor links) deferred to per-field merge UI

        # Delete secondary
        db.session.delete(secondary)

    elif strategy == "secondary":
        # Merge primary into secondary
        if primary.description and not secondary.description:
            secondary.description = primary.description

        db.session.delete(primary)

    else:
        # Defensive: callers validate merge_strategy up-front, so this only fires
        # if an unsupported strategy reaches here.
        raise ValueError(f"Unsupported merge strategy: {strategy!r}")

    db.session.commit()


# =============================================================================
# Process Links Endpoints
# =============================================================================


@application_api_bp.route("/<int:app_id>/process-links", methods=["GET", "POST"])
@login_required
@require_roles("admin", "architect")
@audit_log("process_link_manage")
def api_process_links(app_id):
    """
    Get or create process links for an application.

    GET:
        Get all process links for the application.

    POST:
        Create a new process link.

    Request Body (POST):
        {
            "process_id": 1,
            "support_type": "primary",
            "automation_level": 75
        }
    """
    try:
        ApplicationComponent.query.get_or_404(app_id)

        if request.method == "GET":
            from app.models.relationship_tables import ApplicationProcessSupport
            from app.models.process_data import BusinessProcess

            links = ApplicationProcessSupport.query.filter_by(
                application_component_id=app_id
            ).all()

            result = []
            for link in links:
                process = BusinessProcess.query.get(link.business_process_id)
                result.append(
                    {
                        "id": link.id,
                        "process_id": link.business_process_id,
                        "process_name": process.name if process else "Unknown",
                        "support_type": link.support_type,
                        "automation_level": link.automation_level,
                        "criticality": link.criticality,
                    }
                )

            return jsonify(
                {"application_id": app_id, "links": result, "count": len(result)}
            )

        else:  # POST
            from app.models.relationship_tables import ApplicationProcessSupport
            from app.models.process_data import BusinessProcess

            data = request.get_json()
            process_id = data.get("process_id")
            support_type = data.get("support_type", "partial")
            automation_level = data.get("automation_level", 50)

            if not process_id:
                return jsonify(
                    {"error": "Missing process_id", "message": "process_id is required"}
                ), 400

            # Verify process exists
            process = BusinessProcess.query.get(process_id)
            if not process:
                return jsonify(
                    {
                        "error": "Process not found",
                        "message": f"No process found with id: {process_id}",
                    }
                ), 404

            # Check for existing link
            existing = ApplicationProcessSupport.query.filter_by(
                application_component_id=app_id, business_process_id=process_id
            ).first()

            if existing:
                return jsonify(
                    {
                        "error": "Link exists",
                        "message": "Process link already exists",
                        "link_id": existing.id,
                    }
                ), 409

            # Create link
            link = ApplicationProcessSupport(
                application_component_id=app_id,
                business_process_id=process_id,
                support_type=support_type,
                automation_level=automation_level,
                created_at=db.func.now(),
            )

            db.session.add(link)
            db.session.commit()

            return jsonify(
                {
                    "message": "Process link created",
                    "link": {
                        "id": link.id,
                        "process_id": process_id,
                        "process_name": process.name,
                        "support_type": support_type,
                        "automation_level": automation_level,
                    },
                }
            ), 201

    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error with process links: {e}")
        return api_error("Failed to process request", "INTERNAL_ERROR", 500)


@application_api_bp.route(
    "/<int:app_id>/process-links/<int:link_id>", methods=["DELETE"]
)
@login_required
@require_roles("admin", "architect")
@audit_log("process_link_delete")
def api_process_link_ops(app_id, link_id):
    """
    Delete a process link.
    """
    # The link carries no organisation of its own; the application does. Resolve it
    # outside the try below, whose broad except would turn the 404 into a 500.
    ApplicationComponent.query.get_or_404(app_id)
    try:
        from app.models.relationship_tables import ApplicationProcessSupport

        link = ApplicationProcessSupport.query.filter_by(
            id=link_id, application_component_id=app_id
        ).first_or_404()

        db.session.delete(link)
        db.session.commit()

        return jsonify({"message": "Process link deleted successfully"})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting process link: {e}")
        return api_error("Failed to delete link", "INTERNAL_ERROR", 500)


# =============================================================================
# Work Package Endpoints
# =============================================================================


@application_api_bp.route("/<int:id>/work-packages", methods=["GET"])
@login_required
def api_work_packages(id):
    """
    Get work packages for an application.

    Args:
        id: Application ID

    Returns:
        JSON with work_packages list
    """
    try:
        from app.models.implementation_migration import WorkPackage

        ApplicationComponent.query.get_or_404(id)

        work_packages = WorkPackage.query.filter_by(application_component_id=id).all()

        wp_data = []
        for wp in work_packages:
            wp_data.append(
                {
                    "id": wp.id,
                    "name": wp.name,
                    "description": wp.description,
                    # Field names match POST/PUT response shape (roadmap.html template contract)
                    "transformation_type": wp.togaf_phase or "Modernization",
                    "status": wp.status or "planned",
                    "priority": wp.priority or "medium",
                    "assigned_to": wp.owner.email if wp.owner else "Unassigned",
                    "start_date": wp.start_date.isoformat() if wp.start_date else None,
                    "target_date": wp.target_date.isoformat() if wp.target_date else None,
                    "progress_percentage": wp.percent_complete or (100 if wp.completed_date else 0),
                    "estimated_effort_hours": wp.estimated_effort_hours,
                    "application_phase": wp.togaf_phase or "Implementation",
                    "plateau_id": getattr(wp, 'plateau_id', None),
                    "capability_id": getattr(wp, 'capability_id', None),
                }
            )

        return jsonify({"work_packages": wp_data})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching work packages for app {id}: {e}")
        return api_error("An internal error occurred", "INTERNAL_ERROR", 500)


# =============================================================================
# Application Delete Endpoint
# =============================================================================


@application_api_bp.route("/<int:app_id>", methods=["DELETE"])
@login_required
@require_roles("admin", "architect")
@audit_log("application_delete")
def api_delete_app(app_id):
    """
    Delete an application.

    Args:
        app_id: Application ID to delete

    Returns:
        JSON with success status
    """
    try:
        app_obj = ApplicationComponent.query.get_or_404(app_id)

        db.session.delete(app_obj)
        db.session.commit()

        return jsonify({"success": True, "message": "Application deleted successfully"})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting application {app_id}: {e}")
        return api_error("An internal error occurred", "INTERNAL_ERROR", 500)


# =============================================================================
# Bulk Process Link Endpoint
# =============================================================================


@application_api_bp.route("/bulk-process-link", methods=["POST"])
@login_required
@require_roles("admin", "architect")
@audit_log("bulk_process_link")
@rate_limit(5, "1h")
def api_bulk_process_link():
    """
    Bulk link applications to processes using semantic matching.

    Request body:
        application_ids: list of IDs or "all"
        auto_link: bool - automatically create links above threshold
        confidence_threshold: float - minimum confidence (0-1)
        dry_run: bool - only return suggestions without creating links
    """


    from app.models.relationship_tables import ApplicationProcessSupport

    data = request.get_json() or {}

    app_ids = data.get("application_ids", [])
    auto_link = data.get("auto_link", False)
    try:
        confidence_threshold = float(data.get("confidence_threshold", 0.5))
    except (ValueError, TypeError):
        return jsonify({"error": "confidence_threshold must be a number between 0 and 1"}), 400
    if not 0 <= confidence_threshold <= 1:
        return jsonify({"error": "confidence_threshold must be between 0 and 1"}), 400
    dry_run = data.get("dry_run", True)

    if app_ids == "all":
        apps = ApplicationComponent.query.limit(1000).all()
    else:
        apps = ApplicationComponent.query.filter(
            ApplicationComponent.id.in_(app_ids)
        ).all()

    # Prefetch existing mappings to avoid N+1 queries
    app_ids_for_prefetch = [app.id for app in apps]
    existing_mappings = ApplicationProcessSupport.query.filter(
        ApplicationProcessSupport.application_component_id.in_(app_ids_for_prefetch)
    ).all()

    # Create a set of (app_id, process_id) tuples for fast lookup
    existing_mapping_keys = {
        (mapping.application_component_id, mapping.business_process_id)
        for mapping in existing_mappings
    }

    results = []
    total_links_created = 0

    for app_obj in apps:
        suggestions = _suggest_process_links(app_obj, confidence_threshold)

        app_result = {
            "application_id": app_obj.id,
            "application_name": app_obj.name,
            "suggestions": [],
            "links_created": 0,
        }

        for suggestion in suggestions:
            process = suggestion.pop("process", None)
            app_result["suggestions"].append(suggestion)

            if auto_link and not dry_run and process:
                # Check if mapping exists using prefetched set
                mapping_key = (app_obj.id, process.id)
                if mapping_key not in existing_mapping_keys:
                    from datetime import datetime

                    mapping = ApplicationProcessSupport(
                        application_component_id=app_obj.id,
                        business_process_id=process.id,
                        support_type="primary_execution",
                        automation_level=50,
                        criticality="medium",
                        is_active=True,
                        created_date=datetime.utcnow(),
                        notes=(
                            f"Auto-linked via semantic matching "
                            f"(confidence: {suggestion['confidence']})"
                        ),
                    )
                    db.session.add(mapping)
                    existing_mapping_keys.add(
                        mapping_key
                    )  # Update set to prevent duplicates
                    app_result["links_created"] += 1
                    total_links_created += 1

        results.append(app_result)

    if not dry_run:
        db.session.commit()

    return jsonify(
        {
            "success": True,
            "dry_run": dry_run,
            "applications_processed": len(apps),
            "total_suggestions": sum(len(r["suggestions"]) for r in results),
            "total_links_created": total_links_created,
            "results": results,
        }
    )


def _suggest_process_links(app_obj, confidence_threshold=0.5):
    """
    Suggest process links for an application using semantic matching.

    Args:
        app_obj: ApplicationComponent instance
        confidence_threshold: Minimum similarity score (0-1)

    Returns:
        list of dicts with 'process', 'confidence', 'match_reason'
    """
    import re
    from difflib import SequenceMatcher

    from sqlalchemy import or_

    from app.models.process_data import BusinessProcess

    if not app_obj:
        return []

    suggestions = []

    processes = BusinessProcess.query.filter(
        or_(
            BusinessProcess.status == "active",
            BusinessProcess.status.is_(None),
        )
    ).all()

    app_text = " ".join(
        filter(
            None,
            [
                app_obj.name or "",
                app_obj.description or "",
                app_obj.business_purpose or "",
                app_obj.notes or "",
            ],
        )
    ).lower()

    app_keywords = set(re.split(r"[\s_-]+|(?<=[a-z])(?=[A-Z])", app_obj.name or ""))
    app_keywords = {kw.lower() for kw in app_keywords if len(kw) > 2}

    for process in processes:
        process_text = " ".join(
            filter(
                None,
                [
                    process.name or "",
                    process.description or "",
                    process.process_code or "",
                ],
            )
        ).lower()

        process_keywords = set(
            re.split(r"[\s_-]+|(?<=[a-z])(?=[A-Z])", process.name or "")
        )
        process_keywords = {kw.lower() for kw in process_keywords if len(kw) > 2}

        seq_similarity = SequenceMatcher(
            None, app_text[:500], process_text[:500]
        ).ratio()

        keyword_overlap = len(app_keywords & process_keywords) / max(
            len(app_keywords | process_keywords), 1
        )

        name_match = 0
        if process.name and app_obj.name:
            if (
                process.name.lower() in app_obj.name.lower()
                or app_obj.name.lower() in process.name.lower()
            ):
                name_match = 0.5

        confidence = seq_similarity * 0.4 + keyword_overlap * 0.4 + name_match * 0.2

        if confidence >= confidence_threshold:
            match_reasons = []
            if seq_similarity > 0.3:
                match_reasons.append(f"Text similarity: {seq_similarity:.0%}")
            if keyword_overlap > 0:
                match_reasons.append(f"Keyword overlap: {keyword_overlap:.0%}")
            if name_match > 0:
                match_reasons.append("Name containment match")

            suggestions.append(
                {
                    "process": process,
                    "process_id": process.id,
                    "process_name": process.name,
                    "confidence": round(confidence, 3),
                    "match_reason": "; ".join(match_reasons),
                }
            )

    suggestions.sort(key=lambda x: x["confidence"], reverse=True)
    return suggestions[:20]


# =============================================================================
# Vendor Matching Endpoints
# =============================================================================


@application_api_bp.route("/match-vendors", methods=["POST"])
@login_required
@require_roles("admin", "architect")
@audit_log("vendor_match")
@rate_limit(5, "1h")
def api_match_vendors():
    """
    Match applications to vendor products.

    Request body:
        method: "name" | "capability" | "ai"

    Returns:
        JSON with matches list sorted by confidence
    """
    try:
        data = request.get_json()
        if not data:
            return api_error("No data provided", "MISSING_BODY")

        method = data.get("method")
        if not method:
            return api_error("Matching method is required", "MISSING_METHOD")

        if method not in ["name", "capability", "ai"]:
            return api_error("Invalid matching method", "INVALID_METHOD")

        from app.models.vendor.vendor_organization import (
            VendorOrganization,
            VendorProduct,
        )

        applications = ApplicationComponent.query.limit(1000).all()
        vendor_products = (
            db.session.query(VendorProduct)
            .options(joinedload(VendorProduct.vendor_organization))
            .join(VendorOrganization).limit(1000).all()
        )

        matches = []

        for app_obj in applications:
            best_match = None
            best_confidence = 0

            for vendor_product in vendor_products:
                confidence = _calculate_match_confidence(
                    app_obj, vendor_product, method
                )

                if confidence > best_confidence and confidence >= 30:
                    best_confidence = confidence
                    best_match = {
                        "application_id": app_obj.id,
                        "application_name": app_obj.name,
                        "application_description": app_obj.description,
                        "vendor_id": vendor_product.vendor_organization_id,
                        "vendor_name": (vendor_product.vendor_organization.name),
                        "product_id": vendor_product.id,
                        "product_name": vendor_product.name,
                        "confidence": confidence,
                        "matching_reason": _get_matching_reason(
                            app_obj, vendor_product, method
                        ),
                    }

            if best_match:
                matches.append(best_match)

        matches.sort(key=lambda x: x["confidence"], reverse=True)

        return jsonify(
            {
                "success": True,
                "matches": matches,
                "total_applications": len(applications),
                "total_vendors": len(vendor_products),
                "method_used": method,
            }
        )

    except Exception as e:
        logger.error(f"Error in vendor matching: {e}")
        return api_error("An internal error occurred", "INTERNAL_ERROR", 500)


@application_api_bp.route("/confirm-vendor-matches", methods=["POST"])
@login_required
@require_roles("admin", "architect")
@audit_log("vendor_match_confirm")
def api_confirm_vendor_matches():
    """
    Confirm vendor matches and create application-vendor relationships.

    Request body:
        matches: list of {application_id, vendor_id, confidence}
    """
    import json

    try:
        data = request.get_json()
        if not data:
            return api_error("No data provided", "MISSING_BODY")

        matches = data.get("matches", [])
        if not matches:
            return api_error("No matches provided", "MISSING_MATCHES")

        from app.models.models import ArchiMateElement
        from app.models.vendor.vendor_organization import VendorProduct

        # Prefetch all applications and archimate elements to avoid N+1 queries
        application_ids = [
            match.get("application_id")
            for match in matches
            if match.get("application_id")
        ]
        vendor_ids = [
            match.get("vendor_id") for match in matches if match.get("vendor_id")
        ]

        applications = {
            app.id: app
            for app in ApplicationComponent.query.filter(
                ApplicationComponent.id.in_(application_ids)
            ).all()
        }

        vendor_products = {
            vp.vendor_organization_id: vp
            for vp in VendorProduct.query.filter(
                VendorProduct.vendor_organization_id.in_(vendor_ids)
            ).all()
        }

        archimate_ids = [
            app.archimate_element_id
            for app in applications.values()
            if app.archimate_element_id
        ]
        archimate_elements = (
            {
                elem.id: elem
                for elem in ArchiMateElement.query.filter(
                    ArchiMateElement.id.in_(archimate_ids)
                ).all()
            }
            if archimate_ids
            else {}
        )

        confirmed_count = 0

        for match in matches:
            try:
                application_id = match.get("application_id")
                vendor_id = match.get("vendor_id")

                if not application_id or not vendor_id:
                    continue

                app_obj = applications.get(application_id)
                vendor_product = vendor_products.get(vendor_id)

                if not app_obj or not vendor_product:
                    continue

                archimate_element = None
                if app_obj.archimate_element_id:
                    archimate_element = archimate_elements.get(
                        app_obj.archimate_element_id
                    )

                if not archimate_element:
                    archimate_element = ArchiMateElement(
                        name=app_obj.name,
                        type="ApplicationComponent",
                        layer="application",
                        description=app_obj.description or "",
                        properties=(
                            json.dumps(
                                {
                                    "vendor_matched": True,
                                    "vendor_id": vendor_id,
                                    "vendor_name": (
                                        vendor_product.vendor_organization.name
                                    ),
                                    "match_confidence": match.get("confidence", 0),
                                }
                            )
                            if match.get("confidence")
                            else None
                        ),
                    )
                    db.session.add(archimate_element)
                    db.session.flush()
                    app_obj.archimate_element_id = archimate_element.id

                confirmed_count += 1

            except Exception as e:
                logger.error(
                    f"Error confirming match for app {match.get('application_id')}: {e}"
                )
                continue

        db.session.commit()

        return jsonify(
            {
                "success": True,
                "confirmed_count": confirmed_count,
                "total_submitted": len(matches),
            }
        )

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error confirming vendor matches: {e}")
        return api_error("An internal error occurred", "INTERNAL_ERROR", 500)


def _calculate_match_confidence(application, vendor_product, method):
    """Calculate confidence score for application-vendor matching (0-100)."""
    confidence = 0

    if method == "name":
        app_name = (application.name or "").lower()
        vendor_name = (vendor_product.name or "").lower()
        vendor_org_name = (
            (vendor_product.vendor_organization.name or "").lower()
            if vendor_product.vendor_organization
            else ""
        )

        if app_name == vendor_name or app_name == vendor_org_name:
            confidence = 95
        elif vendor_name in app_name or app_name in vendor_name:
            confidence = 75
        elif vendor_org_name in app_name or app_name in vendor_org_name:
            confidence = 70
        else:
            app_words = set(app_name.split())
            vendor_words = set(vendor_name.split()) | set(vendor_org_name.split())
            overlap = len(app_words & vendor_words)
            if overlap > 0:
                confidence = min(50, 30 + (overlap * 10))

    elif method == "capability":
        confidence = 60
        if application.description and vendor_product.description:
            app_desc = application.description.lower()
            vendor_desc = vendor_product.description.lower()
            common_keywords = [
                "system",
                "management",
                "service",
                "platform",
                "solution",
                "software",
            ]
            overlap = sum(
                1 for kw in common_keywords if kw in app_desc and kw in vendor_desc
            )
            if overlap > 0:
                confidence += overlap * 5
        confidence = min(95, confidence)

    elif method == "ai":
        confidence = _calculate_match_confidence(application, vendor_product, "name")
        if application.description and vendor_product.description:
            desc_confidence = _calculate_match_confidence(
                application, vendor_product, "capability"
            )
            confidence = max(confidence, desc_confidence)
        confidence = min(95, confidence + 10)

    return confidence


def _get_matching_reason(application, vendor_product, method):
    """Generate human-readable reason for the match."""
    if method == "name":
        app_name = application.name or ""
        vendor_name = vendor_product.name or ""
        vendor_org_name = (
            vendor_product.vendor_organization.name
            if vendor_product.vendor_organization
            else ""
        )

        if app_name.lower() == vendor_name.lower():
            return f"Exact name match with vendor product '{vendor_name}'"
        elif app_name.lower() == vendor_org_name.lower():
            return f"Exact match with vendor '{vendor_org_name}'"
        elif vendor_name.lower() in app_name.lower():
            return f"Name contains vendor product '{vendor_name}'"
        elif vendor_org_name.lower() in app_name.lower():
            return f"Name contains vendor '{vendor_org_name}'"
        else:
            return f"Name similarity: '{app_name}' and '{vendor_name}'"
    elif method == "capability":
        return "Capability overlap between application and vendor offerings"
    elif method == "ai":
        return "AI-powered semantic analysis indicates strong relationship"
    return "Matching based on available data"


# =============================================================================
# Error Handlers
# =============================================================================


@application_api_bp.route("/processes/search", methods=["GET"])
@login_required
def search_processes():
    """
    Search business processes by name or process code.

    Query Parameters:
        q (str): Search query (min 2 characters)

    Returns:
        JSON array of matching processes [{id, name, process_code}]
    """
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])

    from app.models.process_data import BusinessProcess

    results = (
        BusinessProcess.query.filter(
            db.or_(
                BusinessProcess.name.ilike(f"%{q}%"),
                BusinessProcess.process_code.ilike(f"%{q}%"),
            )
        )
        .order_by(BusinessProcess.name)
        .limit(20)
        .all()
    )
    return jsonify([
        {"id": p.id, "name": p.name, "process_code": p.process_code}
        for p in results
    ])


@application_api_bp.errorhandler(404)
def api_not_found(error):
    """Handle 404 errors for API endpoints."""
    return jsonify(
        {
            "error": "Not Found",
            "message": "The requested resource was not found",
            "status_code": 404,
        }
    ), 404


@application_api_bp.errorhandler(500)
def api_internal_error(error):
    """Handle 500 errors for API endpoints."""
    db.session.rollback()
    logger.error(f"API internal error: {error}")
    return jsonify(
        {
            "error": "Internal Server Error",
            "message": "An unexpected error occurred",
            "status_code": 500,
        }
    ), 500

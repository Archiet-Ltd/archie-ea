"""
API v1 Dashboard Endpoints

Standardized dashboard API endpoints following PRD - 003.
"""

import logging

from flask import Blueprint, request
from flask_login import login_required
from sqlalchemy import inspect

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.business_capabilities import BusinessCapability
from app.models.vendor_organization import VendorOrganization
from app.utils.api_response import error_response, success_response
from app.utils.pagination import safe_int_arg

dashboard_bp = Blueprint("dashboard_v1", __name__)
logger = logging.getLogger(__name__)


@dashboard_bp.route("/applications/table-data", methods=["GET"])
@login_required
def get_applications_table_data():
    """
    Get applications table data for dashboard
    ---
    tags:
      - Dashboard
    summary: Get applications table data
    description: Returns paginated applications data for dashboard table display
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
      - name: sort_by
        in: query
        type: string
        default: name
        description: Sort column
      - name: sort_order
        in: query
        type: string
        default: asc
        description: Sort order (asc/desc)
    responses:
      200:
        description: Applications table data
    """
    try:
        # Defensive rollback for stale failed transaction state from prior requests.
        try:
            db.session.rollback()
        except Exception as rollback_error:
            logger.error(
                "Failed to reset SQLAlchemy session before get_applications_table_data: %s",
                rollback_error,
                exc_info=True,
            )

        page = max(safe_int_arg('page', 1, minimum=1), 1)
        per_page = min(max(safe_int_arg('per_page', 50, minimum=1, maximum=500), 1), 100)
        search = request.args.get("search", "", type=str)
        sort_by = request.args.get("sort_by", "name")
        sort_order = request.args.get("sort_order", "asc")

        # Guard against schema drift: only query columns that actually exist in DB.
        table_columns = {
            col["name"] for col in inspect(db.engine).get_columns(ApplicationComponent.__tablename__)
        }
        status_field = (
            "deployment_status"
            if "deployment_status" in table_columns
            else "lifecycle_status" if "lifecycle_status" in table_columns else None
        )

        selected_column_names = [
            "id",
            "name",
            "description",
            "business_owner",
            "technical_owner",
            "created_at",
            "updated_at",
        ]
        if status_field:
            selected_column_names.append(status_field)

        selected_columns = [getattr(ApplicationComponent, col) for col in selected_column_names]

        # ISS-022: Whitelist sortable columns to prevent timing attacks
        ALLOWED_SORT_COLUMNS = {
            "name",
            "description",
            "business_owner",
            "technical_owner",
            "created_at",
            "updated_at",
        }
        if status_field:
            ALLOWED_SORT_COLUMNS.add(status_field)
        if sort_by not in ALLOWED_SORT_COLUMNS:
            sort_by = "name"
        if sort_order not in ("asc", "desc"):
            sort_order = "asc"

        # Build query
        query = db.session.query(*selected_columns)

        # Apply search filter
        if search:
            query = query.filter(ApplicationComponent.name.ilike(f"%{search}%"))

        # Apply sorting
        sort_column = getattr(ApplicationComponent, sort_by, ApplicationComponent.name)
        if sort_order == "desc":
            query = query.order_by(sort_column.desc())
        else:
            query = query.order_by(sort_column.asc())

        # Paginate without relying on model-wide selects.
        total = query.count()
        items = query.offset((page - 1) * per_page).limit(per_page).all()
        pages = (total + per_page - 1) // per_page if total > 0 else 0

        applications = []
        for idx, row in enumerate(items):
            app = row._mapping
            applications.append(
                {
                    "id": str(app["id"]),
                    "row_number": (page - 1) * per_page + idx + 1,
                    "name": app["name"],
                    "description": app["description"] or "",
                    "status": app.get(status_field, "unknown") if status_field else "unknown",
                    "business_owner": app["business_owner"] or "",
                    "technical_owner": app["technical_owner"] or "",
                    "created_at": app["created_at"].isoformat() if app["created_at"] else None,
                    "updated_at": app["updated_at"].isoformat() if app["updated_at"] else None,
                }
            )

        return success_response(
            {
                "data": applications,
                "total": total,
                "page": page,
                "per_page": per_page,
                "applications": applications,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total,
                    "pages": pages,
                    "has_prev": page > 1,
                    "has_next": page < pages,
                },
            }
        )

    except Exception as e:
        logger.error(f"Applications table data error: {str(e)}", exc_info=True)
        
        # Attempt to rollback any failed transaction
        try:
            db.session.rollback()
        except Exception as rollback_error:
            logger.warning(
                "Rollback failed after applications table data error: %s",
                rollback_error,
                exc_info=True,
            )
        
        return error_response(
            message="Failed to retrieve applications table data",
            code="APPLICATIONS_TABLE_ERROR",
            details={"error": str(e) if hasattr(e, '__str__') else "Database error"},
            status_code=500,
        )


@dashboard_bp.route("/metrics", methods=["GET"])
@login_required
def get_dashboard_metrics():
    """
    Get dashboard metrics
    ---
    tags:
      - Dashboard
    summary: Get dashboard metrics
    description: Returns key metrics for dashboard display
    responses:
      200:
        description: Dashboard metrics
    """
    try:
        # Calculate metrics
        from app.services.application_kind import applications_query

        total_applications = applications_query().count()
        active_applications = applications_query().filter_by(deployment_status="production").count()

        total_capabilities = BusinessCapability.query.count()
        total_vendors = VendorOrganization.query.count()
        strategic_vendors = VendorOrganization.query.filter(
            VendorOrganization.strategic_tier == "tier_1_strategic"
        ).count()

        metrics = {
            "applications": {
                "total": total_applications,
                "active": active_applications,
                "inactive": total_applications - active_applications,
            },
            "capabilities": {
                "total": total_capabilities,
                "covered": 0,
                "gaps": 0,
            },
            "vendors": {
                "total": total_vendors,
                "strategic": strategic_vendors,
                "tactical": total_vendors - strategic_vendors,
            },
            "health": {
                "overall": "healthy",
                "applications": "healthy",
                "database": "healthy",
                "services": "healthy",
            },
        }

        return success_response(metrics)

    except Exception as e:
        logger.error(f"Dashboard metrics error: {str(e)}", exc_info=True)
        
        # Attempt to rollback any failed transaction
        try:
            from app import db
            db.session.rollback()
        except Exception as rollback_error:
            logger.warning(
                "Rollback failed after dashboard metrics error: %s",
                rollback_error,
                exc_info=True,
            )
        
        return error_response(
            message="Failed to retrieve dashboard metrics",
            code="DASHBOARD_METRICS_ERROR",
            details={"error": str(e) if hasattr(e, '__str__') else "Database error"},
            status_code=500,
        )


@dashboard_bp.route("/widgets", methods=["GET"])
@login_required
def get_dashboard_widgets():
    """
    Get dashboard widgets data
    ---
    tags:
      - Dashboard
    summary: Get dashboard widgets
    description: Returns data for all dashboard widgets
    responses:
      200:
        description: Dashboard widgets data
    """
    try:
        active_count = db.session.query(ApplicationComponent).filter(
            ApplicationComponent.lifecycle_status.in_(["active", "Active", "production", "Production"])
        ).count()
        inactive_count = db.session.query(ApplicationComponent).filter(
            ApplicationComponent.lifecycle_status.in_(["inactive", "Inactive", "retired", "Retired"])
        ).count()
        deprecated_count = db.session.query(ApplicationComponent).filter(
            ApplicationComponent.lifecycle_status.in_(["deprecated", "Deprecated", "end_of_life", "End of Life"])
        ).count()

        total_capabilities = db.session.query(BusinessCapability).count()
        total_vendors = db.session.query(VendorOrganization).count()

        db_healthy = True
        try:
            db.session.execute(db.text("SELECT 1"))  # tenant-exempt: health check
        except Exception:
            db_healthy = False

        widgets = {
            "application_status": {
                "type": "pie_chart",
                "data": {
                    "active": active_count,
                    "inactive": inactive_count,
                    "deprecated": deprecated_count,
                },
            },
            "capability_coverage": {
                "type": "progress_bar",
                "data": {
                    "total_capabilities": total_capabilities,
                    "total_vendors": total_vendors,
                },
            },
            "recent_activities": {"type": "list", "data": []},
            "system_health": {
                "type": "status_cards",
                "data": {
                    "database": "healthy" if db_healthy else "unhealthy",
                    "api": "healthy",
                    "services": "healthy",
                },
            },
        }

        return success_response(widgets)

    except Exception as e:
        logger.error("Failed to retrieve dashboard widgets: %s", e, exc_info=True)
        return error_response(
            message="Failed to retrieve dashboard widgets",
            code="DASHBOARD_WIDGETS_ERROR",
            details={"error": "See server logs for details"},
            status_code=500,
        )

"""
Implementation Planning Routes

CRUD operations for ArchiMate 3.2 Implementation & Migration Planning elements:
- WorkPackage management
- Deliverable tracking
- Gap analysis and management
- Plateau state management
- ImplementationEvent tracking

Complies with:
- ArchiMate 3.2 Specification
- RESTful API design principles
- Flask best practices
"""

from datetime import datetime  # dead-code-ok

from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_, text  # dead-code-ok
from sqlalchemy.orm import joinedload  # dead-code-ok

from .. import db  # dead-code-ok
from ..models.implementation_migration import (
    Deliverable,
    ImplementationEvent,
    Gap as ImplementationGap,
    Plateau as ImplementationPlateau,
    WorkPackage as ImplementationWorkPackage,
)
from ..models.unified_capability import UnifiedCapability
from ..services.gap_discovery_service import GapDiscoveryService
from . import implementation_planning
from app.utils.pagination import safe_int_arg


def _to_iso(value):
    return value.isoformat() if value else None


def _serialize_entity(entity):
    if hasattr(entity, "to_dict"):
        return entity.to_dict()
    if hasattr(entity, "to_roadmap_dict"):
        return entity.to_roadmap_dict()
    return {"id": getattr(entity, "id", None), "name": getattr(entity, "name", None)}


@implementation_planning.route("/")
@login_required
def implementation_dashboard():
    """
    Main implementation planning dashboard.
    """
    try:
        # Get statistics
        stats = {
            "work_packages": ImplementationWorkPackage.query.count(),
            "deliverables": Deliverable.query.count(),
            "gaps": ImplementationGap.query.count(),
            "plateaus": ImplementationPlateau.query.count(),
            "in_progress": ImplementationWorkPackage.query.filter_by(
                status="in_progress"
            ).count(),
            "completed": ImplementationWorkPackage.query.filter_by(
                status="completed"
            ).count(),
            "critical_gaps": ImplementationGap.query.filter_by(
                priority="critical"
            ).count(),
        }

        # Get recent work packages
        recent_work_packages = (
            ImplementationWorkPackage.query.order_by(
                ImplementationWorkPackage.updated_at.desc()
            )
            .limit(5)
            .all()
        )

        # Get critical gaps
        critical_gaps = (
            ImplementationGap.query.filter_by(priority="critical")
            .order_by(ImplementationGap.created_at.desc())
            .limit(5)
            .all()
        )

        # Get capability implementation summary
        # Maps Capability -> List of Work Packages via Application mapping
        capability_summary = []
        try:
            all_caps = UnifiedCapability.query.filter(
                UnifiedCapability.level <= 2
            ).all()
            for cap in all_caps:
                # Find applications mapped to this capability
                app_ids = [
                    m.application_id for m in cap.application_capability_mappings
                ]
                if app_ids:
                    # Find work packages linked to these applications
                    wps = ImplementationWorkPackage.query.filter(
                        ImplementationWorkPackage.application_component_id.in_(app_ids)
                    ).all()
                    if wps:
                        capability_summary.append(
                            {
                                "id": cap.id,
                                "name": cap.name,
                                "work_packages": [
                                    {"id": w.id, "name": w.name, "status": w.status}
                                    for w in wps
                                ],
                                "wp_count": len(wps),
                                "completed_count": len(
                                    [w for w in wps if w.status == "completed"]
                                ),
                            }
                        )
            # Sort by wp_count descending
            capability_summary.sort(key=lambda x: x["wp_count"], reverse=True)
            capability_summary = capability_summary[:10]  # Top 10 for dashboard
        except Exception as cap_e:
            current_app.logger.warning(f"Could not load capability summary: {cap_e}")
            capability_summary = []

        return render_template(
            "implementation_planning/dashboard.html",
            stats=stats,
            recent_work_packages=recent_work_packages,
            critical_gaps=critical_gaps,
            capability_summary=capability_summary,
        )

    except Exception:
        from app import db

        db.session.rollback()
        current_app.logger.exception("Error loading implementation planning dashboard")
        flash("Error loading dashboard. Please try again.", "error")
        return render_template(
            "implementation_planning/dashboard.html",
            stats=None,
            recent_work_packages=[],
            critical_gaps=[],
            load_error="Implementation planning data could not be read.",
        )


@implementation_planning.route("/work-packages")
@login_required
def work_packages_list():
    """
    List all work packages with filtering and search.
    """
    try:
        # Get query parameters
        page = safe_int_arg('page', 1, minimum=1)
        search = request.args.get("search", "")
        status = request.args.get("status", "")
        priority = request.args.get("priority", "")

        # Build query
        query = ImplementationWorkPackage.query

        if search:
            query = query.filter(
                or_(
                    ImplementationWorkPackage.name.ilike(f"%{search}%"),
                    ImplementationWorkPackage.description.ilike(f"%{search}%"),
                    ImplementationWorkPackage.assigned_to.ilike(f"%{search}%"),
                )
            )

        if status:
            query = query.filter(ImplementationWorkPackage.status == status)

        if priority:
            query = query.filter(ImplementationWorkPackage.priority == priority)

        # Paginate
        work_packages = query.order_by(
            ImplementationWorkPackage.created_at.desc()
        ).paginate(page=page, per_page=20, error_out=False)

        return render_template(
            "implementation_planning/dashboard.html",
            work_packages=work_packages,
            search=search,
            status=status,
            priority=priority,
        )

    except Exception:
        flash("Error loading work packages. Please try again.", "error")
        return redirect(url_for("implementation_planning.implementation_dashboard"))


@implementation_planning.route("/work-packages/<int:work_package_id>")
@login_required
def work_package_detail(work_package_id):
    """
    Show detailed view of a work package.
    """
    try:
        work_package = ImplementationWorkPackage.query.get_or_404(work_package_id)

        # Get related deliverables
        deliverables = Deliverable.query.filter_by(
            work_package_id=work_package_id
        ).all()

        # Get related implementation events
        events = ImplementationEvent.query.filter_by(
            work_package_id=work_package_id
        ).all()

        return render_template(
            "implementation_planning/dashboard.html",
            work_package=work_package,
            deliverables=deliverables,
            events=events,
        )

    except Exception:
        flash("Error loading work package. Please try again.", "error")
        return redirect(url_for("implementation_planning.work_packages_list"))


@implementation_planning.route("/work-packages/create", methods=["GET", "POST"])
@login_required
def create_work_package():
    """
    Create a new work package.
    """
    if request.method == "POST":
        try:
            data = request.get_json() if request.is_json else request.form.to_dict()

            # Validate required fields
            if not data.get("name"):
                return jsonify({"success": False, "errors": {"name": "Name is required"}}), 400

            # Create work package
            work_package = ImplementationWorkPackage(
                name=data["name"],
                description=data.get("description", ""),
                assigned_to=data.get("assigned_to", ""),
                priority=data.get("priority", "medium"),
                status=data.get("status", "planned"),
                start_date=datetime.strptime(data["start_date"], "%Y-%m-%d")
                if data.get("start_date")
                else None,
                end_date=datetime.strptime(data["end_date"], "%Y-%m-%d")
                if data.get("end_date")
                else None,
                estimated_cost=float(data.get("estimated_cost", 0)),
                progress_percentage=float(data.get("progress_percentage", 0)),
                properties=data.get("properties", {}),
                created_by=current_user.username,
            )

            # Calculate duration if dates are provided
            if work_package.start_date and work_package.end_date:
                work_package.calculate_duration()

            db.session.add(work_package)
            db.session.commit()

            if request.is_json:
                return jsonify({"success": True, "id": work_package.id, "name": work_package.name})

            flash("Work package created successfully!", "success")
            return redirect(
                url_for(
                    "implementation_planning.work_package_detail",
                    work_package_id=work_package.id,
                )
            )

        except Exception as e:
            db.session.rollback()
            if request.is_json:
                return jsonify({"success": False, "errors": {"general": str(e)}}), 500
            flash("Error creating work package. Please try again.", "error")

    # GET — redirect to dashboard (modal handles creation inline)
    return redirect(url_for("implementation_planning.implementation_dashboard"))


@implementation_planning.route(
    "/work-packages/<int:work_package_id>/edit", methods=["GET", "POST"]
)
@login_required
def edit_work_package(work_package_id):
    """
    Edit an existing work package.
    """
    work_package = ImplementationWorkPackage.query.get_or_404(work_package_id)

    if request.method == "POST":
        try:
            data = request.get_json() if request.is_json else request.form.to_dict()

            # Update work package
            work_package.name = data.get("name", work_package.name)
            work_package.description = data.get("description", work_package.description)
            work_package.assigned_to = data.get("assigned_to", work_package.assigned_to)
            work_package.priority = data.get("priority", work_package.priority)
            work_package.status = data.get("status", work_package.status)
            work_package.progress_percentage = float(
                data.get("progress_percentage", work_package.progress_percentage)
            )
            work_package.estimated_cost = float(
                data.get("estimated_cost", work_package.estimated_cost)
            )
            work_package.properties = data.get("properties", work_package.properties)

            # Update dates if provided
            if data.get("start_date"):
                work_package.start_date = datetime.strptime(
                    data["start_date"], "%Y-%m-%d"
                )

            if data.get("end_date"):
                work_package.end_date = datetime.strptime(data["end_date"], "%Y-%m-%d")

            # Recalculate duration
            if work_package.start_date and work_package.end_date:
                work_package.calculate_duration()

            db.session.commit()

            if request.is_json:
                return jsonify({"success": True})

            flash("Work package updated successfully!", "success")
            return redirect(
                url_for(
                    "implementation_planning.work_package_detail",
                    work_package_id=work_package.id,
                )
            )

        except Exception:
            db.session.rollback()
            if request.is_json:
                return jsonify({"error": "An internal error occurred"}), 500
            flash("Error updating work package. Please try again.", "error")

    # GET request - show form
    return render_template(
        "implementation_planning/work_package_form.html",
        work_package=work_package,
        action="Edit",
    )


@implementation_planning.route(
    "/work-packages/<int:work_package_id>/delete", methods=["POST"]
)
@login_required
def delete_work_package(work_package_id):
    """
    Delete a work package.
    """
    try:
        work_package = ImplementationWorkPackage.query.get_or_404(work_package_id)

        # Check for dependencies
        if work_package.child_work_packages:
            return (
                jsonify(
                    {"error": "Cannot delete work package with dependent work packages"}
                ),
                400,
            )

        # Delete NO-ACTION FK children with raw SQL before ORM delete to avoid
        # FK constraint violations (planning_deliverables has NO ACTION on delete)
        sp = db.session.begin_nested()
        try:
            db.session.execute(
                text("DELETE FROM planning_deliverables WHERE work_package_id = :wp_id"),
                {"wp_id": work_package_id},
            )
            sp.commit()
        except Exception:
            sp.rollback()
            raise

        db.session.delete(work_package)
        db.session.commit()

        if request.is_json:
            return jsonify({"success": True})

        flash("Work package deleted successfully!", "success")
        return redirect(url_for("implementation_planning.work_packages_list"))

    except Exception:
        db.session.rollback()
        if request.is_json:
            return jsonify({"error": "An internal error occurred"}), 500
        flash("Error deleting work package. Please try again.", "error")
        return redirect(url_for("implementation_planning.work_packages_list"))


# API Routes for Dashboard Stats
@implementation_planning.route("/api/dashboard-stats", methods=["GET"])
@login_required
def api_dashboard_stats():
    """
    API endpoint to get dashboard statistics.
    """
    try:
        stats = {
            "work_packages": ImplementationWorkPackage.query.count(),
            "deliverables": Deliverable.query.count(),
            "gaps": ImplementationGap.query.count(),
            "plateaus": ImplementationPlateau.query.count(),
            "in_progress": ImplementationWorkPackage.query.filter_by(
                status="in_progress"
            ).count(),
            "completed": ImplementationWorkPackage.query.filter_by(
                status="completed"
            ).count(),
            "critical_gaps": ImplementationGap.query.filter_by(
                priority="critical"
            ).count(),
        }
        return jsonify({"stats": stats})
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


# API Routes for Work Packages
@implementation_planning.route("/api/work-packages", methods=["GET"])
@login_required
def api_work_packages():
    """
    API endpoint to get work packages.
    Supports query parameters: limit, sort (field name), order (asc/desc).
    """
    try:
        query = ImplementationWorkPackage.query
        sort_field = request.args.get("sort", "created_at")
        order = request.args.get("order", "desc")
        # ISS-022: Whitelist sortable columns
        ALLOWED_SORT = {"name", "status", "priority", "created_at", "updated_at", "start_date", "end_date"}
        if sort_field not in ALLOWED_SORT:
            sort_field = "created_at"
        if order not in ("asc", "desc"):
            order = "desc"
        if hasattr(ImplementationWorkPackage, sort_field):
            col = getattr(ImplementationWorkPackage, sort_field)
            query = query.order_by(col.desc() if order == "desc" else col.asc())
        limit = safe_int_arg('limit', None, minimum=1, maximum=500)
        if limit:
            query = query.limit(limit)
        work_packages = query.all()
        return jsonify({"work_packages": [_serialize_entity(wp) for wp in work_packages]})
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@implementation_planning.route(
    "/api/work-packages/<int:work_package_id>", methods=["GET"]
)
@login_required
def api_work_package_detail(work_package_id):
    """
    API endpoint to get work package details.
    """
    try:
        work_package = ImplementationWorkPackage.query.get_or_404(work_package_id)
        return jsonify(_serialize_entity(work_package))
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@implementation_planning.route("/api/work-packages", methods=["POST"])
@login_required
def api_create_work_package():
    """
    API endpoint to create a new work package.
    """
    try:
        data = request.get_json()

        # Validate required fields
        if not data.get("name"):
            return jsonify({"error": "Name is required"}), 400

        # Create work package
        work_package = ImplementationWorkPackage(
            name=data["name"],
            description=data.get("description", ""),
            assigned_to=data.get("assigned_to", ""),
            priority=data.get("priority", "medium"),
            status=data.get("status", "planned"),
            start_date=datetime.strptime(data["start_date"], "%Y-%m-%d")
            if data.get("start_date")
            else None,
            end_date=datetime.strptime(data["end_date"], "%Y-%m-%d")
            if data.get("end_date")
            else None,
            estimated_cost=float(data.get("estimated_cost", 0)),
            progress_percentage=float(data.get("progress_percentage", 0)),
            properties=data.get("properties", {}),
            created_by=current_user.username,
        )

        # Calculate duration if dates are provided
        if work_package.start_date and work_package.end_date:
            work_package.calculate_duration()

        db.session.add(work_package)
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "work_package_id": work_package.id,
                "work_package": _serialize_entity(work_package),
            }
        )

    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500


@implementation_planning.route(
    "/api/work-packages/<int:work_package_id>", methods=["PUT"]
)
@login_required
def api_update_work_package(work_package_id):
    """
    API endpoint to update a work package.
    """
    try:
        work_package = ImplementationWorkPackage.query.get_or_404(work_package_id)
        data = request.get_json()

        # Update work package
        work_package.name = data.get("name", work_package.name)
        work_package.description = data.get("description", work_package.description)
        work_package.assigned_to = data.get("assigned_to", work_package.assigned_to)
        work_package.priority = data.get("priority", work_package.priority)
        work_package.status = data.get("status", work_package.status)
        work_package.progress_percentage = float(
            data.get("progress_percentage", work_package.progress_percentage)
        )
        work_package.estimated_cost = float(
            data.get("estimated_cost", work_package.estimated_cost)
        )
        work_package.properties = data.get("properties", work_package.properties)

        # Update dates if provided
        if data.get("start_date"):
            work_package.start_date = datetime.strptime(data["start_date"], "%Y-%m-%d")

        if data.get("end_date"):
            work_package.end_date = datetime.strptime(data["end_date"], "%Y-%m-%d")

        # Recalculate duration
        if work_package.start_date and work_package.end_date:
            work_package.calculate_duration()

        db.session.commit()

        return jsonify({"success": True, "work_package": _serialize_entity(work_package)})

    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500


@implementation_planning.route(
    "/api/work-packages/<int:work_package_id>", methods=["DELETE"]
)
@login_required
def api_delete_work_package(work_package_id):
    """
    API endpoint to delete a work package.
    """
    try:
        work_package = ImplementationWorkPackage.query.get_or_404(work_package_id)

        # Check for dependencies
        if work_package.child_work_packages:
            return (
                jsonify(
                    {"error": "Cannot delete work package with dependent work packages"}
                ),
                400,
            )

        # Delete NO-ACTION FK children with raw SQL before ORM delete to avoid
        # FK constraint violations (planning_deliverables has NO ACTION on delete)
        sp = db.session.begin_nested()
        try:
            db.session.execute(
                text("DELETE FROM planning_deliverables WHERE work_package_id = :wp_id"),
                {"wp_id": work_package_id},
            )
            sp.commit()
        except Exception:
            sp.rollback()
            raise

        db.session.delete(work_package)
        db.session.commit()

        return jsonify({"success": True})

    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500


# Gap Management Routes
@implementation_planning.route("/gaps")
@login_required
def gaps_list():
    """
    List all identified gaps with filtering and analysis.
    """
    try:
        # Get query parameters
        page = safe_int_arg('page', 1, minimum=1)
        search = request.args.get("search", "")
        gap_type = request.args.get("gap_type", "")
        priority = request.args.get("priority", "")
        status = request.args.get("status", "")

        # Build query
        query = ImplementationGap.query

        if search:
            query = query.filter(
                or_(
                    ImplementationGap.name.ilike(f"%{search}%"),
                    ImplementationGap.description.ilike(f"%{search}%"),
                )
            )

        if gap_type:
            query = query.filter(ImplementationGap.gap_type == gap_type)

        if priority:
            query = query.filter(ImplementationGap.priority == priority)

        if status:
            query = query.filter(ImplementationGap.resolution_status == status)

        # Order by priority and creation date
        query = query.order_by(
            ImplementationGap.priority.desc(), ImplementationGap.created_at.desc()
        )

        # Paginate
        gaps = query.paginate(page=page, per_page=20, error_out=False)

        # Get statistics
        stats = {
            "total": ImplementationGap.query.count(),
            "critical": ImplementationGap.query.filter_by(priority="critical").count(),
            "high": ImplementationGap.query.filter_by(priority="high").count(),
            "identified": ImplementationGap.query.filter_by(
                resolution_status="identified"
            ).count(),
            "in_progress": ImplementationGap.query.filter_by(
                resolution_status="in_progress"
            ).count(),
            "resolved": ImplementationGap.query.filter_by(resolution_status="resolved").count(),
        }

        return render_template(
            "implementation_planning/dashboard.html",
            gaps=gaps,
            stats=stats,
            search=search,
            gap_type=gap_type,
            priority=priority,
            status=status,
        )

    except Exception:
        flash("Error loading gaps. Please try again.", "error")
        return redirect(url_for("implementation_planning.implementation_dashboard"))


@implementation_planning.route("/gaps/discover", methods=["POST"])
@login_required
def discover_gaps():
    """
    Run intelligent gap discovery analysis and save the results to the gap
    register. Called by the dashboard's "Run Gap Discovery" button
    (Platform.fetch.post), so this responds with JSON -- it never had a
    plain HTML-form caller, and a redirect response makes fetch() try to
    parse the following page's HTML as JSON.
    """
    try:
        architecture_id = request.form.get("architecture_id", type=int)

        # Run gap discovery
        gap_service = GapDiscoveryService()
        gaps_data = gap_service.discover_all_gaps(architecture_id)

        # Save discovered gaps to the gap register, skipping any already there
        result = gap_service.save_discovered_gaps(
            gaps_data, architecture_id, current_user.organization_id
        )

        return jsonify({
            "success": True,
            "found": len(gaps_data["gaps"]),
            "saved": result["saved"],
            "duplicates": result["duplicates"],
            "failed": result["failed"],
        })

    except Exception:
        return jsonify({"success": False, "error": "Error during gap discovery. Please try again."}), 500


@implementation_planning.route("/gaps/<int:gap_id>")
@login_required
def gap_detail(gap_id):
    """
    Show detailed view of a gap.
    """
    try:
        gap = ImplementationGap.query.get_or_404(gap_id)

        # Get related work packages (Gap.work_packages: the gap_work_packages
        # association table, not a required_work_packages id list -- that
        # field belongs to the unrelated, similarly-named
        # app.models.implementation_planning.ImplementationGap)
        related_work_packages = list(gap.work_packages)

        return render_template(
            "implementation_planning/dashboard.html",
            gap=gap,
            related_work_packages=related_work_packages,
        )

    except Exception:
        flash("Error loading gap. Please try again.", "error")
        return redirect(url_for("implementation_planning.gaps_list"))


# API Routes for Gaps
@implementation_planning.route("/api/gaps", methods=["GET"])
@login_required
def api_gaps():
    """
    API endpoint to get gaps.
    Supports query parameters: priority (filter), limit.
    """
    try:
        query = ImplementationGap.query
        priority = request.args.get("priority")
        if priority:
            query = query.filter_by(priority=priority)
        query = query.order_by(ImplementationGap.created_at.desc())
        limit = safe_int_arg('limit', None, minimum=1, maximum=500)
        if limit:
            query = query.limit(limit)
        gaps = query.all()
        return jsonify({"gaps": [_serialize_entity(gap) for gap in gaps]})
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@implementation_planning.route("/api/gaps/discover", methods=["POST"])
@login_required
def api_discover_gaps():
    """
    API endpoint to run gap discovery.
    """
    try:
        data = request.get_json()
        architecture_id = data.get("architecture_id")

        # Run gap discovery
        gap_service = GapDiscoveryService()
        gaps_data = gap_service.discover_all_gaps(architecture_id)

        # Optionally save to the gap register
        save_to_db = data.get("save_to_db", False)
        if save_to_db:
            result = gap_service.save_discovered_gaps(
                gaps_data, architecture_id, current_user.organization_id
            )
            gaps_data["saved_count"] = result["saved"]
            gaps_data["duplicate_count"] = result["duplicates"]
            gaps_data["failed_count"] = result["failed"]

        return jsonify({"success": True, "gaps_data": gaps_data})

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


# Deliverable Management Routes
@implementation_planning.route("/deliverables")
@login_required
def deliverables_list():
    """
    List all deliverables with filtering.
    """
    try:
        # Get query parameters
        page = safe_int_arg('page', 1, minimum=1)
        search = request.args.get("search", "")
        status = request.args.get("status", "")
        work_package_id = request.args.get("work_package_id", type=int)

        # Build query
        query = Deliverable.query

        if search:
            query = query.filter(
                or_(
                    Deliverable.name.ilike(f"%{search}%"),
                    Deliverable.description.ilike(f"%{search}%"),
                )
            )

        if status:
            query = query.filter(Deliverable.status == status)

        if work_package_id:
            query = query.filter(Deliverable.work_package_id == work_package_id)

        # Paginate
        deliverables = query.order_by(Deliverable.created_at.desc()).paginate(
            page=page, per_page=20, error_out=False
        )

        return render_template(
            "implementation_planning/dashboard.html",
            deliverables=deliverables,
            search=search,
            status=status,
            work_package_id=work_package_id,
        )

    except Exception:
        flash("Error loading deliverables. Please try again.", "error")
        return redirect(url_for("implementation_planning.implementation_dashboard"))


# API Routes for Deliverables
@implementation_planning.route("/api/deliverables", methods=["GET"])
@login_required
def api_deliverables():
    """
    API endpoint to get all deliverables.
    """
    try:
        deliverables = Deliverable.query.all()
        return jsonify(
            {"deliverables": [deliverable.to_dict() for deliverable in deliverables]}
        )
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@implementation_planning.route("/api/deliverables", methods=["POST"])
@login_required
def api_create_deliverable():
    """
    API endpoint to create a new deliverable.
    """
    try:
        data = request.get_json()

        # Validate required fields
        if not data.get("name"):
            return jsonify({"error": "Name is required"}), 400

        # Create deliverable
        deliverable = Deliverable(
            name=data["name"],
            description=data.get("description", ""),
            deliverable_type=data.get("deliverable_type", ""),
            format=data.get("format", ""),
            status=data.get("status", "planned"),
            due_date=datetime.strptime(data["due_date"], "%Y-%m-%d")
            if data.get("due_date")
            else None,
            work_package_id=data.get("work_package_id"),
            properties=data.get("properties", {}),
            created_by=current_user.username,
        )

        db.session.add(deliverable)
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "deliverable_id": deliverable.id,
                "deliverable": deliverable.to_dict(),
            }
        )

    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500


# Plateau Management Routes
@implementation_planning.route("/plateaus")
@login_required
def plateaus_list():
    """
    List all architecture plateaus.
    """
    try:
        plateaus = ImplementationPlateau.query.order_by(
            ImplementationPlateau.start_date
        ).all()

        return render_template(
            "implementation_planning/plateaus.html", plateaus=plateaus
        )

    except Exception:
        flash("Error loading plateaus. Please try again.", "error")
        return redirect(url_for("implementation_planning.implementation_dashboard"))


# API Routes for Plateaus
@implementation_planning.route("/api/plateaus", methods=["GET"])
@login_required
def api_plateaus():
    """
    API endpoint to get all plateaus.
    """
    try:
        plateaus = ImplementationPlateau.query.all()
        return jsonify({"plateaus": [_serialize_entity(plateau) for plateau in plateaus]})
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@implementation_planning.route("/api/roadmap-data", methods=["GET"])
@login_required
def api_roadmap_data():
    """
    API endpoint for roadmap visualization data.
    Returns work packages, gaps, and plateaus formatted for the roadmap widget.
    """
    try:
        # Get all work packages with their related data
        work_packages = ImplementationWorkPackage.query.options(
            joinedload(ImplementationWorkPackage.application_component),
        ).all()

        # Get all gaps
        gaps = ImplementationGap.query.all()

        # Get all plateaus
        plateaus = ImplementationPlateau.query.order_by(
            ImplementationPlateau.sequence_order
        ).all()

        # Transform work packages to roadmap items
        items = []
        for wp in work_packages:
            wp_start = getattr(wp, "start_date", None)
            wp_end = getattr(wp, "target_date", None) or getattr(wp, "end_date", None)
            wp_progress = getattr(wp, "percent_complete", None)
            if wp_progress is None:
                wp_progress = getattr(wp, "progress_percentage", 0)
            wp_owner = getattr(wp, "assigned_to", None)
            if not wp_owner and getattr(wp, "owner", None):
                wp_owner = getattr(wp.owner, "username", None)
            item = {
                "id": wp.id,
                "type": "work_package",
                "name": wp.name,
                "description": wp.description or "",
                "start_date": _to_iso(wp_start),
                "end_date": _to_iso(wp_end),
                "status": wp.status or "not_started",
                "priority": wp.priority or "medium",
                "progress": wp_progress or 0,
                "assigned_to": wp_owner or "Unassigned",
                "domain_name": wp.application_component.domain
                if wp.application_component
                else "Architecture",
                "level": 1,
                "parent_id": getattr(wp, "parent_id", None)
                or getattr(wp, "parent_work_package_id", None),
                "gap_types": [],
                "work_packages": [],
            }
            items.append(item)

        # Transform gaps to roadmap items
        for gap in gaps:
            gap_start = (
                getattr(gap, "estimated_start_date", None)
                or getattr(gap, "identified_date", None)
                or getattr(gap, "created_at", None)
            )
            gap_end = (
                getattr(gap, "target_resolution_date", None)
                or getattr(gap, "resolved_at", None)
                or getattr(gap, "resolved_date", None)
            )
            gap_status = getattr(gap, "resolution_status", None) or getattr(gap, "status", None)
            item = {
                "id": f"gap_{gap.id}",
                "type": "gap",
                "name": gap.name,
                "description": gap.description or "",
                "start_date": _to_iso(gap_start),
                "end_date": _to_iso(gap_end),
                "status": gap_status or "identified",
                "priority": gap.priority or "medium",
                "progress": 0,
                "assigned_to": getattr(gap, "owner", None) or "Unassigned",
                "domain_name": "Architecture",
                "level": 1,
                "parent_id": None,
                "gap_types": [gap.gap_type] if gap.gap_type else [],
                "work_packages": [],
            }
            items.append(item)

        # Calculate statistics
        stats = {
            "total": len(items),
            "work_packages": len(work_packages),
            "gaps": len(gaps),
            "plateaus": len(plateaus),
            "critical": sum(1 for i in items if i["priority"] == "critical"),
            "high": sum(1 for i in items if i["priority"] == "high"),
            "in_progress": sum(1 for i in items if i["status"] == "in_progress"),
            "completed": sum(1 for i in items if i["status"] == "completed"),
        }

        return jsonify(
            {
                "success": True,
                "items": items,
                "plateaus": [_serialize_entity(p) for p in plateaus],
                "statistics": stats,
            }
        )

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@implementation_planning.route("/reports/generate")
@login_required
def generate_report():
    """
    Generate implementation planning report.
    Returns work packages, gaps, and deliverables summary.
    """
    try:
        # Get work packages
        work_packages = ImplementationWorkPackage.query.all()

        # Get gaps
        gaps = ImplementationGap.query.all()

        # Get deliverables
        deliverables = Deliverable.query.all()

        # Generate report data
        report_data = {
            "generated_at": datetime.now().isoformat(),
            "generated_by": current_user.full_name()
            if current_user.is_authenticated
            else "system",
            "summary": {
                "total_work_packages": len(work_packages),
                "total_gaps": len(gaps),
                "total_deliverables": len(deliverables),
                "work_packages_by_status": {},
                "work_packages_by_priority": {},
                "gaps_by_priority": {},
            },
            "work_packages": [wp.to_dict() for wp in work_packages],
            "gaps": [g.to_dict() for g in gaps],
            "deliverables": [d.to_dict() for d in deliverables],
        }

        # Calculate status distribution
        for wp in work_packages:
            status = wp.status or "planned"
            report_data["summary"]["work_packages_by_status"][status] = (
                report_data["summary"]["work_packages_by_status"].get(status, 0) + 1
            )
            priority = wp.priority or "medium"
            report_data["summary"]["work_packages_by_priority"][priority] = (
                report_data["summary"]["work_packages_by_priority"].get(priority, 0) + 1
            )

        # Calculate gaps by priority
        for gap in gaps:
            priority = gap.priority or "medium"
            report_data["summary"]["gaps_by_priority"][priority] = (
                report_data["summary"]["gaps_by_priority"].get(priority, 0) + 1
            )

        return jsonify({"success": True, "report": report_data})

    except Exception:
        current_app.logger.exception("Error generating implementation planning report")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@implementation_planning.route("/api/estimate-duration", methods=["POST"])
@login_required
def estimate_duration():
    """
    Get AI-predicted duration estimate for a work package.
    """
    try:
        from ..services.duration_estimator import DurationEstimator

        data = request.get_json() or {}

        estimator = DurationEstimator()
        prediction = estimator.estimate_duration(
            gap_type=data.get("gap_type"),
            priority=data.get("priority", "medium"),
            complexity=data.get("complexity", "medium"),
            has_dependencies=data.get("has_dependencies", False),
        )

        # Add suggested buffer
        prediction["suggested_buffer_hours"] = estimator.suggest_buffer(
            prediction["estimated_hours"], prediction["confidence"]
        )

        return jsonify({"success": True, "prediction": prediction})

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@implementation_planning.route("/api/estimation-accuracy")
@login_required
def estimation_accuracy():
    """
    Get accuracy metrics for past estimations.
    """
    try:
        from ..services.duration_estimator import DurationEstimator

        estimator = DurationEstimator()
        accuracy = estimator.get_estimation_accuracy()

        return jsonify({"success": True, "accuracy": accuracy})

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500

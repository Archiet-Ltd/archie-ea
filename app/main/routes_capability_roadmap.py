"""Enhanced Capability-Based Roadmap Routes - Option 1"""

from werkzeug.exceptions import HTTPException
import logging
import re
from datetime import datetime

from flask import abort, current_app, g, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app import db
from app.main.views import main
from app.models.roadmap import RoadmapTask
from app.models.implementation_migration import Deliverable
from app.models.unified_capability import BusinessDomain, UnifiedCapability
from app.models.unified_work_package import UnifiedWorkPackage
from app.services import work_package_service
from app.utils.tenant import current_organization_id
from app.utils.tenant_users import escape_like_literal


def _require_wp(wp_id):
    """This organisation's work package, else a 404 (another organisation's id
    is indistinguishable from a missing one)."""
    work_package = work_package_service.get_work_package(wp_id, current_organization_id())
    if work_package is None:
        abort(404)
    return work_package

logger = logging.getLogger(__name__)

# ============================================================================
# CAPABILITY MANAGEMENT API - Grouped Selection and User-Driven Addition
# ============================================================================


@main.route("/api/capabilities/grouped")
@login_required
def get_grouped_capabilities():
    """Get all capabilities grouped by level (L1, L2, L3) for dropdown selection"""
    try:
        # Get all capabilities ordered by level and name
        capabilities = UnifiedCapability.query.order_by(
            UnifiedCapability.level.asc(), UnifiedCapability.name.asc()
        ).all()

        # Group capabilities by level
        grouped = {"L1": [], "L2": [], "L3": []}

        for cap in capabilities:
            level_key = f"L{cap.level}" if cap.level in [1, 2, 3] else "L3"
            # Convert IDs to strings to prevent JavaScript precision loss with BigInt
            grouped[level_key].append(
                {
                    "id": str(cap.id),
                    "name": cap.name,
                    "description": cap.description,
                    "code": cap.code,
                    "level": cap.level,
                    "domain_id": str(cap.domain_id) if cap.domain_id else None,
                    "domain_name": cap.domain.name if cap.domain else None,
                    "strategic_importance": cap.strategic_importance,
                    "parent_capability_id": str(cap.parent_capability_id)
                    if cap.parent_capability_id
                    else None,
                }
            )

        return jsonify(
            {
                "success": True,
                "grouped_capabilities": grouped,
                "total_count": len(capabilities),
                "counts": {
                    "L1": len(grouped["L1"]),
                    "L2": len(grouped["L2"]),
                    "L3": len(grouped["L3"]),
                },
            }
        )

    except HTTPException:

        raise

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/capabilities/domains")
@login_required
def get_capability_domains():
    """Get all business domains for capability creation"""
    try:
        domains = BusinessDomain.query.order_by(BusinessDomain.name.asc()).all()

        domain_list = [
            {
                "id": d.id,
                "code": d.code,
                "name": d.name,
                "description": d.description,
                "domain_type": d.domain_type,
            }
            for d in domains
        ]

        return jsonify({"success": True, "domains": domain_list, "total_count": len(domain_list)})

    except HTTPException:

        raise

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/capabilities/check-duplicate")
@login_required
def check_capability_duplicate():
    """Check if a capability with the same name and level already exists"""
    try:
        name = request.args.get("name", "").strip()
        level = request.args.get("level", type=int)

        if not name or not level:
            return jsonify({"error": "Name and level are required"}), 400

        # Case-insensitive duplicate check
        existing = UnifiedCapability.query.filter(
            func.lower(func.trim(UnifiedCapability.name)) == name.lower(),
            UnifiedCapability.level == level,
        ).first()

        if existing:
            return jsonify(
                {
                    "success": True,
                    "is_duplicate": True,
                    "existing_capability": {
                        "id": str(existing.id),  # Convert to string for JavaScript BigInt safety
                        "name": existing.name,
                        "level": existing.level,
                        "description": existing.description,
                    },
                    "message": f'A capability named "{existing.name}" already exists at level L{level}',
                }
            )

        return jsonify({"success": True, "is_duplicate": False, "message": "No duplicate found"})

    except HTTPException:

        raise

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/users", methods=["GET"])
@login_required
def list_users_for_assignment():
    """List users for work package assignment dropdowns."""
    try:
        from app.models.user import User

        q = (request.args.get("q") or request.args.get("search") or "").strip()
        limit = request.args.get("limit", type=int)
        # An erased person (data-subject erasure clears the e-mail address) is not a
        # candidate for any assignment picker.
        query = User.query.filter(
            User.organization_id == g.current_org_id, User.email.isnot(None)
        )

        if q:
            escaped = escape_like_literal(q)
            like = f"%{escaped}%"
            query = query.filter(
                db.or_(
                    User.first_name.ilike(like, escape="\\"),
                    User.last_name.ilike(like, escape="\\"),
                    User.email.ilike(like, escape="\\"),
                )
            )

        query = query.order_by(User.first_name.asc(), User.last_name.asc(), User.email.asc())
        if limit is not None and limit > 0:
            query = query.limit(min(limit, 50))

        users = query.all()
        return jsonify(
            {
                "success": True,
                "users": [{"id": u.id, "name": u.full_name(), "email": u.email} for u in users],
                "total": len(users),
            }
        )
    except HTTPException:
        raise
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/capabilities", methods=["GET"])
@login_required
def list_capabilities():
    """List all capabilities (flat list for dropdowns)."""
    try:
        capabilities = UnifiedCapability.query.order_by(
            UnifiedCapability.level.asc(), UnifiedCapability.name.asc()
        ).all()
        return jsonify(
            {
                "success": True,
                "capabilities": [
                    {"id": c.id, "name": c.name, "code": c.code, "level": c.level}
                    for c in capabilities
                ],
                "total": len(capabilities),
            }
        )
    except HTTPException:
        raise
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/capabilities", methods=["POST"])
@login_required
def create_capability():
    """Create a new capability with duplicate prevention"""
    try:
        data = request.get_json()

        # Validate required fields
        name = data.get("name", "").strip()
        level = data.get("level")
        domain_id = data.get("domain_id")

        if not name:
            return jsonify({"error": "Capability name is required"}), 400

        if not level or level not in [1, 2, 3]:
            return jsonify({"error": "Valid level (1, 2, or 3) is required"}), 400

        # Check for duplicates (case-insensitive)
        existing = UnifiedCapability.query.filter(
            func.lower(func.trim(UnifiedCapability.name)) == name.lower(),
            UnifiedCapability.level == level,
        ).first()

        if existing:
            return (
                jsonify(
                    {
                        "error": f'A capability named "{existing.name}" already exists at level L{level}',
                        "is_duplicate": True,
                        "existing_capability": {
                            "id": str(
                                existing.id
                            ),  # Convert to string for JavaScript BigInt safety
                            "name": existing.name,
                            "level": existing.level,
                        },
                    }
                ),
                409,
            )  # Conflict

        # Generate a unique code if not provided
        code = data.get("code")
        if not code:
            # Generate code from name: sanitize and format
            sanitized_name = re.sub(r"[^a-zA-Z0 - 9\s]", "", name)
            code_base = "-".join(sanitized_name.upper().split()[:3])
            code = f"CAP-L{level}-{code_base}"

            # Ensure uniqueness
            counter = 1
            original_code = code
            while UnifiedCapability.query.filter_by(code=code).first():
                code = f"{original_code}-{counter}"
                counter += 1

        # Get default domain if not provided
        if not domain_id:
            # Try to get a default domain or first available
            default_domain = BusinessDomain.query.first()
            if default_domain:
                domain_id = default_domain.id
            else:
                return (
                    jsonify(
                        {"error": "No business domains available. Please create a domain first."}
                    ),
                    400,
                )

        # Handle parent_capability_id - convert to int and validate
        parent_capability_id = data.get("parent_capability_id")
        if (
            parent_capability_id is not None
            and parent_capability_id != ""
            and parent_capability_id != "null"
        ):
            try:
                parent_capability_id = int(parent_capability_id)
                # Verify parent exists
                parent_cap = UnifiedCapability.query.get(parent_capability_id)
                if not parent_cap:
                    return (
                        jsonify(
                            {"error": f"Parent capability with ID {parent_capability_id} not found"}
                        ),
                        400,
                    )
                # Validate hierarchy: L2 can only have L1 parent, L3 can have L1 or L2 parent
                if level == 2 and parent_cap.level != 1:
                    return (
                        jsonify(
                            {"error": "L2 capabilities can only have L1 capabilities as parents"}
                        ),
                        400,
                    )
                if level == 3 and parent_cap.level not in [1, 2]:
                    return (
                        jsonify(
                            {
                                "error": "L3 capabilities can only have L1 or L2 capabilities as parents"
                            }
                        ),
                        400,
                    )
            except (ValueError, TypeError):
                return jsonify({"error": "Invalid parent_capability_id format"}), 400
        else:
            parent_capability_id = None

        # Create the new capability
        new_capability = UnifiedCapability(
            name=name,
            description=data.get("description", "").strip(),
            code=code,
            level=level,
            domain_id=domain_id,
            category=data.get("category", "supporting"),
            capability_type=data.get("capability_type", "operational"),
            strategic_importance=data.get("strategic_importance", "medium"),
            status="defined",
            parent_capability_id=parent_capability_id,
            discovered_by_ai=False,
        )

        db.session.add(new_capability)
        db.session.commit()

        # Get domain name directly to avoid lazy loading issues
        domain_name = None
        if domain_id:
            domain = BusinessDomain.query.get(domain_id)
            domain_name = domain.name if domain else None

        return (
            jsonify(
                {
                    "success": True,
                    "capability": {
                        "id": str(
                            new_capability.id
                        ),  # Convert to string for JavaScript BigInt safety
                        "name": new_capability.name,
                        "description": new_capability.description,
                        "code": new_capability.code,
                        "level": new_capability.level,
                        "domain_id": str(new_capability.domain_id)
                        if new_capability.domain_id
                        else None,
                        "domain_name": domain_name,
                        "strategic_importance": new_capability.strategic_importance,
                        "parent_capability_id": str(new_capability.parent_capability_id)
                        if new_capability.parent_capability_id
                        else None,
                    },
                    "message": f'Capability "{name}" created successfully at level L{level}',
                }
            ),
            201,
        )

    except HTTPException:

        raise

    except Exception as e:
        db.session.rollback()
        import traceback

        error_traceback = traceback.format_exc()
        current_app.logger.error(f"[CREATE_CAPABILITY ERROR] {str(e)}")
        current_app.logger.error(f"[CREATE_CAPABILITY TRACEBACK]\n{error_traceback}")
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/capability-roadmap")
@login_required
def capability_roadmap():
    """Enterprise Capability Roadmap — ArchiMate 3.2 Implementation & Migration.

    Most data is loaded client-side via the API endpoints below.
    Server-side: capability_count, domain_count for stat cards (FAR-011 fix).
    """
    capability_count = UnifiedCapability.query.count()
    domain_count = BusinessDomain.query.count()
    return render_template(
        "capability_roadmap/capability_roadmap.html",
        capability_count=capability_count,
        domain_count=domain_count,
    )


@main.route("/hybrid-roadmap")
@login_required
def hybrid_roadmap_redirect():
    """Consolidated into /capability-roadmap (Feb 2026)."""
    return redirect(url_for("main.capability_roadmap"))


@main.route("/technology-roadmap")
@login_required
def technology_roadmap_redirect():
    """Consolidated into /capability-roadmap (Feb 2026)."""
    return redirect(url_for("main.capability_roadmap"))




@main.route("/api/capability-work-packages")
@login_required
def get_capability_work_packages():
    """API endpoint for capability work packages with level filtering"""
    try:
        # Get filter parameters
        selected_levels = request.args.getlist("levels") or ["L1", "L2", "L3"]
        request.args.get("domain", "")
        request.args.get("importance", "")

        # Convert level strings to integers
        ([
            int(level[1])
            for level in selected_levels
            if level.startswith("L") and level[1:].isdigit()
        ])

        # Use ORM without backend filtering (let frontend handle filtering)
        work_packages_query = UnifiedWorkPackage.query.filter(
            UnifiedWorkPackage.layer.in_(
                ["implementation", "business", "application", "technology"]
            )
        )

        # Get all work packages without filtering - frontend will handle filtering
        all_work_packages = work_packages_query.order_by(
            UnifiedWorkPackage.business_capability.asc(), UnifiedWorkPackage.start_date.asc()
        ).all()

        # OPTIMIZATION: Pre-fetch capabilities into a dictionary to avoid N + 1 queries
        capability_names = list(
            set(wp.business_capability for wp in all_work_packages if wp.business_capability)
        )
        capabilities_dict = {}
        if capability_names:
            from sqlalchemy.orm import joinedload

            from app.models.unified_application_capability_mapping import (
                UnifiedApplicationCapabilityMapping,
            )

            caps = (
                UnifiedCapability.query.filter(UnifiedCapability.name.in_(capability_names))
                .options(
                    joinedload(UnifiedCapability.application_capability_mappings).joinedload(
                        UnifiedApplicationCapabilityMapping.application
                    )
                )
                .all()
            )
            capabilities_dict = {cap.name: cap for cap in caps}

        # OPTIMIZATION: Pre-fetch task and deliverable counts using subqueries
        wp_ids = [wp.id for wp in all_work_packages]
        task_counts = {}
        deliverable_counts = {}

        if wp_ids:
            # Get task counts in a single query
            task_count_query = (
                db.session.query(
                    RoadmapTask.unified_work_package_id, func.count(RoadmapTask.id).label("count")
                )
                .filter(RoadmapTask.unified_work_package_id.in_(wp_ids))
                .group_by(RoadmapTask.unified_work_package_id)
                .all()
            )

            task_counts = {row[0]: row[1] for row in task_count_query}

            # Get deliverable counts in a single query
            deliverable_count_query = (
                db.session.query(
                    Deliverable.unified_work_package_id,
                    func.count(Deliverable.id).label("count"),
                )
                .filter(Deliverable.unified_work_package_id.in_(wp_ids))
                .group_by(Deliverable.unified_work_package_id)
                .all()
            )

            deliverable_counts = {row[0]: row[1] for row in deliverable_count_query}

        work_packages_list = []
        for wp in all_work_packages:
            # Look up capability from pre-fetched dictionary (O(1) instead of DB query)
            capability = capabilities_dict.get(wp.business_capability)

            # Look up counts from pre-fetched dictionaries (O(1) instead of DB query)
            task_count = task_counts.get(wp.id, 0)
            deliverable_count = deliverable_counts.get(wp.id, 0)

            # Determine display name prioritizing capability (as per user request)
            capability_display_name = wp.business_capability
            if (
                wp.capability_names
                and isinstance(wp.capability_names, list)
                and len(wp.capability_names) > 0
            ):
                capability_display_name = ", ".join(wp.capability_names)

            if not capability_display_name:
                capability_display_name = wp.name

            # Get vendor names for this capability
            vendors = []
            if capability:
                # Optimized: Look up mapped applications and their vendors
                vendors = list(
                    set(
                        [
                            m.application.vendor_name
                            for m in capability.application_capability_mappings
                            if m.application and m.application.vendor_name
                        ]
                    )
                )

            work_packages_list.append(
                {
                    "id": str(wp.id),  # String for JavaScript BigInt safety
                    "name": capability_display_name,
                    "wp_name": wp.name,
                    "description": wp.description or "",
                    "business_capability": wp.business_capability,
                    "capability_name": wp.business_capability,
                    "capability_id": str(capability.id)
                    if capability
                    else None,  # For backward compat
                    "capability_ids": wp.capability_ids,  # Multi-capability support
                    "capability_names": wp.capability_names,  # Multi-capability support
                    "capability_level": f"L{capability.level}" if capability else "Unknown",
                    "capability_level_int": capability.level if capability else 0,
                    "domain_name": "Unknown",  # Default since domains table doesn't exist
                    "domain_code": "UNK",  # Default since domains table doesn't exist
                    "vendors": vendors,
                    "vendors_count": len(vendors),
                    "strategic_importance": capability.strategic_importance
                    if capability
                    else "medium",
                    "assigned_to": wp.assigned_to or "Unassigned",
                    "status": wp.status,
                    "start_date": wp.start_date.isoformat() if wp.start_date else None,
                    "end_date": wp.end_date.isoformat() if wp.end_date else None,
                    "progress_percentage": wp.progress_percentage or 0,
                    "estimated_cost": wp.estimated_cost or 0,
                    "priority": wp.priority,
                    "risk_level": wp.risk_level,
                    "layer": wp.layer,
                    "element_type": wp.element_type,
                    "task_count": task_count,
                    "deliverable_count": deliverable_count,
                }
            )

        return jsonify({"work_packages": work_packages_list})
    except HTTPException:
        raise
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/capability-work-packages", methods=["POST"])
@login_required
def create_capability_work_package():
    """Create new capability work package with multi-capability support"""
    try:
        data = request.get_json()

        # Validate required fields
        required_fields = ["name", "start_date", "end_date"]
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400

        # Validate at least one capability is selected (either via business_capability or capability_ids)
        capability_ids = data.get("capability_ids", [])
        capability_names = data.get("capability_names", [])
        business_capability = data.get("business_capability", "")

        if not business_capability and not capability_names:
            return jsonify({"error": "At least one capability must be selected"}), 400

        # Use first capability name if business_capability not provided (backward compatibility)
        if not business_capability and capability_names:
            business_capability = capability_names[0]

        # Create new work package
        # Create through the one writer
        new_wp = work_package_service.create_work_package(
            organization_id=current_organization_id(),
            user_id=current_user.id,
            name=data["name"],
            description=data.get("description", ""),
            business_capability=business_capability,
            capability_ids=capability_ids if capability_ids else None,
            capability_names=capability_names if capability_names else None,
            assigned_to=data.get("assigned_to", "Unassigned"),
            status=data.get("status", "planned"),
            start_date=data["start_date"],
            end_date=data["end_date"],
            progress_percentage=data.get("progress_percentage", 0),
            estimated_cost=data.get("estimated_cost", 0),
            priority=data.get("priority", "medium"),
            risk_level=data.get("risk_level", "medium"),
            layer="implementation",  # Default layer for roadmap work packages
        )
        db.session.commit()

        # Return the created work package with string ID
        return jsonify(
            {
                "success": True,
                "work_package": {
                    "id": str(new_wp.id),
                    "name": new_wp.name,
                    "description": new_wp.description,
                    "business_capability": new_wp.business_capability,
                    "capability_ids": new_wp.capability_ids,
                    "capability_names": new_wp.capability_names,
                    "assigned_to": new_wp.assigned_to,
                    "status": new_wp.status,
                    "start_date": new_wp.start_date.isoformat(),
                    "end_date": new_wp.end_date.isoformat(),
                    "progress_percentage": new_wp.progress_percentage,
                    "estimated_cost": new_wp.estimated_cost,
                    "priority": new_wp.priority,
                    "risk_level": new_wp.risk_level,
                },
            }
        )

    except HTTPException:

        raise

    except work_package_service.WorkPackageNotFound:
        db.session.rollback()
        return jsonify({"error": "Work package not found"}), 404

    except work_package_service.WorkPackageError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400

    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/capability-work-packages/<int:wp_id>", methods=["PUT"])
@login_required
def update_capability_work_package(wp_id):
    """Update capability work package"""
    try:
        data = request.get_json()

        # Update through the one writer
        fields = {
            key: data[key]
            for key in (
                "name", "description", "business_capability", "capability_ids",
                "capability_names", "assigned_to", "status", "start_date", "end_date",
                "progress_percentage", "estimated_cost", "priority", "risk_level",
            )
            if key in data
        }
        for key in ("capability_ids", "capability_names"):
            if key in fields and not fields[key]:
                fields[key] = None
        work_package = work_package_service.update_work_package(
            wp_id,
            organization_id=current_organization_id(),
            user_id=current_user.id,
            **fields,
        )
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "work_package": {
                    "id": str(work_package.id),
                    "name": work_package.name,
                    "description": work_package.description,
                    "business_capability": work_package.business_capability,
                    "capability_ids": work_package.capability_ids,
                    "capability_names": work_package.capability_names,
                    "assigned_to": work_package.assigned_to,
                    "status": work_package.status,
                    "start_date": work_package.start_date.isoformat()
                    if work_package.start_date
                    else None,
                    "end_date": work_package.end_date.isoformat()
                    if work_package.end_date
                    else None,
                    "progress_percentage": work_package.progress_percentage,
                    "estimated_cost": work_package.estimated_cost,
                    "priority": work_package.priority,
                    "risk_level": work_package.risk_level,
                },
            }
        )

    except HTTPException:

        raise

    except work_package_service.WorkPackageNotFound:
        db.session.rollback()
        return jsonify({"error": "Work package not found"}), 404

    except work_package_service.WorkPackageError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400

    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/capability-work-packages/<int:wp_id>", methods=["DELETE"])
@login_required
def delete_capability_work_package(wp_id):
    """Delete capability work package"""
    try:
        work_package_service.delete_work_package(
            wp_id, organization_id=current_organization_id()
        )
        db.session.commit()

        return jsonify({"success": True, "message": f"Work package {wp_id} deleted"})

    except HTTPException:

        raise

    except work_package_service.WorkPackageNotFound:
        db.session.rollback()
        return jsonify({"error": "Work package not found"}), 404

    except work_package_service.WorkPackageError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400

    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500


# ============================================================================
# TASK API ENDPOINTS - ArchiMate 3.2 Implementation Event aligned
# ============================================================================


@main.route("/api/capability-work-packages/<int:wp_id>/tasks")
@login_required
def get_work_package_tasks(wp_id):
    """Get all tasks for a work package"""
    try:
        # Verify work package exists
        work_package = _require_wp(wp_id)

        # Get tasks for this work package
        tasks = (
            RoadmapTask.query.filter_by(unified_work_package_id=wp_id)
            .order_by(RoadmapTask.start_date.asc(), RoadmapTask.priority.desc())
            .all()
        )

        return jsonify(
            {
                "success": True,
                "work_package_id": wp_id,
                "work_package_name": work_package.name,
                "tasks": [task.to_dict() for task in tasks],
                "total_tasks": len(tasks),
            }
        )

    except HTTPException:

        raise

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/capability-work-packages/<int:wp_id>/tasks", methods=["POST"])
@login_required
def create_work_package_task(wp_id):
    """Create a new task for a work package"""
    try:
        # Verify work package exists
        _require_wp(wp_id)

        data = request.get_json()

        # Validate required fields
        if "title" not in data:
            return jsonify({"error": "Missing required field: title"}), 400

        # Create new task
        new_task = RoadmapTask(
            title=data["title"],
            description=data.get("description", ""),
            unified_work_package_id=wp_id,
            start_date=datetime.fromisoformat(data["start_date"]).date()
            if data.get("start_date")
            else None,
            end_date=datetime.fromisoformat(data["end_date"]).date()
            if data.get("end_date")
            else None,
            status=data.get("status", "planned"),
            capability_level=data.get("capability_level", "L3"),
            priority=data.get("priority", "medium"),
            assigned_to=data.get("assigned_to"),
            estimated_hours=data.get("estimated_hours", 0.0),
            percent_complete=data.get("percent_complete", 0),
            archimate_element_type="ImplementationEvent",
            created_by=current_user.id,
        )

        db.session.add(new_task)
        db.session.commit()

        return jsonify({"success": True, "task": new_task.to_dict()}), 201

    except HTTPException:

        raise

    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/capability-work-packages/<int:wp_id>/tasks/<int:task_id>", methods=["PUT"])
@login_required
def update_work_package_task(wp_id, task_id):
    """Update a task"""
    try:
        # Verify work package and task exist
        _require_wp(wp_id)
        task = RoadmapTask.query.filter_by(id=task_id, unified_work_package_id=wp_id).first_or_404()

        data = request.get_json()

        # Update fields
        if "title" in data:
            task.title = data["title"]
        if "description" in data:
            task.description = data["description"]
        if "start_date" in data:
            task.start_date = (
                datetime.fromisoformat(data["start_date"]).date() if data["start_date"] else None
            )
        if "end_date" in data:
            task.end_date = (
                datetime.fromisoformat(data["end_date"]).date() if data["end_date"] else None
            )
        if "status" in data:
            task.status = data["status"]
        if "capability_level" in data:
            task.capability_level = data["capability_level"]
        if "priority" in data:
            task.priority = data["priority"]
        if "assigned_to" in data:
            task.assigned_to = data["assigned_to"]
        if "estimated_hours" in data:
            task.estimated_hours = data["estimated_hours"]
        if "actual_hours" in data:
            task.actual_hours = data["actual_hours"]
        if "percent_complete" in data:
            task.percent_complete = data["percent_complete"]

        task.updated_by = current_user.id
        db.session.commit()

        return jsonify({"success": True, "task": task.to_dict()})

    except HTTPException:

        raise

    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/capability-work-packages/<int:wp_id>/tasks/<int:task_id>", methods=["DELETE"])
@login_required
def delete_work_package_task(wp_id, task_id):
    """Delete a task"""
    try:
        # Verify work package and task exist
        _require_wp(wp_id)
        task = RoadmapTask.query.filter_by(id=task_id, unified_work_package_id=wp_id).first_or_404()

        db.session.delete(task)
        db.session.commit()

        return jsonify({"success": True, "message": f"Task {task_id} deleted"})

    except HTTPException:

        raise

    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500


# ============================================================================
# DELIVERABLE API ENDPOINTS - ArchiMate 3.2 Deliverable aligned
# ============================================================================


@main.route("/api/capability-work-packages/<int:wp_id>/deliverables")
@login_required
def get_work_package_deliverables(wp_id):
    """Get all deliverables for a work package"""
    try:
        # Verify work package exists
        work_package = _require_wp(wp_id)

        # Get deliverables for this work package (the one deliverable store)
        deliverables = (
            work_package_service.deliverables_query(current_organization_id(), wp_id)
            .order_by(Deliverable.target_date.asc())
            .all()
        )

        return jsonify(
            {
                "success": True,
                "work_package_id": wp_id,
                "work_package_name": work_package.name,
                "deliverables": [work_package_service.deliverable_to_roadmap_dict(d) for d in deliverables],
                "total_deliverables": len(deliverables),
            }
        )

    except HTTPException:

        raise

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/capability-work-packages/<int:wp_id>/deliverables", methods=["POST"])
@login_required
def create_work_package_deliverable(wp_id):
    """Create a new deliverable for a work package"""
    try:
        # Verify work package exists
        _require_wp(wp_id)

        data = request.get_json()

        # Validate required fields
        if "name" not in data:
            return jsonify({"error": "Missing required field: name"}), 400

        # Create new deliverable
        fields = work_package_service.roadmap_deliverable_fields(data)
        fields.setdefault("description", "")
        fields.setdefault("delivery_status", "planned")
        try:
            new_deliverable = work_package_service.create_deliverable(
                wp_id, organization_id=current_organization_id(), **fields
            )
        except work_package_service.WorkPackageNotFound:
            abort(404)
        except work_package_service.WorkPackageError as exc:
            db.session.rollback()
            return jsonify({"error": str(exc)}), 400
        db.session.commit()

        return jsonify({
            "success": True,
            "deliverable": work_package_service.deliverable_to_roadmap_dict(new_deliverable),
        }), 201

    except HTTPException:

        raise

    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500


@main.route(
    "/api/capability-work-packages/<int:wp_id>/deliverables/<int:deliverable_id>", methods=["PUT"]
)
@login_required
def update_work_package_deliverable(wp_id, deliverable_id):
    """Update a deliverable"""
    try:
        # Verify work package and deliverable exist
        _require_wp(wp_id)
        data = request.get_json()
        try:
            deliverable = work_package_service.update_deliverable(
                wp_id, deliverable_id, organization_id=current_organization_id(),
                **work_package_service.roadmap_deliverable_fields(data),
            )
        except work_package_service.WorkPackageNotFound:
            abort(404)
        except work_package_service.WorkPackageError as exc:
            db.session.rollback()
            return jsonify({"error": str(exc)}), 400
        db.session.commit()

        return jsonify({
            "success": True,
            "deliverable": work_package_service.deliverable_to_roadmap_dict(deliverable),
        })

    except HTTPException:

        raise

    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500


@main.route(
    "/api/capability-work-packages/<int:wp_id>/deliverables/<int:deliverable_id>",
    methods=["DELETE"],
)
@login_required
def delete_work_package_deliverable(wp_id, deliverable_id):
    """Delete a deliverable"""
    try:
        # Verify work package and deliverable exist
        _require_wp(wp_id)
        try:
            work_package_service.delete_deliverable(
                wp_id, deliverable_id, organization_id=current_organization_id()
            )
        except work_package_service.WorkPackageNotFound:
            abort(404)
        db.session.commit()

        return jsonify({"success": True, "message": f"Deliverable {deliverable_id} deleted"})

    except HTTPException:

        raise

    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500


# ============================================================================
# WORK PACKAGE DETAILS ENDPOINT (includes tasks and deliverables)
# ============================================================================


@main.route("/api/capability-work-packages/<int:wp_id>/details")
@login_required
def get_work_package_details(wp_id):
    """Get complete work package details including tasks and deliverables"""
    try:
        # Get work package
        work_package = _require_wp(wp_id)

        # Get associated capability
        capability = UnifiedCapability.query.filter_by(
            name=work_package.business_capability
        ).first()

        # Get tasks
        tasks = (
            RoadmapTask.query.filter_by(unified_work_package_id=wp_id)
            .order_by(RoadmapTask.start_date.asc())
            .all()
        )

        # Get deliverables
        deliverables = (
            work_package_service.deliverables_query(current_organization_id(), wp_id)
            .order_by(Deliverable.target_date.asc())
            .all()
        )

        # Calculate task statistics
        total_tasks = len(tasks)
        completed_tasks = len([t for t in tasks if t.status == "completed"])
        in_progress_tasks = len([t for t in tasks if t.status == "in_progress"])
        total_estimated_hours = sum(t.estimated_hours or 0 for t in tasks)
        total_actual_hours = sum(t.actual_hours or 0 for t in tasks)

        # Calculate deliverable statistics
        total_deliverables = len(deliverables)
        delivered_count = len([d for d in deliverables if d.delivery_status == "delivered"])
        approved_count = len([d for d in deliverables if d.approval_status == "approved"])

        return jsonify(
            {
                "success": True,
                "work_package": {
                    "id": str(work_package.id) if work_package.id else None,
                    "name": ", ".join(work_package.capability_names)
                    if work_package.capability_names
                    else work_package.business_capability,
                    "wp_name": work_package.name,
                    "description": work_package.description,
                    "business_capability": work_package.business_capability,
                    "capability_ids": work_package.capability_ids,
                    "capability_names": work_package.capability_names,
                    "capability_level": f"L{capability.level}" if capability else "Unknown",
                    "strategic_importance": capability.strategic_importance
                    if capability
                    else "medium",
                    "assigned_to": work_package.assigned_to,
                    "status": work_package.status,
                    "start_date": work_package.start_date.isoformat()
                    if work_package.start_date
                    else None,
                    "end_date": work_package.end_date.isoformat()
                    if work_package.end_date
                    else None,
                    "progress_percentage": work_package.progress_percentage or 0,
                    "estimated_cost": work_package.estimated_cost or 0,
                    "actual_cost": work_package.actual_cost or 0,
                    "priority": work_package.priority,
                    "risk_level": work_package.risk_level,
                    "layer": work_package.layer,
                    "element_type": work_package.element_type,
                    "created_at": work_package.created_at.isoformat()
                    if work_package.created_at
                    else None,
                    "updated_at": work_package.updated_at.isoformat()
                    if work_package.updated_at
                    else None,
                },
                "tasks": [task.to_dict() for task in tasks],
                "deliverables": [work_package_service.deliverable_to_roadmap_dict(d) for d in deliverables],
                "statistics": {
                    "total_tasks": total_tasks,
                    "completed_tasks": completed_tasks,
                    "in_progress_tasks": in_progress_tasks,
                    "task_completion_rate": round((completed_tasks / total_tasks * 100), 1)
                    if total_tasks > 0
                    else 0,
                    "total_estimated_hours": total_estimated_hours,
                    "total_actual_hours": total_actual_hours,
                    "total_deliverables": total_deliverables,
                    "delivered_count": delivered_count,
                    "approved_count": approved_count,
                    "deliverable_completion_rate": round(
                        (delivered_count / total_deliverables * 100), 1
                    )
                    if total_deliverables > 0
                    else 0,
                },
            }
        )

    except HTTPException:

        raise

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500

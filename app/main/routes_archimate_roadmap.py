"""ArchiMate 3.2 Implementation & Migration Roadmap Routes - Option 2"""

import logging
from datetime import datetime  # dead-code-ok

from flask import flash, g, jsonify, render_template, request  # dead-code-ok
from flask_login import current_user, login_required
from sqlalchemy import text  # dead-code-ok

from app import db

# Import main blueprint from views to avoid circular import
from app.main.views import main
from app.models.archimate_core import ArchiMateElement
from app.models.implementation_migration import Gap, Plateau  # dead-code-ok
from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
from app.models.unified_capability import UnifiedCapability  # dead-code-ok
from app.models.unified_work_package import UnifiedWorkPackage
from app.services import work_package_service
from app.utils.tenant import current_organization_id

logger = logging.getLogger(__name__)


@main.route("/archimate-roadmap")
@login_required
def archimate_roadmap():
    """Enhanced ArchiMate 3.2 Implementation & Migration roadmap"""

    try:
        # Get filter parameters with defaults
        selected_levels = request.args.getlist("levels") or ["L1", "L2", "L3"]
        selected_domain = request.args.get("domain", "")
        selected_importance = request.args.get("importance", "")

        # Convert level strings to integers
        level_ints = [
            int(level[1])
            for level in selected_levels
            if level.startswith("L") and level[1:].isdigit()
        ]

        # Get capabilities for dropdown with hierarchy and level filtering
        capabilities_query = UnifiedCapability.query
        if level_ints:
            capabilities_query = capabilities_query.filter(UnifiedCapability.level.in_(level_ints))
        # Remove domain filtering for now since business_domains table doesn't exist
        capabilities = capabilities_query.order_by(
            UnifiedCapability.level, UnifiedCapability.name
        ).all()

        # Get users for dropdown using ORM
        _org_id = getattr(g, 'current_org_id', None)
        _user_org_filter = "WHERE u.organization_id = :org_id" if _org_id else ""
        users = db.session.execute(  # tenant-filtered
            text(
                f"""
            SELECT u.id, u.first_name, u.last_name, u.email,
                   r.name as role_name
            FROM users u
            LEFT JOIN roles r ON u.role_id = r.id
            {_user_org_filter}
            ORDER BY u.first_name, u.last_name
        """
            ),
            {"org_id": _org_id} if _org_id else {},
        ).fetchall()

        # Get unmapped capabilities (no applications mapped) with level filtering
        unmapped_capabilities = []
        try:
            # Get all capability IDs that have mappings
            mapped_capability_ids = (
                db.session.query(UnifiedApplicationCapabilityMapping.unified_capability_id)
                .distinct()
                .all()
            )
            mapped_ids = [item[0] for item in mapped_capability_ids]

            # Get capabilities that are not mapped
            unmapped_query = UnifiedCapability.query
            if level_ints:
                unmapped_query = unmapped_query.filter(UnifiedCapability.level.in_(level_ints))
            if mapped_ids:
                unmapped_query = unmapped_query.filter(~UnifiedCapability.id.in_(mapped_ids))

            unmapped_capabilities = unmapped_query.order_by(
                UnifiedCapability.strategic_importance.desc(), UnifiedCapability.name
            ).all()
        except Exception as e:
            logger.warning("Could not load unmapped capabilities: %s", e)
            unmapped_capabilities = []

        # Get mapping statistics
        total_capabilities = len(capabilities)
        mapped_capabilities = total_capabilities - len(unmapped_capabilities)
        mapping_coverage = (
            round((mapped_capabilities / total_capabilities * 100), 1)
            if total_capabilities > 0
            else 0
        )

        # Generate timeline (6 years from current year)
        current_year = datetime.now().year
        start_date = datetime(current_year, 1, 1)
        end_date = datetime(current_year + 5, 12, 31)
        months = []
        current = start_date
        while current <= end_date:
            months.append(current.strftime("%b %Y"))
            if current.month == 12:
                current = datetime(current.year + 1, 1, 1)
            else:
                current = datetime(current.year, current.month + 1, 1)

        # Get work packages from database without backend filtering (let frontend handle filtering)
        work_packages_query = UnifiedWorkPackage.query.filter(
            UnifiedWorkPackage.layer.in_(
                ["implementation", "business", "application", "technology"]
            )
        )

        # Get all work packages without filtering - frontend will handle filtering
        all_work_packages = work_packages_query.order_by(
            UnifiedWorkPackage.start_date.asc(), UnifiedWorkPackage.priority.desc()
        ).all()

        # Batch-prefetch capabilities and elements to avoid N+1 queries
        cap_names = {wp.business_capability for wp in all_work_packages if wp.business_capability}
        capabilities_by_name = {
            c.name: c for c in UnifiedCapability.query.filter(UnifiedCapability.name.in_(cap_names)).all()
        } if cap_names else {}

        element_ids = {wp.archimate_element_id for wp in all_work_packages if wp.archimate_element_id}
        elements_by_id = {
            e.id: e for e in ArchiMateElement.query.filter(ArchiMateElement.id.in_(element_ids)).all()
        } if element_ids else {}

        # Convert to list of dicts for template with capability level information
        work_packages_list = []
        for wp in all_work_packages:
            capability = capabilities_by_name.get(wp.business_capability)

            # Get linked element capabilities
            element_capabilities = []
            if wp.archimate_element_id:
                element = elements_by_id.get(wp.archimate_element_id)
                if element:
                    element_capabilities = [c.name for c in element.unified_capabilities]

            work_packages_list.append(
                {
                    "id": wp.id,
                    "name": wp.name,
                    "description": wp.description or "",
                    "capability_id": wp.business_capability,
                    "capability_name": wp.business_capability,
                    "element_capabilities": element_capabilities,
                    "capability_level": f"L{capability.level}" if capability else "Unknown",
                    "capability_level_int": capability.level if capability else 0,
                    "domain_name": "Unknown",  # Default since domains table doesn't exist
                    "domain_code": "UNK",  # Default since domains table doesn't exist
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
                }
            )

        # Query gap and plateau data from the database
        gaps_summary = {"total": 0, "by_severity": {}, "open_gaps": []}
        plateaus_list = []
        try:
            # D3: exclude interface-register plateau-transition gaps -- this
            # is the capability roadmap's gap summary, not the interface
            # register's.
            open_gaps = (
                Gap.query.filter(
                    Gap.resolution_status.in_(["identified", "analyzed", "planned", "in_progress"]),
                    Gap.gap_kind != "plateau_transition",
                )
                .order_by(Gap.severity.asc(), Gap.priority.asc())
                .all()
            )
            severity_counts = {}
            for gap in open_gaps:
                sev = gap.severity or "medium"
                severity_counts[sev] = severity_counts.get(sev, 0) + 1
            gaps_summary = {
                "total": len(open_gaps),
                "by_severity": severity_counts,
                "open_gaps": [
                    {
                        "id": gap.id,
                        "name": gap.name,
                        "gap_type": gap.gap_type or "unclassified",
                        "severity": gap.severity or "medium",
                        "priority": gap.priority or "medium",
                        "resolution_status": gap.resolution_status or "identified",
                    }
                    for gap in open_gaps[:20]
                ],
            }
            _plateaus = Plateau.query.order_by(Plateau.sequence_order.asc()).all()
            # The count is of this organisation's work packages that realise the
            # plateau, from the one store (relationships), not the older list.
            _plateau_wps = work_package_service.plateau_work_package_ids(
                [p.id for p in _plateaus], current_organization_id()
            )
            plateaus_list = [
                {
                    "id": p.id,
                    "name": p.name,
                    "description": p.description or "",
                    "sequence_order": p.sequence_order or 0,
                    "target_date": p.target_date.isoformat() if p.target_date else None,
                    "work_package_count": len(_plateau_wps.get(p.id, ())),
                }
                for p in _plateaus
            ]
        except Exception as e:
            logger.debug("Gap/Plateau tables may not exist yet: %s", e)

        return render_template(
            "archimate_roadmap/enhanced_roadmap_fixed.html",
            domains=[],  # Empty list since business_domains table doesn't exist
            capabilities=capabilities,
            users=users,
            unmapped_capabilities=unmapped_capabilities,
            total_capabilities=total_capabilities,
            mapped_capabilities=mapped_capabilities,
            mapping_coverage=mapping_coverage,
            work_packages=work_packages_list,
            start_date=start_date,
            end_date=end_date,
            months=months,
            gaps_summary=gaps_summary,
            plateaus=plateaus_list,
            selected_levels=selected_levels or ["L1", "L2", "L3"],  # Ensure always defined
            selected_domain=selected_domain or "",  # Ensure always defined
            selected_importance=selected_importance or "",  # Ensure always defined
        )

    except Exception as e:
        db.session.rollback()
        logger.error("Error loading ArchiMate roadmap: %s", e, exc_info=True)
        flash("Error loading ArchiMate roadmap. Please try again.", "error")
        # The timeline axis is a display range, not a measurement, so it stays.
        # Every counted figure becomes None: "0 gaps" here would read as an
        # all-clear produced by a database error.
        current_year = datetime.now().year
        start_date = datetime(current_year, 1, 1)
        end_date = datetime(current_year + 5, 12, 31)
        return render_template(
            "archimate_roadmap/enhanced_roadmap_fixed.html",
            domains=[],
            capabilities=[],
            users=[],
            work_packages=[],
            months=[],
            unmapped_capabilities=[],
            total_capabilities=None,
            mapped_capabilities=None,
            mapping_coverage=None,
            start_date=start_date,
            end_date=end_date,
            gaps_summary=None,
            plateaus=[],
            selected_levels=["L1", "L2", "L3"],  # Ensure always defined
            selected_domain="",  # Ensure always defined
            selected_importance="",  # Ensure always defined
            load_error="The ArchiMate roadmap could not be read.",
        )


@main.route("/api/archimate-work-packages", methods=["GET"])
@login_required
def get_archimate_work_packages():
    """API endpoint for ArchiMate work packages with level filtering"""
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
            UnifiedWorkPackage.start_date.asc(), UnifiedWorkPackage.priority.desc()
        ).all()

        # Batch-prefetch capabilities and elements to avoid N+1 queries
        api_cap_names = {wp.business_capability for wp in all_work_packages if wp.business_capability}
        api_caps_by_name = {
            c.name: c for c in UnifiedCapability.query.filter(UnifiedCapability.name.in_(api_cap_names)).all()
        } if api_cap_names else {}

        api_element_ids = {wp.archimate_element_id for wp in all_work_packages if wp.archimate_element_id}
        api_elements_by_id = {
            e.id: e for e in ArchiMateElement.query.filter(ArchiMateElement.id.in_(api_element_ids)).all()
        } if api_element_ids else {}

        work_packages_list = []
        for wp in all_work_packages:
            capability = api_caps_by_name.get(wp.business_capability)

            # Get linked element capabilities
            element_capabilities = []
            if wp.archimate_element_id:
                element = api_elements_by_id.get(wp.archimate_element_id)
                if element:
                    element_capabilities = [c.name for c in element.unified_capabilities]

            work_packages_list.append(
                {
                    "id": wp.id,
                    "name": wp.name,
                    "description": wp.description or "",
                    "business_capability": wp.business_capability,
                    "capability_name": wp.business_capability,
                    "element_capabilities": element_capabilities,
                    "capability_level": f"L{capability.level}" if capability else "Unknown",
                    "capability_level_int": capability.level if capability else 0,
                    "domain_name": "Unknown",  # Default since domains table doesn't exist
                    "domain_code": "UNK",  # Default since domains table doesn't exist
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
                }
            )

        return jsonify({"work_packages": work_packages_list})
    except Exception as e:
        logger.error("Error getting ArchiMate work packages: %s", e, exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/archimate-work-packages", methods=["POST"])
@login_required
def create_archimate_work_package():
    """Create new ArchiMate work package"""
    try:
        data = request.get_json()

        # Validate required fields
        required_fields = ["name", "business_capability", "start_date", "end_date"]
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400

        # Create new work package
        # Create through the one writer
        new_wp = work_package_service.create_work_package(
            organization_id=current_organization_id(),
            user_id=current_user.id,
            name=data["name"],
            description=data.get("description", ""),
            business_capability=data["business_capability"],
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

        # Return the created work package
        return jsonify(
            {
                "success": True,
                "work_package": {
                    "id": new_wp.id,
                    "name": new_wp.name,
                    "description": new_wp.description,
                    "business_capability": new_wp.business_capability,
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

    except work_package_service.WorkPackageNotFound:
        db.session.rollback()
        return jsonify({"error": "Work package not found"}), 404

    except work_package_service.WorkPackageError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400

    except Exception as e:
        db.session.rollback()
        logger.error("Error creating ArchiMate work package: %s", e, exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/archimate-work-packages/<int:wp_id>", methods=["PUT"])
@login_required
def update_archimate_work_package(wp_id):
    """Update ArchiMate work package"""
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
                    "id": work_package.id,
                    "name": work_package.name,
                    "description": work_package.description,
                    "business_capability": work_package.business_capability,
                    "assigned_to": work_package.assigned_to,
                    "status": work_package.status,
                    "start_date": work_package.start_date.isoformat(),
                    "end_date": work_package.end_date.isoformat(),
                    "progress_percentage": work_package.progress_percentage,
                    "estimated_cost": work_package.estimated_cost,
                    "priority": work_package.priority,
                    "risk_level": work_package.risk_level,
                },
            }
        )

    except work_package_service.WorkPackageNotFound:
        db.session.rollback()
        return jsonify({"error": "Work package not found"}), 404

    except work_package_service.WorkPackageError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400

    except Exception as e:
        db.session.rollback()
        logger.error("Error updating ArchiMate work package: %s", e, exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/archimate-work-packages/<int:wp_id>", methods=["DELETE"])
@login_required
def delete_archimate_work_package(wp_id):
    """Delete ArchiMate work package"""
    try:
        work_package_service.delete_work_package(
            wp_id, organization_id=current_organization_id()
        )
        db.session.commit()

        return jsonify({"success": True, "message": f"ArchiMate work package {wp_id} deleted"})

    except work_package_service.WorkPackageNotFound:
        db.session.rollback()
        return jsonify({"error": "Work package not found"}), 404

    except work_package_service.WorkPackageError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400

    except Exception as e:
        db.session.rollback()
        logger.error("Error deleting ArchiMate work package %d: %s", wp_id, e, exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500

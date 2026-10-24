"""Strategic Roadmap Routes - Strategic Planning & Vision"""

from datetime import datetime  # dead-code-ok

from flask import current_app, flash, g, jsonify, render_template, request  # dead-code-ok
from flask_login import current_user, login_required
from sqlalchemy import text  # dead-code-ok

from app import db
from app.modules.genome.services.roadmap_links import redirect_to_roadmap
from app.main.views import main
from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
from app.models.unified_capability import UnifiedCapability  # dead-code-ok
from app.models.unified_work_package import UnifiedWorkPackage
from app.services import work_package_service
from app.utils.tenant import current_organization_id


@main.route("/strategic/roadmap")
@login_required
def strategic_roadmap():
    """The strategic roadmap is the one roadmap, Strategic view."""
    return redirect_to_roadmap("strategic")


@main.route("/api/strategic-work-packages")
@login_required
def get_strategic_work_packages():
    """API endpoint for strategic work packages with level filtering"""
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

        work_packages_list = []
        for wp in all_work_packages:
            # Find the capability for this work package
            capability = UnifiedCapability.query.filter_by(name=wp.business_capability).first()

            work_packages_list.append(
                {
                    "id": wp.id,
                    "name": wp.name,
                    "description": wp.description or "",
                    "business_capability": wp.business_capability,
                    "capability_name": wp.business_capability,
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
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/strategic-work-packages", methods=["POST"])
@login_required
def create_strategic_work_package():
    """Create new strategic work package"""
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

    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/strategic-work-packages/<int:wp_id>", methods=["PUT"])
@login_required
def update_strategic_work_package(wp_id):
    """Update strategic work package"""
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

    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/strategic-work-packages/<int:wp_id>", methods=["DELETE"])
@login_required
def delete_strategic_work_package(wp_id):
    """Delete strategic work package"""
    try:
        work_package_service.delete_work_package(
            wp_id, organization_id=current_organization_id()
        )
        db.session.commit()

        return jsonify({"success": True, "message": f"Strategic work package {wp_id} deleted"})

    except work_package_service.WorkPackageNotFound:
        db.session.rollback()
        return jsonify({"error": "Work package not found"}), 404

    except work_package_service.WorkPackageError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400

    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500

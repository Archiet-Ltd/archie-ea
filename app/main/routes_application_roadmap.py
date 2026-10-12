"""Application-Specific Roadmap Routes - Application Transformation Roadmaps"""

from app.services.archimate_backbone import sync_archimate_element
from datetime import datetime

from flask import flash, g, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db

# Import blueprint after other imports to avoid circular import
from app.main.views import main
from app.models.application_layer import ApplicationComponent
from app.models.archimate_core import ArchiMateElement
from app.models.implementation_migration import Gap, Plateau, WorkPackage
from app.models.unified_work_package import UnifiedWorkPackage


@main.route("/applications/<int:id>/roadmap")
@login_required
def application_roadmap(id):
    """Application-specific transformation roadmap"""

    # Let get_or_404 raise naturally — don't swallow 404s
    application = ApplicationComponent.query.get_or_404(id)

    try:
        # Get work packages for this application
        work_packages = (
            WorkPackage.query.filter(WorkPackage.application_component_id == id)
            .order_by(WorkPackage.start_date.asc(), WorkPackage.priority.desc())
            .all()
        )

        # Get gaps related to this application
        gaps = (
            Gap.query.filter(Gap.application_component_id == id)
            .order_by(Gap.priority.desc(), Gap.created_at.desc())
            .all()
        )

        # Get plateaus for this application
        plateaus = (
            Plateau.query.filter(Plateau.application_component_id == id)
            .order_by(Plateau.sequence_order.asc())
            .all()
        )

        # RDM-021: Get ArchiMate elements for Target Architecture linking
        target_arch_elements = (
            ArchiMateElement.query.filter(
                ArchiMateElement.layer.in_(
                    ["Implementation_and_Migration", "Application", "Technology"]
                )
            )
            .order_by(ArchiMateElement.name.asc())
            .limit(200)
            .all()
        )

        # Generate timeline (2 years for application transformation)
        start_date = datetime(2024, 1, 1)
        end_date = datetime(2025, 12, 31)
        months = []
        current = start_date
        while current <= end_date:
            months.append(current.strftime("%b %Y"))
            if current.month == 12:
                current = datetime(current.year + 1, 1, 1)
            else:
                current = datetime(current.year, current.month + 1, 1)

        # Convert work packages to application-specific format
        work_packages_list = []
        for wp in work_packages:
            archimate_element = None
            if wp.archimate_element_id:
                archimate_element = ArchiMateElement.query.get(wp.archimate_element_id)

            work_packages_list.append(
                {
                    "id": wp.id,
                    "name": wp.name,
                    "description": wp.description or "",
                    "transformation_type": wp.element_type or "Modernization",
                    "assigned_to": wp.owner.email if wp.owner else "Unassigned",
                    "status": wp.status,
                    "start_date": wp.start_date.isoformat() if wp.start_date else None,
                    "target_date": wp.target_date.isoformat()
                    if wp.target_date
                    else None,
                    "progress_percentage": 100
                    if wp.completed_date
                    else (
                        0
                        if not wp.start_date or wp.start_date > datetime.now().date()
                        else max(
                            0,
                            min(
                                100,
                                (
                                    (datetime.now().date() - wp.start_date).days
                                    * 100.0
                                    / max(1, (wp.target_date - wp.start_date).days)
                                ),
                            ),
                        )
                    ),
                    "estimated_effort_hours": wp.estimated_effort_hours or 0,
                    "priority": wp.priority,
                    "sequence_order": wp.sequence_order,
                    "application_phase": wp.togaf_phase or "Implementation",
                    "archimate_element": archimate_element.type
                    if archimate_element
                    else "ApplicationComponent",
                    "archimate_element_id": wp.archimate_element_id,
                    "archimate_element_name": archimate_element.name
                    if archimate_element
                    else None,
                    "plateau_id": wp.plateau_id,
                    "plateau_name": wp.plateau.name if wp.plateau else None,
                    "capability_id": wp.capability_id,
                    "capability_name": wp.capability.name
                    if wp.capability
                    else None,
                }
            )

        # The one work package store keeps rows created directly on it (an
        # auto-generated work package, or the decommission work package from
        # the retirement action). Rows merged in from the legacy store carry
        # source_table and are already listed above through the legacy query,
        # so only rows created on the store itself are appended here -- a
        # decommission work package for this application appears on the
        # roadmap through the same list.
        unified_work_packages = []
        try:
            unified_work_packages = (
                UnifiedWorkPackage.query.filter(
                    UnifiedWorkPackage.organization_id == getattr(g, "current_org_id", None),
                    UnifiedWorkPackage.application_component_id == id,
                    UnifiedWorkPackage.source_table.is_(None),
                )
                .order_by(UnifiedWorkPackage.end_date.asc(), UnifiedWorkPackage.priority.desc())
                .all()
            )
        except Exception:
            db.session.rollback()
        for uwp in unified_work_packages:
            work_packages_list.append({
                "id": uwp.id,
                "name": uwp.name,
                "description": uwp.description or "",
                "transformation_type": uwp.element_type or "Modernization",
                "assigned_to": uwp.assigned_to or "Unassigned",
                "status": uwp.status,
                "start_date": uwp.start_date.isoformat() if uwp.start_date else None,
                "target_date": uwp.end_date.isoformat() if uwp.end_date else None,
                "progress_percentage": 0,
                "estimated_effort_hours": uwp.estimated_effort_hours or 0,
                "priority": uwp.priority,
                "sequence_order": 0,
                "application_phase": uwp.togaf_phase or "Implementation",
                "archimate_element": "ApplicationComponent",
                "archimate_element_id": uwp.archimate_element_id,
                "archimate_element_name": None,
                "plateau_id": None,
                "plateau_name": None,
                "capability_id": None,
                "capability_name": None,
            })

        return render_template(
            "applications/roadmap.html",
            application=application,
            work_packages=work_packages_list,
            gaps=gaps,
            plateaus=plateaus,
            target_arch_elements=target_arch_elements,
            related_capabilities=[],
            start_date=start_date,
            end_date=end_date,
            months=months,
        )

    except Exception:
        flash("Error loading application roadmap. Please try again.", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))


# NOTE: dashboard_application_roadmap at /dashboard/applications/<id>/roadmap removed —
# canonical version lives in application_mgmt.application_roadmap
# (app/application_mgmt/routes.py, blueprint url_prefix="/dashboard").

# NOTE: GET /api/applications/<id>/work-packages removed — canonical version
# lives in application_api (app/api/application_routes.py:api_work_packages).


@main.route("/api/applications/<int:id>/work-packages", methods=["POST"])
@login_required
def create_application_work_package(id):
    """Create new work package for specific application"""
    try:
        data = request.get_json()

        # Validate required fields
        required_fields = ["name", "transformation_type", "start_date", "target_date"]
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400

        # RDM-021: Use user-selected ArchiMate element or fall back to auto-created
        user_archimate_id = data.get("archimate_element_id")
        if user_archimate_id:
            archimate_element = ArchiMateElement.query.get(int(user_archimate_id))
        else:
            archimate_element = ArchiMateElement.query.filter_by(
                type="ApplicationComponent", layer="Application"
            ).first()

        if not archimate_element:
            archimate_element = ArchiMateElement(
                name=f"ApplicationComponent - {data['name']}",
                type="ApplicationComponent",
                layer="Application",
                description=f"Application component for {data['name']}",
            )
            db.session.add(archimate_element)
            db.session.flush()

        # Create new work package
        new_wp = WorkPackage(
            name=data["name"],
            description=data.get("description", ""),
            archimate_element_id=archimate_element.id,
            application_component_id=id,
            owner_id=current_user.id if current_user.is_authenticated else None,
            status=data.get("status", "planned"),
            start_date=datetime.fromisoformat(data["start_date"]).date()
            if isinstance(data["start_date"], str)
            else data["start_date"],
            target_date=datetime.fromisoformat(data["target_date"]).date()
            if isinstance(data["target_date"], str)
            else data["target_date"],
            estimated_effort_hours=data.get("estimated_effort_hours", 0),
            priority=data.get("priority", "medium"),
            element_type=data["transformation_type"],
            togaf_phase=data.get("application_phase", "Implementation"),
            sequence_order=data.get("sequence_order", 1),
            context="application",
            # ArchiMate 3.2 Implementation & Migration links (RDM-021)
            plateau_id=data.get("plateau_id"),
            capability_id=data.get("capability_id"),
        )

        db.session.add(new_wp)
        sync_archimate_element(new_wp)
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "work_package": {
                    "id": new_wp.id,
                    "name": new_wp.name,
                    "description": new_wp.description,
                    "transformation_type": new_wp.element_type,
                    "assigned_to": current_user.email
                    if current_user.is_authenticated
                    else "Unassigned",
                    "status": new_wp.status,
                    "start_date": new_wp.start_date.isoformat(),
                    "target_date": new_wp.target_date.isoformat(),
                    "progress_percentage": 0,
                    "estimated_effort_hours": new_wp.estimated_effort_hours,
                    "priority": new_wp.priority,
                    "application_phase": new_wp.togaf_phase,
                    "archimate_element": archimate_element.type,
                    "archimate_element_id": new_wp.archimate_element_id,
                    "archimate_element_name": archimate_element.name
                    if archimate_element
                    else None,
                    "plateau_id": new_wp.plateau_id,
                    "plateau_name": new_wp.plateau.name
                    if new_wp.plateau
                    else None,
                    "capability_id": new_wp.capability_id,
                    "capability_name": new_wp.capability.name
                    if new_wp.capability
                    else None,
                },
            }
        )

    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/applications/<int:id>/work-packages/<int:wp_id>", methods=["PUT"])
@login_required
def update_application_work_package(id, wp_id):
    """Update work package for specific application"""
    try:
        data = request.get_json()

        work_package = WorkPackage.query.get_or_404(wp_id)

        # Verify this work package belongs to the specified application
        if work_package.application_component_id != id:
            return jsonify(
                {"error": "Work package does not belong to this application"}
            ), 403

        # Update fields
        if "name" in data:
            work_package.name = data["name"]
        if "description" in data:
            work_package.description = data["description"]
        if "status" in data:
            work_package.status = data["status"]
            if data["status"] == "completed":
                work_package.completed_date = datetime.now().date()
        if "start_date" in data:
            work_package.start_date = (
                datetime.fromisoformat(data["start_date"]).date()
                if isinstance(data["start_date"], str)
                else data["start_date"]
            )
        if "target_date" in data:
            work_package.target_date = (
                datetime.fromisoformat(data["target_date"]).date()
                if isinstance(data["target_date"], str)
                else data["target_date"]
            )
        if "estimated_effort_hours" in data:
            work_package.estimated_effort_hours = data["estimated_effort_hours"]
        if "priority" in data:
            work_package.priority = data["priority"]
        if "application_phase" in data:
            work_package.togaf_phase = data["application_phase"]
        if "plateau_id" in data:
            work_package.plateau_id = data["plateau_id"]
        if "capability_id" in data:
            work_package.capability_id = data["capability_id"]
        if "archimate_element_id" in data:
            work_package.archimate_element_id = data["archimate_element_id"]

        work_package.updated_at = datetime.now()
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "work_package": {
                    "id": work_package.id,
                    "name": work_package.name,
                    "description": work_package.description,
                    "transformation_type": work_package.element_type,
                    "assigned_to": work_package.owner.email
                    if work_package.owner
                    else "Unassigned",
                    "status": work_package.status,
                    "start_date": work_package.start_date.isoformat(),
                    "target_date": work_package.target_date.isoformat(),
                    "progress_percentage": 100
                    if work_package.completed_date
                    else (
                        0
                        if not work_package.start_date
                        or work_package.start_date > datetime.now().date()
                        else max(
                            0,
                            min(
                                100,
                                (
                                    (
                                        datetime.now().date() - work_package.start_date
                                    ).days
                                    * 100.0
                                    / max(
                                        1,
                                        (
                                            work_package.target_date
                                            - work_package.start_date
                                        ).days,
                                    )
                                ),
                            ),
                        )
                    ),
                    "estimated_effort_hours": work_package.estimated_effort_hours,
                    "priority": work_package.priority,
                    "application_phase": work_package.togaf_phase,
                    "archimate_element_id": work_package.archimate_element_id,
                    "archimate_element_name": work_package.archimate_element.name
                    if work_package.archimate_element
                    else None,
                    "plateau_id": work_package.plateau_id,
                    "plateau_name": work_package.plateau.name
                    if work_package.plateau
                    else None,
                    "capability_id": work_package.capability_id,
                    "capability_name": work_package.capability.name
                    if work_package.capability
                    else None,
                },
            }
        )

    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/applications/<int:id>/work-packages/<int:wp_id>", methods=["DELETE"])
@login_required
def delete_application_work_package(id, wp_id):
    """Delete work package for specific application"""
    try:
        work_package = WorkPackage.query.get_or_404(wp_id)

        # Verify this work package belongs to the specified application
        if work_package.application_component_id != id:
            return jsonify(
                {"error": "Work package does not belong to this application"}
            ), 403

        db.session.delete(work_package)
        db.session.commit()

        return jsonify(
            {"success": True, "message": f"Application work package {wp_id} deleted"}
        )

    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500

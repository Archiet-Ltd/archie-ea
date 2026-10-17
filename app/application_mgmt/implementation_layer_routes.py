"""
Implementation Layer Routes — sub-module extracted from routes.py (BE-054 wave-9).

Handles ArchiMate implementation & migration layer element management:
  * Work-packages, Deliverables, Plateaus, ImplementationEvents
  * Inline CRUD via helpers (add/delete via ArchiMate relationships)
  * Form-based layer update (implementation-update)
  * Direct FK link/unlink and edit routes
"""

import json
import logging

from flask import current_app, flash, jsonify, redirect, request, session, url_for
from flask_login import login_required
from app.decorators import WORK_PACKAGE_WRITE_ROLES, require_roles
from app.services import work_package_service
from werkzeug.exceptions import HTTPException

logger = logging.getLogger(__name__)

from .. import db
from ..models.application_portfolio import ApplicationComponent
from ..models.implementation_migration import Plateau, WorkPackage
from app.utils.deprecation import deprecated_route
from . import application_mgmt
from .forms import ImplementationLayerForm
from .routes import _add_archimate_element, _delete_archimate_element


# Wave 9: Implementation layer routes


@application_mgmt.route("/api/applications/<int:id>/work-packages")
@login_required
@deprecated_route(
    canonical_endpoint="application_api.api_work_packages",
    deprecation_date="2026-02-10",
    migration_guide="Use /api/applications/<id>/work-packages from application_api blueprint instead",
)
def get_application_work_packages(id):
    """API endpoint for application work packages"""
    try:
        ApplicationComponent.query.get_or_404(id)

        # Get work packages for this application
        work_packages = WorkPackage.query.filter_by(application_component_id=id).all()

        wp_data = []
        for wp in work_packages:
            wp_data.append(
                {
                    "id": wp.id,
                    "name": wp.name,
                    "description": wp.description,
                    "work_package_type": "development",
                    "status": wp.status or "planned",
                    "priority": wp.priority or "medium",
                    "assigned_to": wp.owner.username if wp.owner else "Unassigned",
                    "start_date": wp.start_date.isoformat() if wp.start_date else None,
                    "end_date": wp.end_date.isoformat() if wp.end_date else None,
                    "progress_percentage": 0,
                    "estimated_effort_hours": wp.estimated_effort_hours,
                    "technical_details": "",
                }
            )

        return jsonify({"work_packages": wp_data})
    except HTTPException:
        raise
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/applications/<int:id>/layers/implementation-update", methods=["POST"]
)
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
def update_implementation_layer(id):
    """Update Implementation & Migration Layer elements (WorkPackages, Deliverables, Plateaus)"""
    ApplicationComponent.query.get_or_404(id)

    # csrf-ok: global CSRFProtect active

    try:
        form = ImplementationLayerForm(request.form)

        if not form.validate():

            def format_errors(errors_dict):
                formatted = []
                for field, msgs in errors_dict.items():
                    if isinstance(msgs, dict):
                        for sub_field, sub_msgs in msgs.items():
                            if isinstance(sub_msgs, list):
                                formatted.append(
                                    f"{field}.{sub_field}: {', '.join(str(m) for m in sub_msgs)}"
                                )
                            else:
                                formatted.append(f"{field}.{sub_field}: {sub_msgs}")
                    elif isinstance(msgs, list):
                        formatted.append(f"{field}: {', '.join(str(m) for m in msgs)}")
                    else:
                        formatted.append(f"{field}: {msgs}")
                return formatted

            errors = format_errors(form.errors)
            return jsonify({"success": False, "errors": errors}), 400

        changed_fields = []

        # Process work package updates and deletions
        if form.work_packages.data:
            for wp_data in form.work_packages.data:
                wp_id = wp_data.get("id")
                wp_delete = wp_data.get("_delete")

                if wp_id and wp_delete:
                    wp_obj = work_package_service.own_or_404(WorkPackage, wp_id)
                    if wp_obj:
                        db.session.delete(wp_obj)
                        changed_fields.append(f"workpackage_{wp_id}_deleted")
                    continue

                wp_name = wp_data.get("name")
                wp_status = wp_data.get("status")

                if wp_id and wp_name:
                    wp_obj = work_package_service.own_or_404(WorkPackage, wp_id)
                    if wp_obj:
                        old_name = wp_obj.name
                        wp_obj.name = wp_name
                        wp_obj.status = wp_status or "planned"
                        end_date = wp_data.get("end_date")
                        if hasattr(wp_obj, "end_date") and end_date:
                            wp_obj.end_date = end_date

                        if old_name != wp_name:
                            changed_fields.append(f"workpackage_{wp_id}")
                        db.session.add(wp_obj)

        # Process deliverable updates and deletions
        if form.deliverables.data:
            for deliv_data in form.deliverables.data:
                deliv_id = deliv_data.get("id")
                deliv_delete = deliv_data.get("_delete")

                if deliv_id and deliv_delete:
                    deliv_obj = work_package_service.own_deliverable_or_404(deliv_id)
                    if deliv_obj:
                        db.session.delete(deliv_obj)
                        changed_fields.append(f"deliverable_{deliv_id}_deleted")
                    continue

                deliv_name = deliv_data.get("name")
                deliv_type = deliv_data.get("deliverable_type")
                deliv_status = deliv_data.get("status")

                if deliv_id and deliv_name:
                    deliv_obj = work_package_service.own_deliverable_or_404(deliv_id)
                    if deliv_obj:
                        old_name = deliv_obj.name
                        deliv_obj.name = deliv_name
                        deliv_obj.deliverable_type = deliv_type or "artifact"
                        deliv_obj.status = deliv_status or "pending"

                        if old_name != deliv_name:
                            changed_fields.append(f"deliverable_{deliv_id}")
                        db.session.add(deliv_obj)

        # Process plateau updates
        if form.plateaus.data:
            for plat_entry in form.plateaus:
                plat_id = plat_entry.id.data
                plat_name = plat_entry.name.data
                plat_planned = plat_entry.planned_date.data

                if plat_id and plat_name:
                    plat_obj = work_package_service.own_or_404(Plateau, plat_id)
                    if plat_obj:
                        old_name = plat_obj.name
                        plat_obj.name = plat_name
                        if hasattr(plat_obj, "planned_date"):
                            plat_obj.planned_date = plat_planned

                        if old_name != plat_name:
                            changed_fields.append(f"plateau_{plat_id}")
                        db.session.add(plat_obj)

        db.session.commit()

        if changed_fields:
            try:
                session["implementation_changes"] = json.dumps(changed_fields)
            except Exception as e:  # fabricated-ok: guarded skip on error; emits no fabricated value
                logger.debug(f"Ignored: {e}")

        return jsonify(
            {
                "success": True,
                "message": f"Implementation layer updated ({len(changed_fields)} changes)",
                "changed": changed_fields,
            }
        )

    except HTTPException:
        db.session.rollback()
        raise
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(f"Error updating implementation layer: {str(exc)}")
        return jsonify({"success": False, "error": str(exc)}), 500


# --- Implementation & Migration Layer ---
@application_mgmt.route("/applications/<int:app_id>/work-packages", methods=["POST"])
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
def add_work_package(app_id):
    # Application realized by Work Package (Work Package -> Realization -> Application) OR Association
    # Usually Work Package realizes an Application (Outcome).
    # Let's use Realization (Source=WorkPackage, Target=App).
    return _add_archimate_element(
        app_id,
        "implementation",
        "WorkPackage",
        request.json,
        rel_type="realization",
        reverse_rel=True,
    )


@application_mgmt.route(
    "/applications/<int:app_id>/work-packages/<int:id>", methods=["DELETE"]
)
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
def delete_work_package(app_id, id):
    return _delete_archimate_element(id, "realization")


@application_mgmt.route("/applications/<int:app_id>/deliverables", methods=["POST"])
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
def add_deliverable(app_id):
    # Deliverable realizes Application
    return _add_archimate_element(
        app_id,
        "implementation",
        "Deliverable",
        request.json,
        rel_type="realization",
        reverse_rel=True,
    )


@application_mgmt.route(
    "/applications/<int:app_id>/deliverables/<int:id>", methods=["DELETE"]
)
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
def delete_deliverable(app_id, id):
    return _delete_archimate_element(id, "realization")


@application_mgmt.route(
    "/applications/<int:app_id>/implementation-events", methods=["POST"]
)
@login_required
def add_implementation_event(app_id):
    # Event triggers or flows to App? Or associated. Association is safest.
    return _add_archimate_element(
        app_id,
        "implementation",
        "ImplementationEvent",
        request.json,
        rel_type="association",
    )


@application_mgmt.route(
    "/applications/<int:app_id>/implementation-events/<int:id>", methods=["DELETE"]
)
@login_required
def delete_implementation_event(app_id, id):
    return _delete_archimate_element(id, "association")


@application_mgmt.route("/applications/<int:app_id>/plateaus", methods=["POST"])
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
def add_plateau(app_id):
    # Application assigned to Plateau (or Aggregation).
    # Typically Plateau aggregates Application (Architecture).
    # Let's use Aggregation (Source=Plateau, Target=App).
    return _add_archimate_element(
        app_id,
        "implementation",
        "Plateau",
        request.json,
        rel_type="aggregation",
        reverse_rel=True,
    )


@application_mgmt.route(
    "/applications/<int:app_id>/plateaus/<int:id>", methods=["DELETE"]
)
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
def delete_plateau(app_id, id):
    return _delete_archimate_element(id, "aggregation")


@application_mgmt.route(
    "/applications/<int:id>/work-packages/<int:work_package_id>/delete",
    methods=["POST"],
)
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
def work_package_delete(id, work_package_id):
    """Delete a work package from an application"""
    ApplicationComponent.query.get_or_404(id)
    work_package = work_package_service.own_or_404(WorkPackage, work_package_id)

    try:
        db.session.delete(work_package)
        db.session.commit()
        flash(f'Work package "{work_package.name}" deleted successfully', "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            f"Error deleting work package {work_package_id}: {str(e)}"
        )
        flash("Error deleting work package. Please try again.", "danger")

    return redirect(url_for("unified_applications.application_detail", id=id))


@application_mgmt.route(
    "/applications/<int:id>/deliverables/<int:deliverable_id>/delete", methods=["POST"]
)
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
def deliverable_delete(id, deliverable_id):
    """Delete a deliverable from an application"""
    ApplicationComponent.query.get_or_404(id)
    deliverable = work_package_service.own_deliverable_or_404(deliverable_id)

    try:
        db.session.delete(deliverable)
        db.session.commit()
        flash(f'Deliverable "{deliverable.name}" deleted successfully', "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            f"Error deleting deliverable {deliverable_id}: {str(e)}"
        )
        flash("Error deleting deliverable. Please try again.", "danger")

    return redirect(url_for("unified_applications.application_detail", id=id))


@application_mgmt.route(
    "/applications/<int:id>/plateaus/<int:plateau_id>/delete", methods=["POST"]
)
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
def plateau_delete(id, plateau_id):
    """Delete a plateau from an application"""
    ApplicationComponent.query.get_or_404(id)
    plateau = work_package_service.own_or_404(Plateau, plateau_id)

    try:
        db.session.delete(plateau)
        db.session.commit()
        flash(f'Plateau "{plateau.name}" deleted successfully', "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting plateau {plateau_id}: {str(e)}")
        flash("Error deleting plateau. Please try again.", "danger")

    return redirect(url_for("unified_applications.application_detail", id=id))


@application_mgmt.route(
    "/applications/<int:id>/work-packages/<int:work_package_id>/edit", methods=["POST"]
)
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
def work_package_edit(id, work_package_id):
    """Update Work Package properties"""
    ApplicationComponent.query.get_or_404(id)
    work_package = work_package_service.own_or_404(WorkPackage, work_package_id)

    try:
        if "name" in request.form:
            work_package.name = request.form.get("name").strip()
        if "description" in request.form:
            work_package.description = request.form.get("description").strip() or None
        if "status" in request.form:
            work_package.status = request.form.get("status") or None

        db.session.commit()
        flash(f'Work package "{work_package.name}" updated successfully!', "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating work package {work_package_id}: {e}")
        flash("Error updating work package. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#implementation"
    )


@application_mgmt.route(
    "/applications/<int:id>/deliverables/<int:deliverable_id>/edit", methods=["POST"]
)
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
def deliverable_edit(id, deliverable_id):
    """Update Deliverable properties"""
    ApplicationComponent.query.get_or_404(id)
    deliverable = work_package_service.own_deliverable_or_404(deliverable_id)

    try:
        if "name" in request.form:
            deliverable.name = request.form.get("name").strip()
        if "description" in request.form:
            deliverable.description = request.form.get("description").strip() or None
        if "status" in request.form:
            deliverable.status = request.form.get("status") or None

        db.session.commit()
        flash(f'Deliverable "{deliverable.name}" updated successfully!', "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating deliverable {deliverable_id}: {e}")
        flash("Error updating deliverable. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#implementation"
    )


@application_mgmt.route("/applications/<int:id>/work-packages/add", methods=["POST"])
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
def work_package_add(id):
    """Link existing Work Package to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    work_package_id = request.form.get("element_id")

    if not work_package_id:
        flash("No work package selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    work_package = work_package_service.own_or_404(WorkPackage, work_package_id)

    if (
        work_package.application_component_id
        and work_package.application_component_id != app.id
    ):
        other_app = ApplicationComponent.query.get(
            work_package.application_component_id
        )
        flash(
            f'Work package "{work_package.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif work_package.application_component_id == app.id:
        flash(
            f'Work package "{work_package.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            work_package.application_component_id = app.id
            db.session.commit()
            flash(f'Work package "{work_package.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking work package {work_package_id} to app {id}: {e}"
            )
            flash("Error linking work package. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#implementation"
    )


@application_mgmt.route("/applications/<int:id>/deliverables/add", methods=["POST"])
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
def deliverable_add(id):
    """Link existing Deliverable to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    deliverable_id = request.form.get("element_id")

    if not deliverable_id:
        flash("No deliverable selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    deliverable = work_package_service.own_deliverable_or_404(deliverable_id)

    if (
        deliverable.application_component_id
        and deliverable.application_component_id != app.id
    ):
        other_app = ApplicationComponent.query.get(deliverable.application_component_id)
        flash(
            f'Deliverable "{deliverable.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif deliverable.application_component_id == app.id:
        flash(
            f'Deliverable "{deliverable.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            deliverable.application_component_id = app.id
            db.session.commit()
            flash(f'Deliverable "{deliverable.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking deliverable {deliverable_id} to app {id}: {e}"
            )
            flash("Error linking deliverable. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#implementation"
    )


@application_mgmt.route("/applications/<int:id>/plateaus/add", methods=["POST"])
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
def plateau_add(id):
    """Link existing Plateau to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    plateau_id = request.form.get("element_id")

    if not plateau_id:
        flash("No plateau selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    plateau = work_package_service.own_or_404(Plateau, plateau_id)

    if plateau.application_component_id and plateau.application_component_id != app.id:
        other_app = ApplicationComponent.query.get(plateau.application_component_id)
        flash(
            f'Plateau "{plateau.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif plateau.application_component_id == app.id:
        flash(
            f'Plateau "{plateau.name}" is already linked to this application', "warning"
        )
    else:
        try:
            plateau.application_component_id = app.id
            db.session.commit()
            flash(f'Plateau "{plateau.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking plateau {plateau_id} to app {id}: {e}"
            )
            flash("Error linking plateau. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#implementation"
    )

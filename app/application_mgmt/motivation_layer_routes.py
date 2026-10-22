"""
Motivation Layer Routes — sub-module extracted from routes.py (BE-054 wave-6).

Handles ArchiMate motivation-layer element management:
inline CRUD (stakeholder, requirement via _add/_delete_archimate_element) and
form-based linking of Goals, Drivers, Requirements to ApplicationComponents.
"""

from flask import current_app, flash, redirect, request, url_for

from .. import db
from ..core.auth.decorators import require_permission
from ..models.application_portfolio import ApplicationComponent
from ..models.models import Requirement
from ..models.motivation import Driver, Goal
from . import application_mgmt
from .routes import _add_archimate_element, _delete_archimate_element

# --- Motivation Layer ---
@application_mgmt.route("/applications/<int:app_id>/stakeholders", methods=["POST"])
@require_permission("create", "motivation_element")
def add_stakeholder(app_id):
    return _add_archimate_element(
        app_id, "motivation", "Stakeholder", request.json, rel_type="influence"
    )


@application_mgmt.route(
    "/applications/<int:app_id>/stakeholders/<int:id>", methods=["DELETE"]
)
@require_permission("delete", "motivation_element")
def delete_stakeholder(app_id, id):
    return _delete_archimate_element(id, "influence")


@application_mgmt.route("/applications/<int:app_id>/requirements", methods=["POST"])
@require_permission("create", "motivation_element")
def add_requirement(app_id):
    return _add_archimate_element(
        app_id, "motivation", "Requirement", request.json, rel_type="realization"
    )


@application_mgmt.route(
    "/applications/<int:app_id>/requirements/<int:id>", methods=["DELETE"]
)
@require_permission("delete", "motivation_element")
def delete_requirement(app_id, id):
    return _delete_archimate_element(id, "realization")

# ============================================================================
# ADD ROUTES - Motivation Layer
# ============================================================================


@application_mgmt.route("/applications/<int:id>/goals/add", methods=["POST"])
@require_permission("edit", "application")
def goal_add(id):
    """Link existing Goal to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    goal_id = request.form.get("element_id")

    if not goal_id:
        flash("No goal selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    goal = Goal.query.get(goal_id)
    if not goal:
        flash("Goal not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    if goal.application_component_id and goal.application_component_id != app.id:
        other_app = ApplicationComponent.query.get(goal.application_component_id)
        flash(
            f'Goal "{goal.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif goal.application_component_id == app.id:
        flash(f'Goal "{goal.name}" is already linked to this application', "warning")
    else:
        try:
            goal.application_component_id = app.id
            db.session.commit()
            flash(f'Goal "{goal.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error linking goal {goal_id} to app {id}: {e}")
            flash("Error linking goal. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#motivation"
    )


@application_mgmt.route("/applications/<int:id>/drivers/add", methods=["POST"])
@require_permission("edit", "application")
def driver_add(id):
    """Link existing Driver to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    driver_id = request.form.get("element_id")

    if not driver_id:
        flash("No driver selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    driver = Driver.query.get(driver_id)
    if not driver:
        flash("Driver not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    if driver.application_component_id and driver.application_component_id != app.id:
        other_app = ApplicationComponent.query.get(driver.application_component_id)
        flash(
            f'Driver "{driver.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif driver.application_component_id == app.id:
        flash(
            f'Driver "{driver.name}" is already linked to this application', "warning"
        )
    else:
        try:
            driver.application_component_id = app.id
            db.session.commit()
            flash(f'Driver "{driver.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking driver {driver_id} to app {id}: {e}"
            )
            flash("Error linking driver. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#motivation"
    )


@application_mgmt.route("/applications/<int:id>/requirements/add", methods=["POST"])
@require_permission("edit", "application")
def application_requirement_add(id):
    """Link existing Requirement to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    requirement_id = request.form.get("element_id")

    if not requirement_id:
        flash("No requirement selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    requirement = Requirement.query.get(requirement_id)
    if not requirement:
        flash("Requirement not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    if (
        requirement.application_component_id
        and requirement.application_component_id != app.id
    ):
        other_app = ApplicationComponent.query.get(requirement.application_component_id)
        flash(
            f'Requirement "{requirement.display_title}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif requirement.application_component_id == app.id:
        flash(
            f'Requirement "{requirement.display_title}" is already linked to this application',
            "warning",
        )
    else:
        try:
            requirement.application_component_id = app.id
            db.session.commit()
            flash(f'Requirement "{requirement.display_title}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking requirement {requirement_id} to app {id}: {e}"
            )
            flash("Error linking requirement. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#motivation"
    )


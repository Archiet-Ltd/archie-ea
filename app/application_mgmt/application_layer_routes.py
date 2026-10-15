"""
Application Layer Routes — Sub-module extracted from routes.py (BE-054 wave-1).

Handles creation/deletion of ArchiMate application-layer elements
(processes, functions, data objects) and linking of existing application-layer
elements to application components.
"""

from werkzeug.exceptions import HTTPException
from flask import current_app, flash, jsonify, redirect, request, url_for
from flask_login import login_required

from .. import db
from ..models.application_layer import (
    ApplicationCollaboration,
    ApplicationEvent,
    ApplicationFunction,
    ApplicationInteraction,
    ApplicationProcess,
)
from ..models.application_portfolio import ApplicationComponent
from ..models.models import ArchiMateElement, ArchiMateRelationship
from . import application_mgmt
import logging
logger = logging.getLogger(__name__)


# =============================================================================
# APPLICATION LAYER — ELEMENT MANAGEMENT (AJAX)
# =============================================================================


@application_mgmt.route(
    "/applications/<int:app_id>/functions/<int:func_id>", methods=["DELETE"]
)
@login_required
def delete_application_function(app_id, func_id):
    """Delete Application Function (AJAX endpoint)"""
    try:
        function = ArchiMateElement.query.get_or_404(func_id)

        # Verify it belongs to this application
        relationship = ArchiMateRelationship.query.filter_by(
            target_id=func_id, type="composition"
        ).first()

        if relationship:
            db.session.delete(relationship)

        db.session.delete(function)
        db.session.commit()

        return jsonify({"success": True})

    except HTTPException:
        # A 404 or 403 from the lookup is the refusal itself, not an internal error.
        raise
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting application function: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@application_mgmt.route("/applications/<int:app_id>/processes", methods=["POST"])
@login_required
def add_application_process(app_id):
    """Add Application Process to existing application (AJAX endpoint)"""
    from app.services.archimate.application_layer_service import ApplicationLayerService

    try:
        app = ApplicationComponent.query.get_or_404(app_id)

        if not app.archimate_element_id:
            return jsonify({"success": False, "error": "No ArchiMate element"}), 400

        archimate_element = db.session.get(ArchiMateElement, app.archimate_element_id)
        if not archimate_element or not archimate_element.architecture_id:
            return jsonify({"success": False, "error": "No architecture found"}), 400

        service = ApplicationLayerService()

        process_data = {
            "name": (request.get_json() or {}).get("name", ""),
            "description": request.json.get("description", ""),
            "properties": {},
        }

        # Reuse existing service
        process = service._create_application_element(
            process_data,
            architecture_id=archimate_element.architecture_id,
            element_type="ApplicationProcess",
        )

        # Link process to application
        relationship = ArchiMateRelationship(
            type="composition",
            source_id=app.archimate_element_id,
            target_id=process.id,
            architecture_id=archimate_element.architecture_id,
        )
        db.session.add(relationship)
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "process": {
                    "id": process.id,
                    "name": process.name,
                    "description": process.description,
                },
            }
        )

    except HTTPException:
        # A 404 or 403 from the lookup is the refusal itself, not an internal error.
        raise
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error adding application process: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@application_mgmt.route("/applications/<int:app_id>/data-objects", methods=["POST"])
@login_required
def add_data_object(app_id):
    """Add Data Object to existing application (AJAX endpoint)"""
    from app.services.archimate.application_layer_service import ApplicationLayerService

    try:
        app = ApplicationComponent.query.get_or_404(app_id)

        if not app.archimate_element_id:
            return jsonify({"success": False, "error": "No ArchiMate element"}), 400

        archimate_element = db.session.get(ArchiMateElement, app.archimate_element_id)
        if not archimate_element or not archimate_element.architecture_id:
            return jsonify({"success": False, "error": "No architecture found"}), 400

        service = ApplicationLayerService()

        data_obj_data = {
            "name": (request.get_json() or {}).get("name", ""),
            "description": request.json.get("description", ""),
            "properties": {
                "format": request.json.get("format", "JSON"),
                "persistence": request.json.get("persistence", "persistent"),
            },
        }

        # Reuse existing service
        data_obj = service._create_application_element(
            data_obj_data,
            architecture_id=archimate_element.architecture_id,
            element_type="DataObject",
        )

        # Link data object to application
        relationship = ArchiMateRelationship(
            type="composition",
            source_id=app.archimate_element_id,
            target_id=data_obj.id,
            architecture_id=archimate_element.architecture_id,
        )
        db.session.add(relationship)
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "data_object": {
                    "id": data_obj.id,
                    "name": data_obj.name,
                    "description": data_obj.description,
                },
            }
        )

    except HTTPException:
        # A 404 or 403 from the lookup is the refusal itself, not an internal error.
        raise
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error adding data object: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/applications/<int:app_id>/data-objects/<int:obj_id>", methods=["DELETE"]
)
@login_required
def delete_data_object(app_id, obj_id):
    """Delete Data Object (AJAX endpoint)"""
    try:
        data_obj = ArchiMateElement.query.get_or_404(obj_id)

        relationship = ArchiMateRelationship.query.filter_by(
            target_id=obj_id, type="composition"
        ).first()

        if relationship:
            db.session.delete(relationship)

        db.session.delete(data_obj)
        db.session.commit()

        return jsonify({"success": True})

    except HTTPException:
        # A 404 or 403 from the lookup is the refusal itself, not an internal error.
        raise
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting data object: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# APPLICATION LAYER — ELEMENT LINKING (FORM POST)
# =============================================================================


@application_mgmt.route(
    "/applications/<int:id>/application-collaborations/add", methods=["POST"]
)
@login_required
def application_collaboration_add(id):
    """Link existing Application Collaboration to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    collab_id = request.form.get("element_id")

    if not collab_id:
        flash("No application collaboration selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    collab = ApplicationCollaboration.query.get(collab_id)
    if not collab:
        flash("Application collaboration not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    if collab.application_component_id and collab.application_component_id != app.id:
        other_app = ApplicationComponent.query.get(collab.application_component_id)
        flash(
            f'Application collaboration "{collab.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif collab.application_component_id == app.id:
        flash(
            f'Application collaboration "{collab.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            collab.application_component_id = app.id
            db.session.commit()
            flash(
                f'Application collaboration "{collab.name}" linked successfully!',
                "success",
            )
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking application collaboration {collab_id} to app {id}: {e}"
            )
            flash("Error linking application collaboration. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#application"
    )


@application_mgmt.route(
    "/applications/<int:id>/application-functions/add", methods=["POST"]
)
@login_required
def application_function_add(id):
    """Link existing Application Function to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    function_id = request.form.get("element_id")

    if not function_id:
        flash("No application function selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    function = ApplicationFunction.query.get(function_id)
    if not function:
        flash("Application function not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    if (
        function.application_component_id
        and function.application_component_id != app.id
    ):
        other_app = ApplicationComponent.query.get(function.application_component_id)
        flash(
            f'Application function "{function.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif function.application_component_id == app.id:
        flash(
            f'Application function "{function.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            function.application_component_id = app.id
            db.session.commit()
            flash(
                f'Application function "{function.name}" linked successfully!',
                "success",
            )
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking application function {function_id} to app {id}: {e}"
            )
            flash("Error linking application function. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#application"
    )


@application_mgmt.route(
    "/applications/<int:id>/application-processes/add", methods=["POST"]
)
@login_required
def application_process_add(id):
    """Link existing Application Process to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    process_id = request.form.get("element_id")

    if not process_id:
        flash("No application process selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    process = ApplicationProcess.query.get(process_id)
    if not process:
        flash("Application process not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    if process.application_component_id and process.application_component_id != app.id:
        other_app = ApplicationComponent.query.get(process.application_component_id)
        flash(
            f'Application process "{process.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif process.application_component_id == app.id:
        flash(
            f'Application process "{process.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            process.application_component_id = app.id
            db.session.commit()
            flash(
                f'Application process "{process.name}" linked successfully!', "success"
            )
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking application process {process_id} to app {id}: {e}"
            )
            flash("Error linking application process. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#application"
    )


@application_mgmt.route(
    "/applications/<int:id>/application-interactions/add", methods=["POST"]
)
@login_required
def application_interaction_add(id):
    """Link existing Application Interaction to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    interaction_id = request.form.get("element_id")

    if not interaction_id:
        flash("No application interaction selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    interaction = ApplicationInteraction.query.get(interaction_id)
    if not interaction:
        flash("Application interaction not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    if (
        interaction.application_component_id
        and interaction.application_component_id != app.id
    ):
        other_app = ApplicationComponent.query.get(interaction.application_component_id)
        flash(
            f'Application interaction "{interaction.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif interaction.application_component_id == app.id:
        flash(
            f'Application interaction "{interaction.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            interaction.application_component_id = app.id
            db.session.commit()
            flash(
                f'Application interaction "{interaction.name}" linked successfully!',
                "success",
            )
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking application interaction {interaction_id} to app {id}: {e}"
            )
            flash("Error linking application interaction. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#application"
    )


@application_mgmt.route(
    "/applications/<int:id>/application-events/add", methods=["POST"]
)
@login_required
def application_event_add(id):
    """Link existing Application Event to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    event_id = request.form.get("element_id")

    if not event_id:
        flash("No application event selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    event = ApplicationEvent.query.get(event_id)
    if not event:
        flash("Application event not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    if event.application_component_id and event.application_component_id != app.id:
        other_app = ApplicationComponent.query.get(event.application_component_id)
        flash(
            f'Application event "{event.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif event.application_component_id == app.id:
        flash(
            f'Application event "{event.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            event.application_component_id = app.id
            db.session.commit()
            flash(f'Application event "{event.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking application event {event_id} to app {id}: {e}"
            )
            flash("Error linking application event. Please try again.", "error")

    return redirect(
        url_for("unified_applications.application_detail", id=id) + "#application"
    )

# app/application_mgmt/archimate_routes.py
"""ArchiMate link-management and AI generation routes extracted from routes.py (BE-054 wave-4)."""
import asyncio
import json
import logging
from datetime import datetime

from flask import current_app, flash, jsonify, redirect, request, url_for
from flask_login import current_user, login_required

from .. import db
from ..models.application_layer import (
    ApplicationCollaboration,
    ApplicationEvent,
    ApplicationFunction,
    ApplicationInteraction,
    ApplicationInterface,
    ApplicationProcess,
    ApplicationService,
    DataObject,
)
from ..models.application_portfolio import ApplicationComponent
from ..models.models import ArchiMateElement, ArchiMateRelationship, ArchitectureModel
from ..services.archimate.archimate_llm_service import ArchiMateLLMService
from ..services.archimate_validation_service import ArchiMateValidationService
from . import application_mgmt
from .routes import ARCHIMATE_RELATIONSHIP_CHOICES, _redirect_to_detail

logger = logging.getLogger(__name__)


@application_mgmt.route("/applications/<int:id>/archimate-links", methods=["POST"])
@login_required
def application_archimate_link_create(id):
    """Create a lightweight ArchiMate relationship for the application component."""
    app = ApplicationComponent.query.get_or_404(id)

    if not app.archimate_element_id:
        flash(
            "Link this application to a primary ArchiMate element before adding relationships.",
            "error",
        )
        return _redirect_to_detail(app.id, tab="architecture")

    application_element = db.session.get(ArchiMateElement, app.archimate_element_id)
    if not application_element:
        flash("Primary ArchiMate element could not be located.", "error")
        return _redirect_to_detail(app.id, tab="architecture")

    try:
        target_element_id = int(request.form.get("target_element_id", "0"))
    except (TypeError, ValueError):
        target_element_id = 0

    if not target_element_id:
        flash("Select an ArchiMate element to link.", "error")
        return _redirect_to_detail(app.id, tab="architecture")

    if target_element_id == application_element.id:
        flash("Select an element other than the primary application element.", "error")
        return _redirect_to_detail(app.id, tab="architecture")

    target_element = db.session.get(ArchiMateElement, target_element_id)
    if not target_element:
        flash("Selected ArchiMate element was not found.", "error")
        return _redirect_to_detail(app.id, tab="architecture")

    if target_element.architecture_id != application_element.architecture_id:
        flash("Elements must belong to the same architecture model.", "error")
        return _redirect_to_detail(app.id, tab="architecture")

    relationship_type = (
        (request.form.get("relationship_type") or "association").strip().lower()
    )
    allowed_relationships = {choice[0] for choice in ARCHIMATE_RELATIONSHIP_CHOICES}
    if relationship_type not in allowed_relationships:
        flash("Select a supported relationship type.", "error")
        return _redirect_to_detail(app.id, tab="architecture")

    direction = (
        (request.form.get("relationship_direction") or "outbound").strip().lower()
    )
    if direction == "inbound":
        source_id, target_id = target_element.id, application_element.id
        direction = "inbound"
    else:
        source_id, target_id = application_element.id, target_element.id
        direction = "outbound"

    existing = ArchiMateRelationship.query.filter_by(
        source_id=source_id, target_id=target_id, type=relationship_type
    ).first()
    if existing:
        flash("This ArchiMate link already exists.", "info")
        return _redirect_to_detail(app.id, tab="architecture")

    # Validate relationship according to ArchiMate 3.2 metamodel
    from flask_login import current_user

    is_valid, error_msg, rule = ArchiMateValidationService.validate_and_log(
        source_element_id=source_id,
        target_element_id=target_id,
        relationship_type=relationship_type,
        user_id=current_user.id if hasattr(current_user, "id") else None,
        severity="warning",
    )

    if not is_valid:
        flash(error_msg, "error")
        return _redirect_to_detail(app.id, tab="architecture")

    relationship = ArchiMateRelationship(
        type=relationship_type,
        architecture_id=application_element.architecture_id,
        source_id=source_id,
        target_id=target_id,
    )

    try:
        db.session.add(relationship)
        db.session.commit()
        flash("ArchiMate element linked successfully.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Unable to link ArchiMate element: {exc}", "error")

    return _redirect_to_detail(app.id, tab="architecture")

@application_mgmt.route(
    "/applications/<int:id>/archimate-links/<int:relationship_id>/delete",
    methods=["POST"],
)
@login_required
def application_archimate_link_delete(id, relationship_id):
    """Remove a lightweight ArchiMate relationship for the application component."""
    app = ApplicationComponent.query.get_or_404(id)
    relationship = ArchiMateRelationship.query.get_or_404(relationship_id)

    if app.archimate_element_id not in {relationship.source_id, relationship.target_id}:
        flash("That relationship is not associated with this application.", "error")
        return _redirect_to_detail(app.id, tab="architecture")

    allowed_relationships = {choice[0] for choice in ARCHIMATE_RELATIONSHIP_CHOICES}
    relationship_type = (relationship.type or "").lower()
    if relationship_type and relationship_type not in allowed_relationships:
        flash(
            "Only supported relationship types can be removed from this view.", "error"
        )
        return _redirect_to_detail(app.id, tab="architecture")

    try:
        db.session.delete(relationship)
        db.session.commit()
        flash("ArchiMate link removed.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Unable to remove ArchiMate link: {exc}", "error")

    return _redirect_to_detail(app.id, tab="architecture")

@application_mgmt.route("/applications/<int:id>/generate-archimate", methods=["POST"])
@login_required
def generate_application_archimate(id):
    """
    Generate ArchiMate 3.2 application-layer elements for an application using existing AI service.
    Reuses ArchiMateLLMService.generate_archimate_from_requirements (target_layer='application').
    """
    app_obj = ApplicationComponent.query.get_or_404(id)

    try:
        app_arch_element = None
        arch_model = None

        if app_obj.archimate_element_id:
            app_arch_element = db.session.get(
                ArchiMateElement, app_obj.archimate_element_id
            )
            arch_model = (
                db.session.get(ArchitectureModel, app_arch_element.architecture_id)
                if app_arch_element
                else None
            )

        if arch_model is None:
            arch_model = ArchitectureModel(
                name=f"{app_obj.name} Architecture",
                model_data=json.dumps(
                    {
                        "generated_for": app_obj.name,
                        "generated_at": datetime.utcnow().isoformat(),
                    }
                ),
            )
            db.session.add(arch_model)
            db.session.flush()

        if app_arch_element is None:
            app_arch_element = ArchiMateElement(
                name=app_obj.name,
                type="ApplicationComponent",
                layer="application",
                description=app_obj.description or "",
                architecture_id=arch_model.id,
            )
            db.session.add(app_arch_element)
            db.session.flush()
            app_obj.archimate_element_id = app_arch_element.id
        else:
            if not app_arch_element.architecture_id:
                app_arch_element.architecture_id = arch_model.id
                db.session.add(app_arch_element)

        requirement_lines = [
            f"Application: {app_obj.name}",
            f"Description: {app_obj.description or 'N/A'}",
            f"Component Type: {app_obj.component_type or 'N/A'}",
            f"Business Domain: {app_obj.business_domain or 'N/A'}",
            f"Technology Stack: {app_obj.technology_stack or 'N/A'}",
            f"Deployment Status: {app_obj.deployment_status or 'N/A'}",
        ]
        requirements_text = "\n".join(requirement_lines)

        llm_service = ArchiMateLLMService()
        model_data, _ = llm_service.generate_archimate_from_requirements(
            requirements=requirements_text,
            context="Generate ArchiMate 3.2 application layer elements for this single application.",
            model_name=f"{app_obj.name} Application Layer",
            validate=True,
            target_layer="application",
        )

        if model_data.get("error"):
            db.session.rollback()
            flash(f"AI ArchiMate generation failed: {model_data.get('error')}", "error")
            return redirect(url_for("unified_applications.application_detail", id=id))

        elements = model_data.get("elements", []) or []
        relationships = model_data.get("relationships", []) or []

        element_id_map = {}

        for elem in elements:
            elem_type = elem.get("type")
            if elem_type == "ApplicationComponent":
                continue

            arch_elem = ArchiMateElement(
                name=elem.get("name"),
                type=elem_type,
                layer=elem.get("layer", "application"),
                description=elem.get("description", ""),
                properties=json.dumps(elem.get("properties", {}))
                if elem.get("properties")
                else None,
                architecture_id=arch_model.id,
            )
            db.session.add(arch_elem)
            db.session.flush()

            element_id_map[elem.get("name")] = arch_elem.id

            db.session.add(
                ArchiMateRelationship(
                    type="composition",
                    source_id=app_arch_element.id,
                    target_id=arch_elem.id,
                    architecture_id=arch_model.id,
                )
            )

            if elem_type == "ApplicationService":
                db.session.add(
                    ApplicationService(
                        name=arch_elem.name,
                        description=arch_elem.description,
                        application_component_id=app_obj.id,
                        archimate_element_id=arch_elem.id,
                    )
                )
            elif elem_type == "ApplicationFunction":
                db.session.add(
                    ApplicationFunction(
                        name=arch_elem.name,
                        description=arch_elem.description,
                        application_component_id=app_obj.id,
                        archimate_element_id=arch_elem.id,
                    )
                )
            elif elem_type == "ApplicationProcess":
                db.session.add(
                    ApplicationProcess(
                        name=arch_elem.name,
                        description=arch_elem.description,
                        application_component_id=app_obj.id,
                        archimate_element_id=arch_elem.id,
                    )
                )
            elif elem_type == "ApplicationInteraction":
                db.session.add(
                    ApplicationInteraction(
                        name=arch_elem.name,
                        description=arch_elem.description,
                        application_component_id=app_obj.id,
                        archimate_element_id=arch_elem.id,
                    )
                )
            elif elem_type == "ApplicationEvent":
                db.session.add(
                    ApplicationEvent(
                        name=arch_elem.name,
                        description=arch_elem.description,
                        application_component_id=app_obj.id,
                        archimate_element_id=arch_elem.id,
                    )
                )
            elif elem_type == "ApplicationCollaboration":
                db.session.add(
                    ApplicationCollaboration(
                        name=arch_elem.name,
                        description=arch_elem.description,
                        application_component_id=app_obj.id,
                        archimate_element_id=arch_elem.id,
                    )
                )
            elif elem_type == "ApplicationInterface":
                db.session.add(
                    ApplicationInterface(
                        name=arch_elem.name,
                        description=arch_elem.description,
                        application_component_id=app_obj.id,
                        archimate_element_id=arch_elem.id,
                    )
                )
            elif elem_type == "DataObject":
                db.session.add(
                    DataObject(
                        name=arch_elem.name,
                        description=arch_elem.description,
                        application_component_id=app_obj.id,
                        archimate_element_id=arch_elem.id,
                    )
                )

        for rel in relationships:
            source_name = rel.get("source_name") or rel.get("source")
            target_name = rel.get("target_name") or rel.get("target")
            rel_type = rel.get("type")
            source_id = element_id_map.get(source_name) or (
                app_arch_element.id if source_name == app_arch_element.name else None
            )
            target_id = element_id_map.get(target_name) or (
                app_arch_element.id if target_name == app_arch_element.name else None
            )

            if source_id and target_id and rel_type:
                db.session.add(
                    ArchiMateRelationship(
                        type=rel_type,
                        source_id=source_id,
                        target_id=target_id,
                        architecture_id=arch_model.id,
                    )
                )

        db.session.commit()
        flash("AI ArchiMate application-layer elements generated.", "success")
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(
            f"Error generating ArchiMate elements for application {id}: {exc}"
        )
        flash(f"Unable to generate ArchiMate elements: {exc}", "error")

    return redirect(url_for("unified_applications.application_detail", id=id))

@application_mgmt.route("/applications/archimate-generation", methods=["POST"])
@login_required
def archimate_element_generation():
    """
    Generate ArchiMate elements using existing ArchiMateLLMService.

    Creates ArchiMate 3.2 compliant elements from application requirements
    with validation using ArchiMateMappingAgent.

    Request Body:
    {
        "application_id": 123,           // Optional: specific application
        "requirements": "Text requirements", // Optional: direct requirements
        "context": {                    // Optional context information
            "business_domain": "Finance",
            "stakeholders": ["Business Analyst", "IT Architect"]
        }
    }
    """
    from ..models.application_portfolio import ApplicationComponent
    from ..services.agents.archimate_mapping_agent import ArchiMateMappingAgent
    from ..services.archimate.archimate_llm_service import ArchiMateLLMService

    data = request.get_json() or {}

    application_id = data.get("application_id")
    requirements = data.get("requirements", "")
    context = data.get("context", {})

    try:
        # Initialize services
        archimate_service = ArchiMateLLMService()
        archimate_agent = ArchiMateMappingAgent()

        # Determine requirements source
        if application_id and not requirements:
            # Get requirements from application
            app = ApplicationComponent.query.get(application_id)
            if not app:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": f"Application {application_id} not found",
                        }
                    ),
                    404,
                )

            requirements = f"""
            Application: {app.name}
            Description: {app.description or "No description"}
            Vendor: {app.vendor_name or "Unknown"}
            Category: {app.application_category or "Uncategorized"}
            """
            context["application"] = app.name

        if not requirements:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "No requirements provided",
                        "message": "Please provide either application_id or requirements",
                    }
                ),
                400,
            )

        # Generate ArchiMate elements
        archimate_result = archimate_service.generate_archimate_from_requirements(
            requirements, context=context
        )

        # Validate generated elements (handle async properly)
        validation_results = []
        for element in archimate_result.get("elements", []):
            try:
                # Create mapping object for validation
                from ..services.agents.archimate_mapping_agent import ArchiMateMapping

                mapping = ArchiMateMapping(
                    element_type=element.get("type", "BusinessProcess"),
                    layer=element.get("layer", "business"),
                    name=element.get("name", ""),
                    description=element.get("description", ""),
                    confidence_score=0.8,
                    reasoning="Generated from application requirements",
                )

                # Use asyncio.run to handle async validation
                validation = asyncio.run(archimate_agent.validate_mapping(mapping))
                validation_results.append(
                    {
                        "element_name": element.get("name", "Unknown"),
                        "is_valid": validation.is_valid,
                        "errors": validation.errors,
                        "warnings": validation.warnings,
                    }
                )
            except Exception as validation_error:
                logger.error(f"Validation error for element: {validation_error}")
                validation_results.append(
                    {
                        "element_name": element.get("name", "Unknown"),
                        "is_valid": False,
                        "errors": [f"Validation failed: {str(validation_error)}"],
                        "warnings": [],
                    }
                )

        return jsonify(
            {
                "success": True,
                "elements": archimate_result.get("elements", []),
                "relationships": archimate_result.get("relationships", []),
                "validation_results": validation_results,
                "context": context,
                "generated_at": datetime.utcnow().isoformat(),
            }
        )

    except Exception as e:
        logger.error(f"Error in ArchiMate generation: {e}")
        return (
            jsonify(
                {
                    "success": False,
                    "error": str(e),
                    "generated_at": datetime.utcnow().isoformat(),
                }
            ),
            500,
        )


@application_mgmt.route(
    "/api/archimate-elements/<int:element_id>/correct", methods=["POST"]
)
@login_required
def archimate_element_correct(element_id):
    """Record a user correction for a single ArchiMate element (flywheel)."""
    from app.models.archimate_core import ArchiMateElement
    from app.services.archimate.feedback_learning_service import FeedbackLearningService

    data = request.get_json(silent=True) or {}
    action = data.get("action")  # "approve" | "correct"

    elem = ArchiMateElement.query.get_or_404(element_id)
    original = {"name": elem.name, "type": elem.type, "layer": elem.layer}

    if action == "approve":
        # Thumbs up: record as self-correction (original == corrected)
        corrected = original.copy()
    elif action == "correct":
        corrected = {
            "name": data.get("name", elem.name),
            "type": data.get("type", elem.type),
            "layer": data.get("layer", elem.layer),
        }
        # Apply the correction to the DB record
        if corrected["name"] != elem.name:
            elem.name = corrected["name"]
        if corrected["type"] != elem.type:
            elem.type = corrected["type"]
        if corrected["layer"] != elem.layer:
            elem.layer = corrected["layer"]
        db.session.commit()
    else:
        return jsonify({"success": False, "error": "action must be 'approve' or 'correct'"}), 400

    try:
        svc = FeedbackLearningService()
        feedback_id = svc.record_correction(
            original_element=original,
            corrected_element=corrected,
            document_id=None,
            document_hash=f"app-element-{element_id}",
            user_id=current_user.id,
            confidence_before=0.7,
        )
        return jsonify({"success": True, "feedback_id": feedback_id})
    except Exception:
        # Recording the correction IS the endpoint's product (the learning
        # flywheel); losing it is a failure even though the element edit above
        # already committed. Re-submitting is idempotent, so a 500 that prompts
        # a retry is both honest and safe.
        current_app.logger.exception(
            "Failed to record ArchiMate correction feedback for element %s", element_id
        )
        return jsonify(
            {"success": False, "error": "Could not record the correction"}
        ), 500

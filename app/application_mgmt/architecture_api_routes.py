"""
Architecture API Routes — sub-module extracted from routes.py (BE-054 wave-11a).

Handles architecture API endpoints:
  * Application details API
  * ArchiMate element CRUD API (GET, POST, PUT/PATCH, DELETE)
  * Architecture CSV export
  * Architecture documents API (GET, POST, GET/download, DELETE)
  * AI architecture generation
"""

import csv
import io
import json
import logging
from datetime import datetime

from flask import current_app, jsonify, request, send_file
from flask_login import current_user, login_required

logger = logging.getLogger(__name__)

from .. import db
from ..models.application_portfolio import ApplicationComponent
from ..models.business_capabilities import BusinessFunction
from ..models.business_layer import (
    BusinessActor,
    BusinessObject,
    BusinessRole,
    BusinessService,
)
from ..models.implementation_migration import Deliverable, Plateau, WorkPackage
from ..models.models import ArchiMateElement, ArchiMateRelationship, ArchitectureModel, Requirement
from ..models.motivation import Driver, Goal
from ..models.physical_layer import (
    PhysicalDistributionNetwork,
    PhysicalEquipment,
    PhysicalFacility,
    PhysicalMaterial,
)
from ..models.strategy_layer import CourseOfAction, ValueStream
from ..models.technology_layer import Device, Node, SystemSoftware
from ..models.application_layer import ApplicationInterface, ApplicationService, DataObject
from app.utils.deprecation import deprecated_route
from . import application_mgmt
from .routes import (
    get_archimate_elements_batch,
    get_element_counts_batch,
    validate_archimate_element_creation,
)


# Wave 11a: Architecture API routes


@application_mgmt.route("/api/applications/<string:id>/details", methods=["GET"])
@login_required
@deprecated_route(
    canonical_endpoint="application_api.api_application_details",
    deprecation_date="2026-02-10",
    migration_guide="Use /api/applications/<id>/details from application_api blueprint instead",
)
def api_get_application_details(id):
    """
    Get application details for architecture page header.
    Returns element counts by ArchiMate layer.

    Optimized: Uses batch query helper to reduce N + 1 query pattern from 21+ queries to ~6.
    """
    app_obj = ApplicationComponent.query.get_or_404(id)

    # Use batch helper for domain-specific model counts (reduces 21 queries to ~6)
    element_counts = get_element_counts_batch(id)

    # Also count ArchiMateElements linked to this application's architecture model
    if app_obj.archimate_element_id:
        arch_element = db.session.get(ArchiMateElement, app_obj.archimate_element_id)
        if arch_element and arch_element.architecture_id:
            # Single query for all architecture elements, grouped by layer
            elements = ArchiMateElement.query.filter_by(
                architecture_id=arch_element.architecture_id
            ).all()
            for elem in elements:
                layer = (elem.layer or "application").lower()
                if layer in element_counts:
                    element_counts[layer] += 1

    # Also count cloned ArchiMateElements (architecture_id=NULL)
    cloned_elements = ArchiMateElement.query.filter_by(
        application_component_id=id
    ).all()
    for elem in cloned_elements:
        layer = (elem.layer or "application").lower()
        if layer in element_counts:
            element_counts[layer] += 1

    return jsonify(
        {
            "id": app_obj.id,
            "name": app_obj.name,
            "description": app_obj.description,
            "status": app_obj.deployment_status or "Development",
            "architecture_model_id": app_obj.archimate_element_id,
            "created_at": app_obj.created_at.isoformat()
            if app_obj.created_at
            else None,
            "updated_at": app_obj.updated_at.isoformat()
            if app_obj.updated_at
            else None,
            "element_counts_by_layer": element_counts,
        }
    )


@application_mgmt.route(
    "/api/applications/<string:id>/architecture/elements", methods=["GET"]
)
@login_required
@deprecated_route(
    canonical_endpoint="application_api.api_arch_elements",
    deprecation_date="2026-02-10",
    migration_guide="Use /api/applications/<id>/architecture/elements from application_api blueprint instead",
)
def api_get_architecture_elements(id):
    """
    Get all ArchiMate elements for an application with filtering by layer.
    Query params:
        - layer: Filter by ArchiMate layer
        - search: Full-text search in name/description
        - page: Pagination (default: 1)
        - per_page: Items per page (default: 100)

    Optimized: Uses batch query helper to reduce N + 1 query pattern from 21+ queries to ~6.
    """
    app_obj = ApplicationComponent.query.get_or_404(id)

    layer = request.args.get("layer", "").lower()
    search = request.args.get("search", "")
    # Get pagination parameters with bounds checking
    from app.utils.pagination import get_pagination_params

    page, per_page = get_pagination_params(default_per_page=100, max_per_page=200)

    # Use batch query helper for domain-specific models (reduces 21 queries to ~6)
    elements = get_archimate_elements_batch(
        id, layer=layer if layer else None, search=search if search else None
    )

    # Helper for search matching on ArchiMateElement records
    search_lower = search.lower() if search else None

    def matches_search(elem):
        if not search_lower:
            return True
        return (
            search_lower in (elem.name or "").lower()
            or search_lower in (elem.description or "").lower()
        )

    # Also include ArchiMateElement records from architecture model
    if app_obj.archimate_element_id:
        arch_element = db.session.get(ArchiMateElement, app_obj.archimate_element_id)
        if arch_element and arch_element.architecture_id:
            archimate_query = ArchiMateElement.query.filter_by(
                architecture_id=arch_element.architecture_id
            )
            if layer:
                archimate_query = archimate_query.filter_by(layer=layer)

            for elem in (
                archimate_query.all()  # model-safety-ok: single query executed once, then iterating results
            ):
                if matches_search(elem):
                    elements.append(
                        {
                            "id": elem.id,
                            "name": elem.name,
                            "archimate_type": elem.type,
                            "layer": elem.layer or "application",
                            "description": elem.description,
                            "code": None,
                            "framework": None,
                            "category": None,
                            "properties": json.loads(elem.properties)
                            if elem.properties
                            else {},
                            "created_at": None,
                            "model_type": "archimate",
                        }
                    )

    # Also query ArchiMateElements cloned from vendor products (architecture_id=NULL)
    cloned_query = ArchiMateElement.query.filter_by(application_component_id=id)
    if layer:
        cloned_query = cloned_query.filter_by(layer=layer)

    for elem in (
        cloned_query.all()  # model-safety-ok: single query executed once, then iterating results
    ):
        if matches_search(elem):
            elements.append(
                {
                    "id": elem.id,
                    "name": elem.name,
                    "archimate_type": elem.type,
                    "layer": elem.layer or "application",
                    "description": elem.description,
                    "code": None,
                    "framework": None,
                    "category": None,
                    "properties": json.loads(elem.properties)
                    if elem.properties
                    else {},
                    "created_at": None,
                    "model_type": "archimate",
                }
            )

    # Pagination
    total = len(elements)
    start = (page - 1) * per_page
    end = start + per_page
    paginated_elements = elements[start:end]

    return jsonify(
        {
            "elements": paginated_elements,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": (total + per_page - 1) // per_page,
            },
        }
    )


@application_mgmt.route(
    "/api/applications/<string:id>/architecture/elements", methods=["POST"]
)
@login_required
@deprecated_route(
    canonical_endpoint="application_api.api_arch_elements",
    deprecation_date="2026-02-10",
    migration_guide="Use POST /api/applications/<id>/architecture/elements from application_api blueprint instead",
)
def api_create_architecture_element(id):
    """
    Create new ArchiMate element for application.
    """
    # Debug: Log that we hit the API endpoint
    current_app.logger.info(f"API CREATE ELEMENT HIT: app_id={id}")

    app_obj = ApplicationComponent.query.get_or_404(id)
    data = request.get_json()

    if not data:
        current_app.logger.error("No request body")
        return jsonify({"error": "Request body required"}), 400

    current_app.logger.info(f"Request data: {data}")

    name = data.get("name", "").strip()
    archimate_type = data.get("archimate_type", "").strip()
    layer = data.get("layer", "").strip().lower()

    # Enhanced validation with layer-specific rules
    validation_result = validate_archimate_element_creation(
        name=name,
        archimate_type=archimate_type,
        layer=layer,
        properties=data.get("properties", {}),
    )

    if not validation_result["valid"]:
        return jsonify({"error": validation_result["error"]}), 400

    try:
        arch_model = None
        app_arch_element = None

        if app_obj.archimate_element_id:
            app_arch_element = db.session.get(
                ArchiMateElement, app_obj.archimate_element_id
            )
            if app_arch_element:
                arch_model = db.session.get(
                    ArchitectureModel, app_arch_element.architecture_id
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

        new_element = ArchiMateElement(
            name=name,
            type=archimate_type,
            layer=layer,
            description=data.get("description", ""),
            properties=json.dumps(data.get("properties", {}))
            if data.get("properties")
            else None,
            architecture_id=arch_model.id,
        )
        db.session.add(new_element)
        db.session.flush()

        db.session.add(
            ArchiMateRelationship(
                type="composition",
                source_id=app_arch_element.id,
                target_id=new_element.id,
                architecture_id=arch_model.id,
            )
        )

        db.session.commit()

        return (
            jsonify(
                {
                    "id": new_element.id,
                    "name": new_element.name,
                    "archimate_type": new_element.type,
                    "layer": new_element.layer,
                    "description": new_element.description,
                    "properties": json.loads(new_element.properties)
                    if new_element.properties
                    else {},
                    "model_type": "archimate",
                }
            ),
            201,
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating element: {e}")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/applications/<int:id>/architecture/elements/<int:element_id>",
    methods=["PUT"],
)
@login_required
@deprecated_route(
    canonical_endpoint="application_api.api_arch_element_ops",
    deprecation_date="2026-02-10",
    migration_guide="Use PUT /api/applications/<id>/architecture/elements/<element_id> from application_api blueprint instead",
)
def api_update_architecture_element(id, element_id):
    """
    Update existing ArchiMate element (inline editing support).
    """
    from app.modules.architecture_assistant.property_service import PropertyValidationError

    ApplicationComponent.query.filter_by(
        id=id,
        organization_id=current_user.organization_id,
    ).first_or_404()
    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body required"}), 400

    try:
        element = ArchiMateElement.query.filter_by(
            id=element_id,
            organization_id=current_user.organization_id,
        ).first()
        if not element:
            return jsonify({"error": "Element not found"}), 404

        if "name" in data:
            element.name = data["name"].strip()
        if "description" in data:
            element.description = data["description"]
        if "properties" in data:
            from app.modules.architecture_assistant.property_service import PropertyService
            PropertyService().merge_element_properties(element, data["properties"] or {})

        db.session.commit()

        return jsonify(
            {
                "id": element.id,
                "name": element.name,
                "archimate_type": element.type,
                "layer": element.layer,
                "description": element.description,
                "properties": json.loads(element.properties)
                if element.properties
                else {},
                "model_type": "archimate",
            }
        )

    except PropertyValidationError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating element: {e}")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/applications/<string:id>/architecture/elements/<string:element_id>",
    methods=["DELETE"],
)
@login_required
@deprecated_route(
    canonical_endpoint="application_api.api_arch_element_ops",
    deprecation_date="2026-02-10",
    migration_guide="Use DELETE /api/applications/<id>/architecture/elements/<element_id> from application_api blueprint instead",
)
def api_delete_architecture_element(id, element_id):
    """
    Delete ArchiMate element and all its relationships.
    """
    ApplicationComponent.query.get_or_404(id)

    try:
        element = ArchiMateElement.query.get(element_id)
        if not element:
            return jsonify({"error": "Element not found"}), 404

        ArchiMateRelationship.query.filter(
            (ArchiMateRelationship.source_id == element_id)
            | (ArchiMateRelationship.target_id == element_id)
        ).delete(synchronize_session=False)

        db.session.delete(element)
        db.session.commit()

        return jsonify({"success": True, "message": "Element deleted"})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting element: {e}")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/applications/<string:id>/architecture/export-csv", methods=["GET"]
)
@login_required
@deprecated_route(
    canonical_endpoint="application_api.api_arch_export",
    deprecation_date="2026-02-10",
    migration_guide="Use GET /api/applications/<id>/architecture/export-csv from application_api blueprint instead",
)
def api_export_architecture_csv(id):
    """
    Export all application architecture data as CSV.
    """
    app_obj = ApplicationComponent.query.get_or_404(id)
    elements = []

    def add_elements(model, elem_type, layer):
        # Only query models that actually have application_component_id
        # Using try/except since this works with multiple model types dynamically
        try:
            model_instances = model.query.filter_by(
                application_component_id=id
            ).all()  # model-safety-ok: each call queries a different model class, not iterating over data
        except AttributeError:
            # Model doesn't have application_component_id, skip it
            return

        for e in model_instances:
            # Handle optional fields that may not exist on all model types
            try:
                framework = e.framework or ""
            except AttributeError:
                framework = ""

            try:
                code = e.code or ""
            except AttributeError:
                code = ""

            try:
                category = e.category or ""
            except AttributeError:
                category = ""

            try:
                description = e.description or ""
            except AttributeError:
                description = ""

            try:
                created_at = e.created_at.isoformat() if e.created_at else ""
            except AttributeError:
                created_at = ""

            try:
                updated_at = e.updated_at.isoformat() if e.updated_at else ""
            except AttributeError:
                updated_at = ""

            elements.append(
                {
                    "element_id": e.id,
                    "name": e.name,
                    "archimate_type": elem_type,
                    "layer": layer,
                    "framework": framework,
                    "code": code,
                    "category": category,
                    "description": description,
                    "properties_json": "",
                    "created_at": created_at,
                    "updated_at": updated_at,
                }
            )

    add_elements(CourseOfAction, "CourseOfAction", "strategy")
    add_elements(ValueStream, "ValueStream", "strategy")
    add_elements(Goal, "Goal", "motivation")
    add_elements(Driver, "Driver", "motivation")
    add_elements(Requirement, "Requirement", "motivation")
    add_elements(BusinessActor, "BusinessActor", "business")
    add_elements(BusinessRole, "BusinessRole", "business")
    add_elements(BusinessService, "BusinessService", "business")
    add_elements(BusinessFunction, "BusinessFunction", "business")
    add_elements(BusinessObject, "BusinessObject", "business")
    add_elements(ApplicationInterface, "ApplicationInterface", "application")
    add_elements(ApplicationService, "ApplicationService", "application")
    add_elements(DataObject, "DataObject", "application")
    add_elements(Node, "Node", "technology")
    add_elements(Device, "Device", "technology")
    add_elements(SystemSoftware, "SystemSoftware", "technology")
    add_elements(PhysicalEquipment, "PhysicalEquipment", "physical")
    add_elements(PhysicalFacility, "PhysicalFacility", "physical")
    add_elements(PhysicalDistributionNetwork, "DistributionNetwork", "physical")
    add_elements(PhysicalMaterial, "PhysicalMaterial", "physical")
    add_elements(WorkPackage, "WorkPackage", "implementation")
    add_elements(Deliverable, "Deliverable", "implementation")
    add_elements(Plateau, "Plateau", "implementation")

    if app_obj.archimate_element_id:
        arch_element = db.session.get(ArchiMateElement, app_obj.archimate_element_id)
        if arch_element and arch_element.architecture_id:
            for elem in ArchiMateElement.query.filter_by(  # model-safety-ok: single query executed once, not in a loop
                architecture_id=arch_element.architecture_id
            ).all():  # model-safety-ok: single query executed once, not in a loop
                elements.append(
                    {
                        "element_id": elem.id,
                        "name": elem.name,
                        "archimate_type": elem.type,
                        "layer": elem.layer or "application",
                        "framework": "",
                        "code": "",
                        "category": "",
                        "description": elem.description or "",
                        "properties_json": elem.properties or "",
                        "created_at": "",
                        "updated_at": "",
                    }
                )

    output = io.StringIO()
    fieldnames = [
        "element_id",
        "name",
        "archimate_type",
        "layer",
        "framework",
        "code",
        "category",
        "description",
        "properties_json",
        "created_at",
        "updated_at",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(elements)

    output.seek(0)
    filename = (
        f"application_{id}_architecture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )

    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename,
    )


@application_mgmt.route(
    "/api/applications/<string:id>/architecture/documents", methods=["GET"]
)
@login_required
def api_get_architecture_documents(id):
    """Get all architecture documents for an application."""
    from ..models.miscellaneous import ArchitectureDocument

    ApplicationComponent.query.get_or_404(id)
    documents = (
        ArchitectureDocument.query.filter_by(application_id=id)
        .order_by(ArchitectureDocument.created_at.desc())
        .all()
    )

    try:
        result = {"documents": [doc.to_dict() for doc in documents]}
        return jsonify(result)
    except Exception:
        current_app.logger.exception("Error serializing architecture documents")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/applications/<string:id>/architecture/documents", methods=["POST"]
)
@login_required
def api_upload_architecture_document(id):
    """Upload new architecture document."""
    import os

    from werkzeug.utils import secure_filename

    from ..models.miscellaneous import ArchitectureDocument

    ApplicationComponent.query.get_or_404(id)

    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    mime_type = file.content_type

    if mime_type not in ArchitectureDocument.ALLOWED_MIME_TYPES:
        return jsonify({"error": f"File type {mime_type} not allowed"}), 400

    try:
        filename = secure_filename(file.filename)
        upload_dir = os.path.join(
            current_app.config.get("UPLOAD_FOLDER", "uploads"),
            "architecture_docs",
            str(id),
        )
        os.makedirs(upload_dir, exist_ok=True)

        file_path = os.path.join(upload_dir, filename)
        file.save(file_path)
        file_size = os.path.getsize(file_path)

        doc = ArchitectureDocument(
            application_id=id,
            filename=filename,
            document_type=request.form.get("document_type", "Other"),
            description=request.form.get("description", ""),
            version=request.form.get("version", "1.0"),
            file_path=file_path,
            file_size=file_size,
            mime_type=mime_type,
            uploaded_by_id=current_user.id,
        )
        db.session.add(doc)
        db.session.commit()

        result = doc.to_dict()
        return jsonify(result), 201

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error uploading architecture document")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/applications/<string:id>/architecture/documents/<string:doc_id>",
    methods=["GET"],
)
@login_required
def api_download_architecture_document(id, doc_id):
    """Download architecture document file."""
    from ..models.miscellaneous import ArchitectureDocument

    doc = ArchitectureDocument.query.filter_by(
        id=doc_id, application_id=id
    ).first_or_404()

    return send_file(
        doc.file_path,
        mimetype=doc.mime_type,
        as_attachment=True,
        download_name=doc.filename,
    )


@application_mgmt.route(
    "/api/applications/<string:id>/architecture/documents/<string:doc_id>",
    methods=["DELETE"],
)
@login_required
def api_delete_architecture_document(id, doc_id):
    """Delete architecture document."""
    import os

    from ..models.miscellaneous import ArchitectureDocument

    doc = ArchitectureDocument.query.filter_by(
        id=doc_id, application_id=id
    ).first_or_404()

    try:
        if os.path.exists(doc.file_path):
            os.remove(doc.file_path)

        db.session.delete(doc)
        db.session.commit()

        return jsonify({"success": True, "message": "Document deleted"})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting document: {e}")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/applications/<string:id>/generate-architecture", methods=["POST"]
)
@login_required
def api_generate_architecture(id):
    """Generate ArchiMate elements using the existing ArchiMateLLMService."""
    try:
        data = request.get_json()
        layers = data.get(
            "layers",
            [
                "strategy",
                "motivation",
                "business",
                "application",
                "technology",
                "physical",
                "implementation",
            ],
        )
        description = data.get("description", "Generate ArchiMate elements")

        app_obj = ApplicationComponent.query.get_or_404(id)

        # Use the existing ArchiMateLLMService
        from ..services.archimate.archimate_llm_service import ArchiMateLLMService

        # Build requirements text for the AI service
        requirement_lines = [
            f"Application: {app_obj.name}",
            f"Description: {app_obj.description or 'N/A'}",
            f"Component Type: {app_obj.component_type or 'N/A'}",
            f"Business Domain: {app_obj.business_domain or 'N/A'}",
            f"Technology Stack: {app_obj.technology_stack or 'N/A'}",
            f"Deployment Status: {app_obj.deployment_status or 'N/A'}",
            f"Requested Layers: {', '.join(layers)}",
            f"Generation Context: {description}",
        ]
        requirements_text = "\n".join(requirement_lines)

        # Call the real AI service
        llm_service = ArchiMateLLMService()
        model_data, interaction = llm_service.generate_archimate_from_requirements(
            requirements=requirements_text,
            context=f"Generate ArchiMate 3.2 elements for application '{app_obj.name}' across layers: {', '.join(layers)}. {description}",
            model_name=f"{app_obj.name} Generated Architecture",
            validate=True,
            target_layer="complete",  # Generate across all requested layers
        )

        if model_data.get("error"):
            current_app.logger.error(
                f"AI ArchiMate generation failed: {model_data.get('error')}"
            )
            return jsonify({"error": model_data.get("error")}), 500

        elements = model_data.get("elements", []) or []
        relationships = model_data.get("relationships", []) or []
        generated_elements = []

        # Create or get architecture model
        arch_model = ArchitectureModel(
            name=f"{app_obj.name} AI Generated Architecture",
            model_data=json.dumps(
                {
                    "generated_for": app_obj.name,
                    "generated_at": datetime.utcnow().isoformat(),
                    "layers_requested": layers,
                    "description": description,
                    "ai_model_data": model_data,
                }
            ),
        )
        db.session.add(arch_model)
        db.session.flush()

        # Create ArchiMate elements
        element_id_map = {}

        for elem in elements:
            # Filter elements based on requested layers
            elem_layer = elem.get("layer", "").lower()
            if elem_layer not in layers:
                continue

            arch_elem = ArchiMateElement(
                name=elem.get("name"),
                type=elem.get("type"),
                layer=elem_layer,
                description=elem.get("description", ""),
                properties=json.dumps(elem.get("properties", {}))
                if elem.get("properties")
                else None,
                architecture_id=arch_model.id,
            )
            db.session.add(arch_elem)
            db.session.flush()

            element_id_map[elem.get("name")] = arch_elem.id

            generated_elements.append(
                {
                    "id": arch_elem.id,
                    "name": arch_elem.name,
                    "archimate_type": arch_elem.type,
                    "layer": arch_elem.layer,
                    "description": arch_elem.description,
                }
            )

        # Create relationships
        for rel in relationships:
            source_name = rel.get("source")
            target_name = rel.get("target")

            if source_name in element_id_map and target_name in element_id_map:
                arch_rel = ArchiMateRelationship(
                    type=rel.get("type", "association"),
                    source_id=element_id_map[source_name],
                    target_id=element_id_map[target_name],
                    architecture_id=arch_model.id,
                )
                db.session.add(arch_rel)

        db.session.commit()

        return jsonify(
            {
                "success": True,
                "message": f"Generated {len(generated_elements)} ArchiMate elements using AI",
                "elements": generated_elements,
                "relationships": len(relationships),
                "model_id": arch_model.id,
                "validation": model_data.get("validation_results", {}),
                "rationale": model_data.get("rationale", ""),
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error generating architecture: {str(e)}")
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500

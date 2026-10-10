"""Application-scoped ArchiMate element CRUD API routes.

These routes provide API endpoints for:
- Getting application details
- Listing, creating, reading, updating and deleting ArchiMate elements
- Managing element relationships within an application
"""

import json
from flask import current_app, jsonify, request
from flask_login import current_user, login_required  # dead-code-ok
from sqlalchemy.exc import IntegrityError

from app import db
from app.application_mgmt import application_mgmt
from app.models.application_portfolio import ApplicationComponent
from app.utils.duplicate_guard import (
    allow_duplicate_requested,
    duplicate_conflict_response,
    find_duplicate_by_name,
    find_similar_entities,
    lock_name_for_write,
)
from app.utils.pagination import safe_int_arg

@application_mgmt.route("/api/applications/<string:app_id>", methods=["GET"])
@login_required
def get_application_api(app_id):
    """
    Get application details as JSON.

    Returns:
        JSON with application details including architecture model info.
    """
    from app.models.archimate_core import ArchiMateElement, ArchitectureModel

    app_obj = ApplicationComponent.query.get_or_404(app_id)

    # Get architecture model through archimate_element
    arch_model = None
    arch_element = None
    if app_obj.archimate_element_id:
        arch_element = db.session.get(ArchiMateElement, app_obj.archimate_element_id)
        if arch_element and arch_element.architecture_id:
            arch_model = db.session.get(ArchitectureModel, arch_element.architecture_id)

    result = {
        "id": app_obj.id,
        "name": app_obj.name,
        "description": app_obj.description,
        "component_type": getattr(app_obj, "component_type", None),
        "business_domain": getattr(app_obj, "business_domain", None),
        "technology_stack": getattr(app_obj, "technology_stack", None),
        "deployment_status": getattr(app_obj, "deployment_status", None),
        "lifecycle_status": getattr(app_obj, "lifecycle_status", None),
        "vendor": getattr(app_obj, "vendor", None),
        "version": getattr(app_obj, "version", None),
        "archimate_element_id": app_obj.archimate_element_id,
        "created_at": app_obj.created_at.isoformat()
        if hasattr(app_obj, "created_at") and app_obj.created_at
        else None,
        "updated_at": app_obj.updated_at.isoformat()
        if hasattr(app_obj, "updated_at") and app_obj.updated_at
        else None,
    }

    if arch_model:
        result["architecture_model"] = {"id": arch_model.id, "name": arch_model.name}

    return jsonify(result)


@application_mgmt.route("/api/applications/<string:app_id>/elements", methods=["GET"])
@login_required
def get_application_elements(app_id):
    """
    Get all ArchiMate elements for an application.

    Query params:
        - layer: Filter by ArchiMate layer
        - type: Filter by element type
        - search: Search in name/description
        - limit: Max results (default 100)
        - offset: Pagination offset

    Returns:
        JSON array of ArchiMate elements linked to this application.
    """
    from app.models.archimate_core import ArchiMateElement, ArchitectureModel

    app_obj = ApplicationComponent.query.get_or_404(app_id)

    # Get architecture model through archimate_element
    arch_element = None
    arch_model = None
    if app_obj.archimate_element_id:
        arch_element = db.session.get(ArchiMateElement, app_obj.archimate_element_id)
        if arch_element and arch_element.architecture_id:
            arch_model = db.session.get(ArchitectureModel, arch_element.architecture_id)

    if not arch_model:
        return jsonify(
            {"elements": [], "total": 0, "message": "Application has no architecture model"}
        )

    # Build query
    query = ArchiMateElement.query.filter_by(architecture_id=arch_model.id)

    # Apply filters
    layer = request.args.get("layer")
    if layer:
        query = query.filter(ArchiMateElement.layer.ilike(layer))

    element_type = request.args.get("type")
    if element_type:
        query = query.filter(ArchiMateElement.type == element_type)

    search = request.args.get("search")
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            db.or_(
                ArchiMateElement.name.ilike(search_term),
                ArchiMateElement.description.ilike(search_term),
            )
        )

    # Get total count
    total = query.count()

    # Apply pagination
    limit = min(safe_int_arg('limit', 100, minimum=1, maximum=500), 500)
    offset = safe_int_arg('offset', 0, minimum=0)
    elements = query.order_by(ArchiMateElement.name).offset(offset).limit(limit).all()

    return jsonify(
        {
            "elements": [
                {
                    "id": elem.id,
                    "name": elem.name,
                    "type": elem.type,
                    "layer": elem.layer,
                    "description": elem.description,
                    "properties": getattr(elem, "properties", None),
                    "documentation": getattr(elem, "documentation", None),
                }
                for elem in elements
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    )


@application_mgmt.route("/api/applications/<string:app_id>/elements", methods=["POST"])
@login_required
def create_application_element(app_id):
    """
    Create a new ArchiMate element for an application.

    Request body:
        {
            "name": "Element Name",
            "type": "ApplicationComponent",
            "layer": "application",
            "description": "Optional description",
            "properties": {},
            "documentation": "Optional documentation"
        }

    Returns:
        JSON with created element details, or **409** when an element of the
        same type and normalised name already exists in this organisation. The
        409 body carries ``duplicate_of: {id, name}``. Send
        ``allow_duplicate: true`` in the body (or ``?allow_duplicate=true``, or
        the ``X-Allow-Duplicate`` header) to create the duplicate deliberately.
    """
    from app.models.archimate_core import ArchiMateElement, ArchitectureModel

    app_obj = ApplicationComponent.query.get_or_404(app_id)
    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body required"}), 400

    # Validate required fields
    if not data.get("name"):
        return jsonify({"error": "Element name is required"}), 400
    if not data.get("type"):
        return jsonify({"error": "Element type is required"}), 400

    # Get or create architecture model through archimate_element
    arch_model = None
    if app_obj.archimate_element_id:
        arch_element = db.session.get(ArchiMateElement, app_obj.archimate_element_id)
        if arch_element and arch_element.architecture_id:
            arch_model = db.session.get(ArchitectureModel, arch_element.architecture_id)

    if not arch_model:
        # Create new architecture model and link to app
        arch_model = ArchitectureModel(name=f"{app_obj.name} Architecture")
        db.session.add(arch_model)
        db.session.flush()

        # Create archimate element for the app if not exists
        if not app_obj.archimate_element_id:
            app_element = ArchiMateElement(
                name=app_obj.name,
                type="ApplicationComponent",
                layer="application",
                description=app_obj.description or "",
                architecture_id=arch_model.id,
            )
            db.session.add(app_element)
            db.session.flush()
            app_obj.archimate_element_id = app_element.id

    # Validate ArchiMate type
    valid_types = [
        "ApplicationComponent",
        "ApplicationService",
        "ApplicationInterface",
        "ApplicationFunction",
        "ApplicationProcess",
        "ApplicationEvent",
        "ApplicationInteraction",
        "ApplicationCollaboration",
        "DataObject",
        "BusinessProcess",
        "BusinessFunction",
        "BusinessService",
        "BusinessEvent",
        "BusinessActor",
        "BusinessRole",
        "BusinessCollaboration",
        "BusinessInterface",
        "BusinessObject",
        "Contract",
        "Product",
        "Representation",
        "Node",
        "Device",
        "SystemSoftware",
        "TechnologyService",
        "TechnologyInterface",
        "TechnologyFunction",
        "TechnologyProcess",
        "TechnologyEvent",
        "TechnologyInteraction",
        "TechnologyCollaboration",
        "Artifact",
        "CommunicationNetwork",
        "Path",
        "Capability",
        "Resource",
        "ValueStream",
        "CourseOfAction",
        "Stakeholder",
        "Driver",
        "Assessment",
        "Goal",
        "Outcome",
        "Principle",
        "Requirement",
        "Constraint",
        "Meaning",
        "Value",
        "WorkPackage",
        "Deliverable",
        "ImplementationEvent",
        "Plateau",
        "Gap",
        "Location",
        "Grouping",
        "Junction",
        "OrJunction",
        "AndJunction",
    ]

    if data["type"] not in valid_types:
        return (
            jsonify({"error": f"Invalid element type: {data['type']}", "valid_types": valid_types}),
            400,
        )

    # Determine layer from type if not provided
    layer = data.get("layer")
    if not layer:
        type_to_layer = {
            "Application": "application",
            "Business": "business",
            "Technology": "technology",
            "Node": "technology",
            "Device": "technology",
            "System": "technology",
            "Artifact": "technology",
            "Communication": "technology",
            "Path": "technology",
            "Capability": "strategy",
            "Resource": "strategy",
            "ValueStream": "strategy",
            "CourseOfAction": "strategy",
            "Stakeholder": "motivation",
            "Driver": "motivation",
            "Assessment": "motivation",
            "Goal": "motivation",
            "Outcome": "motivation",
            "Principle": "motivation",
            "Requirement": "motivation",
            "Constraint": "motivation",
            "Meaning": "motivation",
            "Value": "motivation",
            "WorkPackage": "implementation",
            "Deliverable": "implementation",
            "Implementation": "implementation",
            "Plateau": "implementation",
            "Gap": "implementation",
            "Location": "physical",
            "Grouping": "other",
            "Junction": "other",
        }
        for prefix, item in type_to_layer.items():
            if data["type"].startswith(prefix):
                layer = item
                break
        if not layer:
            layer = "application"

    # ARCH-030: refuse a same-named element of the same type in this
    # organisation. ArchiMateElement inherits TenantMixin at runtime, so the
    # organisation predicate is injected — do not add one here.
    if not allow_duplicate_requested(data):
        # Serialise the check-then-insert (see lock_name_for_write).
        lock_name_for_write(ArchiMateElement, data["name"])
        existing = find_duplicate_by_name(
            ArchiMateElement,
            data["name"],
            extra_filters=[ArchiMateElement.type == data["type"]],
        )
        if existing is not None:
            return duplicate_conflict_response("An ArchiMate element", existing)

    # S-06: near-duplicate advisory, surfaced before the write commits.
    similar = find_similar_entities(
        ArchiMateElement,
        data["name"],
        extra_filters=[ArchiMateElement.type == data["type"]],
    )

    try:
        element = ArchiMateElement(
            name=data["name"],
            type=data["type"],
            layer=layer,
            description=data.get("description", ""),
            properties=data.get("properties", {}),
            documentation=data.get("documentation", ""),
            architecture_id=arch_model.id,
        )
        db.session.add(element)
        db.session.commit()

        return (
            jsonify(
                {
                    "success": True,
                    "element": {
                        "id": element.id,
                        "name": element.name,
                        "type": element.type,
                        "layer": element.layer,
                        "description": element.description,
                        "properties": getattr(element, "properties", None),
                        "documentation": getattr(element, "documentation", None),
                        "architecture_id": element.architecture_id,
                    },
                    "similar_entities": similar,
                    "similar_entities_count": len(similar),
                }
            ),
            201,
        )

    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Element already exists or constraint violation"}), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error creating element")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/applications/<string:app_id>/elements/<string:element_id>", methods=["GET"]
)
@login_required
def get_application_element(app_id, element_id):
    """
    Get a specific ArchiMate element for an application.

    Returns:
        JSON with element details including relationships.
    """
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    app_obj = ApplicationComponent.query.get_or_404(app_id)

    # Get architecture model through archimate_element
    arch_model_id = None
    if app_obj.archimate_element_id:
        arch_element = db.session.get(ArchiMateElement, app_obj.archimate_element_id)
        if arch_element:
            arch_model_id = arch_element.architecture_id

    if not arch_model_id:
        return jsonify({"error": "Application has no architecture model"}), 404

    element = ArchiMateElement.query.filter_by(
        id=element_id, architecture_id=arch_model_id
    ).first_or_404()

    # Get relationships
    outgoing = ArchiMateRelationship.query.filter_by(source_id=element_id).all()
    incoming = ArchiMateRelationship.query.filter_by(target_id=element_id).all()

    return jsonify(
        {
            "id": element.id,
            "name": element.name,
            "type": element.type,
            "layer": element.layer,
            "description": element.description,
            "properties": getattr(element, "properties", None),
            "documentation": getattr(element, "documentation", None),
            "architecture_id": element.architecture_id,
            "relationships": {
                "outgoing": [
                    {
                        "id": r.id,
                        "type": r.type,
                        "target_id": r.target_id,
                        "target_name": r.target.name if r.target else None,
                    }
                    for r in outgoing
                ],
                "incoming": [
                    {
                        "id": r.id,
                        "type": r.type,
                        "source_id": r.source_id,
                        "source_name": r.source.name if r.source else None,
                    }
                    for r in incoming
                ],
            },
        }
    )


@application_mgmt.route(
    "/api/applications/<int:app_id>/elements/<int:element_id>", methods=["PUT"]
)
@login_required
def update_application_element(app_id, element_id):
    """
    Update an ArchiMate element for an application.

    Request body:
        {
            "name": "Updated Name",
            "description": "Updated description",
            "properties": {},
            "documentation": "Updated docs"
        }

    Returns:
        JSON with updated element details.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.modules.architecture_assistant.property_service import PropertyValidationError

    app_obj = ApplicationComponent.query.get_or_404(app_id)

    # Get architecture model through archimate_element
    arch_model_id = None
    if app_obj.archimate_element_id:
        arch_element = db.session.get(ArchiMateElement, app_obj.archimate_element_id)
        if arch_element:
            arch_model_id = arch_element.architecture_id

    if not arch_model_id:
        return jsonify({"error": "Application has no architecture model"}), 404

    element = ArchiMateElement.query.filter_by(
        id=element_id,
        architecture_id=arch_model_id,
        organization_id=current_user.organization_id,
    ).first_or_404()

    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    try:
        # Update allowed fields
        if "name" in data:
            element.name = data["name"]
        if "description" in data:
            element.description = data["description"]
        if "properties" in data:
            from app.modules.architecture_assistant.property_service import PropertyService
            props = data["properties"]
            if isinstance(props, str):
                try:
                    props = json.loads(props)
                except (TypeError, ValueError):
                    props = {}
            if isinstance(props, dict):
                PropertyService().merge_element_properties(element, props)
        if "documentation" in data:
            element.documentation = data["documentation"]
        if "layer" in data:
            element.layer = data["layer"]

        # Type change requires validation
        if "type" in data and data["type"] != element.type:
            valid_types = [
                "ApplicationComponent",
                "ApplicationService",
                "ApplicationInterface",
                "ApplicationFunction",
                "ApplicationProcess",
                "DataObject",
                "BusinessProcess",
                "BusinessFunction",
                "BusinessService",
                "BusinessActor",
                "BusinessRole",
                "BusinessObject",
                "Node",
                "Device",
                "SystemSoftware",
                "TechnologyService",
                "Artifact",
                "CommunicationNetwork",
                "Capability",
                "Resource",
                "ValueStream",
                "CourseOfAction",
                "Stakeholder",
                "Driver",
                "Goal",
                "Requirement",
                "Constraint",
                "WorkPackage",
                "Deliverable",
                "Plateau",
                "Gap",
            ]
            if data["type"] in valid_types:
                element.type = data["type"]
            else:
                return jsonify({"error": f"Invalid element type: {data['type']}"}), 400

        db.session.commit()

        return jsonify(
            {
                "success": True,
                "element": {
                    "id": element.id,
                    "name": element.name,
                    "type": element.type,
                    "layer": element.layer,
                    "description": element.description,
                    "properties": getattr(element, "properties", None),
                    "documentation": getattr(element, "documentation", None),
                },
            }
        )

    except PropertyValidationError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400

    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Update failed due to constraint violation"}), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error updating element")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/applications/<string:app_id>/elements/<string:element_id>", methods=["DELETE"]
)
@login_required
def delete_application_element(app_id, element_id):
    """
    Delete an ArchiMate element from an application.

    Query params:
        - cascade: If true, also delete related relationships (default: true)

    Returns:
        JSON with deletion status.
    """
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    app_obj = ApplicationComponent.query.get_or_404(app_id)

    # Get architecture model through archimate_element
    arch_model_id = None
    if app_obj.archimate_element_id:
        arch_element = db.session.get(ArchiMateElement, app_obj.archimate_element_id)
        if arch_element:
            arch_model_id = arch_element.architecture_id

    if not arch_model_id:
        return jsonify({"error": "Application has no architecture model"}), 404

    element = ArchiMateElement.query.filter_by(
        id=element_id, architecture_id=arch_model_id
    ).first_or_404()

    cascade = request.args.get("cascade", "true").lower() == "true"

    try:
        deleted_relationships = 0

        if cascade:
            # Delete related relationships
            outgoing = ArchiMateRelationship.query.filter_by(source_id=element_id).all()
            incoming = ArchiMateRelationship.query.filter_by(target_id=element_id).all()

            for r in outgoing + incoming:
                db.session.delete(r)
                deleted_relationships += 1

        # Store element info before deletion
        element_info = {"id": element.id, "name": element.name, "type": element.type}

        db.session.delete(element)
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "deleted_element": element_info,
                "deleted_relationships": deleted_relationships,
                "message": f"Element '{element_info['name']}' deleted successfully",
            }
        )

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error deleting element")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/applications/<string:app_id>/elements/<string:element_id>/relationships", methods=["GET"]
)
@login_required
def get_element_relationships(app_id, element_id):
    """
    Get all relationships for a specific element.

    Returns:
        JSON with incoming and outgoing relationships.
    """
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    app_obj = ApplicationComponent.query.get_or_404(app_id)

    # Get architecture model through archimate_element
    arch_model_id = None
    if app_obj.archimate_element_id:
        arch_element = db.session.get(ArchiMateElement, app_obj.archimate_element_id)
        if arch_element:
            arch_model_id = arch_element.architecture_id

    if not arch_model_id:
        return jsonify({"error": "Application has no architecture model"}), 404

    element = ArchiMateElement.query.filter_by(
        id=element_id, architecture_id=arch_model_id
    ).first_or_404()

    outgoing = ArchiMateRelationship.query.filter_by(source_id=element_id).all()
    incoming = ArchiMateRelationship.query.filter_by(target_id=element_id).all()

    return jsonify(
        {
            "element_id": element_id,
            "element_name": element.name,
            "outgoing": [
                {
                    "id": r.id,
                    "type": r.type,
                    "target_id": r.target_id,
                    "target_name": r.target.name if r.target else None,
                    "target_type": r.target.type if r.target else None,
                    "properties": r.properties,
                }
                for r in outgoing
            ],
            "incoming": [
                {
                    "id": r.id,
                    "type": r.type,
                    "source_id": r.source_id,
                    "source_name": r.source.name if r.source else None,
                    "source_type": r.source.type if r.source else None,
                    "properties": r.properties,
                }
                for r in incoming
            ],
            "total_outgoing": len(outgoing),
            "total_incoming": len(incoming),
        }
    )


@application_mgmt.route(
    "/api/applications/<string:app_id>/elements/<string:element_id>/relationships", methods=["POST"]
)
@login_required
def create_element_relationship(app_id, element_id):
    """
    Create a relationship from this element to another.

    Request body:
        {
            "type": "Serving",
            "target_id": 123,
            "properties": {}
        }

    Returns:
        JSON with created relationship details.
    """
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    app_obj = ApplicationComponent.query.get_or_404(app_id)

    # Get architecture model through archimate_element
    arch_model_id = None
    if app_obj.archimate_element_id:
        arch_element = db.session.get(ArchiMateElement, app_obj.archimate_element_id)
        if arch_element:
            arch_model_id = arch_element.architecture_id

    if not arch_model_id:
        return jsonify({"error": "Application has no architecture model"}), 404

    source = ArchiMateElement.query.filter_by(
        id=element_id, architecture_id=arch_model_id
    ).first_or_404()

    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    if not data.get("type"):
        return jsonify({"error": "Relationship type is required"}), 400
    if not data.get("target_id"):
        return jsonify({"error": "Target element ID is required"}), 400

    # Validate target exists
    target = ArchiMateElement.query.get(data["target_id"])
    if not target:
        return jsonify({"error": "Target element not found"}), 404

    # Validate relationship type
    valid_types = [
        "Composition",
        "Aggregation",
        "Assignment",
        "Realization",
        "Serving",
        "Access",
        "Influence",
        "Association",
        "Triggering",
        "Flow",
        "Specialization",
    ]

    if data["type"] not in valid_types:
        return (
            jsonify(
                {"error": f"Invalid relationship type: {data['type']}", "valid_types": valid_types}
            ),
            400,
        )

    try:
        relationship = ArchiMateRelationship(
            source_id=element_id,
            target_id=data["target_id"],
            type=data["type"],
            properties=data.get("properties", {}),
            architecture_id=app_obj.architecture_model_id,
        )
        db.session.add(relationship)
        db.session.commit()

        return (
            jsonify(
                {
                    "success": True,
                    "relationship": {
                        "id": relationship.id,
                        "type": relationship.type,
                        "source_id": relationship.source_id,
                        "source_name": source.name,
                        "target_id": relationship.target_id,
                        "target_name": target.name,
                        "properties": relationship.properties,
                    },
                }
            ),
            201,
        )

    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Relationship already exists or constraint violation"}), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error creating relationship")
        return jsonify({"error": "An internal error occurred"}), 500


# ============================================================================
# END OF TEMPLATE ROUTES - ALL 7 PHASES IMPLEMENTED
# ============================================================================

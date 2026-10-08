"""
Business Layer Routes — sub-module extracted from routes.py (BE-054 wave-7).

Handles ArchiMate business-layer element management:
  * Inline CRUD (business-actors, roles, processes, functions) via helpers
  * Form-based linking of BusinessActor, BusinessRole, BusinessProcess,
    BusinessFunction, BusinessService, BusinessObject to ApplicationComponents
  * Application-to-Process Linking API (CRUD + semantic matching)
"""

from app.application_mgmt.routes import _link_application_to_processes
import logging
from datetime import datetime

from flask import current_app, flash, jsonify, redirect, request, url_for
from flask_login import login_required

logger = logging.getLogger(__name__)

from .. import db
from ..models.application_portfolio import ApplicationComponent
from ..models.business_capabilities import BusinessFunction
from ..models.business_layer import BusinessActor, BusinessObject, BusinessRole, BusinessService
from ..models.process_data import BusinessProcess
from ..models.relationship_tables import ApplicationBusinessActorMapping, ApplicationProcessSupport
from ..utils.deprecation import deprecated_route
from . import application_mgmt
from .routes import _add_archimate_element, _delete_archimate_element

# --- Business Layer ---
@application_mgmt.route("/applications/<int:app_id>/business-actors", methods=["POST"])
@login_required
def add_business_actor(app_id):
    return _add_archimate_element(app_id, "business", "BusinessActor", request.json)


@application_mgmt.route(
    "/applications/<int:app_id>/business-actors/<int:id>", methods=["DELETE"]
)
@login_required
def delete_business_actor(app_id, id):
    return _delete_archimate_element(id, "realization")


@application_mgmt.route("/applications/<int:app_id>/business-roles", methods=["POST"])
@login_required
def add_business_role(app_id):
    return _add_archimate_element(app_id, "business", "BusinessRole", request.json)


@application_mgmt.route(
    "/applications/<int:app_id>/business-roles/<int:id>", methods=["DELETE"]
)
@login_required
def delete_business_role(app_id, id):
    return _delete_archimate_element(id, "realization")


@application_mgmt.route(
    "/applications/<int:app_id>/business-processes", methods=["POST"]
)
@login_required
def add_business_process(app_id):
    return _add_archimate_element(app_id, "business", "BusinessProcess", request.json)


@application_mgmt.route(
    "/applications/<int:app_id>/business-processes/<int:id>", methods=["DELETE"]
)
@login_required
def delete_business_process(app_id, id):
    return _delete_archimate_element(id, "realization")


@application_mgmt.route(
    "/applications/<int:app_id>/business-functions", methods=["POST"]
)
@login_required
def add_business_function(app_id):
    return _add_archimate_element(app_id, "business", "BusinessFunction", request.json)


@application_mgmt.route(
    "/applications/<int:app_id>/business-functions/<int:id>", methods=["DELETE"]
)
@login_required
def delete_business_function(app_id, id):
    return _delete_archimate_element(id, "realization")
@application_mgmt.route(
    "/applications/<int:id>/business-processes/add", methods=["POST"]
)
@login_required
def application_business_process_add(id):
    """Link existing Business Process to Application (many-to-many)"""
    app = ApplicationComponent.query.get_or_404(id)
    process_id = request.form.get("element_id")

    if not process_id:
        flash("No business process selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    process = BusinessProcess.query.get(process_id)
    if not process:
        flash("Business process not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    # Check if relationship already exists
    existing = ApplicationProcessSupport.query.filter_by(
        application_component_id=app.id, business_process_id=process.id
    ).first()

    if existing:
        flash(
            f'Business process "{process.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            # Create many-to-many relationship
            link = ApplicationProcessSupport(
                application_component_id=app.id,
                business_process_id=process.id,
                support_type="primary_execution",
                criticality="medium",
                is_active=True,
            )
            db.session.add(link)
            db.session.commit()
            flash(f'Business process "{process.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking business process {process_id} to app {id}: {e}"
            )
            flash("Error linking business process. Please try again.", "error")

    return redirect(url_for("unified_applications.application_detail", id=id) + "#business")


@application_mgmt.route("/applications/<int:id>/business-actors/add", methods=["POST"])
@login_required
def application_business_actor_add(id):
    """Link existing Business Actor to Application (many-to-many)"""
    app = ApplicationComponent.query.get_or_404(id)
    actor_id = request.form.get("element_id")

    if not actor_id:
        flash("No business actor selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    actor = BusinessActor.query.get(actor_id)
    if not actor:
        flash("Business actor not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    # Check if relationship already exists
    existing = ApplicationBusinessActorMapping.query.filter_by(
        application_component_id=app.id, business_actor_id=actor.id
    ).first()

    if existing:
        flash(
            f'Business actor "{actor.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            # Create many-to-many relationship
            link = ApplicationBusinessActorMapping(
                application_component_id=app.id,
                business_actor_id=actor.id,
                relationship_type="Primary User",
                usage_frequency="Daily",
            )
            db.session.add(link)
            db.session.commit()
            flash(f'Business actor "{actor.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking business actor {actor_id} to app {id}: {e}"
            )
            flash("Error linking business actor. Please try again.", "error")

    return redirect(url_for("unified_applications.application_detail", id=id) + "#business")


@application_mgmt.route(
    "/applications/<int:id>/business-services/add", methods=["POST"]
)
@login_required
def application_business_service_add(id):
    """Link existing Business Service to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    service_id = request.form.get("element_id")

    if not service_id:
        flash("No business service selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    service = BusinessService.query.get(service_id)
    if not service:
        flash("Business service not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    # Check if already linked to another application
    if service.application_component_id and service.application_component_id != app.id:
        other_app = ApplicationComponent.query.get(service.application_component_id)
        flash(
            f'Business service "{service.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif service.application_component_id == app.id:
        flash(
            f'Business service "{service.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            # Update direct FK
            service.application_component_id = app.id
            db.session.commit()
            flash(f'Business service "{service.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking business service {service_id} to app {id}: {e}"
            )
            flash("Error linking business service. Please try again.", "error")

    return redirect(url_for("unified_applications.application_detail", id=id) + "#business")


@application_mgmt.route("/applications/<int:id>/business-roles/add", methods=["POST"])
@login_required
def application_business_role_add(id):
    """Link existing Business Role to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    role_id = request.form.get("element_id")

    if not role_id:
        flash("No business role selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    role = BusinessRole.query.get(role_id)
    if not role:
        flash("Business role not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    # Check if already linked to another application
    if role.application_component_id and role.application_component_id != app.id:
        other_app = ApplicationComponent.query.get(role.application_component_id)
        flash(
            f'Business role "{role.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif role.application_component_id == app.id:
        flash(
            f'Business role "{role.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            # Update direct FK
            role.application_component_id = app.id
            db.session.commit()
            flash(f'Business role "{role.name}" linked successfully!', "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking business role {role_id} to app {id}: {e}"
            )
            flash("Error linking business role. Please try again.", "error")

    return redirect(url_for("unified_applications.application_detail", id=id) + "#business")

# ============================================================================
# ADD ROUTES - Business Layer (Additional Elements)
# ============================================================================


@application_mgmt.route(
    "/applications/<int:id>/business-functions/add", methods=["POST"]
)
@login_required
def application_business_function_add(id):
    """Link existing Business Function to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    function_id = request.form.get("element_id")

    if not function_id:
        flash("No business function selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    function = BusinessFunction.query.get(function_id)
    if not function:
        flash("Business function not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    if (
        function.application_component_id
        and function.application_component_id != app.id
    ):
        other_app = ApplicationComponent.query.get(function.application_component_id)
        flash(
            f'Business function "{function.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif function.application_component_id == app.id:
        flash(
            f'Business function "{function.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            function.application_component_id = app.id
            db.session.commit()
            flash(
                f'Business function "{function.name}" linked successfully!', "success"
            )
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking business function {function_id} to app {id}: {e}"
            )
            flash("Error linking business function. Please try again.", "error")

    return redirect(url_for("unified_applications.application_detail", id=id) + "#business")


@application_mgmt.route("/applications/<int:id>/business-objects/add", methods=["POST"])
@login_required
def application_business_object_add(id):
    """Link existing Business Object to Application (direct FK)"""
    app = ApplicationComponent.query.get_or_404(id)
    object_id = request.form.get("element_id")

    if not object_id:
        flash("No business object selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    business_obj = BusinessObject.query.get(object_id)
    if not business_obj:
        flash("Business object not found", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    if (
        business_obj.application_component_id
        and business_obj.application_component_id != app.id
    ):
        other_app = ApplicationComponent.query.get(
            business_obj.application_component_id
        )
        flash(
            f'Business object "{business_obj.name}" is already linked to "{other_app.name if other_app else "another application"}"',
            "warning",
        )
    elif business_obj.application_component_id == app.id:
        flash(
            f'Business object "{business_obj.name}" is already linked to this application',
            "warning",
        )
    else:
        try:
            business_obj.application_component_id = app.id
            db.session.commit()
            flash(
                f'Business object "{business_obj.name}" linked successfully!', "success"
            )
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error linking business object {object_id} to app {id}: {e}"
            )
            flash("Error linking business object. Please try again.", "error")

    return redirect(url_for("unified_applications.application_detail", id=id) + "#business")

# ============================================================================
# Application-to-Process Linking API Routes
# ============================================================================


def _suggest_process_links_semantic(app, confidence_threshold=0.3):
    """Semantic process-link suggestions are not implemented; return none so
    the endpoint degrades gracefully instead of raising NameError."""
    return []


@application_mgmt.route(
    "/api/applications/<int:app_id>/process-suggestions", methods=["GET"]
)
@login_required
def get_process_suggestions(app_id):
    """
    Get suggested PCF process links for an application using semantic matching.

    Returns top process matches based on name/description similarity.
    """
    app = ApplicationComponent.query.get_or_404(app_id)
    confidence_threshold = float(request.args.get("threshold", 0.3))

    suggestions = _suggest_process_links_semantic(app, confidence_threshold)

    # Filter out 'process' object to make JSON serializable
    for s in suggestions:
        s.pop("process", None)

    return jsonify(
        {
            "success": True,
            "application_id": app_id,
            "application_name": app.name,
            "suggestions": suggestions,
            "count": len(suggestions),
        }
    )


@application_mgmt.route(
    "/api/applications/<int:app_id>/process-links", methods=["POST"]
)
@login_required
def create_process_link(app_id):
    """
    Create a link between an application and a PCF process.

    Request body:
    {
        "process_id": 123,
        "support_type": "primary_execution",  // optional
        "automation_level": 75,  // optional (0 - 100)
        "criticality": "high"  // optional
    }
    """
    from ..models.process_data import BusinessProcess
    from ..models.relationship_tables import ApplicationProcessSupport

    ApplicationComponent.query.get_or_404(app_id)
    data = request.get_json()

    if not data or "process_id" not in data:
        return jsonify({"error": "process_id is required"}), 400

    process = BusinessProcess.query.get(data["process_id"])
    if not process:
        return jsonify({"error": "Process not found"}), 404

    # Check if link already exists
    existing = ApplicationProcessSupport.query.filter_by(
        application_component_id=app_id, business_process_id=process.id
    ).first()

    if existing:
        return jsonify(
            {"error": "Link already exists", "existing_id": existing.id}
        ), 409

    # Create new link
    mapping = ApplicationProcessSupport(
        application_component_id=app_id,
        business_process_id=process.id,
        support_type=data.get("support_type", "primary_execution"),
        automation_level=data.get("automation_level", 50),
        criticality=data.get("criticality", "medium"),
        is_active=True,
        created_date=datetime.utcnow(),
        notes=data.get("notes", "Manually linked"),
    )
    db.session.add(mapping)
    db.session.commit()

    return (
        jsonify(
            {
                "success": True,
                "mapping_id": mapping.id,
                "application_id": app_id,
                "process_id": process.id,
                "process_name": process.name,
            }
        ),
        201,
    )


@application_mgmt.route(
    "/api/applications/<int:app_id>/process-links/<int:link_id>", methods=["DELETE"]
)
@login_required
def delete_process_link(app_id, link_id):
    """Delete a process link."""
    from ..models.relationship_tables import ApplicationProcessSupport

    # The link carries no organisation of its own; the application does.
    ApplicationComponent.query.get_or_404(app_id)
    link = ApplicationProcessSupport.query.filter_by(
        id=link_id, application_component_id=app_id
    ).first_or_404()

    db.session.delete(link)
    db.session.commit()

    return jsonify({"success": True, "deleted_id": link_id})


@application_mgmt.route("/api/applications/<int:app_id>/process-links", methods=["GET"])
@login_required
@deprecated_route(
    canonical_endpoint="application_api.api_process_links",
    deprecation_date="2026-02-10",
    migration_guide="Use GET /api/applications/<app_id>/process-links from application_api blueprint instead",
)
def get_process_links(app_id):
    """Get all process links for an application."""
    from ..models.process_data import BusinessProcess
    from ..models.relationship_tables import ApplicationProcessSupport

    app = ApplicationComponent.query.get_or_404(app_id)

    links = ApplicationProcessSupport.query.filter_by(
        application_component_id=app_id, is_active=True
    ).all()

    result = []
    for link in links:
        process = BusinessProcess.query.get(link.business_process_id)
        if process:
            result.append(
                {
                    "link_id": link.id,
                    "process_id": process.id,
                    "process_name": process.name,
                    "process_code": process.process_code,
                    "process_type": process.process_type,
                    "process_level": process.level,
                    "support_type": link.support_type,
                    "automation_level": link.automation_level,
                    "criticality": link.criticality,
                    "created_date": link.created_date.isoformat()
                    if link.created_date
                    else None,
                    "notes": link.notes,
                }
            )

    return jsonify(
        {
            "success": True,
            "application_id": app_id,
            "application_name": app.name,
            "process_links": result,
            "count": len(result),
        }
    )


@application_mgmt.route("/api/applications/bulk-process-link", methods=["POST"])
@login_required
@deprecated_route(
    canonical_endpoint="application_api.api_bulk_process_link",
    deprecation_date="2026-02-10",
    migration_guide="Use POST /api/applications/bulk-process-link from application_api blueprint instead",
)
def bulk_process_link():
    """
    Bulk link applications to processes using semantic matching.

    Request body:
    {
        "application_ids": [1, 2, 3],  // or "all" for all apps
        "auto_link": true,  // if true, automatically create links above threshold
        "confidence_threshold": 0.5,  // minimum confidence to auto-link
        "dry_run": false  // if true, only return suggestions without creating links
    }
    """
    from ..models.relationship_tables import ApplicationProcessSupport

    data = request.get_json() or {}

    app_ids = data.get("application_ids", [])
    auto_link = data.get("auto_link", False)
    try:
        confidence_threshold = float(data.get("confidence_threshold", 0.5))
    except (ValueError, TypeError):
        return jsonify({"error": "confidence_threshold must be a number between 0 and 1"}), 400
    if not 0 <= confidence_threshold <= 1:
        return jsonify({"error": "confidence_threshold must be between 0 and 1"}), 400
    dry_run = data.get("dry_run", True)

    if app_ids == "all":
        apps = ApplicationComponent.query.limit(
            1000
        ).all()  # Limit to prevent OOM on large datasets
    else:
        apps = ApplicationComponent.query.filter(
            ApplicationComponent.id.in_(app_ids)
        ).all()

    results = []
    total_links_created = 0

    # Prefetch all existing ApplicationProcessSupport mappings to avoid N+1
    all_app_ids = [app.id for app in apps]
    existing_mappings_list = ApplicationProcessSupport.query.filter(
        ApplicationProcessSupport.application_component_id.in_(all_app_ids)
    ).all()
    existing_mappings = {
        (m.application_component_id, m.business_process_id): m
        for m in existing_mappings_list
    }

    for app in apps:
        suggestions = _suggest_process_links_semantic(app, confidence_threshold)

        app_result = {
            "application_id": app.id,
            "application_name": app.name,
            "suggestions": [],
            "links_created": 0,
        }

        for suggestion in suggestions:
            process = suggestion.pop("process", None)
            app_result["suggestions"].append(suggestion)

            if auto_link and not dry_run and process:
                # Check if link already exists using prefetched data
                existing = existing_mappings.get((app.id, process.id))

                if not existing:
                    mapping = ApplicationProcessSupport(
                        application_component_id=app.id,
                        business_process_id=process.id,
                        support_type="primary_execution",
                        automation_level=50,
                        criticality="medium",
                        is_active=True,
                        created_date=datetime.utcnow(),
                        notes=f"Auto-linked via semantic matching (confidence: {suggestion['confidence']})",
                    )
                    db.session.add(mapping)
                    app_result["links_created"] += 1
                    total_links_created += 1

        results.append(app_result)

    if not dry_run:
        db.session.commit()

    return jsonify(
        {
            "success": True,
            "dry_run": dry_run,
            "applications_processed": len(apps),
            "total_suggestions": sum(len(r["suggestions"]) for r in results),
            "total_links_created": total_links_created,
            "results": results,
        }
    )



@application_mgmt.route("/applications/semantic-mapping", methods=["POST"])
@login_required
def semantic_process_mapping():
    """
    Enhanced APQC process mapping using existing SemanticAPQCService.

    Uses vector embeddings and LLM to map free-text descriptions
    to APQC PCF processes with confidence scores.

    Request Body:
    {
        "description": "Free text description of business processes",
        "threshold": 0.7,  // Optional similarity threshold
        "max_results": 10   // Optional maximum number of results
    }
    """
    from ..services.semantic_apqc_service import SemanticAPQCService

    data = request.get_json() or {}

    description = data.get("description", "")
    data.get("threshold", 0.7)
    max_results = data.get("max_results", 10)

    if not description:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "No description provided",
                    "message": "Please provide description in the request body",
                }
            ),
            400,
        )

    try:
        # Use existing SemanticAPQCService
        semantic_service = SemanticAPQCService()

        # Perform semantic classification (use sync method)
        classification_result = semantic_service.classify_text_sync(
            description, max_results=max_results
        )

        # Handle different result types from different APQC services
        matches_list = []
        if classification_result:
            if hasattr(classification_result, "matches"):
                # APQCClassificationResult object (from semantic service)
                matches_list = classification_result.matches
            elif isinstance(classification_result, list):
                # Raw list of matches (from other services)
                matches_list = classification_result

        # Format results
        matches = []
        for match in matches_list:
            # Handle both dataclass objects and dictionaries
            if hasattr(match, "process_id"):
                # APQCMatch dataclass object
                matches.append(
                    {
                        "process_id": match.process_id,
                        "process_code": match.process_code,
                        "process_name": match.process_name,
                        "level": match.level,
                        "category_level_1": match.category_level_1,
                        "category_level_2": match.category_level_2,
                        "similarity_score": match.similarity_score,
                        "match_method": match.match_method,
                        "confidence": match.confidence,
                        "source": "semantic_similarity",  # Add source for UI compatibility
                    }
                )
            else:
                # Dictionary object
                matches.append(
                    {
                        "process_id": match.get("process_id"),
                        "process_code": match.get("process_code"),
                        "process_name": match.get("process_name"),
                        "level": match.get("level"),
                        "category_level_1": match.get("category_level_1"),
                        "category_level_2": match.get("category_level_2"),
                        "similarity_score": match.get("similarity_score"),
                        "match_method": match.get("match_method"),
                        "confidence": match.get("confidence"),
                    }
                )

        return jsonify(
            {
                "success": True,
                "input_text": description,
                "primary_category": getattr(
                    classification_result, "primary_category", None
                ),
                "matches": matches,
                "processing_time_ms": getattr(
                    classification_result, "processing_time_ms", 0
                ),
                "model_used": getattr(
                    classification_result, "model_used", "semantic_apqc"
                ),
                "total_candidates_evaluated": getattr(
                    classification_result, "total_candidates_evaluated", len(matches)
                ),
                "generated_at": datetime.utcnow().isoformat(),
            }
        )

    except Exception as e:
        logger.error(f"Error in semantic process mapping: {e}")
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
    "/api/applications/<int:app_id>/link-from-text", methods=["POST"]
)
@login_required
def link_processes_from_text(app_id):
    """
    Link an application to processes from a text string (functional capabilities field).

    Request body:
    {
        "functional_capabilities": "Order Management, Inventory Control, Customer Service"
    }
    """
    app = ApplicationComponent.query.get_or_404(app_id)
    data = request.get_json() or {}

    functional_capabilities = data.get("functional_capabilities", "")

    if not functional_capabilities:
        return jsonify({"error": "functional_capabilities is required"}), 400

    result = _link_application_to_processes(app, functional_capabilities)
    db.session.commit()

    return jsonify(
        {
            "success": True,
            "application_id": app_id,
            "application_name": app.name,
            "linked": result["linked"],
            "not_found": result["not_found"],
        }
    )

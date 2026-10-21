"""Template Selection Routes for Framework-Based Element Population.

These routes provide API endpoints for:
- Browsing available element templates
- Getting template recommendations
- Instantiating templates for applications
- Managing template usage

SECURITY:
- All mutation endpoints require authentication (@login_required)
- Rate limiting applied to prevent DoS attacks (10 requests/minute)
- Input validation (max 100 templates per bulk operation)
- Specific error handling for database constraints
"""

from flask import current_app, flash, jsonify, redirect, request, url_for
from werkzeug.exceptions import HTTPException
from flask_login import current_user, login_required
from marshmallow import ValidationError
from sqlalchemy import func
from sqlalchemy.exc import DatabaseError, IntegrityError, OperationalError

from app import db
from app.application_mgmt import application_mgmt
from app.factories.domain_model_factory import DomainModelFactory
from app.models.application_portfolio import ApplicationComponent
from app.models.element_templates import ElementTemplate
from app.repositories.template_repository import (
    ElementTemplateRepository,
    ElementTemplateUsageRepository,
)
from app.schemas.template_schemas import (
    BulkInstantiateTemplateSchema,
    InstantiateTemplateSchema,
    RemoveTemplateUsageSchema,
    TemplateQuerySchema,
)
from app.services.archimate_validation_service import ArchiMateValidationService
from app.services.rate_limiter import RateLimitExceeded, rate_limit
from app.services.template_instantiation_service import TemplateInstantiationService
from app.services.template_performance_optimizer import template_optimizer
from app.utils.pagination import safe_int_arg
from app.models.archimate_element_types import application_kind_types

# ============================================================================
# SECURITY CONFIGURATION
# ============================================================================
MAX_BULK_TEMPLATES = 100  # Maximum templates per bulk operation (DoS prevention)
RATE_LIMIT_INSTANTIATE = (10, "1m")  # 10 requests per minute for instantiation
RATE_LIMIT_LINK = (10, "1m")  # 10 requests per minute for linking


@application_mgmt.route("/api/templates", methods=["GET"])
@login_required
def get_templates():
    """
    Get available element templates with optional filtering.

    Performance optimizations:
    - Response cached for 5 minutes
    - Eager loading of relationships
    - Pagination with configurable limit

    Query params:
        - framework: Filter by framework (PCF, ITIL, COBIT, etc.)
        - element_type: Filter by ArchiMate element type
        - category: Filter by framework category
        - search: Search term for name/description/keywords
        - application_type: Filter by relevant application types
        - limit: Max results (default 100)
        - offset: Offset for pagination (default 0)

    Returns:
        JSON array of template objects
    """
    # Use performance-optimized query
    framework = request.args.get("framework")
    layer = request.args.get("layer")
    archimate_type = request.args.get("element_type")

    try:
        templates = template_optimizer.get_all_templates_optimized(
            framework=framework, layer=layer, archimate_type=archimate_type
        )
        return jsonify(templates)
    except Exception as e:
        current_app.logger.error(f"Error fetching templates: {str(e)}")
        return jsonify({"error": "Failed to fetch templates"}), 500


@application_mgmt.route("/api/templates/frameworks", methods=["GET"])
@login_required
def get_frameworks():
    """
    Get list of available frameworks.

    Returns:
        JSON array of active framework names, including [] for an empty catalog.
    """
    frameworks = ElementTemplate.get_frameworks()

    # Match the applications API: an empty collection is a successful query.
    return jsonify(frameworks)


# ============================================================================
# PHASE 3: ENTERPRISE SEARCH & FILTER IMPROVEMENTS
# ============================================================================


@application_mgmt.route("/api/templates/search-advanced", methods=["POST"])
@login_required
def search_templates_advanced():
    """
    Advanced template search with enterprise features.

    ENTERPRISE FEATURES:
    - Fuzzy matching (typo tolerance)
    - Multi-field search (name, description, keywords, tags)
    - Relevance scoring
    - Search result highlighting
    - Recently used templates boost
    - Application-specific recommendations

    Request body:
        {
            "query": "customer api",
            "fuzzy": true,  // Enable fuzzy matching
            "boost_recent": true,  // Boost recently used templates
            "application_id": 123,  // For personalized recommendations
            "frameworks": ["PCF", "TOGAF"],  // Multi-select
            "element_types": ["ApplicationComponent", "ApplicationInterface"],
            "tags": ["microservice", "rest-api"],  // Tag-based filtering
            "limit": 20
        }

    Returns:
        JSON with ranked results and metadata
    """
    data = request.get_json() or {}
    query = data.get("query", "").strip()

    if not query:
        return jsonify({"error": "query parameter required"}), 400

    # Build advanced search query
    base_query = ElementTemplate.query

    # Multi-framework filter
    frameworks = data.get("frameworks", [])
    if frameworks:
        base_query = base_query.filter(ElementTemplate.framework.in_(frameworks))

    # Multi-element-type filter
    element_types = data.get("element_types", [])
    if element_types:
        base_query = base_query.filter(ElementTemplate.element_type.in_(element_types))

    # Tag-based filtering (if tags column exists)
    tags = data.get("tags", [])
    if tags and hasattr(ElementTemplate, "tags"):
        for tag in tags:
            base_query = base_query.filter(ElementTemplate.tags.contains([tag]))

    # Imported here rather than inside the `if` below: or_ is used by BOTH
    # branches, but the import only ran in the fuzzy one, so the else branch -
    # which is the DEFAULT, since fuzzy defaults to False - raised
    # NameError: name 'or_' is not defined and returned a 500.
    from sqlalchemy import func, or_  # noqa: F401  (func used by the fuzzy branch)

    # Fuzzy search across multiple fields
    if data.get("fuzzy", False):
        # PostgreSQL-compatible fuzzy search using similarity

        search_pattern = f"%{query}%"
        base_query = base_query.filter(
            or_(
                ElementTemplate.name.ilike(search_pattern),
                ElementTemplate.description.ilike(search_pattern),
                ElementTemplate.keywords.ilike(search_pattern),
            )
        )
    else:
        # Exact match
        base_query = base_query.filter(
            or_(
                ElementTemplate.name.contains(query),
                ElementTemplate.description.contains(query),
                ElementTemplate.keywords.contains(query),
            )
        )

    # Execute query
    try:
        limit = min(int(data.get("limit", 20)), 100)
    except (ValueError, TypeError):
        return jsonify({"error": "limit must be an integer"}), 400
    results = base_query.limit(limit).all()

    # Calculate relevance scores
    scored_results = []
    for template in results:
        score = 0

        # Name match = highest score
        if query.lower() in template.name.lower():
            score += 10

        # Description match = medium score
        if template.description and query.lower() in template.description.lower():
            score += 5

        # Keywords match = medium score
        if template.keywords and query.lower() in template.keywords.lower():
            score += 5

        # Boost recent templates (if requested)
        if data.get("boost_recent", False) and hasattr(template, "last_used_at"):
            if template.last_used_at:
                from datetime import datetime

                days_old = (datetime.utcnow() - template.last_used_at).days
                if days_old < 30:
                    score += 3

        scored_results.append({"template": template.to_dict(), "relevance_score": score})

    # Sort by relevance
    scored_results.sort(key=lambda x: x["relevance_score"], reverse=True)

    return jsonify(
        {"query": query, "total_results": len(scored_results), "results": scored_results}
    )


@application_mgmt.route("/api/templates/tags", methods=["GET"])
@login_required
def get_all_tags():
    """
    Get all unique tags from templates.

    ENTERPRISE FEATURE: Tag cloud for faceted search

    Returns:
        JSON array of {tag, count} objects
    """
    # Aggregate tags if column exists
    if not hasattr(ElementTemplate, "tags"):
        return jsonify([])


    # Get all unique tags with counts
    # Note: This assumes tags are stored as JSON array or comma-separated
    templates = ElementTemplate.query.all()
    tag_counts = {}

    for template in templates:
        if template.tags:
            # Handle JSON array or comma-separated string
            tags = template.tags if isinstance(template.tags, list) else template.tags.split(",")
            for tag in tags:
                tag = tag.strip()
                if tag:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1

    # Format as array sorted by popularity
    result = [{"tag": tag, "count": count} for tag, count in tag_counts.items()]
    result.sort(key=lambda x: x["count"], reverse=True)

    return jsonify(result)


@application_mgmt.route("/api/templates/categories", methods=["GET"])
@login_required
def get_categories():
    """
    Get categories for a specific framework.

    Query params:
        - framework: Framework name (required)

    Returns:
        JSON array of category names
    """
    framework = request.args.get("framework")
    if not framework:
        return jsonify({"error": "framework parameter required"}), 400

    categories = ElementTemplate.get_categories(framework)
    return jsonify([c[0] for c in categories if c[0]])


@application_mgmt.route("/api/templates/element-types", methods=["GET"])
@login_required
def get_element_types():
    """
    Get element types for a specific layer.

    Query params:
        - layer: ArchiMate layer (motivation, strategy, business, application, technology, physical, implementation)

    Returns:
        JSON array of element type names
    """
    layer = request.args.get("layer")
    if not layer:
        return jsonify({"error": "layer parameter required"}), 400

    # Get distinct element types for this layer (case-insensitive)
    types = (
        db.session.query(ElementTemplate.element_type)
        .filter(
            func.lower(ElementTemplate.layer) == layer.lower(), ElementTemplate.is_active == True
        )
        .distinct()
        .order_by(ElementTemplate.element_type)
        .all()
    )

    return jsonify([t[0] for t in types if t[0]])


@application_mgmt.route(
    "/api/applications/<string:app_id>/templates/recommendations", methods=["GET"]
)
@login_required
def get_template_recommendations(app_id):
    """
    Get recommended templates for an application.

    Returns:
        JSON array of recommended template objects
    """
    limit = safe_int_arg('limit', 20, minimum=1, maximum=500)

    # Instantiate service with dependencies
    template_repo = ElementTemplateRepository()
    usage_repo = ElementTemplateUsageRepository()
    domain_factory = DomainModelFactory()
    service = TemplateInstantiationService(template_repo, usage_repo, domain_factory)

    try:
        recommendations = service.get_recommended_templates(application_id=app_id, limit=limit)
        return jsonify([t.to_dict() for t in recommendations])
    except Exception as e:
        current_app.logger.error(f"Error fetching recommendations: {str(e)}")
        return jsonify({"error": "Failed to fetch recommendations"}), 500


@application_mgmt.route("/api/applications/<string:app_id>/templates/instantiate", methods=["POST"])
@login_required  # SECURITY: Authentication required
@rate_limit(*RATE_LIMIT_INSTANTIATE)  # SECURITY: Rate limiting (10 req/min)
def instantiate_template(app_id):
    """
    Instantiate one or more templates for an application.

    SECURITY: Requires authentication and rate limiting.

    Request body:
        {
            "template_ids": [1, 2, 3],  # Array of template IDs (max 100)
            "customizations": {  # Optional per-template customizations
                "1": {"name": "Custom Name", "description": "Custom desc"}
            },
            "create_relationships": true  # Optional, default true
        }

    Returns:
        JSON with success/error status and created elements
    """
    data = request.get_json()
    current_app.logger.info(f"[INSTANTIATE] Received data: {data}")

    if not data:
        return jsonify({"error": "Request body required"}), 400

    # COMPATIBILITY: Handle both old and new frontend formats
    # New format: {templates: [{template_id: 1, name: "...", description: "..."}]}
    # Old format: {template_ids: [1, 2, 3], customizations: {...}}
    if "templates" in data and isinstance(data["templates"], list):
        # Convert new format to old format
        template_ids = []
        customizations = {}
        for t in data["templates"]:
            tid = str(t["template_id"])
            template_ids.append(tid)
            # Only add non-empty customizations
            custom = {}
            if t.get("name"):
                custom["name"] = t["name"]
            if t.get("description"):
                custom["description"] = t["description"]
            if custom:
                customizations[tid] = custom
        data = {
            "template_ids": template_ids,
            "customizations": customizations,
            "create_relationships": data.get("create_relationships", True),
        }
    elif "template_id" in data and "template_ids" not in data:
        # Normalize single template_id to array
        data["template_ids"] = [data["template_id"]]

    # Instantiate service with dependencies
    template_repo = ElementTemplateRepository()
    usage_repo = ElementTemplateUsageRepository()
    domain_factory = DomainModelFactory()
    service = TemplateInstantiationService(template_repo, usage_repo, domain_factory)

    try:
        template_ids = data.get("template_ids", [])
        if not isinstance(template_ids, list):
            template_ids = [template_ids]

        # SECURITY: Validate input
        if not template_ids:
            return jsonify({"error": "At least one template_id required"}), 400

        if len(template_ids) > MAX_BULK_TEMPLATES:
            return (
                jsonify(
                    {
                        "error": f"Maximum {MAX_BULK_TEMPLATES} templates per request",
                        "details": f"Requested: {len(template_ids)}, Maximum: {MAX_BULK_TEMPLATES}",
                    }
                ),
                400,
            )

        if len(template_ids) == 1:
            # Single instantiation with validation
            schema = InstantiateTemplateSchema()
            # Extract customization for this specific template (if any)
            all_customizations = data.get("customizations", {})
            single_custom = (
                all_customizations.get(str(template_ids[0])) if all_customizations else None
            )

            validated_data = schema.load(
                {
                    "template_id": str(template_ids[0]),  # Keep as string for schema validation
                    "application_id": app_id,
                    "customizations": single_custom,
                    "create_relationships": data.get("create_relationships", True),
                }
            )

            # Convert string ID to integer after validation
            validated_data["template_id"] = int(validated_data["template_id"])

            # ENTERPRISE ENHANCEMENT: Create session for single instantiation
            from app.models.architecture_session import ArchitectureSession

            session = ArchitectureSession(
                application_id=app_id,
                user_id=current_user.id,
                operation_type="add_template_single",
                operation_description=f"Added single template {validated_data['template_id']}",
            )
            db.session.add(session)
            db.session.flush()  # Get session ID

            validated_data["session_id"] = session.id

            element, model = service.instantiate_template(**validated_data)

            db.session.commit()  # Commit session + element

            return jsonify(
                {
                    "success": True,
                    "session_id": session.id,
                    "element": {
                        "id": element.id,
                        "name": element.name,
                        "type": element.type,
                        "layer": element.layer,
                    },
                    "message": "Successfully instantiated template",
                }
            )
        else:
            # Bulk instantiation with validation
            schema = BulkInstantiateTemplateSchema()
            validated_data = schema.load(
                {
                    "template_ids": [
                        str(tid) for tid in template_ids
                    ],  # Keep as strings for schema validation
                    "create_relationships": data.get("create_relationships", True),
                }
            )

            # Convert string IDs to integers after validation
            validated_data["template_ids"] = [int(tid) for tid in validated_data["template_ids"]]

            # Add application_id from URL (not from schema)
            validated_data["application_id"] = app_id

            # ENTERPRISE ENHANCEMENT: Create session for bulk operation
            from app.models.architecture_session import ArchitectureSession

            session = ArchitectureSession(
                application_id=app_id,
                user_id=current_user.id,
                operation_type="add_templates_bulk",
                operation_description=f"Added {len(template_ids)} templates from framework",
                template_count=len(template_ids),
            )
            db.session.add(session)
            db.session.flush()  # Get session ID

            validated_data["session_id"] = session.id

            results, errors = service.instantiate_bulk(**validated_data)

            db.session.commit()  # Commit session + all elements

            response = {
                "success": len(errors) == 0,
                "session_id": session.id,
                "count": len(results),
                "elements": [
                    {"id": elem.id, "name": elem.name, "type": elem.type, "layer": elem.layer}
                    for elem, model in results
                ],
                "message": f"Successfully instantiated {len(results)} templates",
            }

            if errors:
                response["errors"] = errors
                response["message"] += f" ({len(errors)} failed)"

            return jsonify(response), 200 if len(errors) == 0 else 207  # 207 Multi-Status

    except ValidationError as e:
        current_app.logger.error(f"[INSTANTIATE] Validation error: {e.messages}")
        return jsonify({"error": "Validation failed", "details": e.messages}), 400
    except ValueError as e:
        current_app.logger.error(f"[INSTANTIATE] ValueError: {str(e)}")
        return jsonify({"error": "Invalid request parameters"}), 400
    except RateLimitExceeded as e:
        return (
            jsonify(
                {
                    "error": "Rate limit exceeded",
                    "limit": e.limit,
                    "window": e.window,
                    "retry_after": e.retry_after,
                }
            ),
            429,
        )
    except IntegrityError as e:
        db.session.rollback()
        current_app.logger.warning(f"Database integrity error: {str(e.orig)}")
        # Check for specific constraint violations
        if "uq_template_application" in str(e.orig):
            return jsonify({"error": "Template already added to this application"}), 409
        elif "foreign key" in str(e.orig).lower():
            return jsonify({"error": "Referenced template or application not found"}), 404
        return jsonify({"error": "Database constraint violation"}), 500
    except OperationalError as e:
        db.session.rollback()
        current_app.logger.error(f"Database operational error: {str(e)}")
        return jsonify({"error": "Database operation failed. Please try again."}), 503
    except DatabaseError as e:
        db.session.rollback()
        current_app.logger.error(f"Database error: {str(e)}")
        return jsonify({"error": "Database error occurred"}), 500
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Unexpected error instantiating template")
        return jsonify({"error": "Internal server error"}), 500


@application_mgmt.route(
    "/api/applications/<string:app_id>/elements/check-duplicate", methods=["GET"]
)
@login_required
def check_duplicate_element(app_id):
    """
    Check if an element with the given name already exists in the application.

    Query Parameters:
        name (str): Element name to check

    Returns:
        JSON: { "exists": true/false, "element_id": <id if exists> }
    """
    from app.models import ArchiMateElement

    name = request.args.get("name", "").strip()
    if not name:
        return jsonify({"exists": False}), 200

    # Check if element with this name exists for this application
    # We check across all ArchiMate element types (stakeholders, capabilities, etc.)
    existing = ArchiMateElement.query.filter(
        ArchiMateElement.application_component_id == app_id,
        ArchiMateElement.name.ilike(name),  # Case-insensitive comparison
    ).first()

    if existing:
        return (
            jsonify(
                {
                    "exists": True,
                    "element_id": existing.id,
                    "element_type": existing.__class__.__name__,
                }
            ),
            200,
        )

    return jsonify({"exists": False}), 200


@application_mgmt.route("/api/applications/<string:app_id>/templates/link", methods=["POST"])
@login_required  # SECURITY: Authentication required
@rate_limit(*RATE_LIMIT_LINK)  # SECURITY: Rate limiting (10 req/min)
def link_template_elements(app_id):
    """
    Link application to existing framework elements without creating duplicates.

    SECURITY: Requires authentication and rate limiting.

    Creates relationships between the application and elements that were already
    instantiated from templates (either by this app or others).

    Request body:
        {
            "template_ids": [1, 2, 3],  # Max 100
            "relationship_type": "realization"  # ArchiMate relationship type
        }

    Returns:
        JSON with success status and relationships created
    """
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.models.element_templates import ElementTemplateUsage

    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body required"}), 400

    template_ids = data.get("template_ids", [])
    relationship_type = data.get("relationship_type", "realization")

    if not template_ids:
        return jsonify({"error": "template_ids required"}), 400

    # SECURITY: Validate array length to prevent DoS attacks
    if len(template_ids) > MAX_BULK_TEMPLATES:
        return (
            jsonify(
                {
                    "error": f"Maximum {MAX_BULK_TEMPLATES} templates per request",
                    "details": f"Requested: {len(template_ids)}, Maximum: {MAX_BULK_TEMPLATES}",
                }
            ),
            400,
        )

    # Get application
    app = ApplicationComponent.query.get_or_404(app_id)

    # Get the application's ArchiMate element
    app_element = ArchiMateElement.query.filter(
        ArchiMateElement.name == app.name,
        ArchiMateElement.type.in_(application_kind_types()),
    ).first()

    if not app_element:
        return jsonify({"error": "Application ArchiMate element not found"}), 404

    relationships_created = []
    errors = []

    for template_id in template_ids:
        try:
            template_id = int(template_id)

            # Check if already linked
            existing_usage = ElementTemplateUsage.query.filter_by(
                application_id=app_id, template_id=template_id, link_only=True
            ).first()

            if existing_usage:
                errors.append(
                    {"template_id": template_id, "error": "Already linked to this application"}
                )
                continue

            # Find existing element created from this template
            # Look for any element with this template_id in usage table
            any_usage = ElementTemplateUsage.query.filter_by(
                template_id=template_id, link_only=False
            ).first()

            if not any_usage:
                errors.append(
                    {
                        "template_id": template_id,
                        "error": "No existing element found for this template",
                    }
                )
                continue

            target_element = any_usage.element

            # Check if relationship already exists
            existing_rel = ArchiMateRelationship.query.filter_by(
                source_element_id=app_element.id,
                target_element_id=target_element.id,
                relationship_type=relationship_type,
            ).first()

            if existing_rel:
                # Just record the link usage
                link_usage = ElementTemplateUsage(
                    template_id=template_id,
                    application_id=app_id,
                    archimate_element_id=target_element.id,
                    link_only=True,
                    instantiated_by=current_user.id,
                    created_by_id=current_user.id,
                )
                db.session.add(link_usage)
                relationships_created.append(
                    {
                        "template_id": template_id,
                        "relationship_id": existing_rel.id,
                        "element_name": target_element.name,
                        "status": "already_exists",
                    }
                )
            else:
                # Validate relationship according to ArchiMate 3.2 metamodel
                is_valid, error_msg, rule = ArchiMateValidationService.validate_and_log(
                    source_element_id=app_element.id,
                    target_element_id=target_element.id,
                    relationship_type=relationship_type,
                    user_id=current_user.id if hasattr(current_user, "id") else None,
                    severity="warning",
                )

                if not is_valid:
                    errors.append(
                        {"template_id": template_id, "error": f"Invalid relationship: {error_msg}"}
                    )
                    continue

                # Create new relationship
                relationship = ArchiMateRelationship(
                    source_element_id=app_element.id,
                    target_element_id=target_element.id,
                    relationship_type=relationship_type,
                    architecture_id=app_element.architecture_id,
                )
                db.session.add(relationship)
                db.session.flush()

                # Record link usage
                link_usage = ElementTemplateUsage(
                    template_id=template_id,
                    application_id=app_id,
                    archimate_element_id=target_element.id,
                    link_only=True,
                    instantiated_by=current_user.id,
                    created_by_id=current_user.id,
                )
                db.session.add(link_usage)

                relationships_created.append(
                    {
                        "template_id": template_id,
                        "relationship_id": relationship.id,
                        "element_name": target_element.name,
                        "status": "created",
                    }
                )

        except Exception as e:
            current_app.logger.error(f"Error linking template {template_id}: {str(e)}")
            errors.append({"template_id": template_id, "error": str(e)})

    try:
        db.session.commit()
    except RateLimitExceeded as e:
        return (
            jsonify(
                {
                    "error": "Rate limit exceeded",
                    "limit": e.limit,
                    "window": e.window,
                    "retry_after": e.retry_after,
                }
            ),
            429,
        )
    except IntegrityError as e:
        db.session.rollback()
        current_app.logger.warning(f"Database integrity error in link operation: {str(e.orig)}")
        return jsonify({"error": "Database constraint violation"}), 500
    except OperationalError as e:
        db.session.rollback()
        current_app.logger.error(f"Database operational error in link operation: {str(e)}")
        return jsonify({"error": "Database operation failed. Please try again."}), 503
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Unexpected error linking templates")
        return jsonify({"error": "An internal error occurred"}), 500

    response = {
        "success": len(errors) == 0,
        "count": len(relationships_created),
        "relationships": relationships_created,
        "message": f"Successfully linked {len(relationships_created)} elements",
    }

    if errors:
        response["errors"] = errors
        response["message"] += f" ({len(errors)} failed)"

    return jsonify(response), 200 if len(errors) == 0 else 207


@application_mgmt.route(
    "/api/applications/<string:app_id>/templates/<int:template_id>/remove", methods=["POST"]
)
@login_required
def remove_template(app_id, template_id):
    """
    Remove a template instantiation from an application.

    Request body:
        {
            "delete_element": true  # Optional, default true
        }

    Returns:
        JSON with success/error status
    """
    data = request.get_json() or {}

    schema = RemoveTemplateUsageSchema()
    try:
        validated_data = schema.load(
            {
                "application_id": app_id,
                "template_id": template_id,
                "delete_element": data.get("delete_element", True),
            }
        )
    except ValidationError as e:
        return jsonify({"error": "Validation failed", "details": e.messages}), 400

    # Instantiate service with dependencies
    template_repo = ElementTemplateRepository()
    usage_repo = ElementTemplateUsageRepository()
    domain_factory = DomainModelFactory()
    service = TemplateInstantiationService(template_repo, usage_repo, domain_factory)

    try:
        service.remove_template_usage(**validated_data)

        return jsonify({"success": True, "message": "Successfully removed template usage"})

    except ValueError:
        return jsonify({"error": "Resource not found"}), 404
    except Exception as e:
        current_app.logger.error(f"Error removing template: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/applications/<string:app_id>/add-from-template", methods=["POST"])
@login_required
def add_from_template_page(app_id):
    """
    Form-based template instantiation (for non-JS browsers).
    Redirects back to application detail page with flash message.
    """
    template_ids = request.form.getlist("template_ids")

    if not template_ids:
        flash("No templates selected", "error")
        return redirect(url_for("unified_applications.application_detail", id=app_id))

    try:
        # Convert to integers
        template_ids = [int(tid) for tid in template_ids]

        # Instantiate service with dependencies
        template_repo = ElementTemplateRepository()
        usage_repo = ElementTemplateUsageRepository()
        domain_factory = DomainModelFactory()
        service = TemplateInstantiationService(template_repo, usage_repo, domain_factory)

        # Instantiate templates (returns results, errors tuple)
        results, errors = service.instantiate_bulk(
            template_ids=template_ids, application_id=app_id, create_relationships=True
        )

        if results:
            if errors:
                flash(f"Added {len(results)} elements ({len(errors)} failed)", "warning")
            else:
                flash(f"Successfully added {len(results)} elements from templates", "success")
        else:
            flash("No templates were instantiated", "warning")

    except Exception:
        flash("Error adding templates. Please try again.", "error")

    return redirect(url_for("unified_applications.application_detail", id=app_id))


@application_mgmt.route("/api/templates/<int:template_id>", methods=["GET"])
@login_required
def get_template_detail(template_id):
    """
    Get detailed information about a specific template.

    Returns:
        JSON template object with full details
    """
    template = ElementTemplate.query.get_or_404(template_id)

    return jsonify(template.to_dict())


@application_mgmt.route("/api/applications/<string:app_id>/template-usage", methods=["GET"])
@login_required
def get_application_template_usage(app_id):
    """
    Get all templates currently instantiated for an application.

    Returns:
        JSON array of template usage records
    """
    application = ApplicationComponent.query.get_or_404(app_id)

    usage_records = [
        {
            "template_id": usage.template_id,
            "template_name": usage.template.name,
            "template_code": usage.template.code,
            "framework": usage.template.framework,
            "element_id": usage.archimate_element_id,
            "element_name": usage.archimate_element.name if usage.archimate_element else None,
            "instantiated_at": usage.instantiated_at.isoformat() if usage.instantiated_at else None,
        }
        for usage in application.template_usages
    ]

    return jsonify(usage_records)


# ============================================================================
# PHASE 2: ENTERPRISE BULK OPERATIONS & VALIDATION
# ============================================================================


@application_mgmt.route(
    "/api/applications/<string:app_id>/templates/validate-bulk", methods=["POST"]
)
@login_required
def validate_bulk_instantiation(app_id):
    """
    Validate bulk template instantiation before executing.

    ENTERPRISE FEATURE: Pre-flight validation to avoid errors and provide warnings.

    Request body:
        {
            "template_ids": [1, 2, 3, ...]
        }

    Returns:
        JSON validation report with:
        - valid: Templates that can be instantiated
        - invalid: Templates with errors
        - warnings: Potential issues (duplicates, conflicts)
        - estimated_time: Processing time estimate
    """
    data = request.get_json()

    if not data or "template_ids" not in data:
        return jsonify({"error": "template_ids required"}), 400

    template_ids = data["template_ids"]

    if not isinstance(template_ids, list):
        return jsonify({"error": "template_ids must be an array"}), 400

    if len(template_ids) > MAX_BULK_TEMPLATES:
        return jsonify({"error": f"Maximum {MAX_BULK_TEMPLATES} templates allowed"}), 400

    # Instantiate service
    template_repo = ElementTemplateRepository()
    usage_repo = ElementTemplateUsageRepository()
    domain_factory = DomainModelFactory()
    service = TemplateInstantiationService(template_repo, usage_repo, domain_factory)

    try:
        report = service.validate_bulk_instantiation(
            template_ids=[int(tid) for tid in template_ids], application_id=app_id
        )
        return jsonify(report)
    except Exception as e:
        current_app.logger.error(f"Validation error: {str(e)}")
        return jsonify({"error": "Validation failed"}), 500


@application_mgmt.route(
    "/api/applications/<string:app_id>/templates/instantiate-bulk-enterprise", methods=["POST"]
)
@login_required
@rate_limit(5, "1m")  # Stricter rate limit for bulk operations
def instantiate_bulk_enterprise(app_id):
    """
    Enterprise-grade bulk instantiation with advanced features.

    ENTERPRISE FEATURES:
    - Pre-flight validation
    - Batched processing (configurable batch size)
    - Session tracking for undo capability
    - Progress reporting
    - Partial success handling
    - Detailed error reporting

    Request body:
        {
            "template_ids": [1, 2, 3, ...],
            "create_relationships": true,
            "batch_size": 10,  // Optional, default 10
            "dry_run": false   // Optional, validate only without executing
        }

    Returns:
        JSON with detailed results
    """
    data = request.get_json()

    if not data or "template_ids" not in data:
        return jsonify({"error": "template_ids required"}), 400

    template_ids = data["template_ids"]
    create_relationships = data.get("create_relationships", True)
    try:
        batch_size = int(data.get("batch_size", 10))
    except (ValueError, TypeError):
        return jsonify({"error": "batch_size must be an integer"}), 400
    dry_run = data.get("dry_run", False)

    if not isinstance(template_ids, list):
        return jsonify({"error": "template_ids must be an array"}), 400

    if len(template_ids) > MAX_BULK_TEMPLATES:
        return jsonify({"error": f"Maximum {MAX_BULK_TEMPLATES} templates allowed"}), 400

    if batch_size < 1 or batch_size > 50:
        return jsonify({"error": "batch_size must be between 1 and 50"}), 400

    # Instantiate service
    template_repo = ElementTemplateRepository()
    usage_repo = ElementTemplateUsageRepository()
    domain_factory = DomainModelFactory()
    service = TemplateInstantiationService(template_repo, usage_repo, domain_factory)

    try:
        # Step 1: Validate
        validation_report = service.validate_bulk_instantiation(
            template_ids=[int(tid) for tid in template_ids], application_id=app_id
        )

        if dry_run:
            return jsonify({"success": True, "dry_run": True, "validation": validation_report})

        # Step 2: Execute if validation passes
        if validation_report["invalid"]:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Validation failed",
                        "validation": validation_report,
                    }
                ),
                400,
            )

        # Step 3: Instantiate with session tracking
        result = service.instantiate_bulk_with_session(
            template_ids=[int(tid) for tid in validation_report["valid"]],
            application_id=app_id,
            create_relationships=create_relationships,
            batch_size=batch_size,
        )

        result["validation"] = validation_report

        return jsonify(result), 200 if result["success"] else 500

    except RateLimitExceeded as e:
        return jsonify({"error": "Rate limit exceeded", "retry_after": e.retry_after}), 429
    except Exception:
        current_app.logger.exception("Error in bulk instantiation")
        return jsonify({"error": "An internal error occurred"}), 500


# ============================================================================
# ENTERPRISE FEATURE: Architecture Session Management (Undo/Rollback)
# ============================================================================


@application_mgmt.route("/api/applications/<string:app_id>/sessions/history", methods=["GET"])
@login_required
def get_session_history(app_id):
    """
    Get history of architecture sessions for an application.

    ENTERPRISE FEATURE: Shows audit trail of bulk operations for undo capability.

    Query params:
        - limit: Max sessions to return (default 10)
        - include_rolled_back: Whether to include rolled back sessions (default true)

    Returns:
        JSON array of session summaries
    """
    from app.services.session_rollback_service import SessionRollbackService

    limit = safe_int_arg('limit', 10, minimum=1, maximum=500)
    include_rolled_back = request.args.get("include_rolled_back", "true").lower() == "true"

    service = SessionRollbackService()

    try:
        history = service.get_session_history(
            application_id=app_id, limit=limit, include_rolled_back=include_rolled_back
        )
        return jsonify({"success": True, "sessions": history})
    except Exception as e:
        current_app.logger.error(f"Error fetching session history: {str(e)}")
        return jsonify({"error": "Failed to fetch session history"}), 500


@application_mgmt.route("/api/sessions/<int:session_id>/rollback", methods=["POST"])
@login_required
@rate_limit(5, "1m")  # SECURITY: Limit rollback requests
def rollback_session(session_id):
    """
    Rollback an architecture session (undo bulk operation).

    ENTERPRISE FEATURE: Critical safety mechanism for architects.

    Request body:
        {
            "reason": "Optional reason for rollback"
        }

    Returns:
        JSON with rollback results
    """
    from app.services.session_rollback_service import SessionRollbackService

    data = request.get_json() or {}
    reason = data.get("reason", "User-initiated rollback")

    service = SessionRollbackService()

    try:
        result = service.rollback_session(
            session_id=session_id, reason=reason, user_id=current_user.id
        )

        return jsonify(result)

    except ValueError:
        return jsonify({"success": False, "error": "Invalid request parameters"}), 400
    except PermissionError:
        return jsonify({"success": False, "error": "Forbidden"}), 403
    except RuntimeError:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500
    except Exception:
        current_app.logger.exception("Unexpected error in rollback")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@application_mgmt.route("/api/applications/<string:app_id>/sessions/latest", methods=["GET"])
@login_required
def get_latest_session(app_id):
    """
    Get the most recent architecture session for an application.

    Returns:
        JSON session object or null if none exists
    """
    from app.services.session_rollback_service import SessionRollbackService

    service = SessionRollbackService()

    try:
        session = service.get_latest_session(app_id)

        if not session:
            return jsonify({"session": None})

        return jsonify({"success": True, "session": session.to_dict()})
    except Exception as e:
        current_app.logger.error(f"Error fetching latest session: {str(e)}")
        return jsonify({"error": "Failed to fetch session"}), 500


@application_mgmt.route("/api/sessions/<int:session_id>/can-rollback", methods=["GET"])
@login_required
def check_can_rollback(session_id):
    """
    Check if a session can be rolled back.

    Returns:
        JSON with can_rollback flag and reason
    """
    from app.models.architecture_session import ArchitectureSession
    from app.services.session_rollback_service import SessionRollbackService
    from app.utils.route_guards import require_entity

    # A rollback verdict for a session that does not exist is invented data.
    require_entity(ArchitectureSession, session_id, description="Session not found")

    service = SessionRollbackService()

    try:
        result = service.can_rollback_session(session_id)
        return jsonify(result)
    except HTTPException:
        raise
    except Exception as e:
        current_app.logger.error(f"Error checking rollback capability: {str(e)}")
        return jsonify({"error": "Failed to check rollback capability"}), 500


# ============================================================================
# PHASE 4: TEMPLATE PREVIEW CAPABILITIES
# ============================================================================


@application_mgmt.route("/api/templates/<int:template_id>/preview", methods=["POST"])
@login_required
def preview_template_instantiation(template_id):
    """
    Preview what will be created if this template is instantiated.

    ENTERPRISE FEATURE: See-before-you-commit capability

    Request body:
        {
            "application_id": 123,
            "customizations": {"name": "Custom Name"}  // Optional
        }

    Returns:
        JSON preview object with:
        - element: What ArchiMate element will be created
        - relationships: What relationships will be created
        - domain_model: What domain model will be created
        - conflicts: Any potential naming conflicts
    """
    data = request.get_json() or {}
    app_id = data.get("application_id")
    customizations = data.get("customizations", {})

    if not app_id:
        return jsonify({"error": "application_id required"}), 400

    template = ElementTemplate.query.get_or_404(template_id)
    ApplicationComponent.query.get_or_404(app_id)

    # Build preview
    preview = {
        "template": template.to_dict(),
        "will_create": {
            "archimate_element": {
                "name": customizations.get("name", template.name),
                "type": template.element_type,
                "layer": template.layer,
                "description": customizations.get("description", template.description),
            },
            "domain_model": {
                "type": template.element_type,
                "estimated_attributes": template.default_attributes or {},
            },
        },
        "conflicts": [],
        "warnings": [],
    }

    # Check for naming conflicts
    from app.models import ArchiMateElement

    existing = ArchiMateElement.query.filter_by(
        name=preview["will_create"]["archimate_element"]["name"], type=template.element_type
    ).first()

    if existing:
        preview["conflicts"].append(
            {
                "type": "duplicate_name",
                "message": f"Element '{existing.name}' already exists",
                "existing_id": existing.id,
            }
        )

    # Check if already instantiated for this app
    from app.models.element_templates import ElementTemplateUsage

    existing_usage = ElementTemplateUsage.query.filter_by(
        application_id=app_id, template_id=template_id
    ).first()

    if existing_usage:
        preview["warnings"].append(
            {
                "type": "already_instantiated",
                "message": "This template is already instantiated for this application",
                "element_id": existing_usage.archimate_element_id,
            }
        )

    return jsonify(preview)


@application_mgmt.route(
    "/api/applications/<string:app_id>/templates/preview-bulk", methods=["POST"]
)
@login_required
def preview_bulk_instantiation(app_id):
    """
    Preview bulk instantiation before executing.

    ENTERPRISE FEATURE: Impact analysis before bulk operations

    Request body:
        {
            "template_ids": [1, 2, 3, ...]
        }

    Returns:
        JSON with:
        - summary: Count of elements, relationships, conflicts
        - elements: List of what will be created
        - conflicts: Naming conflicts, duplicates
        - estimated_time: Processing time estimate
    """
    data = request.get_json() or {}
    template_ids = data.get("template_ids", [])

    if not template_ids:
        return jsonify({"error": "template_ids required"}), 400

    if len(template_ids) > MAX_BULK_TEMPLATES:
        return jsonify({"error": f"Maximum {MAX_BULK_TEMPLATES} templates allowed"}), 400

    templates = ElementTemplate.query.filter(ElementTemplate.id.in_(template_ids)).all()

    preview = {
        "summary": {
            "total_templates": len(templates),
            "total_elements": len(templates),
            "total_relationships": 0,  # Calculated below
            "conflicts": 0,
            "estimated_time_seconds": len(templates) * 0.5,  # ~0.5s per template
        },
        "elements": [],
        "conflicts": [],
        "warnings": [],
    }

    # Check each template
    for template in templates:
        element_preview = {
            "template_id": template.id,
            "template_name": template.name,
            "will_create": {
                "name": template.name,
                "type": template.element_type,
                "layer": template.layer,
            },
        }

        # Check for conflicts
        from app.models import ArchiMateElement

        existing = ArchiMateElement.query.filter_by(
            name=template.name, type=template.element_type
        ).first()

        if existing:
            preview["conflicts"].append(
                {
                    "template_id": template.id,
                    "type": "duplicate_name",
                    "message": f"Element '{template.name}' already exists",
                }
            )
            preview["summary"]["conflicts"] += 1

        preview["elements"].append(element_preview)

    return jsonify(preview)


# ============================================================================
# PHASE 5: PERFORMANCE OPTIMIZATION
# ============================================================================


@application_mgmt.route("/api/templates/cached", methods=["GET"])
@login_required
def get_templates_cached():
    """
    Get templates with caching for improved performance.

    ENTERPRISE FEATURE: Redis/memory caching for frequently accessed templates

    Query params: Same as /api/templates
    Cache TTL: 5 minutes

    Returns:
        JSON array of templates (cached)
    """
    from app.services.core.cache_service import CacheService

    # Build cache key from query params
    framework = request.args.get("framework", "all")
    element_type = request.args.get("element_type", "all")
    category = request.args.get("category", "all")

    cache_key = f"templates:{framework}:{element_type}:{category}"

    cache = CacheService()

    # Try cache first
    cached_data = cache.get(cache_key)
    if cached_data:
        return jsonify(cached_data), 200, {"X-Cache": "HIT"}

    # Cache miss - fetch from database
    schema = TemplateQuerySchema()
    try:
        params = schema.load(request.args)
    except ValidationError as e:
        return jsonify({"error": "Validation failed", "details": e.messages}), 400

    template_repo = ElementTemplateRepository()
    usage_repo = ElementTemplateUsageRepository()
    domain_factory = DomainModelFactory()
    service = TemplateInstantiationService(template_repo, usage_repo, domain_factory)

    try:
        templates = service.get_available_templates(**params)
        data = [t.to_dict() for t in templates]

        # Cache for 5 minutes
        cache.set(cache_key, data, ttl=300)

        return jsonify(data), 200, {"X-Cache": "MISS"}
    except Exception as e:
        current_app.logger.error(f"Error fetching templates: {str(e)}")
        return jsonify({"error": "Failed to fetch templates"}), 500


@application_mgmt.route("/api/templates/batch-details", methods=["POST"])
@login_required
def get_batch_template_details():
    """
    Get details for multiple templates in a single request.

    ENTERPRISE FEATURE: Reduces round trips for bulk operations

    Request body:
        {
            "template_ids": [1, 2, 3, ...]
        }

    Returns:
        JSON object with template_id as keys
    """
    data = request.get_json() or {}
    template_ids = data.get("template_ids", [])

    if not template_ids:
        return jsonify({"error": "template_ids required"}), 400

    if len(template_ids) > 100:
        return jsonify({"error": "Maximum 100 templates per request"}), 400

    # Fetch all in single query
    templates = ElementTemplate.query.filter(ElementTemplate.id.in_(template_ids)).all()

    # Build response as dict for O(1) lookup
    result = {str(t.id): t.to_dict() for t in templates}

    return jsonify(result)


@application_mgmt.route("/api/templates/stats", methods=["GET"])
@login_required
def get_template_stats():
    """
    Get aggregate statistics about templates.

    ENTERPRISE FEATURE: Dashboard metrics without expensive queries

    Returns:
        JSON with:
        - total_templates
        - by_framework
        - by_layer
        - by_element_type
        - most_used (top 10)
    """
    from sqlalchemy import func

    # Use aggregation queries for performance
    stats = {
        "total_templates": ElementTemplate.query.filter_by(is_active=True).count(),
        "by_framework": {},
        "by_layer": {},
        "by_element_type": {},
        "most_used": [],
    }

    # By framework
    framework_counts = (
        db.session.query(ElementTemplate.framework, func.count(ElementTemplate.id).label("count"))
        .filter_by(is_active=True)
        .group_by(ElementTemplate.framework)
        .all()
    )

    stats["by_framework"] = {fw: count for fw, count in framework_counts}

    # By layer
    layer_counts = (
        db.session.query(ElementTemplate.layer, func.count(ElementTemplate.id).label("count"))
        .filter_by(is_active=True)
        .group_by(ElementTemplate.layer)
        .all()
    )

    stats["by_layer"] = {layer: count for layer, count in layer_counts}

    # By element type
    type_counts = (
        db.session.query(
            ElementTemplate.element_type, func.count(ElementTemplate.id).label("count")
        )
        .filter_by(is_active=True)
        .group_by(ElementTemplate.element_type)
        .limit(20)
        .all()
    )

    stats["by_element_type"] = {etype: count for etype, count in type_counts}

    # Most used (if usage tracking exists)
    from app.models.element_templates import ElementTemplateUsage

    most_used = (
        db.session.query(
            ElementTemplate.id,
            ElementTemplate.name,
            func.count(ElementTemplateUsage.id).label("usage_count"),
        )
        .join(ElementTemplateUsage)
        .group_by(ElementTemplate.id, ElementTemplate.name)
        .order_by(func.count(ElementTemplateUsage.id).desc())
        .limit(10)
        .all()
    )

    stats["most_used"] = [
        {"template_id": tid, "name": name, "count": count} for tid, name, count in most_used
    ]

    return jsonify(stats)


# ============================================================================
# PHASE 6: DECOMPOSITION HIERARCHY SUPPORT
# ============================================================================


@application_mgmt.route("/api/templates/decomposition/levels", methods=["GET"])
@login_required
def get_decomposition_levels():
    """
    Get available decomposition levels for a specific framework.

    Query params:
        - framework: Framework name (required)

    Returns:
        JSON with level information:
        {
            "framework": "PCF",
            "levels": [
                {"level": 1, "name": "L1 - Category", "count": 13},
                {"level": 2, "name": "L2 - Process Group", "count": 45},
                {"level": 3, "name": "L3 - Process", "count": 180},
                {"level": 4, "name": "L4 - Activity", "count": 520}
            ]
        }
    """
    framework = request.args.get("framework")
    if not framework:
        return jsonify({"error": "framework parameter required"}), 400

    # Get level counts for framework
    level_data = (
        db.session.query(ElementTemplate.level, func.count(ElementTemplate.id).label("count"))
        .filter(
            ElementTemplate.framework == framework,
            ElementTemplate.is_active == True,
            ElementTemplate.level.isnot(None),
        )
        .group_by(ElementTemplate.level)
        .order_by(ElementTemplate.level)
        .all()
    )

    # Define level names per framework
    LEVEL_NAMES = {
        "PCF": {1: "Category", 2: "Process Group", 3: "Process", 4: "Activity", 5: "Task"},
        "COBIT": {1: "Domain", 2: "Process", 3: "Practice", 4: "Activity"},
        "SCOR": {
            1: "Process Type",
            2: "Process Category",
            3: "Process Element",
            4: "Implementation",
        },
        "ISA - 95": {1: "Functional Level", 2: "Function", 3: "Sub-Function"},
        "ACM": {1: "Domain", 2: "Capability", 3: "Sub-Capability"},
        "ITIL": {1: "Practice Area", 2: "Practice", 3: "Activity"},
        "TOGAF": {1: "Phase", 2: "Step", 3: "Activity"},
    }

    fw_level_names = LEVEL_NAMES.get(framework, {})

    levels = []
    for level, count in level_data:
        level_name = fw_level_names.get(level, f"Level {level}")
        levels.append({"level": level, "name": f"L{level} - {level_name}", "count": count})

    return jsonify({"framework": framework, "levels": levels})


@application_mgmt.route("/api/templates/decomposition/tree", methods=["GET"])
@login_required
def get_decomposition_tree():
    """
    Get hierarchical tree structure for a framework's templates.

    Query params:
        - framework: Framework name (required)
        - parent_code: Parent code to get children (optional, null for root)
        - max_depth: Maximum depth to return (optional, default 2)

    Returns:
        JSON tree structure:
        {
            "framework": "PCF",
            "tree": [
                {
                    "code": "1.0",
                    "name": "Develop Vision and Strategy",
                    "level": 1,
                    "element_type": "BusinessProcess",
                    "children_count": 5,
                    "children": [
                        {"code": "1.1", "name": "Define business strategy", "level": 2, ...}
                    ]
                }
            ]
        }
    """
    framework = request.args.get("framework")
    if not framework:
        return jsonify({"error": "framework parameter required"}), 400

    parent_code = request.args.get("parent_code")  # None means get root elements
    max_depth = min(int(request.args.get("max_depth", 2)), 5)  # Cap at 5 levels

    def build_tree(parent, current_depth):
        """Recursively build tree structure."""
        if current_depth > max_depth:
            return []

        # Query children of this parent
        query = ElementTemplate.query.filter(
            ElementTemplate.framework == framework, ElementTemplate.is_active == True
        )

        if parent is None:
            # Get root elements (no parent OR level 1)
            query = query.filter(
                db.or_(ElementTemplate.parent_code.is_(None), ElementTemplate.level == 1)
            )
        else:
            query = query.filter(ElementTemplate.parent_code == parent)

        templates = query.order_by(ElementTemplate.code, ElementTemplate.name).all()

        result = []
        for t in templates:
            # Count children
            children_count = ElementTemplate.query.filter(
                ElementTemplate.framework == framework,
                ElementTemplate.parent_code == t.code,
                ElementTemplate.is_active == True,
            ).count()

            node = {
                "id": t.id,
                "code": t.code,
                "name": t.name,
                "level": t.level,
                "element_type": t.element_type,
                "layer": t.layer,
                "description": t.description[:200] if t.description else None,
                "children_count": children_count,
                "has_children": children_count > 0,
            }

            # Recursively get children if depth allows
            if current_depth < max_depth and children_count > 0:
                node["children"] = build_tree(t.code, current_depth + 1)

            result.append(node)

        return result

    tree = build_tree(parent_code, 1)

    return jsonify({"framework": framework, "parent_code": parent_code, "tree": tree})


@application_mgmt.route("/api/templates/decomposition/children", methods=["GET"])
@login_required
def get_decomposition_children():
    """
    Get direct children of a template by its code (lazy loading for tree).

    Query params:
        - framework: Framework name (required)
        - parent_code: Parent code (required)

    Returns:
        JSON array of child templates
    """
    framework = request.args.get("framework")
    parent_code = request.args.get("parent_code")

    if not framework or not parent_code:
        return jsonify({"error": "framework and parent_code parameters required"}), 400

    children = (
        ElementTemplate.query.filter(
            ElementTemplate.framework == framework,
            ElementTemplate.parent_code == parent_code,
            ElementTemplate.is_active == True,
        )
        .order_by(ElementTemplate.code, ElementTemplate.name)
        .all()
    )

    return jsonify(
        [
            {
                "id": t.id,
                "code": t.code,
                "name": t.name,
                "level": t.level,
                "element_type": t.element_type,
                "layer": t.layer,
                "description": t.description[:200] if t.description else None,
                "has_children": ElementTemplate.query.filter(
                    ElementTemplate.framework == framework,
                    ElementTemplate.parent_code == t.code,
                    ElementTemplate.is_active == True,
                ).count()
                > 0,
            }
            for t in children
        ]
    )


@application_mgmt.route("/api/templates/decomposition/path", methods=["GET"])
@login_required
def get_decomposition_path():
    """
    Get the full path (breadcrumb) from root to a specific template.

    Query params:
        - framework: Framework name (required)
        - code: Template code (required)

    Returns:
        JSON array representing the path from root to template:
        [
            {"code": "1.0", "name": "Develop Vision and Strategy", "level": 1},
            {"code": "1.1", "name": "Define business strategy", "level": 2},
            {"code": "1.1.1", "name": "Assess external environment", "level": 3}
        ]
    """
    framework = request.args.get("framework")
    code = request.args.get("code")

    if not framework or not code:
        return jsonify({"error": "framework and code parameters required"}), 400

    path = []
    current_code = code
    visited = set()  # Prevent infinite loops

    while current_code and current_code not in visited:
        visited.add(current_code)

        template = ElementTemplate.query.filter(
            ElementTemplate.framework == framework,
            ElementTemplate.code == current_code,
            ElementTemplate.is_active == True,
        ).first()

        if template:
            path.insert(
                0,
                {
                    "id": template.id,
                    "code": template.code,
                    "name": template.name,
                    "level": template.level,
                    "element_type": template.element_type,
                },
            )
            current_code = template.parent_code
        else:
            break

    return jsonify(path)


@application_mgmt.route("/api/templates/by-level", methods=["GET"])
@login_required
def get_templates_by_level():
    """
    Get templates filtered by decomposition level.

    Query params:
        - framework: Framework name (required)
        - level: Decomposition level (required, 1 - 5)
        - layer: ArchiMate layer (optional)
        - element_type: ArchiMate element type (optional)
        - limit: Max results (default 100)
        - offset: Pagination offset (default 0)

    Returns:
        JSON array of templates at the specified level
    """
    framework = request.args.get("framework")
    level = request.args.get("level", type=int)

    if not framework or level is None:
        return jsonify({"error": "framework and level parameters required"}), 400

    query = ElementTemplate.query.filter(
        ElementTemplate.framework == framework,
        ElementTemplate.level == level,
        ElementTemplate.is_active == True,
    )

    # Optional filters
    layer = request.args.get("layer")
    if layer:
        query = query.filter(func.lower(ElementTemplate.layer) == layer.lower())

    element_type = request.args.get("element_type")
    if element_type:
        query = query.filter(ElementTemplate.element_type == element_type)

    # Pagination
    limit = min(safe_int_arg('limit', 100, minimum=1, maximum=500), 500)
    offset = safe_int_arg('offset', 0, minimum=0)

    total = query.count()
    templates = (
        query.order_by(ElementTemplate.code, ElementTemplate.name).offset(offset).limit(limit).all()
    )

    return jsonify(
        {
            "framework": framework,
            "level": level,
            "total": total,
            "limit": limit,
            "offset": offset,
            "templates": [t.to_dict() for t in templates],
        }
    )


@application_mgmt.route(
    "/api/applications/<string:app_id>/templates/instantiate-with-hierarchy", methods=["POST"]
)
@login_required
@rate_limit(*RATE_LIMIT_INSTANTIATE)
def instantiate_template_with_hierarchy(app_id):
    """
    Instantiate a template along with its parent/child hierarchy.

    ENTERPRISE FEATURE: Automatically creates parent elements and
    composition relationships to maintain framework hierarchy.

    Request body:
        {
            "template_id": 123,
            "include_parents": true,   // Create parent elements up to root
            "include_children": false, // Create all child elements
            "max_depth": 2,            // Max child depth (if include_children)
            "create_relationships": true
        }

    Returns:
        JSON with all created elements and relationships
    """
    data = request.get_json()

    if not data or "template_id" not in data:
        return jsonify({"error": "template_id required"}), 400

    try:
        template_id = int(data["template_id"])
        max_depth = min(int(data.get("max_depth", 2)), 5)
    except (ValueError, TypeError):
        return jsonify({"error": "template_id and max_depth must be integers"}), 400
    include_parents = data.get("include_parents", False)
    include_children = data.get("include_children", False)
    create_relationships = data.get("create_relationships", True)

    # Get the main template
    template = ElementTemplate.query.get_or_404(template_id)

    templates_to_instantiate = [template]

    # Collect parent templates if requested
    if include_parents and template.parent_code:
        current_code = template.parent_code
        visited = set()

        while current_code and current_code not in visited:
            visited.add(current_code)
            parent = ElementTemplate.query.filter(
                ElementTemplate.framework == template.framework,
                ElementTemplate.code == current_code,
                ElementTemplate.is_active == True,
            ).first()

            if parent:
                templates_to_instantiate.insert(0, parent)  # Add to front
                current_code = parent.parent_code
            else:
                break

    # Collect child templates if requested
    if include_children:

        def collect_children(parent_code, depth):
            if depth > max_depth:
                return []

            children = ElementTemplate.query.filter(
                ElementTemplate.framework == template.framework,
                ElementTemplate.parent_code == parent_code,
                ElementTemplate.is_active == True,
            ).all()

            result = list(children)
            for child in children:
                result.extend(collect_children(child.code, depth + 1))

            return result

        children = collect_children(template.code, 1)
        templates_to_instantiate.extend(children)

    # Remove duplicates while preserving order
    seen = set()
    unique_templates = []
    for t in templates_to_instantiate:
        if t.id not in seen:
            seen.add(t.id)
            unique_templates.append(t)

    # Limit to prevent DoS
    if len(unique_templates) > MAX_BULK_TEMPLATES:
        return (
            jsonify(
                {
                    "error": f"Hierarchy too large. Maximum {MAX_BULK_TEMPLATES} templates allowed.",
                    "requested_count": len(unique_templates),
                }
            ),
            400,
        )

    # Instantiate all templates
    template_repo = ElementTemplateRepository()
    usage_repo = ElementTemplateUsageRepository()
    domain_factory = DomainModelFactory()
    service = TemplateInstantiationService(template_repo, usage_repo, domain_factory)

    try:
        from app.models.architecture_session import ArchitectureSession

        session = ArchitectureSession(
            application_id=app_id,
            user_id=current_user.id,
            operation_type="add_templates_hierarchy",
            operation_description=f"Added {len(unique_templates)} templates with hierarchy from {template.framework}",
            template_count=len(unique_templates),
        )
        db.session.add(session)
        db.session.flush()

        results, errors = service.instantiate_bulk(
            template_ids=[t.id for t in unique_templates],
            application_id=app_id,
            create_relationships=create_relationships,
            session_id=session.id,
        )

        db.session.commit()

        return jsonify(
            {
                "success": len(errors) == 0,
                "session_id": session.id,
                "count": len(results),
                "hierarchy_info": {
                    "main_template": template.name,
                    "parents_included": include_parents,
                    "children_included": include_children,
                },
                "elements": [
                    {"id": elem.id, "name": elem.name, "type": elem.type, "layer": elem.layer}
                    for elem, model in results
                ],
                "errors": errors if errors else None,
            }
        )

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error instantiating hierarchy")
        return jsonify({"error": "An internal error occurred"}), 500


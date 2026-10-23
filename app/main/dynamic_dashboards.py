"""
Dynamic Dashboard Generator - Universal Model Dashboard Routes

This module provides a SINGLE universal endpoint that automatically generates
dashboards for ANY SQLAlchemy model without requiring manual route creation.
"""
from datetime import datetime

from flask import Blueprint, abort, current_app, render_template, url_for  # dead-code-ok
from flask_login import current_user

from app.models.application_layer import (
    ApplicationEvent,
    ApplicationFunction,
    ApplicationInterface,
    ApplicationProcess,
    DataObject,
)
from app.models.application_portfolio import ApplicationComponent

# Import ONLY the models we need to avoid conflicts
from app.models.business_capabilities import BusinessCapability

# Import models from implementation planning (avoid conflicts with implementation_migration)
from app.models.implementation_planning import ImplementationPlateau as Plateau
from app.models.project_models import Milestone, Project, Task  # dead-code-ok
from app.services.api_dashboard_generator import APIDashboardGenerator
from flask_login import login_required

# Create blueprint
dynamic_dashboards = Blueprint("dynamic_dashboards", __name__, url_prefix="/auto-dashboard")


@dynamic_dashboards.before_request
def _platform_admin_only():
    """Platform tooling, not a customer feature: every route here is platform-admin only.

    Customers browse element types through the Element Catalog. One guard on the
    blueprint covers every current and future route, so a new route cannot be
    added ungated.
    """
    from app.middleware.tenant_decorators import is_platform_admin

    if not current_user.is_authenticated:
        return current_app.login_manager.unauthorized()
    if not is_platform_admin(current_user):
        abort(403)

# Model registry - ONLY working models to avoid conflicts
MODEL_REGISTRY = {
    # ========== BUSINESS ARCHITECTURE ==========
    "business-capability": {
        "class": BusinessCapability,
        "name": "Business Capability",
        "plural": "Business Capabilities",
        "icon": "🏢",
        "category": "Business Architecture",
    },
    # ========== PROJECT MANAGEMENT ==========
    "project": {
        "class": Project,
        "name": "Project",
        "plural": "Projects",
        "icon": "📁",
        "category": "Project Management",
    },
    "task": {
        "class": Task,
        "name": "Task",
        "plural": "Tasks",
        "icon": "✅",
        "category": "Project Management",
    },
    "milestone": {
        "class": Milestone,
        "name": "Milestone",
        "plural": "Milestones",
        "icon": "🗓️",
        "category": "Project Management",
    },
    # ========== APPLICATION ARCHITECTURE ==========
    "application-component": {
        "class": ApplicationComponent,
        "name": "Application Component",
        "plural": "Application Components",
        "icon": "📱",
        "category": "Application Architecture",
    },
    "application-function": {
        "class": ApplicationFunction,
        "name": "Application Function",
        "plural": "Application Functions",
        "icon": "⚡",
        "category": "Application Architecture",
    },
    "application-process": {
        "class": ApplicationProcess,
        "name": "Application Process",
        "plural": "Application Processes",
        "icon": "🔄",
        "category": "Application Architecture",
    },
    "application-interface": {
        "class": ApplicationInterface,
        "name": "Application Interface",
        "plural": "Application Interfaces",
        "icon": "🔌",
        "category": "Application Architecture",
    },
    "application-event": {
        "class": ApplicationEvent,
        "name": "Application Event",
        "plural": "Application Events",
        "icon": "⚡",
        "category": "Application Architecture",
    },
    "data-object": {
        "class": DataObject,
        "name": "Data Object",
        "plural": "Data Objects",
        "icon": "📊",
        "category": "Application Architecture",
    },
    # ========== IMPLEMENTATION & MIGRATION ==========
    "plateau": {
        "class": Plateau,
        "name": "Plateau",
        "plural": "Plateaus",
        "icon": "🏔️",
        "category": "Implementation",
    },
}


@dynamic_dashboards.route("/")
@dynamic_dashboards.route("/registry")
@login_required
def model_registry_index():
    """Model registry index page."""
    from flask import url_for

    # Get all models from registry
    models = []
    for slug, info in MODEL_REGISTRY.items():
        models.append(
            {
                "slug": slug,
                "name": info["name"],
                "plural": info["plural"],
                "icon": info.get("icon", "📊"),
                "category": info.get("category", "Other"),
                "url": url_for("dynamic_dashboards.universal_dashboard", model_slug=slug),
            }
        )

    # Group by category
    categories = {}
    for model in models:
        category = model["category"]
        if category not in categories:
            categories[category] = []
        categories[category].append(model)

    return render_template("dashboards/model_dashboard.html", categories=categories, models=models)


@dynamic_dashboards.route("/<model_slug>")
@login_required
def universal_dashboard(model_slug):
    """
    UNIVERSAL DASHBOARD GENERATOR

    Automatically generates a dashboard for ANY registered model.
    No manual route creation needed!

    Usage:
        /auto-dashboard/business-capability
        /auto-dashboard/application
        /auto-dashboard/vendor
        ... etc for ANY model in MODEL_REGISTRY

    Args:
        model_slug: URL-friendly model identifier (e.g., 'business-capability')

    Returns:
        Rendered dashboard with live data
    """
    from sqlalchemy.exc import OperationalError, ProgrammingError

    # Lookup model in registry
    if model_slug not in MODEL_REGISTRY:
        abort(404, f"Model '{model_slug}' not found in registry")

    model_info = MODEL_REGISTRY[model_slug]
    model_class = model_info["class"]

    # Initialize generator
    generator = APIDashboardGenerator()

    # Generate custom config
    custom_config = {
        "title": f"{model_info['plural']} Dashboard",
        "subtitle": f"Live data and analytics for {model_info['plural'].lower()}",
        "metrics": [
            {
                "title": f"Total {model_info['plural']}",
                "field": "id",
                "aggregation": "COUNT",
                "format": "number",
            }
        ],
    }

    try:
        # Generate dashboard config with live data
        config = generator.generate_from_model_with_live_data(model_class, custom_config)

        # Render with the new beautiful dashboard template
        return render_template("dashboards/model_dashboard.html", config=config)
    except (OperationalError, ProgrammingError):
        # Table doesn't exist in database
        abort(
            404,
            f"Database table for '{model_info['name']}' does not exist. Please run migrations to create the table.",
        )


@dynamic_dashboards.route("/<model_slug>/<int:record_id>")
@login_required
def universal_detail(model_slug, record_id):
    """
    UNIVERSAL DETAIL PAGE GENERATOR

    Automatically generates detail page for ANY registered model record.

    Usage:
        /auto-dashboard/business-capability/123
        /auto-dashboard/application/456
    """
    from sqlalchemy.exc import OperationalError, ProgrammingError

    # Lookup model in registry
    if model_slug not in MODEL_REGISTRY:
        abort(404, f"Model '{model_slug}' not found in registry")

    model_info = MODEL_REGISTRY[model_slug]
    model_class = model_info["class"]

    try:
        # Initialize generator
        generator = APIDashboardGenerator()

        current_app.logger.debug(f"Generating detail page for {model_class.__name__} ID {record_id}")

        # Generate detail config
        detail_config = generator.generate_detail_page(
            model_class=model_class,
            record_id=record_id,
            list_url=url_for("dynamic_dashboards.universal_dashboard", model_slug=model_slug),
        )

        current_app.logger.debug(f"Detail config generated: {type(detail_config)}")
        if isinstance(detail_config, dict):
            current_app.logger.debug(f"Config keys: {list(detail_config.keys())}")

        # Render with detail template
        return render_template("dashboards/generic_detail.html", config=detail_config)

    except (OperationalError, ProgrammingError):
        # Table doesn't exist in database
        abort(
            404,
            f"Database table for '{model_info['name']}' does not exist. Please run migrations to create the table.",
        )
    except Exception as e:
        # Other errors
        current_app.logger.error(f"in detail page generation: {e}")
        abort(500, f"Error generating detail page: {str(e)}")


@dynamic_dashboards.route("/workflow-pipeline")
@login_required
def workflow_pipeline():
    """Capability Gaps Dashboard - Shows capabilities, applications, and gaps"""
    try:
        # Get capability mapping data
        from app.models.application_layer import ApplicationComponent
        from app.models.application_portfolio import ApplicationCapabilityMapping
        from app.models.business_capabilities import BusinessCapability

        # Get all capabilities
        all_capabilities = BusinessCapability.query.limit(2000).all()

        # Get all applications
        all_applications = ApplicationComponent.query.limit(2000).all()

        # Get all mappings
        # tenant-scoping-ok: scoped via the TenantMixin FK parent
        # BusinessCapability, not ACM.organization_id -- that column is NULL
        # on every row in production, so a predicate on it would match zero
        # rows and report every capability as a gap. Joining
        # BusinessCapability lets do_orm_execute scope the join
        # automatically. See e622d36 / rationalization_scoring_service.py.
        mappings = (
            # tenant-scoping-ok: scoped via TenantMixin FK parent BusinessCapability, ACM.organization_id is NULL in prod (see e622d36).
            ApplicationCapabilityMapping.query
            .join(
                BusinessCapability,
                ApplicationCapabilityMapping.business_capability_id == BusinessCapability.id,
            )
            .limit(5000)
            .all()
        )

        # Analyze gaps
        capability_gaps = []
        supported_capabilities = set()

        for mapping in mappings:
            supported_capabilities.add(mapping.business_capability_id)

        for capability in all_capabilities:
            if capability.id not in supported_capabilities:
                capability_gaps.append(
                    {
                        "capability_id": capability.id,
                        "capability_name": capability.name,
                        "capability_level": capability.level,
                        "domain": capability.business_domain or "Unknown",
                        "severity": "high" if capability.level == 1 else "medium",
                    }
                )

        # Create gap analysis results in the format expected by portfolio template.
        # None = analysis not yet implemented (template shows "N/A", not "0").
        portfolio_gaps = {
            "timestamp": datetime.utcnow().isoformat(),
            "unsupported_capabilities": capability_gaps,
            "single_point_failures": None,
            "compliance_risks": None,
            "orphaned_applications": None,
        }

        return render_template(
            "application_mgmt/portfolio_gap_analysis.html",
            portfolio_gaps=portfolio_gaps,
            total_applications=len(all_applications),
        )

    except Exception as e:
        current_app.logger.error(f"in workflow pipeline: {e}")
        import traceback

        traceback.print_exc()
        abort(500, f"Error loading workflow pipeline: {str(e)}")

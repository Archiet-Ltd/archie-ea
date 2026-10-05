"""
Unified Low Priority Routes

Architecture, strategic, consolidation-list and policy-monitoring routes under
/enterprise, defined directly on ``unified_low_priority_bp`` below.

The header used to promise a consolidation of five other route modules
(architecture_routes.py, capability_map_routes.py, strategic_routes.py,
consolidation_list_routes.py, policy_monitoring_routes.py) via star imports.
None of those modules exist here; the aggregator never ran. Every route this
module serves is written out below.
"""

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
)
from flask_login import login_required

from app.decorators import audit_log
from sqlalchemy import or_, text

from .. import db
from ..models.application_portfolio import ApplicationComponent
from ..models.business_capabilities import (
    ApplicationCapabilityCoverage,
    BusinessCapability,
)
from ..models.models import ArchiMateElement, ArchiMateRelationship, ArchitectureModel
from ..services.consolidation_service import ConsolidationService
from ..services.policy_monitoring_service import PolicyMonitoringService

# Import new services
from ..services.strategic_service import StrategicService

# Initialize services
strategic_service = StrategicService()
consolidation_service = ConsolidationService()
policy_monitoring_service = PolicyMonitoringService()

# Create unified blueprint
unified_low_priority_bp = Blueprint(
    "unified_low_priority", __name__, url_prefix="/enterprise"
)

# There used to be a `try: from .architecture_routes import * ...` aggregator here,
# star-importing architecture_routes, capability_map_routes, policy_monitoring_routes
# and strategic_routes and setting LOW_PRIORITY_ROUTES_AVAILABLE.
#
# It never ran. The first of the four modules does not exist in this codebase, so the
# whole block raised ModuleNotFoundError on line one and the except arm set the flag to
# False — every time, since before capability_map_routes.py was removed. No name below
# came from it (ruff reports no F405), and nothing anywhere read the flag. Removed
# rather than left as a comment that reads like a working feature.


# === ARCHITECTURE ROUTES ===


# REMOVED: "/" route — conflicts with enterprise_bp.enterprise_dashboard() in
# unified_enterprise_routes.py which renders enterprise/enterprise_dashboard.html
# with actual data (data_models_count, solutions_count, etc.)


@unified_low_priority_bp.route("/architecture")
@login_required
def architecture_dashboard():
    """Architecture management dashboard - redirects to ArchiMate CRUD dashboard"""
    return redirect("/architecture/dashboard")


@unified_low_priority_bp.route("/architecture/models")
@login_required
def architecture_models():
    """List architecture models"""
    try:
        models = ArchitectureModel.query.order_by(ArchitectureModel.name).all()
        return render_template("architecture/models.html", models=models)
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Architecture models error: %s", e)
        flash("Error loading architecture models", "error")
        return render_template(
            "architecture/models.html",
            models=[],
            load_error="Architecture models could not be read.",
        )


@unified_low_priority_bp.route("/architecture/elements")
@login_required
def architecture_elements():
    """List architecture elements"""
    try:
        elements = ArchiMateElement.query.order_by(ArchiMateElement.name).all()
        return render_template("architecture/elements.html", elements=elements)
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Architecture elements error: %s", e)
        flash("Error loading architecture elements", "error")
        return render_template(
            "architecture/elements.html",
            elements=[],
            load_error="ArchiMate elements could not be read.",
        )


@unified_low_priority_bp.route("/architecture/relationships")
@login_required
def architecture_relationships():
    """List architecture relationships.

    F-05(b), Capgemini dry-run: fed the template rel.source_element/
    rel.relationship_type — attributes ArchiMateRelationship never declares
    (it has source_id/target_id FKs and a `type` column, no relationship()).
    Jinja silently falls back to the bare id for a missing attribute, so this
    route showed "20 rows of bare numeric IDs" despite querying the right
    table. Resolve names explicitly instead. Same fix as
    architecture_crud.list_relationships, the other route sharing this
    template.
    """
    try:
        rels = ArchiMateRelationship.query.order_by(ArchiMateRelationship.type).all()
        element_ids = {
            eid for rel in rels for eid in (rel.source_id, rel.target_id) if eid is not None
        }
        names_by_id = {}
        if element_ids:
            names_by_id = dict(
                db.session.query(ArchiMateElement.id, ArchiMateElement.name)
                .filter(ArchiMateElement.id.in_(element_ids))
            )
        relationships = [
            {
                "id": rel.id,
                "type": rel.type,
                "source_id": rel.source_id,
                "target_id": rel.target_id,
                "source_name": names_by_id.get(rel.source_id),
                "target_name": names_by_id.get(rel.target_id),
            }
            for rel in rels
        ]
        return render_template(
            "architecture/relationships.html", relationships=relationships
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Architecture relationships error: %s", e)
        flash("Error loading architecture relationships", "error")
        return render_template(
            "architecture/relationships.html",
            relationships=[],
            load_error="ArchiMate relationships could not be read.",
        )


# === CAPABILITY MAP ROUTES ===


@unified_low_priority_bp.route("/capability-map")
@unified_low_priority_bp.route("/capability-map/")
@login_required
def capability_map_dashboard():
    """Capability mapping dashboard"""
    # capability_map/index.html's "Total Capabilities" tile reads
    # `total_capabilities`, not `capability_count` -- this route passed the
    # latter, so the tile was Undefined here and always rendered the null-state
    # em dash regardless of how many capabilities actually existed. Route both
    # variables through the same counting function `/capability-map/`'s index()
    # uses (app/modules/capabilities/services/capability_count_service.py) so
    # this surface cannot drift from that one (ADR 0008 / store-agreement).
    from app.modules.capabilities.services.capability_count_service import (
        count_business_capabilities,
    )

    try:
        # Get capability statistics
        capability_count = count_business_capabilities()
        application_count = ApplicationComponent.query.count()

        # Get capability categories
        categories = db.session.query(BusinessCapability.level).distinct().all()
        categories = [c[0] for c in categories if c[0]]

        return render_template(
            "capability_map/index.html",
            capability_count=capability_count,
            total_capabilities=capability_count,
            application_count=application_count,
            categories=categories,
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Capability map dashboard error: %s", e)
        flash("Error loading capability map dashboard", "error")
        # None, not 0: a counted zero and an uncounted one must not look alike.
        return render_template(
            "capability_map/index.html",
            capability_count=None,
            total_capabilities=None,
            application_count=None,
            categories=[],
            load_error="Capability map statistics could not be read.",
        )


@unified_low_priority_bp.route("/capability-map/capabilities")
@login_required
def capability_map_capabilities():
    """List capabilities for mapping"""
    try:
        capabilities = BusinessCapability.query.order_by(BusinessCapability.name).all()
        # Parent names, so a sub-capability can say what it is a sub-capability OF.
        parent_names = {c.id: c.name for c in capabilities}
        return render_template(
            "capability_map/capabilities.html",
            capabilities=capabilities,
            parent_names=parent_names,
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Capability map capabilities error: %s", e)
        flash("Error loading capabilities", "error")
        return render_template(
            "capability_map/capabilities.html",
            capabilities=[],
            parent_names={},
            load_error="Capabilities could not be read.",
        )


@unified_low_priority_bp.route("/capability-map/applications")
@login_required
def capability_map_applications():
    """List applications for mapping"""
    try:
        applications = ApplicationComponent.query.order_by(
            ApplicationComponent.name
        ).all()
        return render_template(
            "capability_map/applications.html", applications=applications
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Capability map applications error: %s", e)
        flash("Error loading applications", "error")
        return render_template(
            "capability_map/applications.html",
            applications=[],
            load_error="Applications could not be read.",
        )


@unified_low_priority_bp.route("/capability-map/mapping")
@login_required
def capability_map_mapping():
    """Capability mapping interface"""
    try:
        capabilities = BusinessCapability.query.order_by(BusinessCapability.name).all()
        applications = ApplicationComponent.query.order_by(
            ApplicationComponent.name
        ).all()

        # The page called itself a Coverage Matrix and rendered no coverage at
        # all -- the template carried a comment saying the join data "would be
        # required from the route" and printed "N apps available" (the total
        # application count, repeated on every row) in its place. Read the real
        # mappings.
        #
        # ApplicationCapabilityCoverage is TenantMixin and the ORM's
        # tenant-isolation listener already applies the organization_id
        # predicate to this query automatically. The explicit restriction to
        # THIS organisation's capability ids below is kept as belt-and-braces
        # scoping, not the only thing preventing cross-tenant rows here.
        mapped_apps = {c.id: [] for c in capabilities}
        cap_ids = list(mapped_apps)
        if cap_ids:
            app_names = {a.id: a.name for a in applications}
            rows = ApplicationCapabilityCoverage.query.filter(
                ApplicationCapabilityCoverage.capability_id.in_(cap_ids)
            ).all()
            for row in rows:
                name = app_names.get(row.application_component_id)
                if name is None:
                    continue  # an application outside this organisation
                mapped_apps[row.capability_id].append(
                    {
                        "id": row.application_component_id,
                        "name": name,
                        "mapping_id": row.id,
                        "support_level": row.support_level,
                    }
                )
            for entries in mapped_apps.values():
                entries.sort(key=lambda e: e["name"])

        covered = sum(1 for c in capabilities if mapped_apps[c.id])
        # None, not 0: with no capabilities at all there is nothing to be a
        # percentage OF, and a rendered "0%" would read as a measured result.
        coverage_pct = round(covered * 100 / len(capabilities)) if capabilities else None

        return render_template(
            "capability_map/mapping.html",
            capabilities=capabilities,
            applications=applications,
            mapped_apps=mapped_apps,
            covered_count=covered,
            coverage_pct=coverage_pct,
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Capability mapping error: %s", e)
        flash("Error loading capability mapping", "error")
        return render_template(
            "capability_map/mapping.html",
            capabilities=[],
            applications=[],
            mapped_apps={},
            covered_count=None,
            coverage_pct=None,
            load_error="The capability mapping data could not be read.",
        )


# === STRATEGIC ROUTES ===


# REMOVED: "/strategic" route — conflicts with enterprise_bp.strategic_planning_dashboard()
# in unified_enterprise_routes.py. Also, the template strategic/dashboard.html does not exist.
# The enterprise_bp version renders enterprise/strategic_planning_dashboard.html (which exists).


@unified_low_priority_bp.route("/strategic/roadmap")
@login_required
def strategic_roadmap():
    """Strategic roadmap view with swimlane visualization"""
    try:
        # Get filter parameters
        year = request.args.get("year", type=int)
        quarter = request.args.get("quarter")

        # Get roadmap data from services
        roadmap_by_lane_result = strategic_service.get_roadmap_by_lane()
        roadmap_items_result = strategic_service.get_roadmap_items(
            year=year, quarter=quarter
        )
        health_scores_result = strategic_service.get_initiative_health_scores()

        # Extract data using correct keys (not "data")
        roadmap_by_lane = (
            roadmap_by_lane_result.get("roadmap_by_lane", {})
            if roadmap_by_lane_result.get("success")
            else {}
        )
        roadmap_items = (
            roadmap_items_result.get("roadmap_items", [])
            if roadmap_items_result.get("success")
            else []
        )
        health_scores = (
            health_scores_result.get("health_scores", [])
            if health_scores_result.get("success")
            else []
        )

        return render_template(
            "strategic/roadmap.html",
            roadmap_items=roadmap_items,
            roadmap_by_lane=roadmap_by_lane,
            health_scores=health_scores,
            year=year,
            quarter=quarter,
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Strategic roadmap error: %s", e)
        flash("Error loading strategic roadmap", "error")
        return render_template(
            "strategic/roadmap.html",
            roadmap_items=[],
            roadmap_by_lane={},
            health_scores=[],
            year=None,
            quarter=None,
            load_error="The strategic roadmap could not be read.",
        )


@unified_low_priority_bp.route("/strategic/initiatives")
@login_required
def strategic_initiatives():
    """Strategic initiatives management"""
    try:
        # Get initiatives from service
        status_filter = request.args.get("status")
        initiatives_result = strategic_service.get_all_initiatives(
            status_filter=status_filter
        )
        health_scores = strategic_service.get_initiative_health_scores()

        initiatives_list = (
            initiatives_result.get("initiatives", [])
            if initiatives_result.get("success")
            else []
        )
        health_scores_list = (
            health_scores.get("health_scores", [])
            if health_scores.get("success")
            else []
        )

        return render_template(
            "strategic/initiatives.html",
            initiatives=initiatives_list,
            health_scores=health_scores_list,
            status_filter=status_filter,
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Strategic initiatives error: %s", e)
        flash("Error loading strategic initiatives", "error")
        return render_template(
            "strategic/initiatives.html",
            initiatives=[],
            health_scores=[],
            status_filter=None,
            load_error="Strategic initiatives could not be read.",
        )


# === CONSOLIDATION LIST ROUTES ===


@unified_low_priority_bp.route("/consolidation")
@login_required
def consolidation_dashboard():
    """Application consolidation dashboard - renders existing consolidation list dashboard"""
    try:
        dashboard_data = consolidation_service.get_consolidation_dashboard_data()
        statistics = consolidation_service.get_consolidation_statistics()

        if dashboard_data.get("success"):
            data = dashboard_data["data"]
            return render_template(
                "consolidation_list/dashboard.html",
                total_applications=ApplicationComponent.query.count(),
                duplicate_candidates=data.get("recent_candidates", []),
                consolidation_opportunities=data.get("recent_opportunities", []),
                statistics=statistics.get("data", {})
                if statistics.get("success")
                else {},
                savings_forecast=data.get("savings_forecast", {}),
            )
        else:
            return render_template(
                "consolidation_list/dashboard.html",
                total_applications=ApplicationComponent.query.count(),
                duplicate_candidates=[],
                consolidation_opportunities=[],
            )
    except Exception as e:
        current_app.logger.error(f"Consolidation dashboard error: {str(e)}")
        flash("Error loading consolidation dashboard", "error")
        return redirect("/enterprise/")


@unified_low_priority_bp.route("/consolidation/candidates")
@login_required
def consolidation_candidates():
    """List consolidation candidates"""
    try:
        # Get candidates from service with optional status filter
        status_filter = request.args.get("status")
        candidates_result = consolidation_service.get_all_candidates(
            status_filter=status_filter
        )

        # get_all_candidates returns a plain list (not a {success,data} dict)
        candidates = (
            candidates_result
            if isinstance(candidates_result, list)
            else (candidates_result.get("data", []) if candidates_result.get("success") else [])
        )
        return render_template("consolidation/candidates.html", candidates=candidates)
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Consolidation candidates error: %s", e)
        flash("Error loading consolidation candidates", "error")
        return render_template(
            "consolidation/candidates.html",
            candidates=[],
            load_error="Consolidation candidates could not be read.",
        )


@unified_low_priority_bp.route("/consolidation/opportunities")
@login_required
def consolidation_opportunities():
    """List consolidation opportunities"""
    try:
        # Get opportunities from service with optional status filter
        status_filter = request.args.get("status")
        opportunities_result = consolidation_service.get_all_opportunities(
            status_filter=status_filter
        )
        savings_forecast = consolidation_service.get_savings_forecast()

        opportunities = (
            opportunities_result
            if isinstance(opportunities_result, list)
            else (opportunities_result.get("data", []) if opportunities_result.get("success") else [])
        )
        savings = (
            savings_forecast.get("data", {})
            if isinstance(savings_forecast, dict) and savings_forecast.get("success")
            else (savings_forecast if isinstance(savings_forecast, dict) else {})
        )
        return render_template(
            "consolidation/opportunities.html",
            opportunities=opportunities,
            savings_forecast=savings,
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Consolidation opportunities error: %s", e)
        flash("Error loading consolidation opportunities", "error")
        return render_template(
            "consolidation/opportunities.html",
            opportunities=[],
            load_error="Consolidation opportunities could not be read.",
        )


# === POLICY MONITORING ROUTES ===


@unified_low_priority_bp.route("/policy-monitoring")
@login_required
def policy_monitoring_dashboard():
    """Policy Monitoring Dashboard — compliance scan results and violation tracking."""
    return render_template("policy_monitoring/dashboard.html")


# === API ENDPOINTS ===


@unified_low_priority_bp.route("/api/architecture/elements", methods=["GET"])
@login_required
def api_architecture_elements():
    """API endpoint for architecture elements"""
    try:
        elements = ArchiMateElement.query.limit(5000).all()
        data = []
        for element in elements:
            data.append(
                {
                    "id": element.id,
                    "name": element.name,
                    "type": element.type,
                    "description": element.description or "",
                }
            )
        return jsonify({"success": True, "data": data})
    except Exception as e:
        current_app.logger.error(f"API architecture elements error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_low_priority_bp.route("/api/capability-map/capabilities", methods=["GET"])
@login_required
def api_capability_map_capabilities():
    """API endpoint for capability map capabilities"""
    try:
        capabilities = BusinessCapability.query.limit(2000).all()
        data = []
        for capability in capabilities:
            data.append(
                {
                    "id": capability.id,
                    "name": capability.name,
                    "description": capability.description or "",
                    "level": capability.level or 1,
                    "category": capability.category or "",
                }
            )
        return jsonify({"success": True, "data": data})
    except Exception as e:
        current_app.logger.error(f"API capability map capabilities error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_low_priority_bp.route(
    "/api/capability-map/manufacturing-capabilities", methods=["GET"]
)
@login_required
def api_manufacturing_capabilities():
    """API endpoint for manufacturing-specific capabilities"""
    try:
        # Filter capabilities related to manufacturing
        manufacturing_capabilities = BusinessCapability.query.filter(
            or_(
                BusinessCapability.name.ilike("%manufacturing%"),
                BusinessCapability.name.ilike("%production%"),
                BusinessCapability.name.ilike("%factory%"),
                BusinessCapability.name.ilike("%shop%"),
                BusinessCapability.description.ilike("%manufacturing%"),
            )
        ).all()

        data = []
        for capability in manufacturing_capabilities:
            data.append(
                {
                    "id": capability.id,
                    "name": capability.name,
                    "description": capability.description or "",
                    "level": capability.level or 1,
                    "category": capability.category or "Manufacturing",
                }
            )
        return jsonify({"success": True, "data": data})
    except Exception as e:
        current_app.logger.error(f"API manufacturing capabilities error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_low_priority_bp.route(
    "/api/capability-map/unified-capabilities", methods=["GET"]
)
@login_required
def api_unified_capabilities():
    """API endpoint for unified capabilities view"""
    try:
        # Pre-load app counts in a single query to avoid N+1
        app_counts_rows = db.session.execute(  # tenant-filtered: scoped via parent FK
            text(
                "SELECT business_capability_id, COUNT(DISTINCT application_component_id) as app_count "
                "FROM application_capability_mapping GROUP BY business_capability_id"
            )
        ).fetchall()
        app_counts = {row[0]: row[1] for row in app_counts_rows}

        capabilities = BusinessCapability.query.limit(2000).all()

        data = []
        for capability in capabilities:
            data.append(
                {
                    "id": capability.id,
                    "name": capability.name,
                    "description": capability.description or "",
                    "level": capability.level or 1,
                    "category": capability.category or "",
                    "application_count": app_counts.get(capability.id, 0),
                    "maturity_level": getattr(
                        capability, "maturity_level", "defined"
                    ),  # model-safety-ok: BusinessCapability uses current_maturity_level, kept for API compatibility
                    "criticality": getattr(
                        capability, "criticality", "medium"
                    ),  # model-safety-ok: BusinessCapability uses business_criticality, kept for API compatibility
                }
            )

        return jsonify({"success": True, "data": data})
    except Exception as e:
        current_app.logger.error(f"API unified capabilities error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_low_priority_bp.route("/api/strategic/metrics", methods=["GET"])
@login_required
def api_strategic_metrics():
    """API endpoint for strategic metrics"""
    try:
        # Get comprehensive strategic metrics from service
        service_metrics = strategic_service.get_strategic_metrics()

        metrics = {
            "total_capabilities": BusinessCapability.query.count(),
            "total_applications": ApplicationComponent.query.count(),
            "total_elements": ArchiMateElement.query.count(),
            "total_relationships": ArchiMateRelationship.query.count(),
        }

        # Merge service metrics if available
        if service_metrics.get("success"):
            metrics.update(service_metrics.get("data") or {})

        return jsonify({"success": True, "data": metrics})
    except Exception as e:
        current_app.logger.error(f"API strategic metrics error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# === ADDITIONAL API ENDPOINTS FOR NEW SERVICES ===


@unified_low_priority_bp.route("/api/consolidation/detect", methods=["POST"])
@login_required
@audit_log("consolidation_detect")
def api_detect_duplicates():
    """API endpoint to trigger duplicate detection"""
    try:
        result = consolidation_service.detect_duplicate_candidates()
        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f"API detect duplicates error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_low_priority_bp.route("/api/consolidation/statistics", methods=["GET"])
@login_required
def api_consolidation_statistics():
    """API endpoint for consolidation statistics"""
    try:
        result = consolidation_service.get_consolidation_statistics()
        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f"API consolidation statistics error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_low_priority_bp.route("/api/policy-monitoring/scan", methods=["POST"])
@login_required
@audit_log("compliance_scan")
def api_scan_compliance():
    """API endpoint to trigger compliance scan"""
    try:
        application_id = request.json.get("application_id") if request.json else None
        if application_id:
            result = policy_monitoring_service.scan_application_compliance(
                application_id
            )
        else:
            result = policy_monitoring_service.scan_all_applications()
        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f"API scan compliance error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_low_priority_bp.route("/api/policy-monitoring/violations", methods=["GET"])
@login_required
def api_get_violations():
    """API endpoint for policy violations"""
    try:
        status_filter = request.args.get("status")
        severity_filter = request.args.get("severity")
        result = policy_monitoring_service.get_violations(
            status_filter=status_filter, severity_filter=severity_filter
        )
        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f"API get violations error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# REMOVED: Legacy route mappings — they duplicated routes already defined above
# in this same file (/architecture, /capability-map, /strategic, /consolidation,
# /policy-monitoring), causing Flask endpoint name conflicts.
# The "/strategic" legacy route also conflicted with enterprise_bp.


# === UTILITY FUNCTIONS ===


def get_architecture_statistics():
    """Get architecture statistics"""
    try:
        return {
            "elements": ArchiMateElement.query.count(),
            "relationships": ArchiMateRelationship.query.count(),
            "models": ArchitectureModel.query.count(),
        }
    except Exception:
        return {"elements": None, "relationships": None, "models": None}


def get_capability_map_statistics():
    """Get capability map statistics"""
    try:
        return {
            "capabilities": BusinessCapability.query.count(),
            "applications": ApplicationComponent.query.count(),
            "mapped_relationships": 0,  # Placeholder
        }
    except Exception:
        return {"capabilities": None, "applications": None, "mapped_relationships": None}


def get_strategic_statistics():
    """Get strategic statistics"""
    try:
        result = strategic_service.get_strategic_metrics()
        if result.get("success"):
            data = result["data"]
            return {
                "initiatives": data.get("initiatives", {}).get("total", 0),
                "milestones": data.get("milestones", {}).get("total", 0),
                "risks": data.get("initiatives", {}).get("at_risk", 0),
            }
        return {"initiatives": None, "milestones": None, "risks": None}
    except Exception:
        return {"initiatives": None, "milestones": None, "risks": None}


def get_consolidation_statistics():
    """Get consolidation statistics"""
    try:
        result = consolidation_service.get_consolidation_statistics()
        if result.get("success"):
            data = result["data"]
            return {
                "candidates": data.get("candidates", {}).get("total", 0),
                "opportunities": data.get("opportunities", {}).get("total", 0),
                "savings_estimate": data.get("estimated_pipeline_savings", 0),
            }
        return {"candidates": None, "opportunities": None, "savings_estimate": None}
    except Exception:
        return {"candidates": None, "opportunities": None, "savings_estimate": None}


def get_policy_monitoring_statistics():
    """Get policy monitoring statistics"""
    try:
        result = policy_monitoring_service.get_policy_monitoring_dashboard()
        if result.get("success"):
            data = result["data"]
            return {
                "policies": data.get("total_policies", 0),
                "violations": data.get("total_violations", 0),
                "compliance_rate": data.get("compliance_percentage", 0),
            }
        return {"policies": None, "violations": None, "compliance_rate": None}
    except Exception:
        return {"policies": None, "violations": None, "compliance_rate": None}


# === ERROR HANDLERS ===


@unified_low_priority_bp.errorhandler(404)
def not_found(error):
    """Handle 404 errors"""
    return render_template("errors/404.html"), 404


@unified_low_priority_bp.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    db.session.rollback()
    return render_template("errors/500.html"), 500



# P-04: a blueprint-local CSRFError handler used to live here, catching a
# missing/invalid token and silently 302-redirecting to the dashboard (or to
# request.referrer) instead of the app-wide 400 JSON response
# (app/_bootstrap/extensions.py::handle_csrf_error). Two of this blueprint's
# JSON POST endpoints — /api/consolidation/detect and
# /api/policy-monitoring/scan — are @login_required-only otherwise, so a
# request with no session at all resolved to a 302 that read exactly like a
# successful redirect rather than a rejected write, and every AJAX caller
# here got an unparseable HTML redirect instead of the
# {"error_type": "csrf"} contract every other endpoint in the app relies on
# (see core/03-fetch.js's isSessionExpired handling, which keys off that
# field). Removed so this blueprint gets the same CSRF-failure contract as
# everything else — see app/_bootstrap/csrf_coverage.py and
# tests/test_csrf_coverage.py (R-30), which caught this.

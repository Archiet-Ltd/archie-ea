"""
DEPRECATED - Migrated to app/modules/dashboard/routes/dashboard_pages_routes.py
This file is kept as a fallback during migration. Do NOT add new code here.
Set USE_NEW_DASHBOARD=true to use the new module instead.
Remove after Phase 6 cleanup.

Original: Dashboard Routes - Enterprise Architecture Frontend Interfaces
Phase 2c: Updated to support BusinessCapability fallback for heatmap profiling (v2).
"""

import logging

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from app.decorators import audit_log
from app.middleware.tenant_decorators import platform_admin_required

from app.services.application_consolidation_service import (
    ApplicationConsolidationService,
)
from app.services.capability_heatmap_service import CapabilityHeatmapService
# GovernanceService import removed — governance routes deleted
from app.services.rationalization_scoring_service import RationalizationScoringService
from app.utils.pagination import safe_int_arg

logger = logging.getLogger(__name__)

# Create blueprint - named 'dashboard_pages' to avoid conflict with main dashboard blueprint
dashboard_bp = Blueprint("dashboard_pages", __name__, url_prefix="/dashboard")


@dashboard_bp.route("/api/capability-heatmap", methods=["GET"])
@login_required
def api_capability_heatmap():
    """
    Get capability maturity heatmap data.
    ---
    tags:
      - Capabilities
    summary: Capability maturity heatmap
    description: Returns capability maturity heatmap data for dashboard visualisation and profiling.
    responses:
      200:
        description: Heatmap data
        schema:
          type: object
          properties:
            success:
              type: boolean
            data:
              type: object
      500:
        description: Internal server error
    """
    try:
        heatmap_service = CapabilityHeatmapService()
        heatmap_data = heatmap_service.get_maturity_heatmap()
        return jsonify({"success": True, "data": heatmap_data}), 200
    except Exception as e:
        logger.exception(f"Error getting capability heatmap: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500



# apqc_browser route removed — zero backend API, empty shell


@dashboard_bp.route("/import-history")
@login_required
def import_history():
    """Import History interface for viewing and managing import audit trail."""
    return render_template("dashboard/import_history.html")


# vendor-catalog route removed — page provided no value (redundant with /vendors/)


# ============================================================================
# Enterprise Services - Rationalization
# ============================================================================


@dashboard_bp.route("/rationalization")
@login_required
def rationalization_dashboard():
    """Application Rationalization Dashboard - TIME Framework."""
    from app.models import ApplicationComponent
    from app.services.unified_duplicate_detection_service import (
        UnifiedDuplicateDetectionService,
    )
    from config import CurrencyConfig

    try:
        currency_symbol = CurrencyConfig.get_currency_config().get("symbol", "£")
    except Exception:
        currency_symbol = "£"

    try:
        service = UnifiedDuplicateDetectionService()
        total_apps = ApplicationComponent.query.count()
        groups = service.get_duplicate_groups("simple", include_applications=True)
        runs = service.get_detection_runs("simple")

        total_groups = len(groups)
        pending_groups = len([g for g in groups if g.get("status") == "pending"])
        resolved_groups = len(
            [g for g in groups if g.get("status") in ["resolved", "approved"]]
        )
        estimated_savings = sum(g.get("estimated_savings", 0) for g in groups)

        stats = {
            "total_applications": total_apps,
            "duplicate_groups": total_groups,
            "total_groups": total_groups,
            "pending_groups": pending_groups,
            "resolved_groups": resolved_groups,
            "estimated_savings": estimated_savings,
        }

        # Add pipeline stats the template expects (time_scored, consolidation, roadmap)
        try:
            from app.models.application_rationalization import ApplicationRationalizationScore
            stats["time_scored_count"] = ApplicationRationalizationScore.query.count()
        except Exception:
            stats["time_scored_count"] = 0

        try:
            from app.models.consolidation_list import ConsolidationListEntry
            stats["consolidation_count"] = ConsolidationListEntry.query.count()
        except Exception:
            stats["consolidation_count"] = 0

        try:
            from app.models.roadmap import RoadmapTask
            stats["roadmap_count"] = RoadmapTask.query.count()
        except Exception:
            stats["roadmap_count"] = 0

        return render_template(
            "applications/rationalization.html",
            stats=stats,
            groups=groups,
            runs=runs[:10] if runs else [],
            currency_symbol=currency_symbol,
        )
    except Exception as e:
        from app import db

        db.session.rollback()
        logger.exception("Could not load rationalization stats: %s", e)
        flash("Error loading rationalization data. Please try again.", "error")
        # stats=None rather than a zeroed dict. "0 duplicate groups, 0 estimated
        # savings" is a conclusion about the portfolio; nothing was counted here.
        return render_template(
            "applications/rationalization.html",
            stats=None,
            groups=[],
            runs=[],
            currency_symbol=currency_symbol,
            load_error="Rationalization statistics could not be read.",
        )


@dashboard_bp.route("/api/rationalization/calculate/<int:app_id>", methods=["POST"])
@login_required
@audit_log("rationalization_score_calculate")
def calculate_rationalization_score(app_id):
    """Calculate rationalization score for a specific application."""
    try:
        score = RationalizationScoringService.calculate_app_score(app_id)
        if score:
            return jsonify(
                {
                    "success": True,
                    "data": {
                        "application_id": app_id,
                        "overall_score": score.overall_health_score,
                        "technical_score": score.technical_health_score,
                        "business_score": score.business_value_score,
                        "cost_score": score.cost_efficiency_score,
                        "vendor_score": score.vendor_risk_score,
                        "vendor_lock_in_level": score.vendor_lock_in_level,
                        "vendor_viability_score": score.vendor_viability_score,
                        "exit_complexity": score.exit_complexity,
                        "exit_cost_estimate": float(score.exit_cost_estimate) if score.exit_cost_estimate is not None else None,
                        "alternative_vendors_available": score.alternative_vendors_available,
                        "time_action": score.rationalization_action,
                        "rationale": score.action_rationale,
                    },
                }
            )
        return jsonify({"success": False, "error": "Failed to calculate score"}), 500
    except Exception as e:
        logger.error(f"Error calculating rationalization score: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_bp.route(
    "/api/rationalization/options-analysis/<int:app_id>", methods=["POST"]
)
@login_required
@audit_log("rationalization_options_analysis")
def analyze_migration_options(app_id):
    """
    Analyze migration/investment options for an application.

    Uses OptionsAnalysisEngine to perform multi-criteria decision analysis.
    Supports MIGRATE and INVEST actions from rationalization service.
    """
    from app.models import ApplicationComponent
    from app.services.options_analysis_engine import (
        AnalysisOption,
        get_options_analysis_engine,
    )

    try:
        # Validate application exists
        app = ApplicationComponent.query.get(app_id)
        if not app:
            return jsonify({"success": False, "error": "Application not found"}), 404

        # Parse request body
        data = request.get_json() or {}
        requirements = data.get("requirements", {})
        options_data = data.get("options", [])

        # Build AnalysisOption objects from request
        options = []
        for opt_data in options_data:
            option = AnalysisOption(
                id=opt_data.get("id"),
                name=opt_data.get("name"),
                vendor_id=opt_data.get("vendor_id"),
                product_id=opt_data.get("product_id"),
                description=opt_data.get("description", ""),
                technical_specs=opt_data.get("technical_specs", {}),
                cost_estimates=opt_data.get("cost_estimates", {}),
                metadata=opt_data.get("metadata", {}),
            )
            options.append(option)

        if not options:
            return jsonify(
                {"success": False, "error": "No options provided for analysis"}
            ), 400

        # Get analysis engine and run analysis (synchronous)
        engine = get_options_analysis_engine()
        from flask_login import current_user

        result = engine.analyze_options(
            requirements=requirements,
            options=options,
            user_id=str(current_user.id) if current_user else None,
            session_id=data.get("session_id"),
        )

        return jsonify({"success": True, "data": result})

    except ImportError as e:
        # Handle missing dependencies gracefully
        logger.warning(f"OptionsAnalysisEngine dependencies unavailable: {e}")
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Options analysis service temporarily unavailable",
                    "details": "Please contact administrator",
                }
            ),
            503,
        )
    except Exception as e:
        logger.error(f"Error analyzing options for app {app_id}: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_bp.route("/api/rationalization/portfolio", methods=["POST"])
@login_required
@audit_log("rationalization_portfolio_calculate")
def calculate_portfolio_scores():
    """Calculate rationalization scores for entire portfolio."""
    try:
        # request.json raises UnsupportedMediaType (a plain Exception) when the
        # caller sends no body/Content-Type — which is exactly what the scorecard
        # pages do (Platform.fetch.post(url, null) omits both). The blanket
        # `except Exception` below then turned that into a 500 and the page
        # showed "Unhandled promise rejection: An internal error occurred".
        # get_json(silent=True) returns None instead of raising.
        payload = request.get_json(silent=True) or {}
        force_recalc = bool(payload.get("force_recalculate", False))
        results = RationalizationScoringService.calculate_portfolio_scores(force_recalc)
        # Add flat keys expected by scorecard JS (updateMetrics function)
        avg = results.get("average_scores", {})
        results["total_scored"] = results.get("processed", 0)
        results["average_overall_score"] = avg.get("overall")
        results["average_technical_score"] = avg.get("technical")
        results["average_business_score"] = avg.get("business")
        results["average_cost_score"] = avg.get("cost")
        results["average_vendor_score"] = avg.get("vendor")
        return jsonify({"success": True, "data": results})
    except Exception as e:
        logger.error(f"Error calculating portfolio scores: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_bp.route("/api/rationalization/elimination-candidates", methods=["GET"])
@login_required
def get_elimination_candidates():
    """Get top candidates for elimination."""
    try:
        limit = safe_int_arg('limit', 20, minimum=1, maximum=500)
        candidates = RationalizationScoringService.get_elimination_candidates(
            limit=limit
        )
        return jsonify({"success": True, "data": candidates})
    except Exception as e:
        logger.error(f"Error getting elimination candidates: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ============================================================================
# Enterprise Services - Governance (removed — empty shell page)
# ============================================================================

# governance_dashboard + 3 governance APIs removed
# (governance_dashboard, check_architecture_compliance, get_governance_metrics,
#  get_portfolio_governance_summary — 4 routes removed)


# ============================================================================
# Enterprise Services - Vendor Risk (removed — Phase 5)
# ============================================================================


# ============================================================================
# Enterprise Services - Application Consolidation
# ============================================================================


@dashboard_bp.route("/consolidation")
@login_required
def consolidation_dashboard():
    """Redirect to canonical consolidation list page."""
    return redirect(url_for("consolidation_list.dashboard"))


@dashboard_bp.route("/api/consolidation/analyze-portfolio", methods=["POST"])
@login_required
@audit_log("consolidation_portfolio_analyze")
def analyze_portfolio_duplicates():
    """Analyze portfolio for duplicate applications."""
    try:
        service = ApplicationConsolidationService()
        threshold = request.json.get("threshold", 40) if request.json else 40
        force = request.json.get("force_reanalysis", False) if request.json else False
        results = service.analyze_portfolio_for_duplicates(threshold, force)
        return jsonify({"success": True, "data": results})
    except Exception as e:
        logger.error(f"Error analyzing portfolio duplicates: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_bp.route(
    "/api/consolidation/similarity/<int:app1_id>/<int:app2_id>", methods=["POST"]
)
@login_required
@audit_log("consolidation_similarity_calculate")
def calculate_app_similarity(app1_id, app2_id):
    """Calculate similarity between two applications."""
    try:
        service = ApplicationConsolidationService()
        analysis = service.calculate_similarity_score(app1_id, app2_id)
        if analysis:
            return jsonify(
                {
                    "success": True,
                    "data": {
                        "app_1_id": app1_id,
                        "app_2_id": app2_id,
                        "overall_score": analysis.overall_similarity_score,
                        "capability_overlap": analysis.capability_overlap_score,
                        "technology_similarity": analysis.technology_similarity_score,
                        "functional_similarity": analysis.functional_similarity_score,
                        "consolidation_opportunity": analysis.consolidation_opportunity,
                        "recommended_action": analysis.recommended_action,
                        "estimated_savings": float(analysis.estimated_cost_savings)
                        if analysis.estimated_cost_savings
                        else 0,
                    },
                }
            )
        return jsonify(
            {"success": False, "error": "Failed to calculate similarity"}
        ), 500
    except Exception as e:
        logger.error(f"Error calculating similarity: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_bp.route("/api/consolidation/opportunities", methods=["GET"])
@login_required
def get_consolidation_opportunities():
    """Get top consolidation opportunities."""
    try:
        service = ApplicationConsolidationService()
        limit = safe_int_arg('limit', 10, minimum=1, maximum=500)
        opportunities = service.get_consolidation_opportunities(limit)
        return jsonify({"success": True, "data": opportunities})
    except Exception as e:
        logger.error(f"Error getting consolidation opportunities: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_bp.route("/api/consolidation/generate-recommendations", methods=["POST"])
@login_required
@audit_log("consolidation_recommendations_generate")
def generate_consolidation_recommendations():
    """Generate formal consolidation recommendations."""
    try:
        service = ApplicationConsolidationService()
        min_similarity = request.json.get("min_similarity", 60) if request.json else 60
        max_recs = request.json.get("max_recommendations", 20) if request.json else 20
        recommendations = service.generate_consolidation_recommendations(
            min_similarity, max_recs
        )

        results = [
            {
                "id": rec.id,
                "code": rec.recommendation_code,
                "name": rec.recommendation_name,
                "type": rec.consolidation_type,
                "savings": float(rec.estimated_annual_savings)
                if rec.estimated_annual_savings
                else 0,
                "complexity": rec.migration_complexity,
                "priority": rec.priority,
            }
            for rec in recommendations
        ]

        return jsonify({"success": True, "data": results})
    except Exception as e:
        logger.error(f"Error generating recommendations: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ============================================================================
# Enterprise Services - Retirement Impact Analysis
# ============================================================================


@dashboard_bp.route(
    "/api/rationalization/retirement-blockers/<int:app_id>", methods=["GET"]
)
@login_required
def get_retirement_blockers(app_id):
    """
    Get applications that depend on this app and would block its retirement.

    Returns blocking dependencies with criticality assessment.
    Use this before recommending ELIMINATE to understand impact.
    """
    try:
        blockers = RationalizationScoringService.get_retirement_blockers(app_id)
        return jsonify({"success": True, "data": blockers})
    except Exception as e:
        logger.error(
            f"Error getting retirement blockers for app {app_id}: {e}", exc_info=True
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_bp.route("/api/rationalization/blast-radius/<int:app_id>", methods=["GET"])
@login_required
def get_blast_radius(app_id):
    """
    Get the full impact cascade of retiring an application.

    Performs recursive dependency traversal to find all applications
    that would be affected (directly or indirectly) by retiring this app.

    Query params:
        depth: Maximum depth of dependency traversal (default 3, max 5)
    """
    try:
        depth = request.args.get("depth", 3, type=int)
        depth = min(max(depth, 1), 5)  # Clamp between 1 and 5

        blast_radius = RationalizationScoringService.get_blast_radius(app_id, depth)

        if "error" in blast_radius:
            return jsonify({"success": False, "error": blast_radius["error"]}), 404

        return jsonify({"success": True, "data": blast_radius})
    except Exception as e:
        logger.error(
            f"Error calculating blast radius for app {app_id}: {e}", exc_info=True
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ============================================================================
# Scoring Configuration Management - CIO.gov Playbook Compliance
# ============================================================================


@dashboard_bp.route("/api/scoring-configurations", methods=["GET"])
@login_required
def get_scoring_configurations():
    """Get all active scoring configurations."""
    try:
        from app.models.application_rationalization import ScoringConfiguration

        configs = ScoringConfiguration.query.filter_by(is_active=True).all()
        return jsonify(
            {"success": True, "data": [config.to_dict() for config in configs]}
        )
    except Exception as e:
        logger.error(f"Error getting scoring configurations: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_bp.route("/api/scoring-configurations/<int:config_id>", methods=["GET"])
@login_required
def get_scoring_configuration(config_id):
    """Get a specific scoring configuration."""
    try:
        from app.models.application_rationalization import ScoringConfiguration

        config = ScoringConfiguration.query.get(config_id)
        if not config:
            return jsonify({"success": False, "error": "Configuration not found"}), 404

        return jsonify({"success": True, "data": config.to_dict()})
    except Exception as e:
        logger.error(f"Error getting scoring configuration: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_bp.route("/api/scoring-configurations", methods=["POST"])
@login_required
@platform_admin_required
@audit_log("scoring_configuration_create")
def create_scoring_configuration():
    """Create a new scoring configuration."""
    try:
        from app.models.application_rationalization import ScoringConfiguration
        from app import db

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        # Validate weights sum to 100
        total_weight = (
            data.get("technical_health_weight", 30)
            + data.get("business_value_weight", 35)
            + data.get("cost_efficiency_weight", 25)
            + data.get("vendor_risk_weight", 10)
        )
        if total_weight != 100:
            return jsonify(
                {
                    "success": False,
                    "error": f"Weights must sum to 100, got {total_weight}",
                }
            ), 400

        config = ScoringConfiguration(
            name=data.get("name"),
            description=data.get("description"),
            scope_type=data.get("scope_type", "business_unit"),
            scope_entity_id=data.get("scope_entity_id"),
            scope_entity_type=data.get("scope_entity_type"),
            technical_health_weight=data.get("technical_health_weight", 30),
            business_value_weight=data.get("business_value_weight", 35),
            cost_efficiency_weight=data.get("cost_efficiency_weight", 25),
            vendor_risk_weight=data.get("vendor_risk_weight", 10),
            eliminate_threshold=data.get("eliminate_threshold", 40),
            migrate_technical_threshold=data.get("migrate_technical_threshold", 40),
            migrate_business_threshold=data.get("migrate_business_threshold", 50),
            invest_business_threshold=data.get("invest_business_threshold", 70),
            invest_technical_threshold=data.get("invest_technical_threshold", 50),
            tolerate_min_threshold=data.get("tolerate_min_threshold", 40),
            is_default=data.get("is_default", False),
        )

        # If setting as default, unset other defaults
        if config.is_default:
            ScoringConfiguration.query.filter_by(is_default=True).update(
                {"is_default": False}
            )

        db.session.add(config)
        db.session.commit()

        return jsonify({"success": True, "data": config.to_dict()}), 201
    except Exception as e:
        logger.error(f"Error creating scoring configuration: {e}", exc_info=True)
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_bp.route("/api/scoring-configurations/<int:config_id>", methods=["PUT"])
@login_required
@platform_admin_required
@audit_log("scoring_configuration_update")
def update_scoring_configuration(config_id):
    """Update an existing scoring configuration."""
    try:
        from app.models.application_rationalization import ScoringConfiguration
        from app import db

        config = ScoringConfiguration.query.get(config_id)
        if not config:
            return jsonify({"success": False, "error": "Configuration not found"}), 404

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        # Update fields
        if "name" in data:
            config.name = data["name"]
        if "description" in data:
            config.description = data["description"]
        if "technical_health_weight" in data:
            config.technical_health_weight = data["technical_health_weight"]
        if "business_value_weight" in data:
            config.business_value_weight = data["business_value_weight"]
        if "cost_efficiency_weight" in data:
            config.cost_efficiency_weight = data["cost_efficiency_weight"]
        if "vendor_risk_weight" in data:
            config.vendor_risk_weight = data["vendor_risk_weight"]

        # Validate weights if any changed
        if any(
            k in data
            for k in [
                "technical_health_weight",
                "business_value_weight",
                "cost_efficiency_weight",
                "vendor_risk_weight",
            ]
        ):
            is_valid, error = config.validate_weights()
            if not is_valid:
                return jsonify({"success": False, "error": error}), 400

        # Update thresholds
        for threshold in [
            "eliminate_threshold",
            "migrate_technical_threshold",
            "migrate_business_threshold",
            "invest_business_threshold",
            "invest_technical_threshold",
            "tolerate_min_threshold",
        ]:
            if threshold in data:
                setattr(config, threshold, data[threshold])

        # Handle default flag
        if data.get("is_default") and not config.is_default:
            ScoringConfiguration.query.filter_by(is_default=True).update(
                {"is_default": False}
            )
            config.is_default = True

        config.configuration_version += 1
        db.session.commit()

        return jsonify({"success": True, "data": config.to_dict()})
    except Exception as e:
        logger.error(f"Error updating scoring configuration: {e}", exc_info=True)
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_bp.route("/api/scoring-configurations/<int:config_id>", methods=["DELETE"])
@login_required
@platform_admin_required
@audit_log("scoring_configuration_delete")
def delete_scoring_configuration(config_id):
    """Soft delete a scoring configuration."""
    try:
        from app.models.application_rationalization import ScoringConfiguration
        from app import db

        config = ScoringConfiguration.query.get(config_id)
        if not config:
            return jsonify({"success": False, "error": "Configuration not found"}), 404

        if config.is_default:
            return jsonify(
                {"success": False, "error": "Cannot delete default configuration"}
            ), 400

        config.is_active = False
        db.session.commit()

        return jsonify({"success": True, "message": "Configuration deleted"})
    except Exception as e:
        logger.error(f"Error deleting scoring configuration: {e}", exc_info=True)
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_bp.route("/api/scoring-configurations/validate-weights", methods=["POST"])
@login_required
@audit_log("scoring_weights_validate")
def validate_scoring_weights():
    """Validate that provided weights sum to 100."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        total = (
            data.get("technical_health_weight", 0)
            + data.get("business_value_weight", 0)
            + data.get("cost_efficiency_weight", 0)
            + data.get("vendor_risk_weight", 0)
        )

        return jsonify(
            {
                "success": True,
                "data": {
                    "total": total,
                    "is_valid": total == 100,
                    "message": "Valid weights"
                    if total == 100
                    else f"Weights sum to {total}, must be 100",
                },
            }
        )
    except Exception as e:
        logger.error(f"Error validating weights: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_bp.route("/rationalization/assessment")
@login_required
def rationalization_assessment():
    """Render the rationalization assessment questionnaire page."""
    from app.models import ApplicationComponent

    # ApplicationComponent has no is_active field — query all (capped at 2000)
    applications = ApplicationComponent.query.limit(2000).all()
    return render_template(
        "application_mgmt/rationalization_assessment.html", applications=applications
    )


@dashboard_bp.route("/api/rationalization/assessment", methods=["POST"])
@login_required
@audit_log("rationalization_assessment_submit")
def submit_rationalization_assessment():
    """Submit assessment questionnaire responses and recalculate score."""
    try:
        data = request.get_json()
        if not data or "application_id" not in data:
            return jsonify({"success": False, "error": "Application ID required"}), 400

        app_id = data["application_id"]

        # Store assessment responses (could be saved to a new AssessmentResponse model)
        # For now, we'll just trigger a recalculation
        score = RationalizationScoringService.calculate_app_score(app_id)

        if score:
            return jsonify(
                {
                    "success": True,
                    "data": {
                        "application_id": app_id,
                        "overall_score": score.overall_health_score,
                        "time_action": score.rationalization_action,
                        "message": "Assessment completed successfully",
                    },
                }
            )
        return jsonify({"success": False, "error": "Failed to calculate score"}), 500
    except Exception as e:
        logger.error(f"Error submitting assessment: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_bp.route("/api/tco/cost-tiers", methods=["GET"])
@login_required
def get_tco_cost_tiers():
    """Get TCO cost range tiers for executive reporting."""
    try:
        from app.models import ApplicationComponent
        from app import db
        from sqlalchemy import func, case

        # Define cost tiers
        tiers = [
            {
                "name": "Very Low",
                "min": 0,
                "max": 50000,
                "color": "green",
                "description": "<$50K/year",
            },
            {
                "name": "Low",
                "min": 50000,
                "max": 250000,
                "color": "blue",
                "description": "$50K-$250K/year",
            },
            {
                "name": "Medium",
                "min": 250000,
                "max": 1000000,
                "color": "amber",
                "description": "$250K-$1M/year",
            },
            {
                "name": "High",
                "min": 1000000,
                "max": 5000000,
                "color": "orange",
                "description": "$1M-$5M/year",
            },
            {
                "name": "Very High",
                "min": 5000000,
                "max": None,
                "color": "red",
                "description": ">$5M/year",
            },
        ]

        # Use total_cost_of_ownership for annual TCO
        cost_col = ApplicationComponent.total_cost_of_ownership

        # Portfolio-level counts for data completeness
        scope_filter = ApplicationComponent.lifecycle_status.in_(
            ["operational", "testing", "development", "deprecated"]
        )
        total_portfolio = db.session.query(func.count(ApplicationComponent.id)).filter(scope_filter).scalar() or 0
        apps_with_tco = db.session.query(func.count(ApplicationComponent.id)).filter(
            scope_filter, cost_col.isnot(None)
        ).scalar() or 0
        portfolio_tco = db.session.query(func.sum(cost_col)).filter(
            scope_filter, cost_col.isnot(None)
        ).scalar() or 0

        # Optimized: Single query with CASE statements instead of 5 separate queries
        tier_cases = []
        for i, tier in enumerate(tiers):
            if tier["max"]:
                condition = db.and_(cost_col >= tier["min"], cost_col < tier["max"])
            else:
                condition = cost_col >= tier["min"]
            tier_cases.append(
                func.sum(case((condition, 1), else_=0)).label(f"tier_{i}")
            )

        # Execute single query with all counts — only apps with cost data
        counts_result = (
            db.session.query(*tier_cases)
            .filter(cost_col.isnot(None))
            .first()
        )

        # Build results from single query
        results = []
        for i, tier in enumerate(tiers):
            count = (getattr(counts_result, f"tier_{i}", 0) or 0) if counts_result else 0
            results.append(
                {
                    **tier,
                    "application_count": count,
                    "percentage": 0,  # Will calculate after getting total
                }
            )

        # Calculate percentages
        apps_in_tiers = sum(r["application_count"] for r in results)
        if apps_in_tiers > 0:
            for r in results:
                r["percentage"] = round((r["application_count"] / apps_in_tiers) * 100, 1)

        return jsonify({
            "success": True,
            "data": results,
            "summary": {
                "total_portfolio": total_portfolio,
                "apps_with_tco": apps_with_tco,
                "apps_without_tco": total_portfolio - apps_with_tco,
                "coverage_percent": round((apps_with_tco / total_portfolio) * 100, 1) if total_portfolio > 0 else 0,
                "total_tco": round(float(portfolio_tco), 2),
            },
        })
    except Exception as e:
        logger.error(f"Error getting TCO cost tiers: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_bp.route("/rationalization/scorecard")
@login_required
def rationalization_scorecard():
    """Render the executive rationalization scorecard dashboard."""
    return render_template("dashboard/rationalization_scorecard.html")


@dashboard_bp.route("/rationalization/onboard")
@login_required
def rationalization_onboard():
    """Redirect to the application list (onboarding = adding new apps via modal)."""
    return redirect(url_for("unified_applications.application_list"))


@dashboard_bp.route("/api/rationalization/onboard", methods=["POST"])
@login_required
@audit_log("rationalization_onboard")
def api_rationalization_onboard():
    """API endpoint to complete application onboarding."""
    try:
        data = request.get_json()
        if not data or not data.get("name"):
            return jsonify(
                {"success": False, "error": "Application name required"}
            ), 400

        from app.models import ApplicationComponent
        from app import db

        # Create new application
        app = ApplicationComponent(
            name=data.get("name"),
            description=data.get("description"),
            application_type=data.get("type"),
            lifecycle_status=data.get("lifecycle_status", "planning"),
            estimated_cost=data.get("annual_cost"),
        )
        db.session.add(app)
        db.session.flush()

        # Calculate initial rationalization score
        score = RationalizationScoringService.calculate_app_score(app.id, app)

        db.session.commit()

        return jsonify(
            {
                "success": True,
                "data": {
                    "application_id": app.id,
                    "name": app.name,
                    "overall_score": score.overall_health_score if score else None,
                    "time_action": score.rationalization_action if score else None,
                },
            }
        )
    except Exception as e:
        logger.error(f"Error onboarding application: {e}", exc_info=True)
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_bp.route("/rationalization/validate")
@login_required
def response_validation():
    """Render the response validation and data comparison page."""
    from app.models import ApplicationComponent

    # ApplicationComponent has no is_active field — query all (capped at 2000)
    applications = ApplicationComponent.query.limit(2000).all()
    return render_template(
        "application_mgmt/response_validation.html", applications=applications
    )


@dashboard_bp.route("/api/rationalization/validate/<int:app_id>", methods=["GET"])
@login_required
def get_validation_comparison(app_id):
    """Get data comparison for validation between Abacus and manual/assessment sources."""
    try:
        from app.models import ApplicationComponent

        app = ApplicationComponent.query.get(app_id)
        if not app:
            return jsonify({"success": False, "error": "Application not found"}), 404

        # Define fields to compare
        fields = [
            {"name": "Name", "field": "name"},
            {"name": "Description", "field": "description"},
            {"name": "Application Type", "field": "application_type"},
            {"name": "Lifecycle Status", "field": "lifecycle_status"},
            {"name": "Annual Cost", "field": "estimated_cost"},
            {"name": "User Count", "field": "user_count"},
            {"name": "Vendor", "field": "vendor_name"},
            {"name": "Version", "field": "version"},
            {"name": "Strategic Importance", "field": "strategic_importance"},
            {"name": "Technical Debt", "field": "technical_debt_level"},
        ]

        comparisons = []
        for f in fields:
            abacus_val = getattr(app, f["field"], None)
            manual_val = getattr(app, f"manual_{f['field']}", None) or getattr(
                app, f"assessment_{f['field']}", None
            )

            status = "match"
            if not abacus_val and not manual_val:
                status = "missing"
            elif abacus_val != manual_val:
                status = "conflict" if manual_val else "match"

            comparisons.append(
                {
                    "field": f["name"],
                    "field_key": f["field"],
                    "abacus_value": abacus_val,
                    "manual_value": manual_val,
                    "manual_source": "manual" if manual_val else None,
                    "status": status,
                }
            )

        stats = {
            "total": len(comparisons),
            "matched": len([c for c in comparisons if c["status"] == "match"]),
            "conflicts": len([c for c in comparisons if c["status"] == "conflict"]),
            "missing": len([c for c in comparisons if c["status"] == "missing"]),
        }

        return jsonify(
            {
                "success": True,
                "data": {
                    "application_id": app_id,
                    "application_name": app.name,
                    "comparisons": comparisons,
                    "stats": stats,
                },
            }
        )
    except Exception as e:
        logger.error(f"Error getting validation comparison: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_bp.route(
    "/api/rationalization/validate/<int:app_id>/resolve", methods=["POST"]
)
@login_required
@audit_log("rationalization_validation_resolve")
def resolve_validation_conflict(app_id):
    """Resolve a data conflict by choosing a source of truth."""
    try:
        from app.models import ApplicationComponent
        from app import db

        app = ApplicationComponent.query.get(app_id)
        if not app:
            return jsonify({"success": False, "error": "Application not found"}), 404

        data = request.get_json()
        field = data.get("field")
        value = data.get("value")
        source = data.get("source")
        notes = data.get("notes")

        if not field:
            return jsonify({"success": False, "error": "Field name required"}), 400

        # Map display name to field key
        field_map = {
            "Name": "name",
            "Description": "description",
            "Application Type": "application_type",
            "Lifecycle Status": "lifecycle_status",
            "Annual Cost": "estimated_cost",
            "User Count": "user_count",
            "Vendor": "vendor_name",
            "Version": "version",
            "Strategic Importance": "strategic_importance",
            "Technical Debt": "technical_debt_level",
        }

        field_key = field_map.get(field, field.lower().replace(" ", "_"))

        # Update the application record
        if hasattr(app, field_key):
            setattr(app, field_key, value)
            db.session.commit()

            return jsonify(
                {
                    "success": True,
                    "data": {
                        "field": field,
                        "resolved_value": value,
                        "source": source,
                        "notes": notes,
                    },
                }
            )
        else:
            return jsonify(
                {"success": False, "error": f"Field {field} not found on application"}
            ), 400

    except Exception as e:
        logger.error(f"Error resolving conflict: {e}", exc_info=True)
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


def register_dashboard_routes(app):
    """Register dashboard blueprint with Flask app."""
    app.register_blueprint(dashboard_bp)
    logger.info("Dashboard routes registered")

"""
Dashboard Pages Routes v2 — guardrail-enabled.

Enterprise Architecture Frontend Interfaces: review queue, APQC browser,
import history, and enterprise services (rationalization,
governance, vendor risk, consolidation).

Uses the new architecture:
- @timed_route for automatic metrics collection on all endpoints
- Observability (request_id in response headers)

URL prefix preserved: /dashboard (baked into Blueprint definition)
Blueprint name: dashboard_pages (same as v1 — no cross-module url_for refs found)

All 40 routes preserved exactly from v1 dashboard_pages_routes.py.
"""

import logging

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from app.core.compat import mark_blueprint_guardrailed
from app.core.decorators import timed_route
from app.decorators import audit_log
from app.modules.dashboard.v2.services import (
    ApplicationConsolidationService,
    CapabilityHeatmapService,
    RationalizationScoringService,
)
from app.utils.pagination import safe_int_arg

logger = logging.getLogger(__name__)

dashboard_pages_bp_v2 = Blueprint("dashboard_pages", __name__, url_prefix="/dashboard")
mark_blueprint_guardrailed(dashboard_pages_bp_v2)


@dashboard_pages_bp_v2.route("/api/capability-heatmap", methods=["GET"])
@timed_route
@login_required
def api_capability_heatmap():
    """API endpoint for capability maturity heatmap data.

    Query params:
        group_by  – When ``domain``, aggregates investment (sum of solution TCO)
                    per capability domain alongside the maturity heatmap data.
                    The page's "Investment" view mode requests exactly this; a
                    response without it would leave every currency figure on that
                    tab at the template's ``|| 0`` fallback, which reads as a
                    measured zero.
    """
    try:
        heatmap_service = CapabilityHeatmapService()
        heatmap_data = heatmap_service.get_maturity_heatmap()

        if request.args.get("group_by", "") == "domain":
            investment_by_domain = _aggregate_investment_by_domain()
            inv_lookup = {d["domain_code"]: d for d in investment_by_domain}
            for domain_row in heatmap_data.get("domains", []):
                inv = inv_lookup.get(domain_row.get("code", ""), {})
                domain_row["total_investment"] = inv.get("total_investment", 0)
                domain_row["solution_count"] = inv.get("solution_count", 0)
                domain_row["cost_breakdown"] = inv.get("cost_breakdown", {})
            heatmap_data["investment_summary"] = {
                "grand_total": sum(d.get("total_investment", 0) for d in investment_by_domain),
                "domains_with_investment": len(
                    [d for d in investment_by_domain if d["total_investment"] > 0]
                ),
            }

        return jsonify({"success": True, "data": heatmap_data}), 200
    except Exception as e:
        logger.exception(f"Error getting capability heatmap: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


def _aggregate_investment_by_domain():
    """Aggregate solution TCO amounts grouped by capability domain.

    Join chain: BusinessCapability → SolutionCapabilityMapping → Solution → SolutionTCOItem.
    Returns a list of dicts: [{domain_code, domain_name, total_investment, solution_count, cost_breakdown}].
    """
    from sqlalchemy import func as sa_func

    from app import db
    from app.models.business_capabilities import BusinessCapability
    from app.models.solution_lifecycle_models import SolutionTCOItem
    from app.models.solution_models import SolutionCapabilityMapping

    try:
        rows = (
            db.session.query(
                BusinessCapability.business_domain,
                BusinessCapability.code,
                SolutionTCOItem.cost_category,
                sa_func.sum(SolutionTCOItem.amount).label("total"),
                sa_func.count(sa_func.distinct(SolutionTCOItem.solution_id)).label("sol_count"),
            )
            .join(
                SolutionCapabilityMapping,
                SolutionCapabilityMapping.capability_id == BusinessCapability.id,
            )
            .join(
                SolutionTCOItem,
                SolutionTCOItem.solution_id == SolutionCapabilityMapping.solution_id,
            )
            .group_by(
                BusinessCapability.business_domain,
                BusinessCapability.code,
                SolutionTCOItem.cost_category,
            )
            .all()
        )

        domain_map = {}
        for domain_name, domain_code, cost_cat, total, sol_count in rows:
            d_code = domain_code or "UNK"
            entry = domain_map.setdefault(
                d_code,
                {
                    "domain_code": d_code,
                    "domain_name": domain_name or "Unknown",
                    "total_investment": 0,
                    "solution_count": 0,
                    "cost_breakdown": {},
                },
            )
            amount = float(total) if total else 0
            entry["total_investment"] += amount
            category = cost_cat or "other"
            entry["cost_breakdown"][category] = entry["cost_breakdown"].get(category, 0) + amount
            entry["solution_count"] = max(entry["solution_count"], sol_count or 0)

        results = []
        for entry in domain_map.values():
            entry["total_investment"] = round(entry["total_investment"], 2)
            for k in entry["cost_breakdown"]:
                entry["cost_breakdown"][k] = round(entry["cost_breakdown"][k], 2)
            results.append(entry)

        return sorted(results, key=lambda r: r["total_investment"], reverse=True)

    except Exception as e:
        logger.warning(f"Investment aggregation failed (non-fatal): {e}")
        return []


@dashboard_pages_bp_v2.route("/capability-heatmap")
@timed_route
@login_required
def capability_heatmap_page():
    """Capability heatmap and investment-by-domain dashboard page.

    v1 (``app/modules/dashboard/routes/dashboard_pages_routes.py``) served this
    page; the v2 rewrite kept the API and dropped the page, so
    ``/dashboard/capability-heatmap`` 404'd for as long as USE_DASHBOARD_GUARDRAILS
    has been on. The template and the API it calls were both live throughout.
    """
    from config import CurrencyConfig

    try:
        currency_symbol = CurrencyConfig.get_currency_config().get("symbol", "£")
    except Exception:
        currency_symbol = "£"

    return render_template(
        "dashboard/capability_heatmap.html",
        currency_symbol=currency_symbol,
    )


# apqc_browser route removed — zero backend API, empty shell


@dashboard_pages_bp_v2.route("/import-history")
@timed_route
@login_required
def import_history():
    """Import History interface."""
    return render_template("dashboard/import_history.html")


# vendor-catalog route removed — page provided no value (redundant with /vendors/)


# ============================================================================
# Enterprise Services - Rationalization
# ============================================================================


@dashboard_pages_bp_v2.route("/rationalization")
@timed_route
@login_required
def rationalization_dashboard():
    """Application Rationalization Dashboard - TIME Framework."""
    from app.models import ApplicationComponent
    from app.modules.dashboard.v2.services import UnifiedDuplicateDetectionService
    from config import CurrencyConfig

    try:
        currency_symbol = CurrencyConfig.get_currency_config().get("symbol", "\u00a3")
    except Exception:
        currency_symbol = "\u00a3"

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


@dashboard_pages_bp_v2.route(
    "/api/rationalization/calculate/<int:app_id>", methods=["POST"]
)
@timed_route
@login_required
@audit_log("rationalization_calculate")
def calculate_rationalization_score(app_id):
    """Calculate rationalization score for a specific application."""
    try:
        score = RationalizationScoringService.calculate_app_score(app_id)
        if score:
            # evidence_trail is a transient attribute set by calculate_app_score.
            # getattr with default avoids AttributeError if the object was fetched
            # from a separate session (e.g. cached score path).
            evidence_trail = getattr(score, "evidence_trail", None)  # model-safety-ok
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
                        "readiness": {
                            "dimensions": score.readiness_dimensions or {},
                            "readiness_score": score.readiness_score,
                            "is_decision_ready": score.is_decision_ready,
                        },
                        "evidence_trail": evidence_trail,
                    },
                }
            )
        return jsonify({"success": False, "error": "Failed to calculate score"}), 500
    except Exception as e:
        logger.error(f"Error calculating rationalization score: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_pages_bp_v2.route(
    "/api/rationalization/options-analysis/<int:app_id>", methods=["POST"]
)
@timed_route
@login_required
@audit_log("rationalization_options_analysis")
def analyze_migration_options(app_id):
    """Analyze migration/investment options for an application."""
    from app.models import ApplicationComponent
    from app.modules.dashboard.v2.services import (
        AnalysisOption,
        get_options_analysis_engine,
    )

    try:
        app = ApplicationComponent.query.get(app_id)
        if not app:
            return jsonify({"success": False, "error": "Application not found"}), 404

        data = request.get_json() or {}
        requirements = data.get("requirements", {})
        options_data = data.get("options", [])

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


@dashboard_pages_bp_v2.route("/api/rationalization/portfolio", methods=["POST"])
@timed_route
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


@dashboard_pages_bp_v2.route(
    "/api/rationalization/elimination-candidates", methods=["GET"]
)
@timed_route
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
# Enterprise Services - Vendor Risk
# ============================================================================


# vendor_risk_dashboard + vendor risk APIs removed — niche, belongs in vendor detail
# (vendor_risk_dashboard, analyze_vendor_concentration, analyze_portfolio_vendor_risk,
#  get_vendor_exit_strategy — 4 routes removed)


# ============================================================================
# Enterprise Services - Application Consolidation
# ============================================================================


@dashboard_pages_bp_v2.route("/consolidation")
@timed_route
@login_required
def consolidation_dashboard():
    """Redirect to canonical consolidation list page."""
    return redirect(url_for("consolidation_list.dashboard"))


@dashboard_pages_bp_v2.route("/api/consolidation/analyze-portfolio", methods=["POST"])
@timed_route
@login_required
@audit_log("consolidation_analyze_portfolio")
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


@dashboard_pages_bp_v2.route(
    "/api/consolidation/similarity/<int:app1_id>/<int:app2_id>", methods=["POST"]
)
@timed_route
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


@dashboard_pages_bp_v2.route("/api/consolidation/opportunities", methods=["GET"])
@timed_route
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


@dashboard_pages_bp_v2.route(
    "/api/consolidation/generate-recommendations", methods=["POST"]
)
@timed_route
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


@dashboard_pages_bp_v2.route(
    "/api/rationalization/retirement-blockers/<int:app_id>", methods=["GET"]
)
@timed_route
@login_required
def get_retirement_blockers(app_id):
    """Get applications that depend on this app and would block its retirement."""
    from app.models.application_portfolio import ApplicationComponent
    from app.utils.route_guards import require_entity

    require_entity(ApplicationComponent, app_id, description="Application not found")

    try:
        blockers = RationalizationScoringService.get_retirement_blockers(app_id)
        # IA-011: enrich with canonical impact analysis for retirement scenario
        canonical_impact = None
        try:
            from app.modules.ai_chat.services.ai_impact_analysis_service import AIImpactAnalysisService
            raw = AIImpactAnalysisService().analyze_application_impact(
                app_id=app_id, scenario="retirement"
            )
            if raw:
                ra = raw.get("risk_assessment") or {}
                # None, not "LOW"/0 -- this is a retirement risk shown to someone
                # deciding whether to retire an application. An unassessed risk
                # rendered as LOW is indistinguishable from an assessed one, and
                # the `or` chain collapsed None and "" as well as a missing key.
                canonical_impact = {
                    "risk_level": ra.get("risk_level") or raw.get("risk_level"),
                    "total_score": ra.get("total_score"),
                    "breakdown": ra.get("breakdown") or {},
                    "summary": raw.get("executive_summary") or raw.get("summary"),
                }
        except Exception:
            # Best-effort enrichment, but its absence stays visible: the key
            # returns null rather than a confident-looking default.
            logger.exception(
                "canonical impact enrichment failed for app_id=%s", app_id
            )
        return jsonify({"success": True, "data": blockers, "canonical_impact": canonical_impact})
    except Exception as e:
        logger.error(
            f"Error getting retirement blockers for app {app_id}: {e}", exc_info=True
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_pages_bp_v2.route(
    "/api/rationalization/blast-radius/<int:app_id>", methods=["GET"]
)
@timed_route
@login_required
def get_blast_radius(app_id):
    """Get the full impact cascade of retiring an application."""
    try:
        depth = request.args.get("depth", 3, type=int)
        depth = min(max(depth, 1), 5)

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
# Scoring Configuration Management
# ============================================================================


@dashboard_pages_bp_v2.route("/api/scoring-configurations", methods=["GET"])
@timed_route
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


@dashboard_pages_bp_v2.route(
    "/api/scoring-configurations/<int:config_id>", methods=["GET"]
)
@timed_route
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


@dashboard_pages_bp_v2.route("/api/scoring-configurations", methods=["POST"])
@timed_route
@login_required
@audit_log("scoring_configuration_create")
def create_scoring_configuration():
    """Create a new scoring configuration."""
    try:
        from app.models.application_rationalization import ScoringConfiguration
        from app.extensions import db

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

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

        if config.is_default:
            ScoringConfiguration.query.filter_by(is_default=True).update(
                {"is_default": False}
            )

        db.session.add(config)
        db.session.commit()

        return jsonify({"success": True, "data": config.to_dict()}), 201
    except Exception as e:
        logger.error(f"Error creating scoring configuration: {e}", exc_info=True)
        from app.extensions import db

        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_pages_bp_v2.route(
    "/api/scoring-configurations/<int:config_id>", methods=["PUT"]
)
@timed_route
@login_required
@audit_log("scoring_configuration_update")
def update_scoring_configuration(config_id):
    """Update an existing scoring configuration."""
    try:
        from app.models.application_rationalization import ScoringConfiguration
        from app.extensions import db

        config = ScoringConfiguration.query.get(config_id)
        if not config:
            return jsonify({"success": False, "error": "Configuration not found"}), 404

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

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
        from app.extensions import db

        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_pages_bp_v2.route(
    "/api/scoring-configurations/<int:config_id>", methods=["DELETE"]
)
@timed_route
@login_required
@audit_log("scoring_configuration_delete")
def delete_scoring_configuration(config_id):
    """Soft delete a scoring configuration."""
    try:
        from app.models.application_rationalization import ScoringConfiguration
        from app.extensions import db

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
        from app.extensions import db

        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_pages_bp_v2.route(
    "/api/scoring-configurations/validate-weights", methods=["POST"]
)
@timed_route
@login_required
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


@dashboard_pages_bp_v2.route("/rationalization/assessment")
@timed_route
@login_required
def rationalization_assessment():
    """Render the rationalization assessment questionnaire page."""
    from app.models import ApplicationComponent

    # ApplicationComponent has no is_active field — query all
    applications = ApplicationComponent.query.all()
    return render_template(
        "application_mgmt/rationalization_assessment.html", applications=applications
    )


@dashboard_pages_bp_v2.route("/api/rationalization/assessment", methods=["POST"])
@timed_route
@login_required
@audit_log("rationalization_assessment_submit")
def submit_rationalization_assessment():
    """Submit assessment questionnaire responses and recalculate score."""
    try:
        data = request.get_json()
        if not data or "application_id" not in data:
            return jsonify({"success": False, "error": "Application ID required"}), 400

        app_id = data["application_id"]
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


@dashboard_pages_bp_v2.route("/api/tco/cost-tiers", methods=["GET"])
@timed_route
@login_required
def get_tco_cost_tiers():
    """Get TCO cost range tiers for executive reporting."""
    try:
        from app.models import ApplicationComponent
        from app.extensions import db
        from sqlalchemy import func

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

        results = []
        for tier in tiers:
            query = db.session.query(func.count(ApplicationComponent.id)).filter(
                cost_col.isnot(None)
            )

            if tier["max"]:
                query = query.filter(cost_col >= tier["min"], cost_col < tier["max"])
            else:
                query = query.filter(cost_col >= tier["min"])

            count = query.scalar() or 0

            results.append({**tier, "application_count": count, "percentage": None})  # M1: None until computed below

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
                "coverage_percent": round((apps_with_tco / total_portfolio) * 100, 1) if total_portfolio > 0 else None,  # M1: no portfolio yet is unmeasured, not 0%
                "total_tco": round(float(portfolio_tco), 2),
            },
        })
    except Exception as e:
        logger.error(f"Error getting TCO cost tiers: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_pages_bp_v2.route("/scorecard")
@login_required
def scorecard_redirect():
    """Redirect legacy /dashboard/scorecard to the canonical rationalization scorecard."""
    return redirect(url_for("dashboard_pages.rationalization_scorecard"))


@dashboard_pages_bp_v2.route("/rationalization/scorecard")
@timed_route
@login_required
def rationalization_scorecard():
    """Render the executive rationalization scorecard dashboard."""
    return render_template("dashboard/rationalization_scorecard.html")


@dashboard_pages_bp_v2.route("/rationalization/onboard")
@timed_route
@login_required
def rationalization_onboard():
    """Redirect to the application list (onboarding = adding new apps via modal)."""
    return redirect(url_for("unified_applications.application_list"))


@dashboard_pages_bp_v2.route("/api/rationalization/onboard", methods=["POST"])
@timed_route
@login_required
@audit_log("application_onboard")
def api_rationalization_onboard():
    """API endpoint to complete application onboarding."""
    try:
        data = request.get_json()
        if not data or not data.get("name"):
            return jsonify(
                {"success": False, "error": "Application name required"}
            ), 400

        from app.models import ApplicationComponent
        from app.extensions import db

        app = ApplicationComponent(
            name=data.get("name"),
            description=data.get("description"),
            application_type=data.get("type"),
            lifecycle_status=data.get("lifecycle_status", "planning"),
            total_cost_of_ownership=data.get("annual_cost"),
        )
        db.session.add(app)
        db.session.flush()

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
        from app.extensions import db

        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@dashboard_pages_bp_v2.route("/rationalization/validate")
@timed_route
@login_required
def response_validation():
    """Render the response validation and data comparison page."""
    from app.models import ApplicationComponent

    # ApplicationComponent has no is_active field — query all
    applications = ApplicationComponent.query.all()
    return render_template(
        "application_mgmt/response_validation.html", applications=applications
    )


@dashboard_pages_bp_v2.route(
    "/api/rationalization/validate/<int:app_id>", methods=["GET"]
)
@timed_route
@login_required
def get_validation_comparison(app_id):
    """Get data comparison for validation between Abacus and manual/assessment sources."""
    try:
        from app.models import ApplicationComponent

        app = ApplicationComponent.query.get(app_id)
        if not app:
            return jsonify({"success": False, "error": "Application not found"}), 404

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


@dashboard_pages_bp_v2.route(
    "/api/rationalization/validate/<int:app_id>/resolve", methods=["POST"]
)
@timed_route
@login_required
@audit_log("validation_conflict_resolve")
def resolve_validation_conflict(app_id):
    """Resolve a data conflict by choosing a source of truth."""
    try:
        from app.models import ApplicationComponent
        from app.extensions import db

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

        field_key = field_map.get(field)

        if not field_key:
            return jsonify({"success": False, "error": f"Unknown field: {field}"}), 400

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
        from app.extensions import db

        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ===================================================================
# FRAG-035: Executive dashboard API
# ===================================================================

@dashboard_pages_bp_v2.route("/api/executive-summary")
@login_required
def api_executive_summary():
    """FRAG-035: Get executive dashboard summary.

    Returns a flat dict of scalars so the dashboard template can render each
    value with x-text without '[object Object]' artefacts. The Health Score is
    the string the other screens show for it, so the executive summary cannot
    spell the same score differently from the cards beside it.
    """
    try:
        from app.modules.dashboard.v2.services.executive_dashboard_service import (
            ExecutiveDashboardService,
            format_health_score,
        )
        service = ExecutiveDashboardService()
        summary = service.get_executive_summary()
        health = summary.get("architecture_health", {})
        portfolio = summary.get("portfolio_stats", {})
        cap = summary.get("capability_coverage", {})
        risk = summary.get("risk_posture", {})
        arb = summary.get("pending_decisions", {})
        # No `, 0` defaults. A missing key means the metric could not be measured,
        # and a 0 the reader cannot distinguish from a real zero is worse than no
        # number at all (CLAUDE.md). These serialise to JSON null; the dashboard
        # renders null as an em dash.
        health_score = health.get("composite_score")
        flat = {
            "Health Score": None if health_score is None else format_health_score(health_score),
            "Solutions": portfolio.get("solutions"),
            "Applications": portfolio.get("applications"),
            "ArchiMate Elements": portfolio.get("archimate_elements"),
            "Capability Coverage %": cap.get("percentage"),
            "Open Risks": risk.get("total"),
            "ARB Pending": arb.get("pending"),
        }
        return jsonify({"success": True, "data": flat})
    except Exception as e:
        current_app.logger.error(f"Executive summary error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

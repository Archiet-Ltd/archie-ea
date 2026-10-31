"""
Impact Analysis Routes - AI-Powered Impact Analysis Hub

Provides routes for:
- Unified Impact Analysis Hub
- AI-powered application impact analysis
- Portfolio-wide impact analysis
- Scenario comparison
- Integration with existing gap analyses
"""

from flask import current_app, jsonify, render_template, request, url_for
from flask_login import login_required

from ..models.application_portfolio import ApplicationComponent
from ..services.ai_impact_analysis_service import AIImpactAnalysisService
from ..services.llm_service import LLMService
from . import application_mgmt


@application_mgmt.route("/impact-analysis")
@login_required
def impact_hub():
    """
    Impact Analysis Hub - Unified entry point for all impact analyses.

    Provides:
    - Application impact analysis (single app)
    - Portfolio impact analysis (multiple apps)
    - Scenario comparison
    - Links to existing gap analyses (COBIT, Portfolio, Strategic)
    """
    # Get all applications for the dropdown
    applications = ApplicationComponent.query.order_by(ApplicationComponent.name).all()

    # Pre-select app if passed in query param
    selected_app_id = request.args.get("app_id", type=int)

    # Check if AI is configured
    ai_status = LLMService.configuration_status()

    return render_template(
        "applications/dashboard.html",
        applications=applications,
        selected_app_id=selected_app_id,
        ai_available=ai_status.get("ready", False),
        ai_providers=ai_status.get("providers", []),
    )


@application_mgmt.route("/impact-analysis/simulate", methods=["POST"])
@login_required
def simulate_impact():
    """
    AI-powered impact analysis for a single application.

    Enhanced with:
    - Comprehensive risk scoring
    - AI-generated insights and recommendations
    - Business impact translation
    - Mitigation strategies
    """
    try:
        data = request.get_json()
        app_id = data.get("app_id")
        scenario = data.get("scenario", "custom")
        include_ai = data.get("include_ai", True)

        if not app_id:
            return jsonify({"error": "Application ID is required"}), 400

        # Use the AI-powered service
        result = AIImpactAnalysisService.analyze_application_impact(
            app_id=int(app_id), scenario=scenario, include_ai_analysis=include_ai
        )

        # Format response for frontend compatibility
        response = {
            "risk_level": result["risk_assessment"]["risk_level"],
            "blast_radius": result["dependency_analysis"]["blast_radius"],
            "total_score": result["risk_assessment"]["total_score"],
            "impacted_services": _format_impacted_services(
                result["dependency_analysis"]
            ),
            "graph_data": result["graph_data"],
            "risk_breakdown": result["risk_assessment"]["breakdown"],
            "summary": result["summary"],
        }

        # Add AI insights if available
        if result.get("ai_insights", {}).get("available"):
            ai = result["ai_insights"]
            response["ai_analysis"] = {
                "available": True,
                "hidden_dependencies": ai.get("hidden_dependencies", []),
                "business_impact": ai.get("business_impact", {}),
                "mitigation_strategy": ai.get("mitigation_strategy", []),
                "recommendation": ai.get("recommendation", {}),
            }
        else:
            response["ai_analysis"] = {
                "available": False,
                "message": result.get("ai_insights", {}).get(
                    "message", "AI analysis not available"
                ),
                "fallback_recommendations": result.get("ai_insights", {}).get(
                    "fallback_recommendations", []
                ),
            }

        return jsonify(response)

    except ValueError as e:
        current_app.logger.warning(f"Impact analysis validation error: {e}")
        return jsonify({"error": "Invalid request parameters"}), 400
    except Exception as e:
        current_app.logger.error(f"Impact analysis failed: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/impact-analysis/portfolio", methods=["POST"])
@login_required
def portfolio_impact():
    """
    Analyze impact across multiple applications simultaneously.

    Useful for:
    - Technology stack consolidation
    - Vendor contract renegotiation
    - Platform migration planning
    """
    try:
        data = request.get_json()
        app_ids = data.get("app_ids", [])
        scenario = data.get("scenario", "custom")

        if not app_ids:
            return jsonify({"error": "At least one application ID is required"}), 400

        # Validate app_ids are integers
        try:
            app_ids = [int(id) for id in app_ids]
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid application IDs"}), 400

        result = AIImpactAnalysisService.analyze_portfolio_impact(
            app_ids=app_ids, scenario=scenario
        )

        return jsonify(result)

    except Exception as e:
        current_app.logger.error(
            f"Portfolio impact analysis failed: {e}", exc_info=True
        )
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/impact-analysis/compare", methods=["POST"])
@login_required
def compare_scenarios():
    """
    Compare multiple change scenarios for the same application.

    Helps decision-makers choose between different approaches.
    """
    try:
        data = request.get_json()
        app_id = data.get("app_id")
        scenarios = data.get("scenarios", [])

        if not app_id:
            return jsonify({"error": "Application ID is required"}), 400

        if not scenarios or len(scenarios) < 2:
            return jsonify(
                {"error": "At least two scenarios are required for comparison"}
            ), 400

        result = AIImpactAnalysisService.compare_scenarios(
            app_id=int(app_id), scenarios=scenarios
        )

        return jsonify(result)

    except Exception as e:
        current_app.logger.error(f"Scenario comparison failed: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/impact-analysis/ai-analyze", methods=["POST"])
@login_required
def ai_analyze():
    """
    Dedicated AI analysis endpoint for generating deeper insights.

    Can be called separately after initial simulation to get
    AI-powered recommendations without re-running the full analysis.
    """
    try:
        data = request.get_json()
        app_id = data.get("app_id")
        scenario = data.get("scenario", "custom")
        data.get("context", {})

        if not app_id:
            return jsonify({"error": "Application ID is required"}), 400

        # Check if AI is configured
        ai_status = LLMService.configuration_status()
        if not ai_status.get("ready"):
            return (
                jsonify(
                    {
                        "available": False,
                        "error": "No LLM provider configured. Please configure API settings.",
                        "configure_url": url_for("admin.api_settings"),
                    }
                ),
                400,
            )

        # Run full analysis with AI
        result = AIImpactAnalysisService.analyze_application_impact(
            app_id=int(app_id), scenario=scenario, include_ai_analysis=True
        )

        ai_insights = result.get("ai_insights", {})

        if ai_insights.get("available"):
            return jsonify(
                {
                    "available": True,
                    "hidden_dependencies": ai_insights.get("hidden_dependencies", []),
                    "business_impact": ai_insights.get("business_impact", {}),
                    "mitigation_strategy": ai_insights.get("mitigation_strategy", []),
                    "recommendation": ai_insights.get("recommendation", {}),
                    "generated_at": ai_insights.get("generated_at"),
                }
            )
        else:
            return jsonify(
                {
                    "available": False,
                    "message": ai_insights.get("message", "AI analysis failed"),
                    "fallback_recommendations": ai_insights.get(
                        "fallback_recommendations", []
                    ),
                }
            )

    except ValueError:
        return jsonify({"available": False, "error": "Invalid request parameters"}), 400
    except Exception as e:
        current_app.logger.error(f"AI analysis failed: {e}", exc_info=True)
        return jsonify({"available": False, "error": "An internal error occurred"}), 500


@application_mgmt.route("/impact-analysis/status")
@login_required
def ai_status():
    """
    Check AI configuration status.

    Returns whether AI features are available and which providers are configured.
    """
    try:
        status = LLMService.configuration_status()
        return jsonify(
            {
                "ai_available": status.get("ready", False),
                "providers": status.get("providers", []),
                "configure_url": url_for("admin.api_settings"),
            }
        )
    except Exception as e:
        current_app.logger.exception(f"AI status check failed: {e}")
        # "ai_available: false" at 200 is indistinguishable from a genuine
        # "no provider configured", and str(e) leaked the internals besides.
        return jsonify(
            {"success": False, "error": "Could not determine AI configuration status"}
        ), 500


def _format_impacted_services(dependency_analysis):
    """
    Format dependency analysis into a flat list of impacted services.
    """
    services = []

    # Add direct impacts
    for item in dependency_analysis.get("direct_impacts", []):
        services.append(
            {
                "name": item.get("name", "Unknown"),
                "type": item.get("type", "Unknown"),
                "layer": item.get("layer", "Unknown"),
                "depth": 1,
            }
        )

    # Add affected capabilities
    for cap in dependency_analysis.get("affected_capabilities", []):
        services.append(
            {
                "name": cap.get("name", "Unknown"),
                "type": "BusinessCapability",
                "layer": "Business",
                "depth": 1,
                "criticality": cap.get("criticality", "unknown"),
            }
        )

    # Add indirect impacts (flattened)
    indirect = dependency_analysis.get("indirect_impacts", {})
    if isinstance(indirect, dict):
        for depth_str, items in indirect.items():
            try:
                depth = int(depth_str)
            except (ValueError, TypeError):
                depth = 2
            for item in items:
                services.append(
                    {
                        "name": item.get("name", "Unknown"),
                        "type": item.get("type", "Unknown"),
                        "layer": item.get("layer", "Unknown"),
                        "depth": depth,
                    }
                )

    return services

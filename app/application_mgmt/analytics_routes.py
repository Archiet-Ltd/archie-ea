# app/application_mgmt/analytics_routes.py
"""Analysis, gap-analysis, and AI portfolio analysis routes extracted from routes.py (BE-054 wave-5)."""
import logging
from datetime import datetime

from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required
from werkzeug.exceptions import HTTPException

from ..models.application_portfolio import ApplicationComponent
from . import application_mgmt

logger = logging.getLogger(__name__)


@application_mgmt.route("/api/analysis/<int:analysis_id>/replay", methods=["POST"])
@login_required
def replay_analysis(analysis_id):
    """Replay a previous analysis (load results without re-analyzing)."""
    from ..models.document_analysis import DocumentAnalysis

    analysis = DocumentAnalysis.query.get_or_404(analysis_id)

    return jsonify({"success": True, "analysis": analysis.to_dict()}), 200

@application_mgmt.route("/applications/<int:application_id>/gap-analysis")
@login_required
def application_gap_analysis(application_id):
    """
    Display architectural gap analysis for a specific application.

    Shows comprehensive gap analysis across capability, integration, technology,
    process, data, compliance, metadata, and ArchiMate relationship dimensions.
    """
    try:
        from app.modules.solutions_strategic.v2.services.gap_analysis_service import ArchitecturalGapAnalyzer

        app = ApplicationComponent.query.get_or_404(application_id)

        # Run gap analysis
        analyzer = ArchitecturalGapAnalyzer()
        gap_results = analyzer.analyze_application_gaps(application_id)

        return render_template(
            "applications/dashboard.html",
            application=app,
            gap_results=gap_results,
        )

    except Exception as e:
        current_app.logger.error(f"Error in gap analysis: {str(e)}")
        flash("Error analyzing gaps. Please try again.", "error")
        return redirect(
            url_for("unified_applications.application_detail", id=application_id)
        )

@application_mgmt.route("/applications/<int:application_id>/gap-analysis/api")
@login_required
def application_gap_analysis_api(application_id):
    """
    API endpoint for gap analysis data.

    Returns JSON of all detected gaps for programmatic access.
    """
    try:
        from app.modules.solutions_strategic.v2.services.gap_analysis_service import ArchitecturalGapAnalyzer

        ApplicationComponent.query.get_or_404(application_id)

        analyzer = ArchitecturalGapAnalyzer()
        gap_results = analyzer.analyze_application_gaps(application_id)

        return jsonify(gap_results)

    except HTTPException:
        raise  # a missing application is a 404, not a masked 500
    except Exception as e:
        current_app.logger.error(f"Error in gap analysis API: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500

@application_mgmt.route("/portfolio/gap-analysis")
@login_required
def portfolio_gap_analysis():
    """Old address: the enterprise gap register lives under /enterprise/implementation."""
    return redirect(url_for("enterprise.gap_analysis"), code=301)

@application_mgmt.route("/portfolio/gap-analysis/api")
@login_required
def portfolio_gap_analysis_api():
    """
    API endpoint for portfolio gap analysis data.

    Returns JSON of all portfolio-wide gaps.
    """
    try:
        from app.modules.solutions_strategic.v2.services.gap_analysis_service import ArchitecturalGapAnalyzer

        analyzer = ArchitecturalGapAnalyzer()
        portfolio_gaps = analyzer.analyze_portfolio_gaps()

        return jsonify(portfolio_gaps)

    except Exception as e:
        current_app.logger.error(f"Error in portfolio gap analysis API: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/applications/ai-analysis", methods=["POST"])
@login_required
def ai_portfolio_analysis():
    """
    AI-powered portfolio analysis endpoint.

    Provides comprehensive AI architecture generation using existing services:
    - Semantic understanding of applications
    - Relationship analysis between applications
    - Enhanced gap detection
    - Predictive insights
    - AI-powered recommendations
    - APQC process extraction
    - ArchiMate element generation
    - Vector semantic analysis

    Request Body:
    {
        "application_ids": [1, 2, 3],  // List of application IDs to analyze
        "analysis_options": {           // Optional analysis configuration
            "include_semantic": true,
            "include_relationships": true,
            "include_gap_analysis": true,
            "include_predictions": true,
            "include_recommendations": true,
            "include_apqc_extraction": true,
            "include_archimate_generation": true,
            "include_vector_analysis": true
        }
    }
    """
    from ..services.ai_architecture_analysis_service import (
        AIArchitectureAnalysisService,
    )

    data = request.get_json() or {}

    application_ids = data.get("application_ids", [])
    analysis_options = data.get("analysis_options", {})

    if not application_ids:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "No application IDs provided",
                    "message": "Please provide application_ids in the request body",
                }
            ),
            400,
        )

    try:
        # Initialize AI analysis service
        ai_service = AIArchitectureAnalysisService()

        # Perform comprehensive analysis
        analysis_result = ai_service.comprehensive_portfolio_analysis(application_ids)

        if analysis_result.get("success"):
            return jsonify(
                {
                    "success": True,
                    "analysis": analysis_result["analysis"],
                    "analysis_options": analysis_options,
                    "generated_at": datetime.utcnow().isoformat(),
                }
            )
        else:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": analysis_result.get("error", "Unknown error"),
                        "generated_at": datetime.utcnow().isoformat(),
                    }
                ),
                500,
            )

    except Exception as e:
        logger.error(f"Error in AI portfolio analysis: {e}")
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

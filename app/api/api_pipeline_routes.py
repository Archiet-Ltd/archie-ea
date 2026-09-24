"""
API Pipeline Routes

Provides REST API endpoints for data enrichment and market intelligence.
Integrates with the API pipeline orchestrator for comprehensive data gathering.
"""

import logging

from flask import Blueprint, jsonify, request

from app.decorators import audit_log
from app.services.api_clients.pipeline_orchestrator import APIPipelineOrchestrator
from flask_login import login_required

logger = logging.getLogger(__name__)

# Create blueprint
api_pipeline_bp = Blueprint("api_pipeline", __name__, url_prefix="/api/pipeline")

# Initialize orchestrator
pipeline_orchestrator = APIPipelineOrchestrator()


@api_pipeline_bp.route("/health", methods=["GET"])
@login_required
def pipeline_health():
    """
    Check API pipeline health
    ---
    tags:
      - API Pipeline
    summary: Check health of all API clients
    description: Returns health status for G2 Crowd, Crunchbase, and GitHub API clients
    responses:
      200:
        description: Health check results
        schema:
          type: object
          properties:
            overall_healthy:
              type: boolean
              example: true
            clients:
              type: object
              properties:
                g2_crowd:
                  type: object
                crunchbase:
                  type: object
                github:
                  type: object
            timestamp:
              type: string
              format: date-time
    """
    try:
        health_status = pipeline_orchestrator.health_check()
        return jsonify(health_status), 200
    except Exception as e:
        logger.error(f"Pipeline health check failed: {e}")
        return jsonify({"success": False, "error": "Health check failed", "message": "See server logs for details"}), 500


@api_pipeline_bp.route("/enrich/vendor/<vendor_name>", methods=["GET"])
@login_required
def enrich_vendor(vendor_name):
    """
    Enrich vendor data from multiple sources
    ---
    tags:
      - API Pipeline
    summary: Get enriched vendor intelligence
    description: Retrieves comprehensive vendor data from G2 Crowd, Crunchbase, and other sources
    parameters:
      - name: vendor_name
        in: path
        required: true
        type: string
        description: Name of the vendor to enrich
    responses:
      200:
        description: Enriched vendor data
        schema:
          type: object
          properties:
            success:
              type: boolean
            vendor_name:
              type: string
            data_sources:
              type: object
            aggregated_insights:
              type: object
            successful_sources:
              type: array
              items:
                type: string
      500:
        description: Enrichment failed
    """
    try:
        enriched_data = pipeline_orchestrator.enrich_vendor_data(vendor_name)

        if enriched_data["success"]:
            return jsonify(enriched_data), 200
        else:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Vendor enrichment failed",
                        "vendor_name": vendor_name,
                        "message": "No data sources returned valid results",
                    }
                ),
                503,
            )

    except Exception as e:
        # External enrichment providers (G2 Crowd, Crunchbase, etc.) being
        # unreachable/unconfigured is a service-availability condition, not a
        # bug in Entelim - 503 says so instead of masquerading as a 500.
        logger.error(f"Vendor enrichment failed for {vendor_name}: {e}")
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Vendor enrichment failed",
                    "vendor_name": vendor_name,
                    "message": "See server logs for details",
                }
            ),
            503,
        )


@api_pipeline_bp.route("/enrich/product/<product_name>", methods=["GET"])
@login_required
def enrich_product(product_name):
    """
    Enrich product data from multiple sources
    ---
    tags:
      - API Pipeline
    summary: Get enriched product intelligence
    description: Retrieves comprehensive product data from G2 Crowd, GitHub, and other sources
    parameters:
      - name: product_name
        in: path
        required: true
        type: string
        description: Name of the product to enrich
      - name: vendor_name
        in: query
        type: string
        description: Optional vendor name for better matching
    responses:
      200:
        description: Enriched product data
        schema:
          type: object
          properties:
            success:
              type: boolean
            product_name:
              type: string
            vendor_name:
              type: string
            data_sources:
              type: object
            aggregated_insights:
              type: object
            successful_sources:
              type: array
              items:
                type: string
      500:
        description: Enrichment failed
    """
    try:
        vendor_name = request.args.get("vendor_name")
        enriched_data = pipeline_orchestrator.enrich_product_data(product_name, vendor_name)

        if enriched_data["success"]:
            return jsonify(enriched_data), 200
        else:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Product enrichment failed",
                        "product_name": product_name,
                        "vendor_name": vendor_name,
                        "message": "No data sources returned valid results",
                    }
                ),
                503,
            )

    except Exception as e:
        # See enrich_vendor: an unreachable external provider is 503, not 500.
        logger.error(f"Product enrichment failed for {product_name}: {e}")
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Product enrichment failed",
                    "product_name": product_name,
                    "message": "See server logs for details",
                }
            ),
            503,
        )


@api_pipeline_bp.route("/market-analysis/<category>", methods=["GET"])
@login_required
def market_analysis(category):
    """
    Get comprehensive market analysis
    ---
    tags:
      - API Pipeline
    summary: Analyze market category
    description: Retrieves market analysis data from multiple sources for a given category
    parameters:
      - name: category
        in: path
        required: true
        type: string
        description: Market category to analyze (e.g., 'marketing-automation')
    responses:
      200:
        description: Market analysis data
        schema:
          type: object
          properties:
            success:
              type: boolean
            category:
              type: string
            data_sources:
              type: object
            aggregated_analysis:
              type: object
            successful_sources:
              type: array
              items:
                type: string
      500:
        description: Market analysis failed
    """
    try:
        analysis_data = pipeline_orchestrator.get_market_analysis(category)

        if analysis_data["success"]:
            return jsonify(analysis_data), 200
        else:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Market analysis failed",
                        "category": category,
                        "message": "No data sources returned valid results",
                    }
                ),
                500,
            )

    except Exception as e:
        logger.error(f"Market analysis failed for {category}: {e}")
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Market analysis failed",
                    "category": category,
                    "message": "See server logs for details",
                }
            ),
            500,
        )


@api_pipeline_bp.route("/batch/enrich/vendors", methods=["POST"])
@login_required
@audit_log("pipeline_batch_enrich_vendors")
def batch_enrich_vendors():
    """
    Batch enrich multiple vendors
    ---
    tags:
      - API Pipeline
    summary: Enrich multiple vendors in batch
    description: Accepts a list of vendor names and enriches data for each
    parameters:
      - name: vendor_names
        in: body
        required: true
        schema:
          type: object
          properties:
            vendors:
              type: array
              items:
                type: string
              example: ["HubSpot", "Salesforce", "Marketo"]
            skip_errors:
              type: boolean
              default: false
              description: Continue processing even if individual vendors fail
    responses:
      200:
        description: Batch enrichment results
        schema:
          type: object
          properties:
            success:
              type: boolean
            total_vendors:
              type: integer
            successful_enrichments:
              type: integer
            failed_enrichments:
              type: integer
            results:
              type: array
              items:
                type: object
            errors:
              type: array
              items:
                type: string
      400:
        description: Invalid request
      500:
        description: Batch processing failed
    """
    try:
        data = request.get_json()
        if not data or "vendors" not in data:
            return jsonify({"success": False, "error": "Missing vendors list in request body"}), 400

        vendor_names = data["vendors"]
        skip_errors = data.get("skip_errors", False)

        if not isinstance(vendor_names, list) or len(vendor_names) == 0:
            return jsonify({"success": False, "error": "vendors must be a non-empty array"}), 400

        if len(vendor_names) > 50:  # Limit batch size
            return jsonify({"success": False, "error": "Maximum 50 vendors allowed in batch"}), 400

        results = []
        errors = []
        successful_count = 0

        for vendor_name in vendor_names:
            try:
                enriched_data = pipeline_orchestrator.enrich_vendor_data(vendor_name)
                results.append(enriched_data)
                if enriched_data["success"]:
                    successful_count += 1
            except Exception as e:
                error_msg = f"Failed to enrich {vendor_name}: {str(e)}"
                errors.append(error_msg)
                logger.error(error_msg)

                if not skip_errors:
                    break

                results.append({"vendor_name": vendor_name, "success": False, "error": "An internal error occurred"})

        response_data = {
            "success": successful_count > 0,
            "total_vendors": len(vendor_names),
            "successful_enrichments": successful_count,
            "failed_enrichments": len(vendor_names) - successful_count,
            "results": results,
            "errors": errors,
        }

        return jsonify(response_data), 200

    except Exception as e:
        logger.error(f"Batch vendor enrichment failed: {e}")
        return (
            jsonify({"success": False, "error": "Batch processing failed", "message": "See server logs for details"}),
            500,
        )


@api_pipeline_bp.route("/rate-limits", methods=["GET"])
@login_required
def rate_limits():
    """
    Get rate limit status for all API clients
    ---
    tags:
      - API Pipeline
    summary: Check API rate limit status
    description: Returns current rate limiting status for all configured API clients
    responses:
      200:
        description: Rate limit status
        schema:
          type: object
          properties:
            g2_crowd:
              type: object
            crunchbase:
              type: object
            github:
              type: object
    """
    try:
        status = pipeline_orchestrator.get_rate_limit_status()
        return jsonify(status), 200
    except Exception as e:
        logger.error(f"Rate limit status check failed: {e}")
        return (
            jsonify({"success": False, "error": "Rate limit check failed", "message": "See server logs for details"}),
            500,
        )


@api_pipeline_bp.route("/cache/clear", methods=["POST"])
@login_required
@audit_log("pipeline_cache_clear")
def clear_cache():
    """
    Clear all API client caches
    ---
    tags:
      - API Pipeline
    summary: Clear API response caches
    description: Clears cached responses from all API clients to force fresh data retrieval
    responses:
      200:
        description: Cache cleared successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
              example: true
            message:
              type: string
              example: "All API client caches cleared"
    """
    try:
        pipeline_orchestrator.clear_all_caches()
        return jsonify({"success": True, "message": "All API client caches cleared"}), 200
    except Exception as e:
        logger.error(f"Cache clear failed: {e}")
        return jsonify({"success": False, "error": "Cache clear failed", "message": "See server logs for details"}), 500

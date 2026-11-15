"""
Advanced TCO API Routes - LLM-PRD - 03 Implementation

RESTful API endpoints for advanced TCO calculation with 12 - category cost model,
industry benchmarks, sensitivity analysis, and comprehensive reporting.
"""

import logging
from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_login import login_required

from app import db
from app.decorators import audit_log
from app.models.vendor.vendor_organization import TCOCalculation, VendorProduct
from app.services.advanced_tco_engine import AdvancedTCOEngine
from app.utils.pagination import safe_int_arg

logger = logging.getLogger(__name__)

# Create Blueprint
advanced_tco_bp = Blueprint("advanced_tco", __name__, url_prefix="/api/advanced-tco")


@advanced_tco_bp.route("/calculate", methods=["POST"])
@login_required
@audit_log("tco_calculate")
def calculate_tco():
    """
    Calculate comprehensive TCO with 12 cost categories and analysis.

    Request Body:
    {
        "vendor_product_id": 1,
        "user_count": 1000,
        "tco_period_years": 5,
        "deployment_model": "cloud",
        "organization_size": "medium",
        "industry": "manufacturing",
        "include_sensitivity_analysis": true,
        "sensitivity_range": 0.20
    }

    Response:
    {
        "success": true,
        "data": {
            "vendor_product": {...},
            "calculation_parameters": {...},
            "cost_breakdown": {
                "costs": {...},
                "summary": {...},
                "cost_distribution": {...}
            },
            "yearly_breakdown": [...],
            "comparative_metrics": {...},
            "sensitivity_analysis": {...},
            "confidence_level": "high"
        }
    }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"success": False, "error": "No request data provided"}), 400

        # Validate required fields
        vendor_product_id = data.get("vendor_product_id")
        user_count = data.get("user_count")

        if not vendor_product_id or not user_count:
            return (
                jsonify(
                    {"success": False, "error": "vendor_product_id and user_count are required"}
                ),
                400,
            )

        # Extract parameters
        tco_period_years = data.get("tco_period_years", 5)
        deployment_model = data.get("deployment_model", "cloud")
        organization_size = data.get("organization_size", "medium")
        industry = data.get("industry", "manufacturing")
        include_sensitivity_analysis = data.get("include_sensitivity_analysis", True)
        sensitivity_range = data.get("sensitivity_range", 0.20)

        # Initialize TCO engine
        engine = AdvancedTCOEngine()

        # Calculate TCO
        results = engine.calculate_comprehensive_tco(
            vendor_product_id=vendor_product_id,
            user_count=user_count,
            tco_period_years=tco_period_years,
            deployment_model=deployment_model,
            organization_size=organization_size,
            industry=industry,
            include_sensitivity_analysis=include_sensitivity_analysis,
            sensitivity_range=sensitivity_range,
        )

        return jsonify({"success": True, "data": results})

    except Exception as e:
        logger.error(f"TCO calculation error: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@advanced_tco_bp.route("/batch-calculate", methods=["POST"])
@login_required
@audit_log("tco_batch_calculate")
def batch_calculate_tco():
    """
    Calculate TCO for multiple vendor products.

    Request Body:
    {
        "vendor_product_ids": [1, 2, 3],
        "user_count": 1000,
        "tco_period_years": 5,
        "deployment_model": "cloud",
        "organization_size": "medium",
        "industry": "manufacturing"
    }

    Response:
    {
        "success": true,
        "data": {
            "calculations": [
                {
                    "vendor_product_id": 1,
                    "tco_results": {...},
                    "success": true
                },
                {
                    "vendor_product_id": 2,
                    "error": "Product not found",
                    "success": false
                }
            ],
            "summary": {
                "total_calculations": 3,
                "successful": 2,
                "failed": 1
            }
        }
    }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"success": False, "error": "No request data provided"}), 400

        vendor_product_ids = data.get("vendor_product_ids", [])
        user_count = data.get("user_count")

        if not vendor_product_ids or not user_count:
            return (
                jsonify(
                    {"success": False, "error": "vendor_product_ids and user_count are required"}
                ),
                400,
            )

        # Extract common parameters
        tco_period_years = data.get("tco_period_years", 5)
        deployment_model = data.get("deployment_model", "cloud")
        organization_size = data.get("organization_size", "medium")
        industry = data.get("industry", "manufacturing")

        # Initialize TCO engine
        engine = AdvancedTCOEngine()

        # Calculate TCO for each product
        calculations = []
        successful = 0
        failed = 0

        for vendor_product_id in vendor_product_ids:
            try:
                results = engine.calculate_comprehensive_tco(
                    vendor_product_id=vendor_product_id,
                    user_count=user_count,
                    tco_period_years=tco_period_years,
                    deployment_model=deployment_model,
                    organization_size=organization_size,
                    industry=industry,
                    include_sensitivity_analysis=False,  # Skip sensitivity for batch
                )

                calculations.append(
                    {
                        "vendor_product_id": vendor_product_id,
                        "tco_results": results,
                        "success": True,
                    }
                )
                successful += 1

            except Exception:
                calculations.append(
                    {"vendor_product_id": vendor_product_id, "error": "An internal error occurred", "success": False}
                )
                failed += 1

        # Sort by total TCO
        calculations.sort(
            key=lambda x: x["tco_results"]["cost_breakdown"]["summary"]["total_tco"]
            if x["success"]
            else float("inf")
        )

        return jsonify(
            {
                "success": True,
                "data": {
                    "calculations": calculations,
                    "summary": {
                        "total_calculations": len(vendor_product_ids),
                        "successful": successful,
                        "failed": failed,
                    },
                },
            }
        )

    except Exception as e:
        logger.error(f"Batch TCO calculation error: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@advanced_tco_bp.route("/benchmarks", methods=["GET"])
@login_required
def get_industry_benchmarks():
    """
    Get industry benchmarks for TCO calculations.

    Query Parameters:
    - industry: Filter by industry (optional)
    - organization_size: Filter by organization size (optional)

    Response:
    {
        "success": true,
        "data": {
            "benchmarks": [
                {
                    "industry": "manufacturing",
                    "organization_size": "medium",
                    "median_tco_per_user": 1500,
                    "implementation_months": 18,
                    "cost_distribution": {
                        "software_licensing": 30,
                        "implementation_services": 20,
                        "internal_labor": 18
                    }
                }
            ]
        }
    }
    """
    try:
        # Get query parameters
        industry = request.args.get("industry")
        organization_size = request.args.get("organization_size")

        # Initialize TCO engine
        engine = AdvancedTCOEngine()

        # Get benchmarks
        benchmarks = []

        for ind_name, ind_benchmarks in engine.INDUSTRY_BENCHMARKS.items():
            if industry and ind_name != industry:
                continue

            for org_size, benchmark in ind_benchmarks.items():
                if organization_size and org_size != organization_size:
                    continue

                benchmarks.append(
                    {
                        "industry": benchmark.industry,
                        "organization_size": benchmark.organization_size,
                        "median_tco_per_user": float(benchmark.median_tco_per_user),
                        "implementation_months": benchmark.implementation_months,
                        "cost_distribution": benchmark.cost_distribution,
                    }
                )

        from app.services.cost_visibility import withhold_reply

        return jsonify(withhold_reply({"success": True, "data": {"benchmarks": benchmarks}}))

    except Exception as e:
        logger.error(f"Get benchmarks error: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@advanced_tco_bp.route("/categories", methods=["GET"])
@login_required
def get_tco_categories():
    """
    Get TCO cost categories information.

    Response:
    {
        "success": true,
        "data": {
            "categories": [
                {
                    "name": "software_licensing",
                    "cost_type": "recurring",
                    "weight": 0.25,
                    "description": "Annual software license fees...",
                    "typical_percentage": 30.0
                }
            ]
        }
    }
    """
    try:
        engine = AdvancedTCOEngine()

        categories = []
        for cat_name, category in engine.TCO_CATEGORIES.items():
            categories.append(
                {
                    "name": cat_name,
                    "cost_type": category.cost_type,
                    "weight": category.weight,
                    "description": category.description,
                    "typical_percentage": category.typical_percentage,
                }
            )

        return jsonify({"success": True, "data": {"categories": categories}})

    except Exception as e:
        logger.error(f"Get TCO categories error: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@advanced_tco_bp.route("/history", methods=["GET"])
@login_required
def get_tco_history():
    """
    Get TCO calculation history.

    Query Parameters:
    - vendor_product_id: Filter by vendor product ID (optional)
    - limit: Maximum number of results (default: 50)
    - offset: Pagination offset (default: 0)

    Response:
    {
        "success": true,
        "data": {
            "calculations": [
                {
                    "id": 1,
                    "vendor_product_id": 1,
                    "vendor_product_name": "SAP S/4HANA Cloud",
                    "vendor_name": "SAP SE",
                    "user_count": 1000,
                    "tco_period_years": 5,
                    "total_tco": 5000000,
                    "per_user_annual": 1000,
                    "confidence_level": "high",
                    "created_at": "2024 - 01 - 20T10:30:00Z"
                }
            ],
            "total": 25,
            "limit": 50,
            "offset": 0
        }
    }
    """
    try:
        # Get query parameters
        vendor_product_id = request.args.get("vendor_product_id", type=int)
        limit = safe_int_arg('limit', 50, minimum=1, maximum=500)
        offset = safe_int_arg('offset', 0, minimum=0)

        # Build query
        query = db.session.query(
            TCOCalculation,
            VendorProduct.name.label("product_name"),
            VendorProduct.vendor_organization_id,
        ).join(VendorProduct)

        if vendor_product_id:
            query = query.filter(TCOCalculation.vendor_product_id == vendor_product_id)

        # Get total count
        total = query.count()

        # Get calculations with pagination
        calculations = (
            query.order_by(TCOCalculation.created_at.desc()).offset(offset).limit(limit).all()
        )

        # Format results
        calculation_list = []
        for tco, product_name, vendor_org_id in calculations:
            calculation_list.append(
                {
                    "id": tco.id,
                    "vendor_product_id": tco.vendor_product_id,
                    "vendor_product_name": product_name,
                    "vendor_name": tco.vendor_product.vendor_organization.name
                    if tco.vendor_product
                    else "Unknown",
                    "user_count": tco.user_count,
                    "tco_period_years": tco.tco_period_years,
                    "deployment_model": tco.deployment_model,
                    "total_tco": float(tco.total_tco) if tco.total_tco else 0,
                    "annual_average": float(tco.annual_average) if tco.annual_average else 0,
                    "per_user_annual": float(tco.per_user_annual) if tco.per_user_annual else 0,
                    "confidence_level": tco.confidence_level,
                    "vs_industry_percentage": tco.vs_industry_percentage,
                    "created_at": tco.created_at.isoformat() if tco.created_at else None,
                }
            )

        return jsonify(
            {
                "success": True,
                "data": {
                    "calculations": calculation_list,
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                },
            }
        )

    except Exception as e:
        logger.error(f"Get TCO history error: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@advanced_tco_bp.route("/comparison", methods=["POST"])
@login_required
@audit_log("tco_compare")
def compare_tco():
    """
    Compare TCO between multiple vendor products.

    Request Body:
    {
        "vendor_product_ids": [1, 2, 3],
        "user_count": 1000,
        "tco_period_years": 5,
        "deployment_model": "cloud",
        "organization_size": "medium",
        "industry": "manufacturing"
    }

    Response:
    {
        "success": true,
        "data": {
            "comparison": [
                {
                    "vendor_product_id": 1,
                    "vendor_product_name": "SAP S/4HANA Cloud",
                    "vendor_name": "SAP SE",
                    "total_tco": 5000000,
                    "per_user_annual": 1000,
                    "vs_industry_percentage": 15,
                    "rank": 2
                }
            ],
            "summary": {
                "lowest_tco": 4500000,
                "highest_tco": 5500000,
                "average_tco": 5000000,
                "tco_range": 1000000
            }
        }
    }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"success": False, "error": "No request data provided"}), 400

        vendor_product_ids = data.get("vendor_product_ids", [])
        user_count = data.get("user_count")

        if not vendor_product_ids or not user_count:
            return (
                jsonify(
                    {"success": False, "error": "vendor_product_ids and user_count are required"}
                ),
                400,
            )

        # Extract parameters
        tco_period_years = data.get("tco_period_years", 5)
        deployment_model = data.get("deployment_model", "cloud")
        organization_size = data.get("organization_size", "medium")
        industry = data.get("industry", "manufacturing")

        # Initialize TCO engine
        engine = AdvancedTCOEngine()

        # Calculate TCO for comparison
        comparison_data = []

        for vendor_product_id in vendor_product_ids:
            try:
                results = engine.calculate_comprehensive_tco(
                    vendor_product_id=vendor_product_id,
                    user_count=user_count,
                    tco_period_years=tco_period_years,
                    deployment_model=deployment_model,
                    organization_size=organization_size,
                    industry=industry,
                    include_sensitivity_analysis=False,
                )

                comparison_data.append(
                    {
                        "vendor_product_id": vendor_product_id,
                        "vendor_product_name": results["vendor_product"]["name"],
                        "vendor_name": results["vendor_product"]["vendor_name"],
                        "total_tco": results["cost_breakdown"]["summary"]["total_tco"],
                        "per_user_annual": results["cost_breakdown"]["summary"]["per_user_annual"],
                        "vs_industry_percentage": results["comparative_metrics"]["cost_comparison"][
                            "total_tco_vs_benchmark"
                        ],
                        "confidence_level": results["confidence_level"],
                    }
                )

            except Exception as e:
                logger.error(f"Failed to calculate TCO for product {vendor_product_id}: {e}")
                continue

        # Sort by total TCO
        comparison_data.sort(key=lambda x: x["total_tco"])

        # Add rankings
        for i, item in enumerate(comparison_data, 1):
            item["rank"] = i

        # Calculate summary statistics
        if comparison_data:
            tco_values = [item["total_tco"] for item in comparison_data]
            summary = {
                "lowest_tco": min(tco_values),
                "highest_tco": max(tco_values),
                "average_tco": sum(tco_values) / len(tco_values),
                "tco_range": max(tco_values) - min(tco_values),
                "products_compared": len(comparison_data),
            }
        else:
            summary = {"products_compared": 0}

        return jsonify(
            {"success": True, "data": {"comparison": comparison_data, "summary": summary}}
        )

    except Exception as e:
        logger.error(f"TCO comparison error: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@advanced_tco_bp.route("/export", methods=["POST"])
@login_required
@audit_log("tco_export")
def export_tco():
    """
    Export TCO calculation results in various formats.

    Request Body:
    {
        "tco_calculation_id": 1,
        "format": "excel",
        "include_charts": true,
        "include_sensitivity": true
    }

    Response:
    {
        "success": true,
        "data": {
            "download_url": "/downloads/tco_export_123.xlsx",
            "format": "excel",
            "file_size": 1024000,
            "expires_at": "2024 - 01 - 21T10:30:00Z"
        }
    }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"success": False, "error": "No request data provided"}), 400

        tco_calculation_id = data.get("tco_calculation_id")
        data.get("format", "excel")
        include_charts = data.get("include_charts", True)
        include_sensitivity = data.get("include_sensitivity", True)

        if not tco_calculation_id:
            return jsonify({"success": False, "error": "tco_calculation_id is required"}), 400

        # Get TCO calculation
        tco_calc = db.session.query(TCOCalculation).filter_by(id=tco_calculation_id).first()

        if not tco_calc:
            return jsonify({"success": False, "error": "TCO calculation not found"}), 404

        # Generate export using TCO engine
        engine = AdvancedTCOEngine()

        try:
            export_result = engine.export_tco_to_excel(
                tco_calculation_id=tco_calculation_id,
                include_charts=include_charts,
                include_sensitivity=include_sensitivity,
                include_pivot_tables=True,
            )

            return jsonify(
                {
                    "success": True,
                    "data": {
                        "excel_data": export_result["excel_data"],
                        "filename": export_result["filename"],
                        "file_size": export_result["file_size"],
                        "sheets_created": export_result["sheets_created"],
                        "includes_charts": export_result["includes_charts"],
                        "includes_sensitivity": export_result["includes_sensitivity"],
                        "includes_pivot_tables": export_result["includes_pivot_tables"],
                        "download_url": f"/api/advanced-tco/download/{tco_calculation_id}",
                        "expires_at": datetime.utcnow()
                        .replace(hour=datetime.utcnow().hour + 24)
                        .isoformat(),
                    },
                }
            )

        except ImportError:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Excel export not available. Install openpyxl package.",
                    }
                ),
                503,
            )
        except Exception as e:
            logger.error(f"Excel export failed: {e}")
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    except Exception as e:
        logger.error(f"TCO export error: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@advanced_tco_bp.route("/download/<int:tco_calculation_id>", methods=["GET"])
@login_required
def download_tco_file(tco_calculation_id):
    """
    Download TCO Excel file.

    Response:
    Binary Excel file download
    """
    try:
        # Get TCO calculation
        tco_calc = db.session.query(TCOCalculation).filter_by(id=tco_calculation_id).first()

        if not tco_calc:
            return jsonify({"success": False, "error": "TCO calculation not found"}), 404

        # Generate fresh export
        engine = AdvancedTCOEngine()
        export_result = engine.export_tco_to_excel(
            tco_calculation_id=tco_calculation_id,
            include_charts=True,
            include_sensitivity=True,
            include_pivot_tables=True,
        )

        # Decode base64 data
        import base64

        excel_data = base64.b64decode(export_result["excel_data"])

        # Create response
        from flask import Response

        response = Response(
            excel_data,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{export_result["filename"]}"',  # raw-html-ok: filename is regex-sanitized to [a-zA-Z0-9_-] in advanced_tco_engine.py before reaching this dict
                "Content-Length": str(len(excel_data)),
            },
        )

        return response

    except Exception as e:
        logger.error(f"Download failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@advanced_tco_bp.route("/health", methods=["GET"])
@login_required
def health_check():
    """
    Health check endpoint for advanced TCO service.

    Response:
    {
        "success": true,
        "data": {
            "status": "healthy",
            "database_connected": true,
            "tco_categories_loaded": 12,
            "industry_benchmarks_loaded": 15,
            "service_version": "1.0.0"
        }
    }
    """
    try:
        # Check database connection
        db_connected = db.session.execute(db.text("SELECT 1")).scalar() == 1  # tenant-exempt: health check

        # Check TCO engine
        engine = AdvancedTCOEngine()
        categories_loaded = len(engine.TCO_CATEGORIES)
        benchmarks_loaded = sum(
            len(ind_benchmarks) for ind_benchmarks in engine.INDUSTRY_BENCHMARKS.values()
        )

        health_status = {
            "status": "healthy" if db_connected else "unhealthy",
            "database_connected": db_connected,
            "tco_categories_loaded": categories_loaded,
            "industry_benchmarks_loaded": benchmarks_loaded,
            "service_version": "1.0.0",
        }

        from app.services.cost_visibility import withhold_reply

        return jsonify(withhold_reply({"success": True, "data": health_status}))

    except Exception as e:
        logger.error(f"Health check error: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@advanced_tco_bp.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return jsonify({"success": False, "error": "Endpoint not found"}), 404


@advanced_tco_bp.errorhandler(500)
def internal_error(error):
    """Handle 500 errors."""
    logger.error(
        "Unhandled advanced_tco blueprint error route=%s method=%s: %s",
        request.path,
        request.method,
        error,
        exc_info=True,
    )
    db.session.rollback()
    return jsonify({"success": False, "error": "Internal server error"}), 500

"""
MIGRATION: Copied from app/api/vendor_product_routes.py
Changes: `from app import db` -> `from app.extensions import db`
Legacy file preserved at original location.

Vendor Product Catalog API Routes
"""

import logging

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app.decorators import audit_log
from app.extensions import db
from app.modules.vendors.services.vendor_product_service import VendorProductService
from app.utils.pagination import safe_int_arg
from app.utils.tenant_users import escape_like_literal

logger = logging.getLogger(__name__)

# Create blueprint
vendor_product_bp = Blueprint("vendor_product", __name__, url_prefix="/api/vendor")

# Initialize service
vendor_service = VendorProductService()


@vendor_product_bp.route("/extract", methods=["POST"])
@login_required
@audit_log("vendor_product_extract")
def extract_vendor_product():
    """Extract vendor, product family, and specific product from application name.

    **DISABLED FOR INTERNAL ALPHA**: This feature is incomplete and has been disabled.
    The underlying clause extraction service is not production-ready.
    """
    return jsonify(
        {
            "success": False,
            "error": "Feature not available",
            "message": "AI extraction is currently disabled. This feature is under development and will be available in a future release.",
        }
    ), 501  # 501 Not Implemented


@vendor_product_bp.route("/match", methods=["POST"])
@login_required
@audit_log("vendor_product_match")
def find_vendor_product_match():
    """Find the best vendor product match for an application."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body is required"}), 400

        application_name = data.get("application_name", "").strip()
        description = data.get("description", "").strip()

        if not application_name:
            return jsonify(
                {"success": False, "error": "application_name is required"}
            ), 400

        match_result = vendor_service.find_vendor_product_match(
            application_name, description
        )

        return jsonify(
            {"success": match_result["success"], "match_result": match_result}
        )

    except Exception as e:
        logger.error(f"Error finding vendor product match: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@vendor_product_bp.route("/mapping", methods=["POST"])
@login_required
@audit_log("vendor_product_mapping_create")
def create_vendor_product_mapping():
    """Create an application-vendor product mapping."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body is required"}), 400

        application_id = data.get("application_id")
        vendor_product_id = data.get("vendor_product_id")
        confidence_score = data.get("confidence_score", 0.5)
        mapping_method = data.get("mapping_method", "manual")
        deployment_type = data.get("deployment_type", "Production")
        version_deployed = data.get("version_deployed")
        license_type = data.get("license_type", "unknown")
        user_id = current_user.id if current_user.is_authenticated else None

        if not application_id or not vendor_product_id:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "application_id and vendor_product_id are required",
                    }
                ),
                400,
            )

        result = vendor_service.create_vendor_product_mapping(
            application_id=application_id,
            vendor_product_id=vendor_product_id,
            confidence_score=confidence_score,
            mapping_method=mapping_method,
            deployment_type=deployment_type,
            version_deployed=version_deployed,
            license_type=license_type,
            user_id=user_id,
        )

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error creating vendor product mapping: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@vendor_product_bp.route("/hierarchy/<int:vendor_id>", methods=["GET"])
@login_required
def get_vendor_hierarchy(vendor_id: int):
    """Get complete vendor hierarchy including product families and products."""
    try:
        hierarchy = vendor_service.get_vendor_hierarchy(vendor_id)

        if "error" in hierarchy:
            return jsonify({"success": False, "error": hierarchy["error"]}), 404

        return jsonify({"success": True, "hierarchy": hierarchy})

    except Exception as e:
        logger.error(f"Error getting vendor hierarchy: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@vendor_product_bp.route("/risk-analysis/<int:vendor_id>", methods=["GET"])
@login_required
def get_vendor_risk_analysis(vendor_id: int):
    """Calculate vendor risk analysis scores."""
    try:
        risk_analysis = vendor_service.get_vendor_risk_analysis(vendor_id)

        if "error" in risk_analysis:
            return jsonify({"success": False, "error": risk_analysis["error"]}), 404

        return jsonify({"success": True, "risk_analysis": risk_analysis})

    except Exception as e:
        logger.error(f"Error calculating vendor risk analysis: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@vendor_product_bp.route("/statistics/<int:vendor_id>", methods=["GET"])
@login_required
def get_vendor_statistics(vendor_id: int):
    """Get comprehensive statistics for a vendor."""
    try:
        statistics = vendor_service.get_vendor_statistics(vendor_id)

        if "error" in statistics:
            return jsonify({"success": False, "error": statistics["error"]}), 404

        return jsonify({"success": True, "statistics": statistics})

    except Exception as e:
        logger.error(f"Error getting vendor statistics: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@vendor_product_bp.route("/search", methods=["GET"])
@login_required
def search_vendor_products():
    """Search vendor products and organizations with intelligent matching."""
    try:
        query = request.args.get("q", "").strip()
        if not query:
            return jsonify({"success": False, "error": "Search query is required"}), 400

        vendor_id = request.args.get("vendor_id", type=int)
        category = request.args.get("category")
        limit = min(safe_int_arg('limit', 50, minimum=1, maximum=500), 100)

        products = vendor_service.search_vendor_products(
            query, vendor_id, category, limit
        )

        # Also search vendor organizations so the API returns results
        # even when vendor_products table is empty.
        org_results = []
        try:
            from app.models.vendor.vendor_organization import VendorOrganization

            safe_q = escape_like_literal(query)
            orgs = (
                VendorOrganization.query
                .filter(VendorOrganization.name.ilike(f"%{safe_q}%", escape="\\"))
                .order_by(VendorOrganization.name)
                .limit(limit)
                .all()
            )
            org_results = [
                {
                    "id": o.id,
                    "name": o.name,
                    "vendor_name": o.display_name or o.name,
                    "type": "organization",
                    "vendor_type": o.vendor_type,
                    "website": o.website,
                }
                for o in orgs
            ]
        except Exception as org_err:
            logger.warning("Vendor organization search failed: %s", org_err)

        return jsonify(
            {
                "success": True,
                "query": query,
                "vendor_id": vendor_id,
                "category": category,
                "results": products,
                "organizations": org_results,
                "total_found": len(products) + len(org_results),
            }
        )

    except Exception as e:
        logger.error(f"Error searching vendor products: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@vendor_product_bp.route("/vendors", methods=["GET"])
@login_required
def list_vendors():
    """List all vendor organizations with basic information."""
    try:
        from app.models.vendor.vendor_organization import VendorOrganization

        limit = min(safe_int_arg('limit', 50, minimum=1, maximum=500), 100)
        strategic_tier = request.args.get("strategic_tier")

        query = VendorOrganization.query

        if strategic_tier:
            query = query.filter(VendorOrganization.strategic_tier == strategic_tier)

        vendors = query.limit(limit).all()

        return jsonify(
            {
                "success": True,
                "vendors": [vendor.to_dict() for vendor in vendors],
                "total_found": len(vendors),
            }
        )

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error listing vendors: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@vendor_product_bp.route("/categories", methods=["GET"])
@login_required
def get_product_categories():
    """Get all product categories with vendor counts."""
    try:
        from sqlalchemy import func

        from app.models.vendor.vendor_product import VendorProductFamily

        category_stats = (
            db.session.query(
                VendorProductFamily.category,
                func.count(VendorProductFamily.id).label("family_count"),
            )
            .filter(VendorProductFamily.category.isnot(None))
            .group_by(VendorProductFamily.category)
            .all()
        )

        categories = []
        for category, family_count in category_stats:
            if category:
                categories.append(
                    {
                        "category": category,
                        "family_count": family_count,
                        "total_count": family_count,
                    }
                )

        categories.sort(key=lambda x: x["total_count"], reverse=True)

        return jsonify(
            {
                "success": True,
                "categories": categories,
                "total_categories": len(categories),
            }
        )

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error getting product categories: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@vendor_product_bp.route("/<int:vendor_id>/products", methods=["POST"])
@login_required
@audit_log("vendor_product_create")
def create_vendor_product(vendor_id):
    """Create a new product for a vendor organisation."""
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

    vendor = VendorOrganization.query.get_or_404(vendor_id)
    logger.info(f"Creating product for vendor {vendor_id} ({vendor.name})")
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "Request body required"}), 400

    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "Product name is required"}), 400

    VALID_PRODUCT_TYPES = {"software", "saas", "platform", "infrastructure", "service"}
    VALID_DEPLOYMENT_MODELS = {"cloud", "on_premise", "hybrid", "saas"}

    product_type = data.get("product_type") or "software"
    if product_type not in VALID_PRODUCT_TYPES:
        product_type = "software"

    deployment_model = data.get("deployment_model") or "cloud"
    if deployment_model not in VALID_DEPLOYMENT_MODELS:
        deployment_model = "cloud"

    if len(name) > 200:
        return jsonify({"success": False, "error": "Product name must be 200 characters or fewer"}), 400

    product = VendorProduct(
        vendor_organization_id=vendor_id,
        name=name,
        product_type=product_type,
        deployment_model=deployment_model,
        target_market=(data.get("target_market") or "")[:50],  # truncate to column width
        status=data.get("status") or "active",
    )
    try:
        db.session.add(product)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating vendor product for vendor {vendor_id}: {e}")
        return jsonify({"success": False, "error": "Failed to create product. Please try again."}), 500
    return jsonify({
        "success": True,
        "product": {
            "id": product.id,
            "name": product.name,
            "product_type": product.product_type,
            "deployment_model": product.deployment_model,
            "target_market": product.target_market,
            "status": product.status,
        },
    }), 201


def register_vendor_product_routes(app):
    """Register vendor product blueprint with Flask app."""
    app.register_blueprint(vendor_product_bp)
    logger.info("Vendor product API routes registered")

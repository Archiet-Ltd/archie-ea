"""
MIGRATION: Copied from app/api/vendor_catalog_routes.py
Changes: `from app import db` -> `from app.extensions import db`
Legacy file preserved at original location.

Vendor Catalog API Routes - Frontend-Backend Integration
"""

import logging

from flask import Blueprint, jsonify, request
from flask_login import login_required
from werkzeug.exceptions import HTTPException

from app.decorators import audit_log
from app.extensions import db
from app.models.business_capabilities import BusinessCapability
from app.models.vendor.vendor_organization import (
    VendorOrganization,
    VendorProduct,
    VendorProductCapability,
)
from app.modules.vendors.services.vendor_product_service import VendorProductService
from app.utils.pagination import safe_int_arg
from app.utils.route_guards import require_entity

logger = logging.getLogger(__name__)

# Create blueprint
vendor_bp = Blueprint("vendor", __name__, url_prefix="/api/vendors")


@vendor_bp.route("", methods=["GET"])
@login_required
def get_vendors():
    """Get all vendors with basic statistics."""
    try:
        service = VendorProductService()

        tier = request.args.get("tier")
        category = request.args.get("category")
        search = request.args.get("search", "").strip()

        vendors = service.get_all_vendors_with_stats(tier=tier, category=category, search=search)

        return jsonify(
            {
                "success": True,
                "vendors": vendors,
                "total": len(vendors),
                "filters": {"tier": tier, "category": category, "search": search},
            }
        )

    except Exception as e:
        logger.error(f"Error getting vendors: {e}")
        return jsonify({"success": False, "error": "Failed to get vendors"}), 500


@vendor_bp.route("/<int:vendor_id>/hierarchy", methods=["GET"])
@login_required
def get_vendor_hierarchy(vendor_id):
    """Get complete vendor product hierarchy."""
    try:
        # Existence guard: an empty hierarchy for a nonexistent vendor is fabricated data.
        require_entity(VendorOrganization, vendor_id, description=f"Vendor {vendor_id} not found")

        service = VendorProductService()
        hierarchy = service.get_vendor_hierarchy(vendor_id)

        if not hierarchy:
            return jsonify({"success": False, "error": "Vendor not found"}), 404

        return jsonify({"success": True, "hierarchy": hierarchy, "vendor_id": vendor_id})

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting vendor hierarchy for {vendor_id}: {e}")
        return (
            jsonify({"success": False, "error": f"Failed to get vendor hierarchy: {str(e)}"}),
            500,
        )


@vendor_bp.route("/search", methods=["GET"])
@login_required
def search_vendor_products():
    """Search vendor products with intelligent matching."""
    try:
        query = request.args.get("q", "").strip()
        vendor_id = request.args.get("vendor_id", type=int)
        category = request.args.get("category")
        tier = request.args.get("tier")
        limit = safe_int_arg('limit', 50, minimum=1, maximum=500)

        if not query:
            return jsonify({"success": False, "error": "Search query is required"}), 400

        service = VendorProductService()
        results = service.search_vendor_products(
            query=query, vendor_id=vendor_id, category=category, limit=limit
        )

        return jsonify(
            {
                "success": True,
                "results": results,
                "total": len(results),
                "query": query,
                "filters": {"vendor_id": vendor_id, "category": category, "tier": tier},
            }
        )

    except Exception as e:
        logger.error(f"Error searching vendor products: {e}")
        return jsonify({"success": False, "error": "Failed to search products"}), 500


@vendor_bp.route("/extract", methods=["POST"])
@login_required
@audit_log("vendor_catalog_extract")
def extract_vendor_product():
    """Extract vendor and product information using AI."""
    try:
        data = request.get_json()
        if not data or "application_description" not in data:
            return jsonify({"success": False, "error": "application_description is required"}), 400

        application_description = data["application_description"]
        service = VendorProductService()

        result = service.extract_vendor_product(application_description)

        if result["success"]:
            return jsonify(
                {
                    "success": True,
                    "extraction_result": result["extraction_result"],
                    "alternatives": result.get("alternatives", []),
                    "confidence": result.get("confidence", 0.0),
                    "extraction_method": result.get("extraction_method", "unknown"),
                }
            )
        else:
            return (
                jsonify({"success": False, "error": result.get("error", "Extraction failed")}),
                400,
            )

    except Exception as e:
        logger.error(f"Error extracting vendor product: {e}")
        return (
            jsonify({"success": False, "error": f"Failed to extract vendor product: {str(e)}"}),
            500,
        )


@vendor_bp.route("/<int:vendor_id>/products", methods=["GET"])
@login_required
def get_vendor_products(vendor_id):
    """Get all products for a specific vendor."""
    try:
        service = VendorProductService()
        products = service.get_vendor_products(vendor_id)

        if not products:
            return jsonify({"success": False, "error": "Vendor not found or no products"}), 404

        return jsonify(
            {"success": True, "products": products, "vendor_id": vendor_id, "total": len(products)}
        )

    except Exception as e:
        logger.error(f"Error getting vendor products for {vendor_id}: {e}")
        return jsonify({"success": False, "error": "Failed to get vendor products"}), 500


@vendor_bp.route("/<int:vendor_id>/families", methods=["GET"])
@login_required
def get_vendor_families(vendor_id):
    """Get all product families for a specific vendor."""
    try:
        service = VendorProductService()
        families = service.get_vendor_product_families(vendor_id)

        if not families:
            return (
                jsonify({"success": False, "error": "Vendor not found or no product families"}),
                404,
            )

        return jsonify(
            {"success": True, "families": families, "vendor_id": vendor_id, "total": len(families)}
        )

    except Exception as e:
        logger.error(f"Error getting vendor families for {vendor_id}: {e}")
        return jsonify({"success": False, "error": "Failed to get vendor families"}), 500


@vendor_bp.route("/families/<int:family_id>/products", methods=["GET"])
@login_required
def get_family_products(family_id):
    """Get all products in a specific product family."""
    try:
        service = VendorProductService()
        products = service.get_family_products(family_id)

        if not products:
            return (
                jsonify({"success": False, "error": "Product family not found or no products"}),
                404,
            )

        return jsonify(
            {"success": True, "products": products, "family_id": family_id, "total": len(products)}
        )

    except Exception as e:
        logger.error(f"Error getting family products for {family_id}: {e}")
        return jsonify({"success": False, "error": "Failed to get family products"}), 500


@vendor_bp.route("/products/<int:product_id>", methods=["GET"])
@login_required
def get_product_details(product_id):
    """Get detailed information about a specific product."""
    try:
        service = VendorProductService()
        product = service.get_product_details(product_id)

        if not product:
            return jsonify({"success": False, "error": "Product not found"}), 404

        applications = service.get_product_applications(product_id)

        return jsonify(
            {
                "success": True,
                "product": product,
                "applications": applications,
                "total_applications": len(applications),
            }
        )

    except Exception as e:
        logger.error(f"Error getting product details for {product_id}: {e}")
        return jsonify({"success": False, "error": "Failed to get product details"}), 500


@vendor_bp.route("/catalog", methods=["GET"])
@login_required
def get_vendor_catalog():
    """Get complete vendor catalog with three-level hierarchy."""
    try:
        service = VendorProductService()

        tier = request.args.get("tier")
        category = request.args.get("category")
        search = request.args.get("search", "").strip()

        catalog = service.get_complete_catalog(tier=tier, category=category, search=search)

        return jsonify(
            {
                "success": True,
                "catalog": catalog,
                "total_vendors": len(catalog.get("vendors", [])),
                "total_families": catalog.get("total_families", 0),
                "total_products": catalog.get("total_products", 0),
                "filters": {"tier": tier, "category": category, "search": search},
            }
        )

    except Exception as e:
        logger.error(f"Error getting vendor catalog: {e}")
        return jsonify({"success": False, "error": "Failed to get vendor catalog"}), 500


@vendor_bp.route("/statistics", methods=["GET"])
@login_required
def get_vendor_statistics():
    """Get vendor catalog statistics."""
    try:
        service = VendorProductService()
        stats = service.get_catalog_statistics()

        return jsonify({"success": True, "statistics": stats})

    except Exception as e:
        logger.error(f"Error getting vendor statistics: {e}")
        return (
            jsonify({"success": False, "error": f"Failed to get vendor statistics: {str(e)}"}),
            500,
        )


@vendor_bp.route("/categories", methods=["GET"])
@login_required
def get_vendor_categories():
    """Get all available vendor categories."""
    try:
        service = VendorProductService()
        categories = service.get_all_categories()

        return jsonify({"success": True, "categories": categories})

    except Exception as e:
        logger.error(f"Error getting vendor categories: {e}")
        return (
            jsonify({"success": False, "error": f"Failed to get vendor categories: {str(e)}"}),
            500,
        )


@vendor_bp.route("/tiers", methods=["GET"])
@login_required
def get_vendor_tiers():
    """Get all available vendor tiers."""
    try:
        service = VendorProductService()
        tiers = service.get_all_tiers()

        return jsonify({"success": True, "tiers": tiers})

    except Exception as e:
        logger.error(f"Error getting vendor tiers: {e}")
        return jsonify({"success": False, "error": "Failed to get vendor tiers"}), 500


@vendor_bp.route("/capability-matrix", methods=["GET"])
@login_required
def get_capability_matrix():
    """Get vendor capability coverage matrix data."""
    try:
        query = (
            db.session.query(
                VendorProductCapability, VendorProduct, VendorOrganization, BusinessCapability
            )
            .join(VendorProduct, VendorProductCapability.vendor_product_id == VendorProduct.id)
            .join(VendorOrganization, VendorProduct.vendor_organization_id == VendorOrganization.id)
            .join(
                BusinessCapability,
                VendorProductCapability.business_capability_id == BusinessCapability.id,
            )
            .filter(VendorProductCapability.coverage_percentage.isnot(None))
            .all()
        )

        matrix_data = []
        for mapping, product, vendor, capability in query:
            gaps = []
            strengths = []

            try:
                if mapping.gaps:
                    gaps = mapping.get_gaps()
                if mapping.strengths:
                    strengths = mapping.get_strengths()
            except Exception as e:
                logger.debug("Failed to parse vendor mapping JSON: %s", e)

            matrix_data.append(
                {
                    "vendor_name": vendor.name,
                    "vendor_display_name": vendor.display_name,
                    "product_name": product.name,
                    "product_category": product.product_type,
                    "capability_name": capability.name,
                    "capability_category": capability.category,
                    "coverage_percentage": int(mapping.coverage_percentage or 0),
                    "maturity_level": mapping.maturity_level,
                    "implementation_complexity": mapping.implementation_complexity,
                    "out_of_box_percentage": int(mapping.out_of_box_percentage or 0),
                    "gaps": gaps,
                    "strengths": strengths,
                    "evidence": {
                        "source": "Vendor Product Capability Mapping",
                        "verified_at": mapping.last_validated_at.isoformat()
                        if mapping.last_validated_at
                        else None,
                        "verified_by": mapping.validated_by.username
                        if mapping.validated_by
                        else None,
                    },
                }
            )

        return jsonify(
            {
                "success": True,
                "matrix_data": matrix_data,
                "total_mappings": len(matrix_data),
                "vendors": list(set(item["vendor_name"] for item in matrix_data)),
                "products": list(
                    set(f"{item['vendor_name']} - {item['product_name']}" for item in matrix_data)
                ),
                "capabilities": list(set(item["capability_name"] for item in matrix_data)),
            }
        )

    except Exception as e:
        logger.error(f"Error getting capability matrix: {e}")
        return (
            jsonify({"success": False, "error": f"Failed to get capability matrix: {str(e)}"}),
            500,
        )


@vendor_bp.route("/<int:vendor_id>/score")
@login_required
def score_vendor(vendor_id):
    """FRAG-036: Score all VendorOptions linked to this vendor organization."""
    try:
        from app.models.vendor_analysis import VendorOption, OptionsAnalysis
        from app.models.business_capabilities import BusinessCapability
        from app.modules.vendors.services.vendor_scoring_service import VendorScoringService

        vendor = VendorOrganization.query.get_or_404(vendor_id)
        # tenant-scoping-ok: VendorOption has no organisation column of its own; the fence is the
        # join to OptionsAnalysis, which is TenantMixin, so the ORM listener scopes this query.
        options = (
            VendorOption.query
            .join(OptionsAnalysis, VendorOption.analysis_id == OptionsAnalysis.id)
            .filter(VendorOption.vendor_organization_id == vendor_id)
            .all()
        )

        if not options:
            return jsonify({"success": True, "vendor": vendor.name, "scores": [], "total": 0})

        svc = VendorScoringService()
        results = []
        for vo in options:
            capability = None
            if vo.analysis_id:
                analysis = OptionsAnalysis.query.get(vo.analysis_id)
                if analysis:
                    capability = BusinessCapability.query.get(analysis.capability_id)
            if capability is None:
                # Use a minimal stub so scoring still works
                from types import SimpleNamespace
                capability = SimpleNamespace(
                    maturity_gap=None,
                    name=vo.vendor_name or vendor.name,
                    category="general",
                )
            svc.score_vendor(vo, capability)
            results.append({
                "vendor_option_id": vo.id,
                "vendor_name": vo.vendor_name or vendor.name,
                "total_score": round(vo.total_score or 0, 1),
                "cost_score": round(vo.cost_score or 0, 1),
                "capability_coverage_score": round(vo.capability_coverage_score or 0, 1),
                "risk_score": round(vo.risk_score or 0, 1),
                "strategic_fit_score": round(vo.strategic_fit_score or 0, 1),
                "implementation_score": round(vo.implementation_score or 0, 1),
            })
        db.session.commit()
        return jsonify({"success": True, "vendor": vendor.name, "scores": results, "total": len(results)})
    except HTTPException:
        # Let get_or_404 (e.g. unknown vendor) surface as its real status
        # instead of being masked as a 500 by the broad handler below.
        raise
    except Exception as e:
        logger.error(f"Error scoring vendor {vendor_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@vendor_bp.route("/ranking")
@login_required
def rank_vendors():
    """FRAG-036: Rank all vendor options by score."""
    try:
        from app.models.vendor_analysis import OptionsAnalysis, VendorOption
        from app.modules.vendors.services.vendor_scoring_service import VendorScoringService

        # tenant-scoping-ok: same join-through-analysis fence as score_vendor above.
        options = (
            VendorOption.query
            .join(OptionsAnalysis, VendorOption.analysis_id == OptionsAnalysis.id)
            .filter(VendorOption.total_score.isnot(None))
            .all()
        )
        if not options:
            return jsonify({"success": True, "ranking": [], "total": 0})

        svc = VendorScoringService()
        ranked = svc.rank_vendors(options)
        db.session.commit()
        return jsonify({
            "success": True,
            "ranking": [
                {
                    "rank": vo.ranking,
                    "vendor_option_id": vo.id,
                    "vendor_name": vo.vendor_name,
                    "total_score": round(vo.total_score or 0, 1),
                }
                for vo in ranked
            ],
            "total": len(ranked),
        })
    except Exception as e:
        logger.error(f"Error ranking vendors: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def register_vendor_catalog_routes(app):
    """Register vendor catalog blueprint with Flask app."""
    app.register_blueprint(vendor_bp)
    logger.info("Vendor catalog API routes registered")

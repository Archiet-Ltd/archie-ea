"""
MIGRATION: Copied from app/routes/vendor_mdm_api.py
Changes: None required (already uses absolute imports, no `from app import db`)
Legacy file preserved at original location.

Vendor MDM API Routes

REST API endpoints for vendor/product MDM reconciliation and taxonomy management.
"""

import json
import logging

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app.modules.vendors.services.vendor_mdm import VendorMDMService
from app.utils.pagination import safe_int_arg

logger = logging.getLogger(__name__)

vendor_mdm_bp = Blueprint("vendor_mdm", __name__, url_prefix="/api/vendor-mdm")


@vendor_mdm_bp.route("/normalize", methods=["POST"])
@login_required
def bulk_normalize():
    """Bulk normalize vendor or product names."""
    try:
        data = request.get_json()
        if not data or "items" not in data:
            return jsonify({"error": "Missing 'items' in request body"}), 400

        items = data["items"]
        name_type = data.get("type", "vendor")

        if name_type not in ["vendor", "product"]:
            return jsonify({"error": "Type must be 'vendor' or 'product'"}), 400

        service = VendorMDMService()
        results = service.bulk_normalize(items, name_type)

        return jsonify(
            {
                "success": True,
                "data": results,
                "summary": {
                    "total": len(results),
                    "exact_matches": len([r for r in results if r["method"] == "exact"]),
                    "fuzzy_matches": len([r for r in results if r["method"] == "fuzzy"]),
                    "no_matches": len([r for r in results if r["method"] == "no_match"]),
                },
            }
        )

    except Exception as e:
        logger.error(f"Bulk normalize failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@vendor_mdm_bp.route("/duplicates", methods=["GET"])
@login_required
def find_duplicates():
    """
    RETIRED — delegates to MatcherService.

    Callers:
    - (route, called by clients)

    Instead of maintaining its own algorithm, this now delegates to the
    matcher for consistent matching across the platform.
    """
    try:
        name_type = request.args.get("type", "vendor")
        threshold = float(request.args.get("threshold", 0.9))

        if name_type not in ["vendor", "product"]:
            return jsonify({"error": "Type must be 'vendor' or 'product'"}), 400

        if not 0.0 <= threshold <= 1.0:
            return jsonify({"error": "Threshold must be between 0.0 and 1.0"}), 400

        service = VendorMDMService()
        duplicates = service.find_duplicates(name_type, threshold)

        return jsonify(
            {
                "success": True,
                "data": duplicates,
                "summary": {
                    "total_groups": len(duplicates),
                    "total_pairs": sum(len(group) for group in duplicates),
                },
            }
        )

    except Exception as e:
        logger.error(f"Find duplicates failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@vendor_mdm_bp.route("/reconciliation", methods=["GET"])
@login_required
def get_reconciliation_candidates():
    """Get candidates for manual reconciliation."""
    try:
        name_type = request.args.get("type", "vendor")
        min_confidence = float(request.args.get("min_confidence", 0.7))
        limit = safe_int_arg('limit', 50, minimum=1, maximum=500)

        if name_type not in ["vendor", "product"]:
            return jsonify({"error": "Type must be 'vendor' or 'product'"}), 400

        service = VendorMDMService()
        candidates = service.get_reconciliation_candidates(name_type, min_confidence)

        # Apply limit
        candidates = candidates[:limit]

        return jsonify(
            {
                "success": True,
                "data": candidates,
                "summary": {
                    "total_candidates": len(candidates),
                    "type": name_type,
                    "min_confidence": min_confidence,
                },
            }
        )

    except Exception as e:
        logger.error(f"Get reconciliation candidates failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@vendor_mdm_bp.route("/validate", methods=["POST"])
@login_required
def validate_mapping():
    """Validate or reject a taxonomy mapping."""
    try:
        data = request.get_json()
        if not data or "mapping_id" not in data:
            return jsonify({"error": "Missing 'mapping_id' in request body"}), 400

        mapping_id = data["mapping_id"]
        is_valid = data.get("is_valid", True)
        user = str(current_user.id) if current_user and current_user.is_authenticated else "system"

        service = VendorMDMService()
        success = service.validate_mapping(mapping_id, is_valid, user)

        if success:
            return jsonify(
                {
                    "success": True,
                    "message": f"Mapping {mapping_id} {'validated' if is_valid else 'rejected'}",
                }
            )
        else:
            return jsonify({"success": False, "error": "Mapping not found"}), 404

    except Exception as e:
        logger.error(f"Validate mapping failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@vendor_mdm_bp.route("/import", methods=["POST"])
@login_required
def import_external_data():
    """Import vendor/product data from external sources."""
    try:
        data = request.get_json()
        if not data or "source" not in data or "data" not in data:
            return jsonify({"error": "Missing 'source' or 'data' in request body"}), 400

        source = data["source"]
        import_data = data["data"]

        if source not in ["g2", "crunchbase", "croud"]:
            return jsonify({"error": "Source must be 'g2', 'crunchbase', or 'croud'"}), 400

        service = VendorMDMService()
        results = service.import_external_data(source, import_data)

        return jsonify({"success": True, "data": results})

    except Exception as e:
        logger.error(f"Import external data failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@vendor_mdm_bp.route("/taxonomy", methods=["GET"])
@login_required
def get_taxonomy():
    """Get taxonomy hierarchy for vendors and products."""
    try:
        taxonomy_type = request.args.get("type", "both")
        active_only = request.args.get("active_only", "true").lower() == "true"

        from app.models.vendor_taxonomy import ProductTaxonomy, VendorTaxonomy

        result = {}

        if taxonomy_type in ["vendor", "both"]:
            query = VendorTaxonomy.query
            if active_only:
                query = query.filter(VendorTaxonomy.is_active.is_(True))
            vendors = query.all()

            result["vendors"] = [
                {
                    "id": v.id,
                    "canonical_name": v.canonical_name,
                    "display_name": v.display_name,
                    "vendor_type": v.vendor_type,
                    "aliases": v.aliases_list,
                    "industry_vertical": v.industry_vertical,
                    "primary_domain": v.primary_domain,
                }
                for v in vendors
            ]

        if taxonomy_type in ["product", "both"]:
            query = ProductTaxonomy.query
            if active_only:
                query = query.filter(ProductTaxonomy.is_active.is_(True))
            products = query.all()

            result["products"] = [
                {
                    "id": p.id,
                    "canonical_name": p.canonical_name,
                    "display_name": p.display_name,
                    "category": p.category,
                    "sub_category": p.sub_category,
                    "aliases": json.loads(p.aliases) if p.aliases else [],
                }
                for p in products
            ]

        return jsonify({"success": True, "data": result})

    except Exception as e:
        logger.error(f"Get taxonomy failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@vendor_mdm_bp.route("/mappings", methods=["GET"])
@login_required
def get_mappings():
    """Get taxonomy mappings with filtering."""
    try:
        name_type = request.args.get("type")
        validated = request.args.get("validated", "all")
        limit = safe_int_arg('limit', 100, minimum=1, maximum=500)

        from app.models.vendor_taxonomy import TaxonomyMapping

        query = TaxonomyMapping.query

        if name_type:
            query = query.filter(TaxonomyMapping.name_type == name_type)

        if validated == "true":
            query = query.filter(TaxonomyMapping.is_validated.is_(True))
        elif validated == "false":
            query = query.filter(TaxonomyMapping.is_validated.is_(False))

        mappings = query.order_by(TaxonomyMapping.created_at.desc()).limit(limit).all()

        result = [
            {
                "id": m.id,
                "raw_name": m.raw_name,
                "name_type": m.name_type,
                "canonical_name": m.canonical_name,
                "match_confidence": m.match_confidence,
                "match_method": m.match_method,
                "match_source": m.match_source,
                "is_validated": m.is_validated,
                "validated_by": m.validated_by,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in mappings
        ]

        return jsonify(
            {
                "success": True,
                "data": result,
                "summary": {
                    "total": len(result),
                    "validated": len([m for m in result if m["is_validated"]]),
                    "pending": len([m for m in result if not m["is_validated"]]),
                },
            }
        )

    except Exception as e:
        logger.error(f"Get mappings failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500

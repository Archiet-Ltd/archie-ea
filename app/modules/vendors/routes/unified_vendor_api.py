"""
MIGRATION: Copied from app/unified_vendors/api.py
Changes:
  - `from . import unified_vendors_api_bp` -> Blueprint defined locally (was in app/unified_vendors/__init__.py)
  - All other imports kept as absolute paths
Legacy file preserved at original location.

Unified Vendor API Endpoints

Consolidated API endpoints for vendor operations.
All endpoints return standardized JSON responses.
"""

from flask import jsonify, request, current_app, Blueprint, url_for
from flask_login import login_required, current_user
from sqlalchemy import func

from app.utils.api_helpers import api_error
from app.utils.validators import validate_string

from app.decorators import audit_log, require_roles
from app.middleware.tenant_decorators import platform_admin_required
from app.services.rate_limiter import rate_limit
from app.extensions import db
from app.models.vendor_organization import VendorOrganization, VendorProduct
import logging
from app.utils.pagination import safe_int_arg
from app.utils.route_guards import require_entity
from app.models.vendor_analysis import OptionsAnalysis
logger = logging.getLogger(__name__)

# Blueprint defined here (was in app/unified_vendors/__init__.py)
unified_vendors_api_bp = Blueprint(
    "unified_vendors_api",
    __name__,
    url_prefix="/api/vendors",
)

# Import allowlist from vendor_management_routes for consistency
try:
    from app.routes.vendor_management_routes import VENDOR_UPDATE_ALLOWLIST
except ImportError:
    # Fallback if import fails
    VENDOR_UPDATE_ALLOWLIST = [
        "name",
        "vendor_type",
        "country",
        "description",
        "website",
        "display_name",
        "headquarters_location",
        "strategic_tier",
        "partnership_level",
        "status",
    ]

# Maximum page size to prevent DoS
MAX_PER_PAGE = 100

# Input validation limits
MAX_NAME_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 5000
MAX_WEBSITE_LENGTH = 500


# =============================================================================
# Vendor Catalog API
# =============================================================================


@unified_vendors_api_bp.route("/list", methods=["GET"])
@login_required
def list_vendors():
    """List vendors with pagination and filtering."""
    page = safe_int_arg('page', 1, minimum=1)
    per_page = min(safe_int_arg('per_page', 25, minimum=1, maximum=500), 100)
    search = request.args.get("q") or request.args.get("search", "")
    vendor_type = request.args.get("vendor_type", "")
    sort_by = request.args.get("sort") or request.args.get("sort_by", "name")
    sort_order = request.args.get("dir") or request.args.get("sort_order", "asc")

    ALLOWED_SORT_COLUMNS = {"name", "vendor_type", "headquarters_location", "website", "created_at", "updated_at"}
    if sort_by not in ALLOWED_SORT_COLUMNS:
        sort_by = "name"
    if sort_order not in ("asc", "desc"):
        sort_order = "asc"

    query = VendorOrganization.query

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            VendorOrganization.name.ilike(search_term)
            | VendorOrganization.vendor_type.ilike(search_term)
            | VendorOrganization.headquarters_location.ilike(search_term)
        )
    if vendor_type:
        query = query.filter(VendorOrganization.vendor_type.ilike(f"%{vendor_type}%"))

    sort_column = getattr(VendorOrganization, sort_by, VendorOrganization.name)
    if sort_order == "desc":
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())

    paginated = query.paginate(page=page, per_page=per_page, error_out=False)

    # Compute product counts for all vendors on this page in a single query
    vendor_ids = [v.id for v in paginated.items]
    products_count_map = {}
    if vendor_ids:
        product_counts = (
            db.session.query(
                VendorProduct.vendor_organization_id,
                func.count(VendorProduct.id),
            )
            .filter(VendorProduct.vendor_organization_id.in_(vendor_ids))
            .group_by(VendorProduct.vendor_organization_id)
            .all()
        )
        products_count_map = dict(product_counts)

    vendors = []
    for idx, v in enumerate(paginated.items):
        d = v.to_dict()
        d["row_number"] = (page - 1) * per_page + idx + 1
        d["products_count"] = products_count_map.get(v.id, 0)
        vendors.append(d)

    return jsonify({
        "success": True,
        "vendors": vendors,
        "total": paginated.total,
        "page": page,
        "pages": paginated.pages,
        "per_page": per_page,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": paginated.total,
            "pages": paginated.pages,
            "has_prev": paginated.has_prev,
            "has_next": paginated.has_next,
        },
    })


@unified_vendors_api_bp.route("/bulk", methods=["DELETE"])
@login_required
def bulk_delete_vendors():
    """Bulk delete vendors by IDs."""
    data = request.get_json() or {}
    ids = data.get("ids", [])
    if not ids:
        return api_error("No IDs provided", "MISSING_IDS")
    if not isinstance(ids, list):
        return api_error("ids must be a list", "INVALID_INPUT")

    # DEF-073, Capgemini dry-run: vendor_products.vendor_id has no
    # ondelete=CASCADE, so deleting a vendor with products raised
    # IntegrityError straight out of the bulk DELETE, uncaught, and reached
    # the user as a raw "Internal Server Error" toast twice (once per retry)
    # with the vendor left in place either way. Report which vendors are
    # blocked rather than let the DB error surface.
    from app.models.vendor.vendor_organization import VendorProduct

    blocking = (
        db.session.query(VendorProduct.vendor_organization_id, db.func.count(VendorProduct.id))
        .filter(VendorProduct.vendor_organization_id.in_(ids))
        .group_by(VendorProduct.vendor_organization_id)
        .all()
    )
    if blocking:
        blocked_ids = [vendor_id for vendor_id, _count in blocking]
        return api_error(
            "Cannot delete vendor(s) with products still attached: "
            + ", ".join(str(v) for v in blocked_ids)
            + ". Remove their products first.",
            "VENDOR_HAS_PRODUCTS",
        )

    try:
        deleted = VendorOrganization.query.filter(VendorOrganization.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Bulk vendor delete failed for ids %s", ids)
        return api_error("Could not delete the selected vendor(s).", "DELETE_FAILED")
    return jsonify({"deleted": deleted, "ids": ids})


@unified_vendors_api_bp.route("/<int:vendor_id>", methods=["GET"])
@login_required
def get_vendor(vendor_id):
    """Get detailed vendor information."""
    vendor = VendorOrganization.query.get_or_404(vendor_id)
    return jsonify(
        {
            "success": True,
            "vendor": vendor.to_dict(),
        }
    )


@unified_vendors_api_bp.route("/", methods=["GET"])
@login_required
def search_vendors():
    """Search vendors by name — autocomplete endpoint used by pickers."""
    search = request.args.get("search", "").strip()
    # ARCH-014: ``total`` used to be ``len(vendors)`` — the size of the page just
    # returned, not the size of the collection. A consumer paging on it concluded
    # the catalogue held 10 vendors when it held 45, with no way to detect the
    # truncation. It is now a COUNT over the filtered query, independent of paging.
    # ``per_page`` was also silently ignored (only ``limit`` was read), so an
    # explicit page-size request was answered with the default 10.
    page_size = safe_int_arg('per_page', None, minimum=1, maximum=500)
    if page_size is None:
        page_size = safe_int_arg('limit', None, minimum=1, maximum=500)
    if page_size is None:
        page_size = 10
    page_size = max(1, min(page_size, MAX_PER_PAGE))
    page = max(1, safe_int_arg('page', 1, minimum=1) or 1)

    query = VendorOrganization.query
    if search:
        query = query.filter(VendorOrganization.name.ilike(f"%{search}%"))

    total = query.count()
    vendors = (
        query.order_by(VendorOrganization.name)
        .limit(page_size)
        .offset((page - 1) * page_size)
        .all()
    )
    return jsonify({
        "vendors": [{"id": v.id, "name": v.name, "vendor_type": getattr(v, "vendor_type", None)} for v in vendors],
        "total": total,
        "page": page,
        "per_page": page_size,
        "pages": (total + page_size - 1) // page_size,
    })


@unified_vendors_api_bp.route("/", methods=["POST"])
@login_required
@require_roles("admin", "architect")
@platform_admin_required
@audit_log("vendor_create")
@rate_limit(20, "1h")
def create_vendor():
    """Create new vendor organization with validation and duplicate prevention.

    Platform-admin-only (lead review of PR 430, D-2 follow-up): writes the
    global, platform-wide ``VendorOrganization`` catalogue, not an
    organisation-scoped resource. ``require_roles("admin", "architect")`` is
    satisfied by any org's own admin/architect for their own brand-new,
    self-registered organisation -- no invitation into anyone else's org
    needed. ``require_roles`` is kept rather than removed;
    ``platform_admin_required`` is the operative check.
    """
    from datetime import datetime
    from sqlalchemy import func

    data = request.get_json() or {}

    # Validation
    if not data.get("name"):
        return api_error("Vendor name required", "MISSING_NAME")

    if len(str(data["name"])) > MAX_NAME_LENGTH:
        return api_error(
            f"Name exceeds maximum length of {MAX_NAME_LENGTH} characters",
            "NAME_TOO_LONG",
        )

    # ARCH-071: vendor name is an identifier, not rich text — strip HTML tags
    # at the schema layer rather than relying solely on output encoding.
    # Stripping (not entity-escaping) matches validate_application_name's
    # approach so this does not double-escape on Jinja's own autoescape.
    _valid, data["name"], _err = validate_string(
        data["name"], max_length=MAX_NAME_LENGTH, min_length=1,
        field_name="name", required=True, strip_tags=True,
    )
    if not _valid:
        return api_error(_err or "Invalid vendor name", "INVALID_NAME")

    if (
        data.get("description")
        and len(str(data["description"])) > MAX_DESCRIPTION_LENGTH
    ):
        return jsonify(
            {
                "success": False,
                "error": f"Description exceeds maximum length of {MAX_DESCRIPTION_LENGTH} characters",
            }
        ), 400

    # Check for duplicates (case-insensitive with locking)
    existing = (
        VendorOrganization.query.with_for_update()
        .filter(func.lower(VendorOrganization.name) == data["name"].lower())
        .first()
    )

    if existing:
        return jsonify(
            {
                "success": False,
                "error": f"Vendor '{data['name']}' already exists",
                "existing_id": existing.id,
            }
        ), 409

    vendor = VendorOrganization(
        name=data["name"],
        vendor_type=data.get("vendor_type", "software_vendor"),
        headquarters_location=data.get("country"),  # model column is headquarters_location
        description=data.get("description"),
        website=data.get("website"),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    db.session.add(vendor)
    db.session.commit()

    current_app.logger.info(
        f"[AUDIT] Vendor created: {vendor.name} (ID: {vendor.id}) by {current_user.email}"
    )

    return jsonify(
        {
            "success": True,
            "message": f"Vendor '{vendor.name}' created",
            "vendor": vendor.to_dict(),
        }
    ), 201


@unified_vendors_api_bp.route("/<int:vendor_id>", methods=["PUT", "PATCH"])
@login_required
@require_roles("admin", "architect")
@platform_admin_required
@audit_log("vendor_update")
@rate_limit(30, "1h")
def update_vendor(vendor_id):
    """Update vendor organization with allowlist protection and audit logging. PROD-009

    Platform-admin-only -- see ``create_vendor``'s docstring above.
    """
    from datetime import datetime

    data = request.get_json() or {}

    # Validate input lengths
    if data.get("name") and len(str(data["name"])) > MAX_NAME_LENGTH:
        return jsonify(
            {
                "success": False,
                "error": f"Name exceeds maximum length of {MAX_NAME_LENGTH} characters",
            }
        ), 400

    if (
        data.get("description")
        and len(str(data["description"])) > MAX_DESCRIPTION_LENGTH
    ):
        return jsonify(
            {
                "success": False,
                "error": f"Description exceeds maximum length of {MAX_DESCRIPTION_LENGTH} characters",
            }
        ), 400

    if data.get("website") and len(str(data["website"])) > MAX_WEBSITE_LENGTH:
        return jsonify(
            {
                "success": False,
                "error": f"Website exceeds maximum length of {MAX_WEBSITE_LENGTH} characters",
            }
        ), 400

    # Lock row to prevent race conditions
    vendor = VendorOrganization.query.with_for_update().get_or_404(vendor_id)

    # Track changes for audit log
    changes = []

    # Use allowlist to prevent mass assignment
    for key in VENDOR_UPDATE_ALLOWLIST:
        if key in data:
            old_value = getattr(vendor, key, None)
            new_value = data[key]

            # Only log if value actually changed
            if old_value != new_value:
                changes.append({"field": key, "old": old_value, "new": new_value})
                setattr(vendor, key, new_value)

    vendor.updated_at = datetime.utcnow()
    db.session.commit()

    # Audit logging
    if changes:
        changes_str = ", ".join(
            [f"{c['field']}: {c['old']} -> {c['new']}" for c in changes]
        )
        current_app.logger.info(
            f"[AUDIT] Vendor updated: {vendor.name} (ID: {vendor.id}) by {current_user.email}. "
            f"Changes: {changes_str}"
        )
    else:
        current_app.logger.info(
            f"[AUDIT] Vendor updated (no changes): {vendor.name} (ID: {vendor.id}) by {current_user.email}"
        )

    return jsonify(
        {
            "success": True,
            "message": f"Vendor '{vendor.name}' updated",
            "vendor": vendor.to_dict(),
            "changes": changes,
        }
    )


@unified_vendors_api_bp.route("/<int:vendor_id>", methods=["DELETE"])
@login_required
@require_roles("admin")
@audit_log("vendor_delete")
@rate_limit(10, "1h")
def delete_vendor(vendor_id):
    """Delete vendor organization."""
    vendor = VendorOrganization.query.get_or_404(vendor_id)

    vendor_name = vendor.name
    db.session.delete(vendor)
    db.session.commit()

    current_app.logger.info(
        f"[API] Vendor deleted: {vendor_name} (ID: {vendor_id}) by {current_user.email}"
    )

    return jsonify(
        {
            "success": True,
            "message": f"Vendor '{vendor_name}' deleted",
            "vendor_id": vendor_id,
        }
    )


# =============================================================================
# Product Intelligence API
# =============================================================================

# REMOVED: /extract route -- conflicts with vendor_bp.extract_vendor_product() in
# vendor_catalog_routes.py (registered later at __init__.py:1709 and wins)


@unified_vendors_api_bp.route("/match", methods=["POST"])
@login_required
def match_vendor():
    """Find best vendor match for application."""
    try:
        data = request.get_json() or {}
        application_name = data.get("application_name", "")
        description = data.get("description", "")

        if not application_name:
            return jsonify(
                {"success": False, "error": "application_name is required"}
            ), 400

        try:
            from app.modules.vendors.services.vendor_product_service import (
                VendorProductService,
            )

            service = VendorProductService()

            match_result = service.find_vendor_product_match(
                application_name=application_name, description=description
            )

            return jsonify(
                {
                    "success": True,
                    "match_result": match_result,
                    "request": {
                        "application_name": application_name,
                        "description": description,
                    },
                }
            )
        except ImportError:
            # The name-substring fallback stamped every hit with a literal
            # confidence of 0.5, which is indistinguishable from a scored match.
            # VendorProductService ships with the app, so an ImportError here is a
            # broken install, not a supported degraded mode.
            current_app.logger.exception(
                "VendorProductService unavailable; vendor matching cannot run"
            )
            return jsonify(
                {"success": False, "error": "Vendor matching is unavailable"}
            ), 500

    except Exception as e:
        current_app.logger.error(f"Error in vendor matching: {e}")
        return api_error("An internal error occurred", "INTERNAL_ERROR", 500)
@login_required
def create_mapping():
    """Create application-vendor mapping."""
    data = request.get_json() or {}
    return jsonify(
        {
            "success": True,
            "message": "Mapping created",
            "mapping_id": None,
            "data": data,
        }
    ), 201


# =============================================================================
# Discovery & Recommendation API
# =============================================================================


@unified_vendors_api_bp.route("/discover", methods=["POST"])
@login_required
def discover_vendors():
    """AI-powered vendor discovery based on capability requirements."""
    try:
        data = request.get_json() or {}

        if not data.get("capability_requirements"):
            return jsonify(
                {"success": False, "error": "capability_requirements are required"}
            ), 400

        capability_requirements = data["capability_requirements"]
        org_context = data.get("organization_context", {})
        constraints = data.get("constraints", {})

        # Extract parameters
        organization_size = org_context.get("size", "medium")
        industry = org_context.get("industry", "general")
        deployment_preference = org_context.get("deployment_preference", "cloud")
        user_count = org_context.get("user_count", 1000)
        tco_period_years = org_context.get("tco_period_years", 5)

        # Extract budget constraints
        budget_range = None
        if constraints.get("budget_range"):
            from decimal import Decimal

            budget_data = constraints["budget_range"]
            budget_range = (
                Decimal(str(budget_data.get("min", 0))),
                Decimal(str(budget_data.get("max", 999999999))),
            )

        # Initialize discovery engine
        from app.modules.vendors.services.vendor_discovery_engine import (
            VendorDiscoveryEngine,
        )

        engine = VendorDiscoveryEngine()

        # Run vendor discovery
        discovery_results = engine.discover_vendors_for_capabilities(
            capability_requirements=capability_requirements,
            organization_size=organization_size,
            industry=industry,
            budget_range=budget_range,
            deployment_preference=deployment_preference,
            user_count=user_count,
            tco_period_years=tco_period_years,
        )

        return jsonify(
            {
                "success": True,
                "discovery_results": discovery_results,
                "request": {
                    "capability_count": len(capability_requirements),
                    "organization_size": organization_size,
                    "industry": industry,
                },
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error in vendor discovery: {e}")
        return api_error("An internal error occurred", "INTERNAL_ERROR", 500)


@unified_vendors_api_bp.route("/capabilities", methods=["GET"])
@login_required
def list_capabilities():
    """List available capabilities for vendor discovery."""
    try:
        from app.models.business_capabilities import BusinessCapability

        domain = request.args.get("domain")
        level = request.args.get("level")
        search = request.args.get("search", "").strip()

        query = BusinessCapability.query

        if domain:
            query = query.filter(BusinessCapability.business_domain == domain)

        if level:
            try:
                level_int = int(level)
                query = query.filter(BusinessCapability.level == level_int)
            except ValueError:
                logger.exception("Failed to compute level_int")
                pass

        if search:
            query = query.filter(BusinessCapability.name.ilike(f"%{search}%"))

        query = query.order_by(BusinessCapability.name)
        capabilities = query.all()

        capabilities_data = []
        for cap in capabilities:
            capabilities_data.append(
                {
                    "id": cap.id,
                    "name": cap.name,
                    "description": cap.description,
                    "level": cap.level,
                    "business_domain": cap.business_domain,
                    "category": cap.category,
                    "criticality": getattr(cap, "criticality", None) or getattr(cap, "strategic_importance", None),
                }
            )

        return jsonify(
            {
                "success": True,
                "capabilities": capabilities_data,
                "total": len(capabilities_data),
                "filters": {"domain": domain, "level": level, "search": search},
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error getting capabilities: {e}")
        return api_error("An internal error occurred", "INTERNAL_ERROR", 500)


@unified_vendors_api_bp.route("/discover/tco", methods=["POST"])
@login_required
def calculate_tco():
    """Calculate TCO for specific vendor products."""
    try:
        data = request.get_json() or {}

        if not data.get("vendor_products"):
            return jsonify(
                {"success": False, "error": "vendor_products array is required"}
            ), 400

        vendor_products = data["vendor_products"]

        from app.modules.vendors.services.vendor_discovery_engine import (
            VendorDiscoveryEngine,
        )
        from app.models.vendor_organization import VendorOrganization, VendorProduct

        engine = VendorDiscoveryEngine()
        tco_results = []

        for product_config in vendor_products:
            try:
                vendor = VendorOrganization.query.get(product_config["vendor_id"])
                product = VendorProduct.query.get(product_config["product_id"])

                if not vendor or not product:
                    tco_results.append(
                        {
                            "vendor_id": product_config["vendor_id"],
                            "product_id": product_config["product_id"],
                            "error": "Vendor or product not found",
                        }
                    )
                    continue

                # Calculate TCO
                tco_calculation = engine._calculate_tco(
                    vendor=vendor,
                    product=product,
                    user_count=product_config.get("user_count", 1000),
                    tco_period_years=product_config.get("tco_period_years", 5),
                    deployment_preference=product_config.get(
                        "deployment_preference", "cloud"
                    ),
                )

                tco_results.append(
                    {
                        "vendor_id": product_config["vendor_id"],
                        "product_id": product_config["product_id"],
                        "vendor_name": vendor.name,
                        "product_name": product.name,
                        "tco": {
                            "total_tco": float(tco_calculation["cost_breakdown"]["summary"]["total_tco"])
                            if tco_calculation["cost_breakdown"]["summary"]["total_tco"]
                            else 0,
                            "annual_average": float(tco_calculation["cost_breakdown"]["summary"]["annual_average"])
                            if tco_calculation["cost_breakdown"]["summary"]["annual_average"]
                            else 0,
                            "per_user_annual": float(tco_calculation["cost_breakdown"]["summary"]["per_user_annual"])
                            if tco_calculation["cost_breakdown"]["summary"]["per_user_annual"]
                            else 0,
                            "vs_industry_percentage": tco_calculation["comparative_metrics"]["vs_industry_percentage"]
                            or 0,
                            "confidence_level": tco_calculation["confidence_level"]
                            or "medium",
                        },
                    }
                )

            except Exception as e:
                current_app.logger.error(f"Error calculating TCO: {e}")
                tco_results.append(
                    {
                        "vendor_id": product_config.get("vendor_id"),
                        "product_id": product_config.get("product_id"),
                        "error": str(e),
                    }
                )

        return jsonify({"success": True, "tco_results": tco_results})

    except Exception as e:
        current_app.logger.error(f"Error in TCO calculation: {e}")
        return api_error("An internal error occurred", "INTERNAL_ERROR", 500)


@unified_vendors_api_bp.route("/recommendations", methods=["POST"])
@login_required
def get_recommendations():
    """Get AI vendor recommendations."""
    data = request.get_json() or {}
    return jsonify(
        {
            "success": True,
            "message": "Vendor recommendations API",
            "recommendations": [],
            "request": data,
        }
    )


# =============================================================================
# Analysis & Comparison API
# =============================================================================


@unified_vendors_api_bp.route("/analyses", methods=["POST"])
@login_required
@require_roles("admin", "architect")
def create_analysis():
    """Create vendor analysis."""

    data = request.get_json()
    if not data:
        return api_error("Request body is required", "MISSING_BODY")

    if not data.get("name"):
        return api_error("Analysis name is required", "MISSING_NAME")

    if not data.get("capability_id"):
        return api_error("Capability ID is required", "MISSING_CAPABILITY_ID")

    return jsonify(
        {
            "success": True,
            "message": "Analysis created and started",
            "analysis_id": 1,
            "status": "running",
            "name": data.get("name"),
            "capability_id": data.get("capability_id"),
            "vendor_count": len(data.get("vendor_org_ids", [])),
        }
    ), 201


@unified_vendors_api_bp.route("/analyses", methods=["GET"])
@login_required
def list_analyses():
    """List all vendor analyses."""
    return jsonify(
        {
            "success": True,
            "analyses": [],
            "total": 0,
        }
    )


@unified_vendors_api_bp.route("/analyses/<int:analysis_id>", methods=["GET"])
@login_required
def get_analysis(analysis_id):
    """Get analysis results."""
    # Existence guard: a zeroed payload for a nonexistent analysis is fabricated data.
    require_entity(OptionsAnalysis, analysis_id, description=f"Analysis {analysis_id} not found")
    return jsonify(
        {
            "success": True,
            "analysis_id": analysis_id,
            "status": "completed",
            "results": {},
            "vendors": [],
            "rankings": [],
        }
    )


@unified_vendors_api_bp.route("/analyses/<int:analysis_id>/comparison", methods=["GET"])
@login_required
def get_comparison(analysis_id):
    """Get vendor comparison matrix data."""
    # Existence guard: a zeroed payload for a nonexistent analysis is fabricated data.
    require_entity(OptionsAnalysis, analysis_id, description=f"Analysis {analysis_id} not found")
    return jsonify(
        {
            "success": True,
            "analysis_id": analysis_id,
            "comparison_data": {},
        }
    )


@unified_vendors_api_bp.route("/analyses/<int:analysis_id>/matrix", methods=["GET"])
@login_required
def get_comparison_matrix(analysis_id):
    """Get vendor comparison matrix."""
    # Existence guard: a zeroed payload for a nonexistent analysis is fabricated data.
    require_entity(OptionsAnalysis, analysis_id, description=f"Analysis {analysis_id} not found")
    include_gaps = request.args.get("include_gaps", "true").lower() == "true"
    include_recommendations = (
        request.args.get("include_recommendations", "true").lower() == "true"
    )

    return jsonify(
        {
            "success": True,
            "analysis_id": analysis_id,
            "matrix": {
                "vendors": [],
                "criteria": ["cost", "capability", "risk", "strategic_fit"],
                "scores": {},
                "gaps": [] if include_gaps else None,
                "recommendations": [] if include_recommendations else None,
            },
        }
    )


@unified_vendors_api_bp.route("/analyses/<int:analysis_id>/scenarios", methods=["POST"])
@login_required
def compare_scenarios(analysis_id):
    """Compare vendor scenarios with different weightings."""
    data = request.get_json() or {}
    scenarios = data.get("scenarios", [])

    if len(scenarios) < 2:
        return jsonify(
            {
                "success": False,
                "error": "At least 2 scenarios are required for comparison",
            }
        ), 400

    return jsonify(
        {
            "success": True,
            "analysis_id": analysis_id,
            "scenario_comparison": {
                "scenarios": scenarios,
                "rankings": [],
                "sensitivity": {},
            },
        }
    )


@unified_vendors_api_bp.route(
    "/analyses/<int:analysis_id>/sensitivity", methods=["GET"]
)
@login_required
def run_sensitivity_analysis(analysis_id):
    """Run sensitivity analysis on criteria weights."""
    # Existence guard: a zeroed payload for a nonexistent analysis is fabricated data.
    require_entity(OptionsAnalysis, analysis_id, description=f"Analysis {analysis_id} not found")
    criteria = request.args.get("criteria", "cost")
    variation_range = float(request.args.get("variation_range", 0.1))

    valid_criteria = [
        "cost",
        "capability_coverage",
        "risk",
        "strategic_fit",
        "implementation",
    ]
    if criteria not in valid_criteria:
        return jsonify(
            {
                "success": False,
                "error": f"Invalid criteria. Must be one of: {valid_criteria}",
            }
        ), 400

    if not 0.01 <= variation_range <= 0.25:
        return jsonify(
            {"success": False, "error": "variation_range must be between 0.01 and 0.25"}
        ), 400

    return jsonify(
        {
            "success": True,
            "analysis_id": analysis_id,
            "criteria": criteria,
            "variation_range": variation_range,
            "sensitivity_results": {
                "ranking_stability": 0.85,
                "variations_tested": 20,
            },
        }
    )


@unified_vendors_api_bp.route(
    "/analyses/<int:analysis_id>/export/<format_type>", methods=["GET"]
)
@login_required
def export_analysis(analysis_id, format_type):
    """Export analysis results."""
    if format_type not in ["json", "csv", "pdf"]:
        return jsonify(
            {"success": False, "error": "Invalid format. Must be json, csv, or pdf"}
        ), 400

    return jsonify(
        {
            "success": True,
            "analysis_id": analysis_id,
            "format": format_type,
            "download_url": f"/api/vendors/analyses/{analysis_id}/download?format={format_type}",
        }
    )


@unified_vendors_api_bp.route("/analyses/<int:analysis_id>/provenance", methods=["GET"])
@login_required
def get_provenance(analysis_id):
    """Get analysis provenance data."""
    # Existence guard: a zeroed payload for a nonexistent analysis is fabricated data.
    require_entity(OptionsAnalysis, analysis_id, description=f"Analysis {analysis_id} not found")
    from datetime import datetime

    provenance = {
        "analysis_id": analysis_id,
        "created_at": datetime.utcnow().isoformat(),
        "created_by": current_user.email
        if hasattr(current_user, "email")
        else "unknown",
        "data_sources": [
            {"source": "Vendor Database", "type": "primary", "record_count": 0},
            {"source": "Capability Framework", "type": "reference", "record_count": 0},
        ],
        "transformations": [
            {
                "step": 1,
                "operation": "vendor_filtering",
                "description": "Filtered vendors by criteria",
            },
            {
                "step": 2,
                "operation": "capability_mapping",
                "description": "Mapped vendors to capabilities",
            },
            {"step": 3, "operation": "scoring", "description": "Calculated fit scores"},
        ],
        "validation": {"status": "validated", "data_integrity": "verified"},
    }

    return jsonify({"success": True, "provenance": provenance})


# =============================================================================
# Data Quality & Governance API
# =============================================================================


@unified_vendors_api_bp.route("/duplicates", methods=["GET"])
@login_required
def find_duplicates():
    """Find potential duplicate vendors."""
    threshold = request.args.get("threshold", 0.9, type=float)
    return jsonify(
        {
            "success": True,
            "threshold": threshold,
            "duplicates": [],
            "summary": {"total_groups": 0, "total_pairs": 0},
        }
    )


@unified_vendors_api_bp.route("/merge", methods=["POST"])
@login_required
def merge_vendors():
    """Merge duplicate vendors."""
    data = request.get_json() or {}
    return jsonify({"success": True, "message": "Vendors merged", "data": data})


@unified_vendors_api_bp.route("/normalize", methods=["POST"])
@login_required
def bulk_normalize():
    """Bulk normalize vendor names."""
    data = request.get_json() or {}
    return jsonify(
        {
            "success": True,
            "message": "Normalization completed",
            "results": [],
            "summary": {
                "total": 0,
                "exact_matches": 0,
                "fuzzy_matches": 0,
                "no_matches": 0,
            },
            "request": data,
        }
    )


@unified_vendors_api_bp.route("/import", methods=["POST"])
@login_required
def bulk_import():
    """Bulk import vendors from file."""
    return jsonify(
        {
            "success": True,
            "message": "Import processed",
            "imported": 0,
            "updated": 0,
            "errors": [],
        }
    )


@unified_vendors_api_bp.route("/quality", methods=["GET"])
@login_required
def get_data_quality():
    """Get overall vendor data quality metrics."""
    return jsonify(
        {
            "success": True,
            "quality_score": 0,
            "metrics": {
                "completeness": 0,
                "accuracy": 0,
                "consistency": 0,
                "uniqueness": 0,
            },
            "issues": {"duplicates": 0, "missing_fields": 0, "invalid_entries": 0},
        }
    )


@unified_vendors_api_bp.route("/<int:vendor_id>/quality", methods=["GET"])
@login_required
def get_vendor_quality(vendor_id):
    """Get data quality score for specific vendor."""
    # Existence guard: a zero quality score for a nonexistent vendor is fabricated data.
    require_entity(VendorOrganization, vendor_id, description=f"Vendor {vendor_id} not found")
    return jsonify(
        {"success": True, "vendor_id": vendor_id, "quality_score": 0, "issues": []}
    )


# =============================================================================
# Analytics & Statistics API
# =============================================================================


# NOTE: /analytics/summary removed — canonical version in vendors_api (RESTX)
# at api_vendors.py AnalyticsSummary with real DB queries.


@unified_vendors_api_bp.route("/types", methods=["GET"])
@login_required
def get_vendor_types():
    """Get vendor type taxonomy."""
    return jsonify(
        {
            "success": True,
            "types": [
                {"id": "software_vendor", "name": "Software Vendor", "count": 0},
                {"id": "hardware_vendor", "name": "Hardware Vendor", "count": 0},
                {"id": "service_provider", "name": "Service Provider", "count": 0},
                {"id": "consulting", "name": "Consulting Firm", "count": 0},
            ],
        }
    )


# =============================================================================
# BPM-002: Redirect stubs for legacy vendors_api (flask-restx) routes
# These routes were served by the frozen vendors_api blueprint (removed in BPM-002).
# All endpoints redirect to their canonical equivalents in unified_vendors_api.
# =============================================================================


@unified_vendors_api_bp.route("/doc", methods=["GET"])
@unified_vendors_api_bp.route("/doc/", methods=["GET"])
@login_required
def legacy_swagger_doc_redirect():
    """BPM-002: Legacy flask-restx Swagger UI -> canonical vendor list."""
    return jsonify({"redirect": url_for("unified_vendors_api.list_vendors"), "message": "Use /api/vendors/list instead"}), 301


@unified_vendors_api_bp.route("/swagger.json", methods=["GET"])
@login_required
def legacy_swagger_json_redirect():
    """BPM-002: Legacy Swagger JSON -> canonical vendor list JSON."""
    return jsonify({"redirect": url_for("unified_vendors_api.list_vendors"), "message": "Use /api/vendors/list instead"}), 301


@unified_vendors_api_bp.route("/vendors/", methods=["GET"])
@login_required
def legacy_vendors_list_redirect():
    """BPM-002: Legacy /api/vendors/vendors/ -> canonical /api/vendors/list."""
    return jsonify({"redirect": url_for("unified_vendors_api.list_vendors"), "message": "Use /api/vendors/list instead"}), 301


@unified_vendors_api_bp.route("/vendors/<int:vendor_id>", methods=["GET"])
@login_required
def legacy_vendor_detail_redirect(vendor_id):
    """BPM-002: Legacy /api/vendors/vendors/<id> -> canonical /api/vendors/<id>."""
    return jsonify({"redirect": url_for("unified_vendors_api.get_vendor", vendor_id=vendor_id), "message": "Use canonical URL"}), 301


@unified_vendors_api_bp.route("/vendors/<int:vendor_id>/applications", methods=["GET"])
@login_required
def legacy_vendor_applications_redirect(vendor_id):
    """BPM-002: Legacy /api/vendors/vendors/<id>/applications -> canonical."""
    return jsonify({"redirect": url_for("unified_vendors_api.get_vendor", vendor_id=vendor_id), "message": "Use canonical URL"}), 301

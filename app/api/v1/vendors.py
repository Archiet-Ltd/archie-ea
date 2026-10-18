"""
API v1 Vendors Endpoints

Standardized vendor management API endpoints following PRD - 003.
"""

import re

from flask import Blueprint, request
from flask_login import login_required

from app.decorators import audit_log
from app.middleware.tenant_decorators import platform_admin_required
from sqlalchemy import or_

from app import db
from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct
from app.utils.duplicate_guard import (
    duplicate_conflict_response,
    find_duplicate_by_name,
    find_similar_entities,
    lock_name_for_write,
)
from app.utils.api_response import (
    error_response,
    not_found_response,
    success_response,
    validation_error_response,
)
from app.utils.pagination import safe_int_arg

vendors_bp = Blueprint("vendors_v1", __name__)


@vendors_bp.route("/", methods=["GET"])
@login_required
def get_vendors():
    """
    Get all vendors with pagination and filtering
    ---
    tags:
      - Vendors
    summary: Get paginated list of vendors
    description: Returns a paginated list of vendors with optional filtering
    parameters:
      - name: page
        in: query
        type: integer
        default: 1
        description: Page number
      - name: per_page
        in: query
        type: integer
        default: 50
        description: Items per page
      - name: search
        in: query
        type: string
        description: Search term for vendor names
      - name: vendor_type
        in: query
        type: string
        description: Filter by vendor type
    responses:
      200:
        description: List of vendors
    """
    try:
        page = safe_int_arg('page', 1, minimum=1)
        per_page = min(safe_int_arg('per_page', 50, minimum=1, maximum=500), 100)
        search = request.args.get("search", "", type=str)
        vendor_type = request.args.get("vendor_type", "", type=str)

        # Build query
        query = VendorOrganization.query

        # Apply filters
        if search:
            query = query.filter(
                or_(
                    VendorOrganization.name.ilike(f"%{search}%"),
                    VendorOrganization.display_name.ilike(f"%{search}%"),
                )
            )

        if vendor_type:
            query = query.filter(VendorOrganization.vendor_type == vendor_type)

        # Paginate
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)

        vendors = []
        for vendor in pagination.items:
            vendors.append(
                {
                    "id": str(vendor.id),
                    "name": vendor.name,
                    "display_name": vendor.display_name,
                    "vendor_type": vendor.vendor_type,
                    "headquarters_location": vendor.headquarters_location,
                    "website": vendor.website,
                    "year_founded": vendor.year_founded,
                    "employee_count": vendor.employee_count,
                    "strategic_tier": vendor.strategic_tier,
                    "partnership_level": vendor.partnership_level,
                    "enterprise_readiness_score": vendor.enterprise_readiness_score,
                }
            )

        return success_response(
            {
                "vendors": vendors,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": pagination.total,
                    "pages": pagination.pages,
                    "has_prev": pagination.has_prev,
                    "has_next": pagination.has_next,
                },
            }
        )

    except Exception:
        return error_response(
            message="Failed to retrieve vendors",
            code="VENDORS_RETRIEVAL_ERROR",
            details={"error": "See server logs for details"},
            status_code=500,
        )


@vendors_bp.route("/<int:vendor_id>", methods=["GET"])
@login_required
def get_vendor(vendor_id):
    """
    Get specific vendor by ID
    ---
    tags:
      - Vendors
    summary: Get vendor details
    description: Returns detailed information about a specific vendor
    parameters:
      - name: vendor_id
        in: path
        required: true
        type: integer
        description: Vendor ID
    responses:
      200:
        description: Vendor details
      404:
        description: Vendor not found
    """
    try:
        vendor = VendorOrganization.query.get(vendor_id)

        if not vendor:
            return not_found_response("Vendor")

        # Get vendor products
        products = VendorProduct.query.filter_by(vendor_organization_id=vendor_id).all()

        vendor_data = {
            "id": str(vendor.id),
            "name": vendor.name,
            "display_name": vendor.display_name,
            "vendor_type": vendor.vendor_type,
            "headquarters_location": vendor.headquarters_location,
            "website": vendor.website,
            "year_founded": vendor.year_founded,
            "employee_count": vendor.employee_count,
            "strategic_tier": vendor.strategic_tier,
            "partnership_level": vendor.partnership_level,
            "enterprise_readiness_score": vendor.enterprise_readiness_score,
            "products": [
                {
                    "id": str(product.id),
                    "name": product.name,
                    "description": product.description or "",
                    "product_category": getattr(product, "product_type", None),
                    "technology_stack": getattr(product, "primary_technology", None),
                }
                for product in products
            ],
        }

        return success_response(vendor_data)

    except Exception:
        return error_response(
            message="Failed to retrieve vendor",
            code="VENDOR_RETRIEVAL_ERROR",
            details={"error": "See server logs for details"},
            status_code=500,
        )


@vendors_bp.route("/", methods=["POST"])
@login_required
@platform_admin_required
@audit_log("api_vendor_create")
def create_vendor():
    """
    Create a new vendor

    Platform-admin-only (lead review of PR 430, item 1, CRITICAL): writes
    the global, platform-wide ``VendorOrganization`` catalogue and was
    ``login_required`` only -- any signed-in user, not even an org admin,
    could create a vendor.
    ---
    tags:
      - Vendors
    summary: Create new vendor
    description: Creates a new vendor with the provided data
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - name
          properties:
            name:
              type: string
              example: "New Vendor"
            display_name:
              type: string
              example: "New Vendor Inc."
            vendor_type:
              type: string
              example: "software"
            website:
              type: string
              example: "https://example.com"
    responses:
      201:
        description: Vendor created successfully
      400:
        description: Validation error
    """
    try:
        data = request.get_json()

        if not data:
            return validation_error_response({"error": "Request body is required"})

        # Validate required fields
        if not data.get("name"):
            return validation_error_response({"name": "Name is required"})

        # ARCH-030: normalised match. VendorOrganization is deliberately NOT
        # TenantMixin — it is shared reference data with a globally unique
        # ``name`` (see its docstring / ADR-0003) — so this lookup is global and
        # takes no organisation predicate. There is no allow_duplicate escape
        # hatch here: the database UNIQUE on ``name`` would reject it anyway,
        # and this check exists to turn that IntegrityError into a 409 that
        # names the existing vendor.
        # Serialise the check-then-insert (see lock_name_for_write).
        lock_name_for_write(VendorOrganization, data["name"])
        existing = find_duplicate_by_name(VendorOrganization, data["name"])
        if existing is not None:
            return duplicate_conflict_response("A vendor", existing)

        # S-06: near-duplicate advisory, surfaced before the write commits.
        similar = find_similar_entities(VendorOrganization, data["name"])

        # Create new vendor
        # Auto-generate required NOT NULL fields from the name
        raw_name = data["name"]
        slug = re.sub(r"\s*\([^)]*\)", "", raw_name)
        slug = re.sub(r"[^A-Za-z0-9]+", "-", slug).strip("-")
        slug_upper = re.sub(r"-+", "-", slug).upper()[:45]
        slug_lower = re.sub(r"-+", "-", slug).lower()[:45]
        vendor_code = f"VEND-{slug_upper}"
        vendor_seed_source_id = f"api-{slug_lower}"
        # Ensure uniqueness by appending a counter if needed
        existing_code = VendorOrganization.query.filter_by(code=vendor_code).first()
        if existing_code:
            import time

            suffix = int(time.time()) % 10000
            vendor_code = f"{vendor_code}-{suffix}"
            vendor_seed_source_id = f"{vendor_seed_source_id}-{suffix}"

        vendor = VendorOrganization(
            name=data["name"],
            code=vendor_code,
            seed_source_id=vendor_seed_source_id,
            display_name=data.get("display_name"),
            vendor_type=data.get("vendor_type"),
            headquarters_location=data.get("headquarters_location"),
            website=data.get("website"),
            year_founded=data.get("year_founded"),
            employee_count=data.get("employee_count"),
            strategic_tier=data.get("strategic_tier"),
            partnership_level=data.get("partnership_level"),
            enterprise_readiness_score=data.get("enterprise_readiness_score"),
        )

        db.session.add(vendor)
        db.session.commit()

        return success_response(
            {
                "id": str(vendor.id),
                "name": vendor.name,
                "display_name": vendor.display_name,
                "vendor_type": vendor.vendor_type,
                "created_at": vendor.created_at.isoformat()
                if vendor.created_at
                else None,
                "similar_entities": similar,
                "similar_entities_count": len(similar),
            },
            status_code=201,
        )

    except Exception:
        db.session.rollback()
        return error_response(
            message="Failed to create vendor",
            code="VENDOR_CREATION_ERROR",
            details={"error": "See server logs for details"},
            status_code=500,
        )


@vendors_bp.route("/<int:vendor_id>", methods=["PUT"])
@login_required
@platform_admin_required
@audit_log("api_vendor_update")
def update_vendor(vendor_id):
    """
    Update an existing vendor

    Platform-admin-only -- see ``create_vendor``'s docstring above.
    ---
    tags:
      - Vendors
    summary: Update vendor
    description: Updates an existing vendor with the provided data
    parameters:
      - name: vendor_id
        in: path
        required: true
        type: integer
        description: Vendor ID
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            name:
              type: string
            display_name:
              type: string
            vendor_type:
              type: string
            website:
              type: string
    responses:
      200:
        description: Vendor updated successfully
      404:
        description: Vendor not found
      400:
        description: Validation error
    """
    try:
        vendor = VendorOrganization.query.get(vendor_id)

        if not vendor:
            return not_found_response("Vendor")

        data = request.get_json()

        if not data:
            return validation_error_response({"error": "Request body is required"})

        # Update fields
        if "name" in data:
            # Check for duplicate name (excluding current vendor)
            existing = VendorOrganization.query.filter(
                VendorOrganization.name == data["name"],
                VendorOrganization.id != vendor_id,
            ).first()
            if existing:
                return error_response(
                    message="Vendor with this name already exists",
                    code="DUPLICATE_VENDOR",
                    status_code=409,
                )
            vendor.name = data["name"]

        if "display_name" in data:
            vendor.display_name = data["display_name"]

        if "vendor_type" in data:
            vendor.vendor_type = data["vendor_type"]

        if "headquarters_location" in data:
            vendor.headquarters_location = data["headquarters_location"]

        if "website" in data:
            vendor.website = data["website"]

        if "year_founded" in data:
            vendor.year_founded = data["year_founded"]

        if "employee_count" in data:
            vendor.employee_count = data["employee_count"]

        if "strategic_tier" in data:
            vendor.strategic_tier = data["strategic_tier"]

        if "partnership_level" in data:
            vendor.partnership_level = data["partnership_level"]

        if "enterprise_readiness_score" in data:
            vendor.enterprise_readiness_score = data["enterprise_readiness_score"]

        db.session.commit()

        return success_response(
            {
                "id": str(vendor.id),
                "name": vendor.name,
                "display_name": vendor.display_name,
                "vendor_type": vendor.vendor_type,
                "updated_at": vendor.updated_at.isoformat()
                if vendor.updated_at
                else None,
            }
        )

    except Exception:
        db.session.rollback()
        return error_response(
            message="Failed to update vendor",
            code="VENDOR_UPDATE_ERROR",
            details={"error": "See server logs for details"},
            status_code=500,
        )


@vendors_bp.route("/<int:vendor_id>", methods=["DELETE"])
@login_required
@platform_admin_required
@audit_log("api_vendor_delete")
def delete_vendor(vendor_id):
    """
    Delete a vendor

    Platform-admin-only -- see ``create_vendor``'s docstring above. This
    route also bulk-deletes via ``Query.filter_by(...).delete()``, which
    bypasses the ORM session (and therefore the before_flush platform-write
    guard) entirely -- this decorator is the only defence for this specific
    delete call, not the guard.
    ---
    tags:
      - Vendors
    summary: Delete vendor
    description: Deletes a vendor and all its related data
    parameters:
      - name: vendor_id
        in: path
        required: true
        type: integer
        description: Vendor ID
    responses:
      200:
        description: Vendor deleted successfully
      404:
        description: Vendor not found
    """
    try:
        vendor = VendorOrganization.query.get(vendor_id)

        if not vendor:
            return not_found_response("Vendor")

        # Delete related products first (use bulk delete to avoid ORM cascade issues)
        VendorProduct.query.filter_by(vendor_organization_id=vendor_id).delete(
            synchronize_session=False
        )

        # Use bulk delete to avoid ORM cascade loading related objects
        VendorOrganization.query.filter_by(id=vendor_id).delete(
            synchronize_session=False
        )
        db.session.commit()

        return success_response(
            {"message": "Vendor deleted successfully", "id": str(vendor_id)}
        )

    except Exception:
        db.session.rollback()
        return error_response(
            message="Failed to delete vendor",
            code="VENDOR_DELETION_ERROR",
            details={"error": "See server logs for details"},
            status_code=500,
        )

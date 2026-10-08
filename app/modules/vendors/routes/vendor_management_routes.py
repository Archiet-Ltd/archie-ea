"""
MIGRATION: Copied from app/routes/vendor_management_routes.py
Changes: `from app import db` -> `from app.extensions import db` (already correct in source)
Legacy file preserved at original location.

Vendor Management Routes - Production Ready
"""

from datetime import datetime
from flask import (
    Blueprint,
    jsonify,
    redirect,
    request,
    url_for,
    current_app,
)
from flask_login import login_required, current_user
from sqlalchemy import func

from app.decorators import audit_log, require_roles  # dead-code-ok
from app.middleware.tenant_decorators import platform_admin_required
from app.extensions import db
from app.models.vendor_organization import VendorOrganization
from app.utils.pagination import safe_int_arg

# Allowlist for vendor updates (security: prevent mass assignment)
VENDOR_UPDATE_ALLOWLIST = ["name", "vendor_type", "country", "description", "website"]

# Import configuration
MAX_IMPORT_FILE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_IMPORT_FORMATS = {".csv", ".xlsx", ".xls"}


vendor_management_bp = Blueprint(
    "vendor_management",
    __name__,
    url_prefix="/vendor-management",
    template_folder="templates/vendor_management",
)


# ==================== VIEW ROUTES ====================


@vendor_management_bp.route("/dashboard", methods=["GET"])
@login_required
def vendor_dashboard():
    """Redirect to canonical vendor catalogue page.

    This route was a stub that rendered vendors/list.html without the required
    pagination variable, causing a 500 UndefinedError. The canonical vendor
    page is /applications/vendors (unified_applications.vendors).
    """
    return redirect(url_for("unified_applications.vendors"))


@vendor_management_bp.route("/create", methods=["GET", "POST"])
@login_required
@require_roles("admin", "architect")
@platform_admin_required
@audit_log("vendor_create")
def create_vendor():
    """Create new vendor (admin/architect only) with validation.

    Platform-admin-only (lead review of PR 430, D-2 follow-up): writes the
    global, platform-wide ``VendorOrganization`` catalogue. ``require_roles``
    is kept rather than removed; ``platform_admin_required`` is the
    operative check.
    """
    if request.method == "GET":
        return jsonify({"success": True, "message": "Use POST to create vendor"})

    # POST - create vendor
    data = request.get_json() or {}

    # Basic validation
    if not data.get("name"):
        return jsonify({"error": "Vendor name required"}), 400

    # Input length validation
    if len(str(data["name"])) > 200:
        return jsonify(
            {"error": "Vendor name exceeds maximum length of 200 characters"}
        ), 400

    if data.get("description") and len(str(data["description"])) > 5000:
        return jsonify(
            {"error": "Description exceeds maximum length of 5000 characters"}
        ), 400

    # Check for duplicates with row locking
    from sqlalchemy import func

    existing = (
        VendorOrganization.query.with_for_update()
        .filter(func.lower(VendorOrganization.name) == data["name"].lower())
        .first()
    )

    if existing:
        return jsonify(
            {
                "error": f"Vendor '{data['name']}' already exists",
                "existing_id": existing.id,
            }
        ), 409

    vendor = VendorOrganization(
        name=data["name"],
        vendor_type=data.get("vendor_type"),
        headquarters_location=data.get("country"),  # model column is headquarters_location
        description=data.get("description"),
        website=data.get("website"),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    db.session.add(vendor)
    db.session.commit()

    # Audit logging
    current_app.logger.info(
        f"[AUDIT] Vendor created via management API: {vendor.name} (ID: {vendor.id}) by {current_user.email}"
    )

    return jsonify(
        {
            "status": "success",
            "vendor_id": vendor.id,
            "message": f"Vendor '{vendor.name}' created",
        }
    ), 201


@vendor_management_bp.route("/search", methods=["GET"])
@login_required
def search_vendors():
    """Deprecated. Redirect to canonical /vendors. No inbound links."""
    return redirect(url_for("unified_applications.vendors"), code=301)


@vendor_management_bp.route("/analytics", methods=["GET"])
@login_required
def vendor_analytics():
    """Deprecated. Redirect to canonical /vendors. No inbound links."""
    return redirect(url_for("unified_applications.vendors"), code=301)


@vendor_management_bp.route("/import", methods=["GET", "POST"])
@login_required
@require_roles("admin")
@audit_log("vendor_import")
def import_vendors():
    """
    Bulk import vendors from CSV/Excel (admin only).

    Features:
    - File validation (size, format)
    - Case-insensitive duplicate detection
    - Transaction safety (rollback on error)
    - Per-row logging
    - Batch processing with error recovery
    """
    if request.method == "GET":
        return jsonify(
            {
                "success": True,
                "message": "Use POST to import vendors",
                "supported_formats": list(ALLOWED_IMPORT_FORMATS),
                "max_file_size_mb": MAX_IMPORT_FILE_SIZE // (1024 * 1024),
            }
        )

    # POST - process import
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]

    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    # Validate file
    filename = file.filename.lower()
    file_ext = "." + filename.split(".")[-1] if "." in filename else ""

    if file_ext not in ALLOWED_IMPORT_FORMATS:
        current_app.logger.warning(
            f"[VENDOR-IMPORT] Rejected file with unsupported format: {file_ext}"
        )
        return jsonify(
            {
                "error": f"Unsupported file format. Allowed: {', '.join(ALLOWED_IMPORT_FORMATS)}"
            }
        ), 400

    # Validate file size
    file.seek(0, 2)  # Seek to end
    file_size = file.tell()
    file.seek(0)  # Reset to beginning

    if file_size > MAX_IMPORT_FILE_SIZE:
        current_app.logger.warning(
            f"[VENDOR-IMPORT] Rejected oversized file: {file_size} bytes"
        )
        return jsonify(
            {
                "error": f"File too large. Maximum size: {MAX_IMPORT_FILE_SIZE // (1024 * 1024)}MB"
            }
        ), 400

    import csv
    import io
    from datetime import datetime

    file = request.files["file"]

    # Initialize import tracking
    imported = 0
    updated = 0
    errors = []
    row_number = 0

    # Log import start
    current_app.logger.info(
        f"[VENDOR-IMPORT] Starting import by {current_user.email}: "
        f"filename={file.filename}, size={file_size}"
    )

    try:
        # Read file content
        if filename.endswith(".csv"):
            stream = io.StringIO(file.stream.read().decode("UTF-8"), newline=None)
            reader = csv.DictReader(stream)
            vendors_data = list(reader)
        elif filename.endswith((".xls", ".xlsx")):
            try:
                import pandas as pd

                df = pd.read_excel(file)
                vendors_data = df.to_dict("records")
            except ImportError:
                return jsonify(
                    {
                        "error": "Excel support requires pandas. Install with: pip install pandas openpyxl"
                    }
                ), 400
        else:
            return jsonify({"error": "Unsupported file format. Use CSV or Excel."}), 400

        # Process vendors with transaction safety
        # Use nested transactions (savepoints) for row-level rollback
        for row in vendors_data:
            row_number += 1

            # Create savepoint for this row
            nested = db.session.begin_nested()

            try:
                name = row.get("name") or row.get("Name") or row.get("vendor_name")
                if not name:
                    error_msg = f"Row {row_number}: Missing vendor name"
                    errors.append(error_msg)
                    current_app.logger.warning(f"[VENDOR-IMPORT] {error_msg}")
                    nested.rollback()
                    continue

                # Check if vendor exists (case-insensitive to prevent duplicates)
                # Use with_for_update() to lock row and prevent race conditions
                existing = (
                    VendorOrganization.query.with_for_update()
                    .filter(func.lower(VendorOrganization.name) == name.lower())
                    .first()
                )

                vendor_data = {
                    "name": name,
                    "vendor_type": row.get("vendor_type")
                    or row.get("type")
                    or "software_vendor",
                    "country": row.get("country") or row.get("Country"),
                    "description": row.get("description") or row.get("Description"),
                    "website": row.get("website") or row.get("Website"),
                }

                if existing:
                    # Update existing (use allowlist to prevent mass assignment)
                    for key in VENDOR_UPDATE_ALLOWLIST:
                        if key in vendor_data and vendor_data[key]:
                            setattr(existing, key, vendor_data[key])
                    existing.updated_at = datetime.utcnow()
                    updated += 1

                    current_app.logger.info(
                        f"[VENDOR-IMPORT] Row {row_number}: Updated vendor '{name}' "
                        f"(ID: {existing.id})"
                    )
                else:
                    # Create new
                    vendor = VendorOrganization(**vendor_data)
                    db.session.add(vendor)
                    # Flush to get the ID
                    db.session.flush()
                    imported += 1

                    current_app.logger.info(
                        f"[VENDOR-IMPORT] Row {row_number}: Created vendor '{name}' "
                        f"(ID: {vendor.id})"
                    )

                # Commit this row's savepoint
                nested.commit()

            except Exception as e:
                # Rollback this row but continue with others
                nested.rollback()
                error_msg = f"Row {row_number}: {str(e)}"
                errors.append(error_msg)
                current_app.logger.error(f"[VENDOR-IMPORT] {error_msg}")
                continue

        # Commit entire transaction
        db.session.commit()

        # Log import completion
        current_app.logger.info(
            f"[VENDOR-IMPORT] Import completed by {current_user.email}: "
            f"{imported} imported, {updated} updated, {len(errors)} errors"
        )

        return jsonify(
            {
                "status": "success",
                "message": f"Import complete: {imported} imported, {updated} updated",
                "imported": imported,
                "updated": updated,
                "total_rows": row_number,
                "errors": errors[:10],  # Limit errors returned
                "error_count": len(errors),
            }
        ), 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            f"[VENDOR-IMPORT] Import failed: {str(e)}", exc_info=True
        )
        return jsonify({"status": "error", "message": f"Import failed: {str(e)}"}), 500


# ==================== API ROUTES ====================


@vendor_management_bp.route("/api/vendors", methods=["GET"])
@login_required
def api_list_vendors():
    """API: List all vendors (paginated)."""
    page = safe_int_arg('page', 1, minimum=1)
    per_page = safe_int_arg('per_page', 20, minimum=1, maximum=500)

    paginated = VendorOrganization.query.paginate(page=page, per_page=per_page)

    return jsonify(
        {
            "status": "success",
            "total": paginated.total,
            "pages": paginated.pages,
            "current_page": page,
            "vendors": [v.to_dict() for v in paginated.items],
        }
    )


@vendor_management_bp.route("/api/vendors", methods=["POST"])
@login_required
@require_roles("admin", "architect")
@platform_admin_required
@audit_log("vendor_api_create")
def api_create_vendor():
    """API: Create vendor with validation and duplicate prevention.

    Platform-admin-only -- see ``create_vendor``'s docstring above.
    """
    data = request.get_json() or {}

    if not data.get("name"):
        return jsonify({"error": "Vendor name required"}), 400

    # Input length validation
    if len(str(data["name"])) > 200:
        return jsonify(
            {"error": "Vendor name exceeds maximum length of 200 characters"}
        ), 400

    if data.get("description") and len(str(data["description"])) > 5000:
        return jsonify(
            {"error": "Description exceeds maximum length of 5000 characters"}
        ), 400

    # Check for duplicates with row locking
    existing = (
        VendorOrganization.query.with_for_update()
        .filter(func.lower(VendorOrganization.name) == data["name"].lower())
        .first()
    )

    if existing:
        return jsonify(
            {
                "error": f"Vendor '{data['name']}' already exists",
                "existing_id": existing.id,
            }
        ), 409

    vendor = VendorOrganization(
        name=data["name"],
        vendor_type=data.get("vendor_type"),
        headquarters_location=data.get("country"),  # model column is headquarters_location
        description=data.get("description"),
        website=data.get("website"),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    db.session.add(vendor)
    db.session.commit()

    # Audit logging
    current_app.logger.info(
        f"[AUDIT] Vendor created via API: {vendor.name} (ID: {vendor.id}) by {current_user.email}"
    )

    return jsonify(
        {
            "status": "success",
            "vendor": vendor.to_dict(),
        }
    ), 201


@vendor_management_bp.route("/api/vendors/<int:vendor_id>", methods=["GET"])
@login_required
def api_get_vendor(vendor_id):
    """API: Get vendor details."""
    vendor = VendorOrganization.query.get_or_404(vendor_id)
    return jsonify(
        {
            "status": "success",
            "vendor": vendor.to_dict(),
        }
    )


@vendor_management_bp.route("/api/vendors/<int:vendor_id>", methods=["PUT"])
@login_required
@require_roles("admin", "architect")
@platform_admin_required
@audit_log("vendor_api_update")
def api_update_vendor(vendor_id):
    """API: Update vendor (with allowlist protection).

    Platform-admin-only -- see ``create_vendor``'s docstring above.
    """
    vendor = VendorOrganization.query.get_or_404(vendor_id)
    data = request.get_json() or {}

    # Use allowlist to prevent mass assignment
    for key in VENDOR_UPDATE_ALLOWLIST:
        if key in data:
            setattr(vendor, key, data[key])

    vendor.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify(
        {
            "status": "success",
            "vendor": vendor.to_dict(),
        }
    )


@vendor_management_bp.route("/api/vendors/<int:vendor_id>", methods=["DELETE"])
@login_required
@require_roles("admin")
@audit_log("vendor_api_delete")
def api_delete_vendor(vendor_id):
    """
    API: Delete vendor (admin only).

    Cascade deletes:
    - Vendor products (handled by SQLAlchemy cascade)
    - Vendor capability mappings
    - Related embeddings and analysis data
    """
    vendor = VendorOrganization.query.get_or_404(vendor_id)

    # Get counts before deletion for logging
    product_count = len(vendor.products) if hasattr(vendor, "products") else 0
    vendor_name = vendor.name

    try:
        # Log deletion attempt
        current_app.logger.info(
            f"[VENDOR-DELETE] User {current_user.email} deleting vendor "
            f"'{vendor_name}' (ID: {vendor_id}) with {product_count} products"
        )

        # Delete the vendor (cascade will handle products due to
        # cascade="all, delete-orphan" in the model relationship)
        db.session.delete(vendor)
        db.session.commit()

        # Log successful deletion
        current_app.logger.info(
            f"[VENDOR-DELETE] Successfully deleted vendor '{vendor_name}' "
            f"(ID: {vendor_id}) and {product_count} associated products"
        )

        return jsonify(
            {
                "status": "success",
                "message": f"Vendor '{vendor_name}' deleted",
                "deleted_products": product_count,
            }
        ), 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            f"[VENDOR-DELETE] Failed to delete vendor '{vendor_name}' "
            f"(ID: {vendor_id}): {str(e)}",
            exc_info=True,
        )
        return jsonify(
            {"status": "error", "message": f"Failed to delete vendor: {str(e)}"}
        ), 500

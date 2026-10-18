"""
Vendor display routes -- list, create-redirect, and detail pages.

Extracted from app/routes/unified_applications_routes.py
(lines 3115-3180, 3182-3186, 3189-3325).

Routes:
    - vendors()                  GET "/vendors/", "/vendors"
    - vendors_create()           GET+POST "/vendors/create"
    - vendor_detail(vendor_id)   GET "/vendors/<int:vendor_id>"
"""

import logging

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app import db
from app.decorators import audit_log
from app.middleware.tenant_decorators import platform_admin_required

from ._helpers import _vendors_impl
from . import unified_applications_bp
from app.utils.pagination import safe_int_arg

logger = logging.getLogger(__name__)


@unified_applications_bp.route("/vendors/")
@unified_applications_bp.route("/vendors")
@login_required
def vendors():
    """Vendor Management - Display vendor organizations with their product portfolios.

    Consolidated vendor management page with pagination, filtering, and search.
    """
    from sqlalchemy.orm import joinedload

    from app.models.vendor.domain_choices import (
        VENDOR_DOMAINS,
        get_domain_color_classes,
        get_domain_filter_choices,
        get_domain_label,
    )
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

    # Get query parameters
    vendor_type_filter = request.args.get("vendor_type", "all")
    domain_filter = request.args.get("domain", "all")
    contract_status_filter = request.args.get("contract_status", "all")
    search_query = request.args.get("search", "")
    page = safe_int_arg('page', 1, minimum=1)
    per_page = min(safe_int_arg('per_page', 10, minimum=1, maximum=500), 200)

    try:
        return _vendors_impl(
            joinedload,
            VendorOrganization,
            VendorProduct,
            VENDOR_DOMAINS,
            get_domain_color_classes,
            get_domain_filter_choices,
            get_domain_label,
            vendor_type_filter,
            domain_filter,
            contract_status_filter,
            search_query,
            page,
            per_page,
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Error loading vendor list: %s", e)
        flash("Error loading vendors. Please try again.", "error")
        # stats=None so every card shows an em dash. A zeroed vendor count
        # reads as "we have no vendors", which is a different fact entirely.
        return render_template(
            "vendors/list.html",
            vendors=[],
            stats=None,
            load_error="The vendor catalogue could not be read.",
            vendor_type_filter_options=[],
            vendor_type_filter=vendor_type_filter,
            domain_filter=domain_filter,
            contract_status_filter=contract_status_filter,
            search_query=search_query,
            pagination=None,
            per_page=per_page,
            domain_choices=get_domain_filter_choices(),
            get_domain_label=get_domain_label,
            get_domain_color_classes=get_domain_color_classes,
            VENDOR_DOMAINS=VENDOR_DOMAINS,
        )


@unified_applications_bp.route("/vendors/create", methods=["GET", "POST"])
@login_required
@platform_admin_required
@audit_log("create_vendor")
def vendors_create():
    """Render and process the standalone vendor create form.

    Platform-admin-only (found while fixing PR 430's named vendor create/
    update routes, same vulnerability class, lead review): this route had
    NO role check at all -- any signed-in user, not even an org admin,
    could create a row in the global, platform-wide ``VendorOrganization``
    catalogue. ``platform_admin_required`` is the fix; there was no existing
    decorator to keep.
    """
    from app.models.vendor.vendor_organization import VendorOrganization

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        vendor_type = request.form.get("vendor_type", "").strip()
        description = request.form.get("description", "").strip()
        website = request.form.get("website", "").strip()
        headquarters_location = request.form.get("headquarters_location", "").strip()

        if not name:
            flash("Vendor name is required.", "error")
            return render_template("vendors/create_simple.html")

        existing = VendorOrganization.query.filter_by(name=name).first()
        if existing:
            flash(f"A vendor named '{name}' already exists.", "error")
            return render_template("vendors/create_simple.html")

        try:
            import re

            from flask_login import current_user

            # code and seed_source_id are NOT NULL + unique; the form doesn't collect
            # them, so derive a stable unique code from the name (VEND-ACME-CLOUD-INC).
            base_code = ("VEND-" + re.sub(r"[^A-Z0-9]+", "-", name.upper()).strip("-"))[:44] or "VEND"
            code = base_code
            suffix = 1
            while VendorOrganization.query.filter_by(code=code).first():
                suffix += 1
                code = f"{base_code[:42]}-{suffix}"

            vendor = VendorOrganization(
                name=name,
                code=code,
                seed_source_id=f"manual:{code}",
                vendor_type=vendor_type or None,
                description=description or None,
                website=website or None,
                headquarters_location=headquarters_location or None,
                status="active",
                created_by_id=getattr(current_user, "id", None),
            )
            db.session.add(vendor)
            db.session.commit()
            flash(f"Vendor '{name}' created successfully.", "success")
            return redirect(url_for("unified_applications.vendor_detail", vendor_id=vendor.id))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error creating vendor: {e}", exc_info=True)
            flash("Failed to create vendor. Please try again.", "error")

    return render_template("vendors/create_simple.html")


@unified_applications_bp.route("/vendors/<int:vendor_id>")
@login_required
def vendor_detail(vendor_id):
    """Display vendor details with products, capabilities, APQC processes, and ArchiMate elements."""

    from flask import current_app
    from sqlalchemy import or_
    from sqlalchemy.orm import joinedload

    from app.models.application_layer import ApplicationComponent
    from app.models.apqc_process import APQCProcess
    from app.models.business_capabilities import BusinessCapability
    from app.models.models import ArchiMateElement
    from app.models.vendor.vendor_organization import (
        VendorOrganization,
        VendorProduct,
        VendorProductCapability,
        application_vendor_products,
    )
    from app.models.vendor_product_apqc_mapping import VendorProductAPQCMapping

    try:
        # Get vendor with products loaded
        vendor = VendorOrganization.query.options(
            joinedload(VendorOrganization.products)
        ).get_or_404(vendor_id)

        # All reads below are wrapped in no_autoflush to prevent cascading
        # rollbacks from invalidating the session for subsequent queries.
        archimate_elements = []
        vendor_capabilities = []
        vendor_apqc_mappings = []
        deployed_applications = []
        all_business_capabilities = []
        products_json = []

        with db.session.no_autoflush:
            # Get all ArchiMate elements linked to this vendor's products
            try:
                archimate_elements = (
                    ArchiMateElement.query.join(ArchiMateElement.vendor_products)
                    .filter(VendorProduct.vendor_organization_id == vendor_id)
                    .distinct()
                    .all()
                )
            except Exception as e:
                current_app.logger.warning("Could not load ArchiMate elements: %s", e, exc_info=True)

            # Get business capabilities for vendor's products
            try:
                vendor_capabilities = (
                    db.session.query(
                        BusinessCapability,
                        VendorProductCapability.coverage_percentage,
                        VendorProductCapability.maturity_level,
                        VendorProductCapability.fit_score,
                    )
                    .join(
                        VendorProductCapability,
                        BusinessCapability.id
                        == VendorProductCapability.business_capability_id,
                    )
                    .join(
                        VendorProduct,
                        VendorProduct.id == VendorProductCapability.vendor_product_id,
                    )
                    .filter(VendorProduct.vendor_organization_id == vendor_id)
                    .all()
                )
            except Exception as e:
                current_app.logger.warning("Could not load vendor capabilities: %s", e, exc_info=True)

            # Get deployed applications from this vendor's products.
            # Query distinct IDs first to avoid DISTINCT on JSON columns (psycopg2 error).
            try:
                app_id_rows = (
                    db.session.query(ApplicationComponent.id)
                    .join(
                        ArchiMateElement,
                        or_(
                            ArchiMateElement.id == ApplicationComponent.archimate_element_id,
                            ArchiMateElement.application_component_id
                            == ApplicationComponent.id,
                        ),
                    )
                    .join(
                        application_vendor_products,
                        application_vendor_products.c.archimate_element_id
                        == ArchiMateElement.id,
                    )
                    .join(
                        VendorProduct,
                        VendorProduct.id == application_vendor_products.c.vendor_product_id,
                    )
                    .filter(VendorProduct.vendor_organization_id == vendor_id)
                    .distinct()
                    .all()
                )
                app_ids = [r.id for r in app_id_rows]
                deployed_applications = (
                    ApplicationComponent.query.filter(
                        ApplicationComponent.id.in_(app_ids)
                    ).all()
                    if app_ids
                    else []
                )
            except Exception as e:
                current_app.logger.warning("Could not load deployed applications: %s", e, exc_info=True)

            # Get all business capabilities for the mapping form
            try:
                all_business_capabilities = BusinessCapability.query.order_by(
                    BusinessCapability.name
                ).all()
            except Exception as e:
                current_app.logger.warning("Could not load business capabilities: %s", e, exc_info=True)

            # Get APQC process mappings for this vendor's products
            try:
                vendor_apqc_mappings = (
                    db.session.query(
                        VendorProduct,
                        APQCProcess,
                        VendorProductAPQCMapping,
                    )
                    .join(
                        VendorProductAPQCMapping,
                        VendorProductAPQCMapping.vendor_product_id == VendorProduct.id,
                    )
                    .join(
                        APQCProcess,
                        APQCProcess.id == VendorProductAPQCMapping.apqc_process_id,
                    )
                    .filter(VendorProduct.vendor_organization_id == vendor_id)
                    .order_by(VendorProductAPQCMapping.relevance_score.desc())
                    .all()
                )
            except Exception as e:
                current_app.logger.warning(
                    "Could not load APQC mappings: %s", e, exc_info=True
                )

            # Serialize data for JavaScript
            try:
                products_json = [{"id": p.id, "name": p.name} for p in vendor.products]
            except Exception as e:
                current_app.logger.warning("Could not load vendor products: %s", e, exc_info=True)

        # Group ArchiMate elements by layer for tabbed view
        elements_by_layer = {
            "strategy": [],
            "motivation": [],
            "business": [],
            "application": [],
            "technology": [],
            "physical": [],
            "implementation": [],
        }

        for element in archimate_elements:
            layer = element.layer.lower() if element.layer else "application"
            if layer in elements_by_layer:
                elements_by_layer[layer].append(element)
            else:
                elements_by_layer["application"].append(element)
        capabilities_json = [
            {"id": c.id, "name": c.name} for c in all_business_capabilities
        ]

        from datetime import datetime, timedelta
        _stale_cutoff = datetime.utcnow() - timedelta(days=90)
        stale_product_ids = {
            p.id for p in vendor.products
            if p.updated_at is None or p.updated_at < _stale_cutoff
        }

        return render_template(
            "vendors/vendor_detail.html",
            vendor=vendor,
            archimate_elements=archimate_elements,
            elements_by_layer=elements_by_layer,
            vendor_capabilities=vendor_capabilities,
            vendor_apqc_mappings=vendor_apqc_mappings,
            deployed_applications=deployed_applications,
            all_business_capabilities=all_business_capabilities,
            products_json=products_json,
            capabilities_json=capabilities_json,
            stale_product_ids=stale_product_ids,
        )
    except Exception as e:
        from werkzeug.exceptions import HTTPException
        # Re-raise HTTP exceptions (404, 403, etc.) so Flask handles them correctly.
        if isinstance(e, HTTPException):
            raise
        # Rollback the transaction on unexpected errors only.
        db.session.rollback()
        current_app.logger.error(f"Error loading vendor detail: {e}", exc_info=True)
        flash("Error loading vendor details. Please try again.", "error")
        return redirect(url_for("unified_applications.vendors"))

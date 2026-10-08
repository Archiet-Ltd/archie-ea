"""
MIGRATION: Copied from app/unified_vendors/routes.py
Changes:
  - `from . import unified_vendors_bp` -> Blueprint defined locally (was in app/unified_vendors/__init__.py)
  - `from app.extensions import db` already used in source
Legacy file preserved at original location.

Unified Vendor Routes - UI Views

Persona-specific dashboards and views for the unified vendor module.
"""

from flask import render_template, request, redirect, url_for, flash, jsonify
from app.utils.tenant_sql import org_scope
from flask_login import login_required, current_user
from flask import current_app, Blueprint
from datetime import datetime
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

from app.decorators import audit_log, require_roles
from app.exceptions import AuthorizationError
from app.middleware.tenant_decorators import platform_admin_required
from app.models.vendor_organization import VendorOrganization
from app.extensions import db
from app.modules.vendors.services.vendor_onboarding_service import (
    VendorOnboardingService,
)


def _get_vendor_list_context():
    """Return real vendor list data for the vendors/list.html template."""
    try:
        rows = db.session.execute(text("""
            SELECT vo.id, vo.name,
                   COUNT(DISTINCT vp.id) AS product_count,
                   COUNT(DISTINCT m.application_component_id) AS app_count,
                   COALESCE(vo.contract_value_annual, 0) AS contract_value_annual,
                   vo.contract_end_date
            FROM vendor_organizations vo
            LEFT JOIN vendor_products vp ON vp.vendor_organization_id = vo.id
            LEFT JOIN application_vendor_product_mappings m ON m.vendor_product_id = vp.id
            GROUP BY vo.id, vo.name, vo.contract_value_annual, vo.contract_end_date
            ORDER BY app_count DESC, vo.name
        """)).fetchall()
        from datetime import datetime, timedelta
        cutoff = datetime.utcnow() + timedelta(days=90)
        vendors = [
            {"id": r[0], "name": r[1], "product_count": r[2], "app_count": r[3]}
            for r in rows
        ]
        total_apps = sum(v["app_count"] for v in vendors)
        total_acv = sum(float(r[4]) for r in rows)
        renewals_due = sum(
            1 for r in rows
            if r[5] is not None and datetime.utcnow() <= r[5] <= cutoff
        )
        stats = {
            "total": len(vendors),
            "active": len(vendors),
            "strategic": sum(1 for v in vendors if v["app_count"] > 5),
            "total_products": sum(v["product_count"] for v in vendors),
            "total_linked_apps": total_apps,
            "total_acv": total_acv,
            "renewals_due": renewals_due,
        }
        return stats, vendors
    except Exception:
        # Query failed: report unknown (em dash), not fabricated zero vendor metrics.
        return {"total": None, "active": None, "strategic": None, "total_products": None, "total_linked_apps": None, "total_acv": None, "renewals_due": None}, []

# Blueprint defined here (was in app/unified_vendors/__init__.py)
unified_vendors_bp = Blueprint(
    "unified_vendors",
    __name__,
    url_prefix="/vendors",
    template_folder="templates/unified_vendors",
)


# =============================================================================
# Main Vendor Catalog
# =============================================================================


# NOTE: vendors_dashboard() at "/" removed — shadowed by main.vendors_redirect
# which is registered earlier in blueprints.py. Both redirected to the same place.
# The main blueprint's /vendors redirect is the canonical entry point.


# =============================================================================
# Integration Architect Dashboard
# =============================================================================


@unified_vendors_bp.route("/integration")
@login_required
def integration_dashboard():
    """Integration Architect dashboard - renders vendor catalog."""
    stats, vendors = _get_vendor_list_context()
    return render_template("vendors/list.html", stats=stats, vendors=vendors, pagination=None)


@unified_vendors_bp.route("/integration/mapping")
@login_required
def vendor_mapping_tool():
    """Application-to-vendor mapping tool - renders vendor applications portfolio."""
    vendor = type("Vendor", (), {"id": 0, "name": "All Vendors"})()
    try:
        # Neither application_vendor_product_mappings nor vendor_products has an
        # organization_id, so this listed every tenant's applications. The
        # predicate belongs on application_components, which does carry one.
        from flask import g as _g
        _org = getattr(_g, "current_org_id", None)
        _org_where = " WHERE ac.organization_id = :org" if _org is not None else ""
        rows = db.session.execute(text(f"""
            SELECT ac.id, ac.name, ac.vendor_name, vp.name AS product_name,
                   m.role_type, ac.description, ac.deployment_status,
                   ac.business_criticality, ac.business_owner
            FROM application_vendor_product_mappings m
            JOIN application_components ac ON ac.id = m.application_component_id
            JOIN vendor_products vp ON vp.id = m.vendor_product_id{_org_where}
            ORDER BY ac.vendor_name, ac.name
        """), ({"org": _org} if _org is not None else {})).fetchall()
        applications = [_app_portfolio_item(r) for r in rows]
    except Exception:
        applications = []
    stats = _portfolio_stats(applications)
    return render_template(
        "vendors/vendor_applications_portfolio.html",
        vendor=vendor,
        stats=stats,
        applications=applications,
    )


@unified_vendors_bp.route("/integration/patterns")
@login_required
def integration_patterns():
    """Vendor-specific integration pattern library - renders vendor list."""
    stats, vendors = _get_vendor_list_context()
    return render_template("vendors/list.html", stats=stats, vendors=vendors, pagination=None)


# =============================================================================
# Technical Architect Dashboard
# =============================================================================


@unified_vendors_bp.route("/technical")
@login_required
def technical_dashboard():
    """Technical Architect dashboard - renders vendor list."""
    stats, vendors = _get_vendor_list_context()
    return render_template("vendors/list.html", stats=stats, vendors=vendors, pagination=None)


@unified_vendors_bp.route("/technical/comparison")
@login_required
def vendor_comparison():
    """Vendor comparison workbench - renders vendor list."""
    stats, vendors = _get_vendor_list_context()
    return render_template("vendors/list.html", stats=stats, vendors=vendors, pagination=None)


def _app_portfolio_item(r):
    """Shape a mapping row into the nested structure the portfolio template expects."""
    return {
        "application": {
            "name": r.name,
            "description": r.description,
            "deployment_status": r.deployment_status,
            "business_criticality": r.business_criticality,
            "hosting_model": None,
            "owner_team": r.business_owner,
        },
        "vendor_product": {"name": r.product_name},
        "role_type": r.role_type,
        "vendor_name": r.vendor_name,
    }


def _portfolio_stats(apps):
    """Compute portfolio summary stats from nested portfolio items."""
    product_names = {a["vendor_product"]["name"] for a in apps}
    return {
        "total_applications": len(apps),
        "products_with_deployments": len(product_names),
        "total_products": len(product_names),
    }


def _get_vendor_apps(vendor_id):
    """Return applications and stats for a specific vendor."""
    try:
        # vendor_organizations carries no organization_id, so filtering on the
        # vendor alone returned EVERY tenant's applications for that vendor -
        # names, descriptions, owners and criticality. Scope on the one table
        # here that does carry it.
        _org_clause, _org_params = org_scope("ac.")
        rows = db.session.execute(text("""
            SELECT ac.id, ac.name, ac.vendor_name, vp.name AS product_name, m.role_type,
                   ac.description, ac.deployment_status, ac.business_criticality,
                   ac.business_owner
            FROM application_vendor_product_mappings m
            JOIN application_components ac ON ac.id = m.application_component_id
            JOIN vendor_products vp ON vp.id = m.vendor_product_id
            JOIN vendor_organizations vo ON vo.id = vp.vendor_organization_id
            WHERE vo.id = :vid
            {org_clause}
            ORDER BY vp.name, ac.name
        """.format(org_clause=_org_clause)), {"vid": vendor_id, **_org_params}).fetchall()
        apps = [_app_portfolio_item(r) for r in rows]
    except Exception:
        apps = []
    stats = _portfolio_stats(apps)
    return apps, stats


@unified_vendors_bp.route("/technical/analytics/<int:vendor_id>")
@login_required
def vendor_analytics(vendor_id):
    """Vendor analytics - renders vendor applications portfolio."""
    # A synthesised "Vendor #<id>" placeholder with "Total Applications: 0" is
    # fabricated data — the user cannot tell it from a real vendor with no
    # applications. A vendor that does not exist is a 404.
    from app.utils.route_guards import require_entity

    vendor = require_entity(
        VendorOrganization, vendor_id, description="Vendor not found"
    )
    applications, stats = _get_vendor_apps(vendor_id)
    return render_template(
        "vendors/vendor_applications_portfolio.html",
        vendor=vendor,
        stats=stats,
        applications=applications,
    )


@unified_vendors_bp.route("/applications-portfolio/<int:vendor_id>")
@login_required
def vendor_applications_portfolio(vendor_id):
    """Vendor applications portfolio view -- linked from vendor detail page."""
    # A synthesised "Vendor #<id>" placeholder with "Total Applications: 0" is
    # fabricated data — the user cannot tell it from a real vendor with no
    # applications. A vendor that does not exist is a 404.
    from app.utils.route_guards import require_entity

    vendor = require_entity(
        VendorOrganization, vendor_id, description="Vendor not found"
    )
    applications, stats = _get_vendor_apps(vendor_id)
    return render_template(
        "vendors/vendor_applications_portfolio.html",
        vendor=vendor,
        stats=stats,
        applications=applications,
    )


@unified_vendors_bp.route("/technical/scenarios")
@login_required
def scenario_analyzer():
    """What-if scenario analyzer - renders vendor list."""
    stats, vendors = _get_vendor_list_context()
    return render_template("vendors/list.html", stats=stats, vendors=vendors, pagination=None)


# =============================================================================
# Data Architect Dashboard
# =============================================================================


@unified_vendors_bp.route("/data")
@login_required
def data_dashboard():
    """Data Architect dashboard - renders vendor list."""
    stats, vendors = _get_vendor_list_context()
    return render_template("vendors/list.html", stats=stats, vendors=vendors, pagination=None)


@unified_vendors_bp.route("/data/quality")
@login_required
def data_quality():
    """Vendor data quality dashboard - renders vendor list."""
    stats, vendors = _get_vendor_list_context()
    return render_template("vendors/list.html", stats=stats, vendors=vendors, pagination=None)


@unified_vendors_bp.route("/data/duplicates")
@login_required
def duplicate_management():
    """Duplicate vendor management - redirects to duplicate detection."""
    return redirect(url_for("unified_duplicate.simple_dashboard"))


@unified_vendors_bp.route("/data/import", methods=["GET", "POST"])
@login_required
def vendor_import():
    """Bulk vendor import - renders vendor list for GET, processes import for POST."""
    if request.method == "GET":
        stats = {"total": 0, "active": 0, "strategic": 0, "total_products": 0, "domain_distribution": {}}
        return render_template("vendors/list.html", stats=stats, vendors=[], pagination=None)
    return import_vendors()


@unified_vendors_bp.route("/data/reconciliation")
@login_required
def vendor_reconciliation():
    """Vendor reconciliation - renders vendor list."""
    stats, vendors = _get_vendor_list_context()
    return render_template("vendors/list.html", stats=stats, vendors=vendors, pagination=None)


# =============================================================================
# Solution Architect Dashboard
# =============================================================================


@unified_vendors_bp.route("/selection")
@login_required
def selection_dashboard():
    """Solution Architect dashboard - renders vendor list."""
    stats, vendors = _get_vendor_list_context()
    return render_template("vendors/list.html", stats=stats, vendors=vendors, pagination=None)


@unified_vendors_bp.route("/selection/requirements")
@login_required
def requirements_definition():
    """Define requirements - renders vendor list."""
    stats, vendors = _get_vendor_list_context()
    return render_template("vendors/list.html", stats=stats, vendors=vendors, pagination=None)


@unified_vendors_bp.route("/selection/discovery")
@login_required
def vendor_discovery():
    """AI-powered vendor discovery - renders vendor list."""
    stats, vendors = _get_vendor_list_context()
    return render_template("vendors/list.html", stats=stats, vendors=vendors, pagination=None)


@unified_vendors_bp.route("/selection/analysis")
@login_required
def selection_analysis():
    """Vendor selection analysis - renders vendor list."""
    stats, vendors = _get_vendor_list_context()
    return render_template("vendors/list.html", stats=stats, vendors=vendors, pagination=None)


# =============================================================================
# Vendor Management Actions (Consolidated from vendor_management_routes.py)
# =============================================================================


@unified_vendors_bp.route("/catalog", methods=["GET"])
@login_required
def vendor_catalog():
    """Vendor catalog - redirects to working vendor list."""
    return redirect(url_for("unified_applications.vendors"))


@unified_vendors_bp.route("/create", methods=["GET", "POST"])
@login_required
@require_roles("admin", "architect")
@platform_admin_required
def create_vendor():
    """Create new vendor - renders simple create form.

    Platform-admin-only (lead review of PR 430, D-2 follow-up): the actual
    create submission writes the global, platform-wide ``VendorOrganization``
    catalogue (app/modules/applications/routes/vendor_display_routes.py's
    ``vendors_create``, fixed the same way below). Gating this form the same
    way keeps a tenant admin from even reaching a create UI for a resource
    they cannot write. ``require_roles`` is kept rather than removed;
    ``platform_admin_required`` is the operative check.
    """
    return render_template(
        "vendors/create_simple.html",
    )


@unified_vendors_bp.route("/<int:vendor_id>", methods=["GET"])
@login_required
def vendor_detail(vendor_id):
    """Vendor detail - redirects to the working vendor detail page."""
    return redirect(url_for("unified_applications.vendor_detail", vendor_id=vendor_id))


@unified_vendors_bp.route("/<int:vendor_id>/edit", methods=["GET", "PUT", "POST"])
@login_required
@require_roles("admin", "architect")
@platform_admin_required
@audit_log("vendor_edit")
def edit_vendor(vendor_id):
    """Edit vendor organization - real implementation.

    Platform-admin-only -- see ``create_vendor``'s docstring above.
    """
    from app.modules.vendors.forms import CreateVendorForm
    from app.models.vendor.vendor_organization import VendorOrganization

    vendor = VendorOrganization.query.get_or_404(vendor_id)
    form = CreateVendorForm(obj=vendor)

    if form.validate_on_submit():
        try:
            vendor.name = form.name.data
            vendor.display_name = form.display_name.data
            vendor.description = form.description.data
            vendor.vendor_type = form.vendor_type.data
            vendor.website = form.website.data
            vendor.headquarters_location = form.headquarters_location.data
            vendor.gartner_magic_quadrant_position = (
                form.gartner_magic_quadrant_position.data
            )
            vendor.forrester_wave_position = form.forrester_wave_position.data
            vendor.market_share_percentage = form.market_share_percentage.data
            vendor.year_founded = form.year_founded.data
            vendor.employee_count = form.employee_count.data
            vendor.annual_revenue_usd = form.annual_revenue_usd.data
            vendor.customer_count = form.customer_count.data
            vendor.public_company = (
                (form.public_company.data == "yes")
                if form.public_company.data
                else None
            )
            vendor.stock_symbol = form.stock_symbol.data
            vendor.strategic_tier = form.strategic_tier.data
            vendor.contract_status = form.contract_status.data
            vendor.contract_start_date = form.contract_start_date.data
            vendor.contract_end_date = form.contract_end_date.data
            vendor.contract_value_annual = form.contract_value_annual.data
            vendor.updated_at = datetime.utcnow()

            db.session.commit()
            flash(f'Vendor "{vendor.name}" updated successfully.', "success")
            return redirect(url_for("unified_vendors.edit_vendor", vendor_id=vendor.id))
        except Exception:
            db.session.rollback()
            flash("Error updating vendor. Please try again.", "error")

    return render_template("vendors/edit.html", form=form, vendor=vendor)


@unified_vendors_bp.route("/<int:vendor_id>/activate", methods=["POST"])
@login_required
@require_roles("admin", "architect")
@platform_admin_required
@audit_log("vendor_activate")
def activate_vendor(vendor_id):
    """Activate a vendor from catalog to contracted status.

    Platform-admin-only (D-08, PR 430 round 3, lead review v2, 2026-10-08):
    this used to rely solely on the structural platform-write guard to
    refuse a non-platform-admin's write to the global VendorOrganization
    catalogue, and the broad ``except Exception`` below swallowed that
    guard's AuthorizationError into a flash message + redirect, so the
    caller never actually saw a 403 -- see ``create_vendor``'s docstring
    above for the same pattern already fixed on the sibling routes.
    """
    try:
        contract_start = request.form.get("contract_start_date")
        contract_end = request.form.get("contract_end_date")
        contract_value = request.form.get("contract_value_annual")

        start_date = (
            datetime.strptime(contract_start, "%Y-%m-%d") if contract_start else None
        )
        end_date = datetime.strptime(contract_end, "%Y-%m-%d") if contract_end else None
        value = float(contract_value) if contract_value else None

        vendor = VendorOnboardingService.activate_vendor(
            vendor_id,
            contract_start_date=start_date,
            contract_end_date=end_date,
            contract_value=value,
        )

        flash(
            f"{vendor.name} activated successfully! Status: {vendor.contract_status}",
            "success",
        )

        if request.headers.get("Accept") == "application/json":
            return jsonify(
                {
                    "success": True,
                    "vendor_id": vendor.id,
                    "contract_status": vendor.contract_status,
                }
            )

        return redirect(url_for("unified_vendors.vendor_detail", vendor_id=vendor_id))
    except AuthorizationError:
        raise
    except Exception as e:
        current_app.logger.error("Error activating vendor %s: %s", vendor_id, e, exc_info=True)
        flash("Error activating vendor. Please try again.", "error")
        if request.headers.get("Accept") == "application/json":
            return jsonify({"success": False, "error": "An internal error occurred. Please try again."}), 400
        return redirect(url_for("unified_vendors.vendor_detail", vendor_id=vendor_id))


@unified_vendors_bp.route(
    "/<int:vendor_id>/products/<int:product_id>/deploy", methods=["POST"]
)
@login_required
@require_roles("admin", "architect")
@platform_admin_required
@audit_log("vendor_product_deploy")
def deploy_vendor_product(vendor_id, product_id):
    """Deploy a vendor product as an application (canonical flask-base-master flow).

    Platform-admin-only (D-08, PR 430 round 3, lead review v2, 2026-10-08):
    see ``activate_vendor``'s docstring above -- same pattern, same guard,
    same previously-swallowed AuthorizationError.
    """
    try:
        # Support both form data (modal submit) and JSON (API)
        if request.content_type and "application/json" in request.content_type:
            data = request.get_json() or {}
            app_name = data.get("application_name") or data.get("applicationName")
        else:
            data = request.form
            app_name = data.get("application_name")

        if not app_name or not str(app_name).strip():
            if request.content_type and "application/json" in request.content_type:
                return jsonify({"success": False, "error": "Application name is required"}), 400
            flash("Application name is required.", "error")
            return redirect(url_for("unified_vendors.vendor_detail", vendor_id=vendor_id))

        deployment_config = {
            "application_name": str(app_name).strip(),
            "description": (data.get("description") or "").strip() or None,
            "deployment_type": data.get("deployment_type") or data.get("deploymentType") or "primary_system",
            "criticality": data.get("criticality") or data.get("criticality") or "business_critical",
            "hosting_model": data.get("hosting_model") or data.get("hostingModel") or "cloud",
            "business_owner": (data.get("business_owner") or data.get("businessOwner") or "").strip() or None,
        }

        application = VendorOnboardingService.deploy_vendor_product_as_application(
            vendor_product_id=product_id,
            application_name=deployment_config["application_name"],
            description=deployment_config["description"],
            deployment_type=deployment_config["deployment_type"],
            criticality=deployment_config["criticality"],
            hosting_model=deployment_config["hosting_model"],
            business_owner=deployment_config["business_owner"],
        )

        if request.content_type and "application/json" in request.content_type:
            return jsonify({
                "success": True,
                "application_id": application.id,
                "application_name": application.name,
            })
        flash(f'Application "{application.name}" deployed successfully.', "success")
        return redirect(url_for("unified_applications.vendor_detail", vendor_id=vendor_id))
    except AuthorizationError:
        raise
    except ValueError as e:
        if request.content_type and "application/json" in request.content_type:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(str(e), "error")
        return redirect(url_for("unified_applications.vendor_detail", vendor_id=vendor_id))
    except Exception as e:
        current_app.logger.error("Error deploying vendor product %s: %s", product_id, e, exc_info=True)
        if request.content_type and "application/json" in request.content_type:
            return jsonify({"success": False, "error": "An internal error occurred. Please try again."}), 400
        flash("An internal error occurred. Please try again.", "error")
        return redirect(url_for("unified_applications.vendor_detail", vendor_id=vendor_id))


@unified_vendors_bp.route("/<int:vendor_id>/deployment-portfolio")
@login_required
def vendor_deployment_portfolio(vendor_id):
    """Deprecated ORPHAN. Redirect to canonical /vendors/<id>. No inbound links."""
    return redirect(url_for("unified_vendors.vendor_detail", vendor_id=vendor_id), code=301)


@unified_vendors_bp.route("/<int:vendor_id>/delete", methods=["DELETE", "POST"])
@login_required
@require_roles("admin")
@audit_log("vendor_delete")
def delete_vendor(vendor_id):
    """Delete vendor - directly deletes and shows success message."""
    vendor = VendorOrganization.query.get_or_404(vendor_id)

    vendor_name = vendor.name
    db.session.delete(vendor)
    db.session.commit()

    current_app.logger.info(
        f"[VENDOR DELETED] {vendor_name} (ID: {vendor_id}) by {current_user.email}"
    )

    if request.headers.get("Accept") == "application/json":
        return jsonify(
            {"status": "success", "message": f"Vendor '{vendor_name}' deleted"}
        ), 200

    flash(f"Vendor '{vendor_name}' deleted successfully", "success")
    stats, vendors = _get_vendor_list_context()
    return render_template("vendors/list.html", stats=stats, vendors=vendors, pagination=None)


@unified_vendors_bp.route("/import", methods=["GET", "POST"])
@login_required
@require_roles("admin")
@audit_log("vendor_import")
def import_vendors():
    """
    Bulk import vendors from CSV/Excel.
    Consolidates /vendor-management/import
    """
    if request.method == "GET":
        if request.headers.get("Accept") == "application/json":
            return jsonify({"success": True, "message": "Use POST to import vendors"})
        stats = {"total": 0, "active": 0, "strategic": 0, "total_products": 0, "domain_distribution": {}}
        return render_template("vendors/list.html", stats=stats, vendors=[], pagination=None)

    # POST - process import
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]

    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    import csv
    import io

    filename = file.filename.lower()

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

        # Process vendors
        imported = 0
        updated = 0
        errors = []

        for row in vendors_data:
            try:
                name = row.get("name") or row.get("Name") or row.get("vendor_name")
                if not name:
                    errors.append("Skipping row: missing vendor name")
                    continue

                # Check if vendor exists
                existing = VendorOrganization.query.filter_by(name=name).first()

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
                    # Update existing
                    for key, value in vendor_data.items():
                        if value and hasattr(existing, key):
                            setattr(existing, key, value)
                    existing.updated_at = datetime.utcnow()
                    updated += 1
                else:
                    # Create new
                    vendor = VendorOrganization(**vendor_data)
                    db.session.add(vendor)
                    imported += 1

            except Exception as e:
                errors.append(f"Error processing row: {str(e)}")

        db.session.commit()

        current_app.logger.info(
            f"[VENDOR IMPORT] {imported} imported, {updated} updated by {current_user.email}"
        )

        return jsonify(
            {
                "status": "success",
                "message": f"Import complete: {imported} imported, {updated} updated",
                "imported": imported,
                "updated": updated,
                "errors": errors[:10],  # Limit errors returned
            }
        ), 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.error("Vendor import failed: %s", e, exc_info=True)
        return jsonify({"status": "error", "message": "Import failed. Please check the file format and try again."}), 500


# =============================================================================
# Vendor Products API (for Alpine.js frontend)
# =============================================================================


@unified_vendors_bp.route("/<int:vendor_id>/products", methods=["GET"])
@login_required
def get_vendor_products(vendor_id):
    """
    Get all products for a specific vendor.

    Called by vendors/list.html Alpine.js frontend.
    """
    from app.models.vendor.vendor_organization import VendorProduct

    vendor = VendorOrganization.query.get_or_404(vendor_id)

    # Get all products for this vendor using the correct FK field
    products = VendorProduct.query.filter_by(vendor_organization_id=vendor_id).all()

    products_data = [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "version": p.version,
            "category": p.product_type or p.product_family_name,
            "deployment_type": p.deployment_model,
            "pricing_model": p.licensing_model,
            "status": p.status,
            "website_url": None,
            "documentation_url": None,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        }
        for p in products
    ]

    return jsonify(
        {
            "success": True,
            "vendor_id": vendor_id,
            "vendor_name": vendor.name,
            "products": products_data,
            "total_products": len(products_data),
        }
    )


@unified_vendors_bp.route("/<int:vendor_id>/concentration-risk", methods=["GET"])
@login_required
def vendor_concentration_risk(vendor_id):
    """Return capability concentration risk for this vendor.

    For each capability this vendor's products cover, reports whether other
    vendors also cover it (safe) or this vendor is the sole provider (risk).
    """
    try:
        from app.models.vendor.vendor_organization import VendorProduct, VendorProductCapability
        from app.utils.route_guards import require_entity_json

        # Existence guard: an empty risk list for a nonexistent vendor is fabricated data.
        _vendor, missing = require_entity_json(VendorOrganization, vendor_id, label="Vendor")
        if missing:
            return missing
        from app.models.business_capabilities import BusinessCapability
        from sqlalchemy import func

        product_ids = [
            p.id for p in VendorProduct.query.filter_by(vendor_organization_id=vendor_id).all()
        ]
        if not product_ids:
            return jsonify({"success": True, "risks": [], "vendor_id": vendor_id})

        my_cap_ids = [
            r[0] for r in db.session.query(VendorProductCapability.business_capability_id)
            .filter(VendorProductCapability.vendor_product_id.in_(product_ids))
            .distinct()
            .all()
        ]
        if not my_cap_ids:
            return jsonify({"success": True, "risks": [], "vendor_id": vendor_id})

        vendor_counts = dict(
            db.session.query(
                VendorProductCapability.business_capability_id,
                func.count(func.distinct(VendorProduct.vendor_organization_id)).label("vendor_count"),
            )
            .join(VendorProduct, VendorProduct.id == VendorProductCapability.vendor_product_id)
            .filter(VendorProductCapability.business_capability_id.in_(my_cap_ids))
            .group_by(VendorProductCapability.business_capability_id)
            .all()
        )

        caps = BusinessCapability.query.filter(BusinessCapability.id.in_(my_cap_ids)).all()
        cap_names = {c.id: c.name for c in caps}

        risks = [
            {
                "capability_id": cap_id,
                "capability_name": cap_names.get(cap_id, f"Capability {cap_id}"),
                "vendor_count": vendor_counts.get(cap_id, 1),
                "is_sole_vendor": vendor_counts.get(cap_id, 1) == 1,
            }
            for cap_id in my_cap_ids
        ]
        risks.sort(key=lambda r: (not r["is_sole_vendor"], r["capability_name"]))

        return jsonify({
            "success": True,
            "vendor_id": vendor_id,
            "risks": risks,
            "sole_vendor_count": sum(1 for r in risks if r["is_sole_vendor"]),
            "safe_count": sum(1 for r in risks if not r["is_sole_vendor"]),
        })
    except Exception as e:
        logger.error(f"Error computing concentration risk for vendor {vendor_id}: {e}")
        return jsonify({"success": False, "error": "Failed to compute concentration risk"}), 500

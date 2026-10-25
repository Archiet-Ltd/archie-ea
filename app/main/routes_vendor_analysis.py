"""Vendor-ArchiMate Mapping Analysis Routes"""

from flask import current_app, flash, jsonify, redirect, render_template, url_for  # dead-code-ok
from flask_login import login_required
from sqlalchemy import text

from app import db
from app.main.views import main
from app.utils.tenant_sql import org_scope


@main.route("/vendor-archimate-analysis")
@login_required
def vendor_archimate_analysis():
    """The vendor mapping page is the Mapping tab of Vendors."""
    return redirect(url_for("unified_applications.vendors", tab="mapping"), code=301)


@main.route("/api/vendor-archimate-analysis/export")
@login_required
def export_vendor_archimate_analysis():
    """Export vendor-ArchiMate mapping analysis as JSON"""

    try:
        _org_and_ae, _org_params = org_scope(prefix="ae.", keyword="AND")

        # Get all unmapped vendor products
        # (global: neither vendor_products nor vendor_organizations has an
        # organization_id column, so there is nothing here to scope against)
        unmapped_products = db.session.execute(
            text(
                """
            SELECT vp.id, vp.name, vp.product_type, vp.product_family, vo.name as vendor_name
            FROM vendor_products vp
            JOIN vendor_organizations vo ON vp.vendor_organization_id = vo.id
            WHERE vp.archimate_product_element_id IS NULL
            ORDER BY vo.name, vp.name
        """
            )
        ).fetchall()

        # Get all orphaned ArchiMate elements
        orphaned_elements = db.session.execute(
            text(
                f"""
            SELECT ae.id, ae.name, ae.type, ae.source_product_id
            FROM archimate_elements ae
            WHERE ae.source_product_id IS NOT NULL{_org_and_ae}
            AND NOT EXISTS (
                SELECT 1 FROM vendor_products vp
                WHERE vp.id = ae.source_product_id
            )
            ORDER BY ae.name
        """
            ),
            _org_params,
        ).fetchall()

        # Format data for export
        export_data = {
            "unmapped_vendor_products": [
                {
                    "id": str(product[0]),
                    "name": product[1],
                    "type": product[2],
                    "family": product[3],
                    "vendor": product[4],
                }
                for product in unmapped_products
            ],
            "orphaned_archimate_elements": [
                {
                    "id": str(element[0]),
                    "name": element[1],
                    "type": element[2],
                    "source_product_id": str(element[3]),
                }
                for element in orphaned_elements
            ],
            "summary": {
                "total_unmapped_products": len(unmapped_products),
                "total_orphaned_elements": len(orphaned_elements),
                "export_date": db.session.execute(text("SELECT CURRENT_TIMESTAMP")).scalar(),  # tenant-exempt: system function
            },
        }

        return jsonify(export_data)

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500

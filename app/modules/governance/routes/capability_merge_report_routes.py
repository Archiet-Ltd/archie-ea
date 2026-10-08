"""Platform-admin report: which duplicate capability records were merged.

Both existing capability admin surfaces in this package
(``capability_management_routes.py``, ``capability_governance_routes.py``) are
explicitly marked "DEPRECATED ... Do NOT modify" (Phase 6 cleanup fallback),
so this is a new, minimal route rather than an addition to either of them.

A merge is not a separate fact this module records: it is what
``flask backfill-capability-catalogs`` and the tenancy cutover
(``app/commands/cutover_capability_tenancy.py``) already leave behind --
more than one legacy row pointing its ``retired_into_id`` (or, for
``business_capability``, its existing ``deprecated_in_favor_of_id``) at the
same ``unified_capabilities`` row. This report only counts what is already
there; it is a read-only JSON view, not a second store.
"""
from flask import Blueprint, jsonify

from app.extensions import db
from app.middleware.tenant_decorators import platform_admin_required

capability_merge_report_bp = Blueprint("capability_merge_report", __name__)

# (label, table, id_column) for every legacy store whose rows retire into a
# unified_capabilities row. `business_capability` reuses the backlink column
# it already had before this consolidation (`deprecated_in_favor_of_id`);
# the other four gained `retired_into_id` for the same purpose.
_LEGACY_BACKLINKS = (
    ("business_capability", "business_capability", "deprecated_in_favor_of_id"),
    ("capabilities", "capabilities", "retired_into_id"),
    ("enterprise_capabilities", "enterprise_capabilities", "retired_into_id"),
    ("archimate_capabilities", "archimate_capabilities", "retired_into_id"),
    ("technical_capabilities", "technical_capabilities", "retired_into_id"),
)


@capability_merge_report_bp.route("/admin/capability-merges", methods=["GET"])
@platform_admin_required
def capability_merges():
    """List every unified_capabilities row more than one legacy record points at.

    Cross-tenant by construction (a platform-admin question, like
    /admin/errors), gated by @platform_admin_required rather than by an
    organisation filter.
    """
    # tenancy-ok: platform-admin-only cross-tenant maintenance report, gated
    # by @platform_admin_required rather than by an organisation filter --
    # the same rationale as ErrorEvent's own /admin/errors view.
    rows = db.session.execute(
        db.text(
            "WITH pointers AS ("
            + " UNION ALL ".join(
                f"SELECT '{label}'::text AS source_table, id AS legacy_id, "  # nosec B608 -- label/table/id_column come from the fixed module tuple above, never request input
                f'"{id_column}" AS target_id FROM "{table}" '
                f'WHERE "{id_column}" IS NOT NULL'
                for label, table, id_column in _LEGACY_BACKLINKS
            )
            + ") "
            "SELECT pointers.target_id, target.name AS target_name, "
            "count(*) AS pointer_count, "
            "array_agg(pointers.source_table || ':' || pointers.legacy_id "
            "ORDER BY pointers.source_table, pointers.legacy_id) AS members "
            "FROM pointers "
            "JOIN unified_capabilities AS target ON target.id = pointers.target_id "
            "GROUP BY pointers.target_id, target.name "
            "HAVING count(*) > 1 "
            "ORDER BY count(*) DESC, pointers.target_id"
        )
    ).mappings().all()

    merges = [
        {
            "unified_capability_id": row["target_id"],
            "unified_capability_name": row["target_name"],
            "merged_record_count": row["pointer_count"],
            "merged_records": row["members"],
        }
        for row in rows
    ]
    return jsonify({"success": True, "merges": merges, "total_groups": len(merges)})


def init_app(app):
    """Register this blueprint. Called from app.modules.governance.register()."""
    app.register_blueprint(capability_merge_report_bp)

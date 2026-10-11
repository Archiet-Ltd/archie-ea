"""Unmapped Capabilities Analysis Routes"""

from flask import current_app, flash, jsonify, render_template  # dead-code-ok
from flask_login import login_required
from sqlalchemy import text

from app import db
from app.main.views import main
from app.utils.tenant_sql import current_org_id


def _capability_org_scope(prefix: str = "uc."):
    """Return (clause, params) scoping unified_capabilities to the signed-in org.

    UnifiedCapability rows carry organization_id and scope.  Rows with
    scope='reference' and no organisation are shared; every other row is
    visible only to its owning organisation.  With no organisation resolved
    (CLI, unauthenticated), only the shared reference rows are shown.
    """
    org_id = current_org_id()
    if org_id is None:
        return (
            f" AND {prefix}scope = 'reference' AND {prefix}organization_id IS NULL",
            {},
        )
    return (
        f" AND ({prefix}organization_id = :org_id OR ({prefix}scope = 'reference' AND {prefix}organization_id IS NULL))",
        {"org_id": org_id},
    )


@main.route("/capability-analysis/unmapped")
@login_required
def unmapped_capabilities():
    """Dedicated page for viewing capabilities with no applications mapped"""

    try:
        # Get unmapped capabilities with detailed information.
        # unified_capabilities rows carry organization_id and scope; only
        # scope='reference' rows with no organisation are shared.  Every
        # other row is visible only to its owning organisation.
        _org_clause, _org_params = _capability_org_scope()

        # The rows the page exists to show. These were never queried: the name
        # `unmapped_capabilities` below resolved to this view function itself, so
        # `len()` raised TypeError, the except branch caught it, and the page has
        # only ever rendered its "could not load" state. The template reads name,
        # description, strategic_importance, status, domain_name and the two
        # maturity levels, so all seven are selected here.
        unmapped_capabilities = db.session.execute(
            text(
                f"""
            SELECT
                uc.id,
                uc.name,
                uc.description,
                uc.strategic_importance,
                uc.status,
                uc.current_maturity_level,
                uc.target_maturity_level,
                bd.name AS domain_name
            FROM unified_capabilities uc
            LEFT JOIN unified_application_capability_mapping uacm
                ON uc.id = uacm.unified_capability_id
            LEFT JOIN business_domains bd ON bd.id = uc.domain_id
            WHERE uacm.unified_capability_id IS NULL
            {_org_clause}
            ORDER BY
                CASE uc.strategic_importance
                    WHEN 'critical' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3
                    WHEN 'low' THEN 4
                    ELSE 5
                END,
                uc.name
        """  # nosec B608 -- only the org_scope fragment is interpolated; values are bound parameters
            ),
            _org_params
        ).fetchall()

        # Get summary statistics
        total_capabilities = db.session.execute(  # tenant-filtered
            text(f"SELECT COUNT(*) FROM unified_capabilities uc WHERE 1=1 {_org_clause}"),  # nosec B608 -- only the org_scope fragment is interpolated; values are bound parameters
            _org_params
        ).scalar()
        mapped_capabilities = db.session.execute(  # tenant-filtered
            text(
                f"""
            SELECT COUNT(DISTINCT uacm.unified_capability_id)
            FROM unified_application_capability_mapping uacm
            JOIN unified_capabilities uc ON uc.id = uacm.unified_capability_id
            WHERE 1=1 {_org_clause}
        """  # nosec B608 -- only the org_scope fragment is interpolated; values are bound parameters
            ),
            _org_params
        ).scalar()

        # Get domain statistics
        domain_stats = db.session.execute(  # tenant-filtered
            text(
                f"""
            SELECT
                bd.name as domain_name,
                bd.code as domain_code,
                COUNT(uc.id) as total_capabilities,
                COUNT(uacm.unified_capability_id) as mapped_capabilities,
                COUNT(uc.id) - COUNT(uacm.unified_capability_id) as unmapped_capabilities
            FROM business_domains bd
            LEFT JOIN unified_capabilities uc ON bd.id = uc.domain_id
            LEFT JOIN unified_application_capability_mapping uacm ON uc.id = uacm.unified_capability_id
            WHERE 1=1 {_org_clause.replace('AND uc.', 'AND uc.')}
            GROUP BY bd.id, bd.name, bd.code
            ORDER BY bd.strategic_weight DESC, bd.name
        """  # nosec B608 -- only the org_scope fragment is interpolated; values are bound parameters
            ),
            _org_params
        )
        domain_stats = domain_stats.fetchall()

        # Get priority breakdown
        priority_breakdown = db.session.execute(  # tenant-filtered
            text(
                f"""
            SELECT
                uc.strategic_importance,
                COUNT(*) as count
            FROM unified_capabilities uc
            LEFT JOIN unified_application_capability_mapping uacm
                ON uc.id = uacm.unified_capability_id
            WHERE uacm.unified_capability_id IS NULL
            {_org_clause}
            GROUP BY uc.strategic_importance
            ORDER BY
                CASE uc.strategic_importance
                    WHEN 'critical' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3
                    WHEN 'low' THEN 4
                END
        """  # nosec B608 -- only the org_scope fragment is interpolated; values are bound parameters
            ),
            _org_params
        )
        priority_breakdown = priority_breakdown.fetchall()

        # Calculate coverage percentage
        mapping_coverage = (
            round((mapped_capabilities / total_capabilities * 100), 1)
            if total_capabilities > 0
            else 0
        )

        return render_template(
            "capability_analysis/unmapped_capabilities.html",
            unmapped_capabilities=unmapped_capabilities,
            total_capabilities=total_capabilities,
            mapped_capabilities=mapped_capabilities,
            unmapped_count=len(unmapped_capabilities),
            mapping_coverage=mapping_coverage,
            domain_stats=domain_stats,
            priority_breakdown=priority_breakdown,
        )

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error loading unmapped capabilities")
        flash("Error loading unmapped capabilities. Please try again.", "error")
        # None, not 0: "0 capabilities, 0% coverage" is a claim about the
        # user's portfolio, and here nothing was measured at all.
        return render_template(
            "capability_analysis/unmapped_capabilities.html",
            unmapped_capabilities=[],
            total_capabilities=None,
            mapped_capabilities=None,
            unmapped_count=None,
            mapping_coverage=None,
            domain_stats=[],
            priority_breakdown=[],
            load_error="Capability mapping coverage could not be calculated.",
        )


@main.route("/api/capability-analysis/unmapped/export")
@login_required
def export_unmapped_capabilities():
    """Export unmapped capabilities as JSON"""

    try:
        # Unmapped = capabilities with no application mapping.
        # unified_capabilities rows carry organization_id and scope; only
        # scope='reference' rows with no organisation are shared.
        _org_clause, _org_params = _capability_org_scope()
        unmapped_result = db.session.execute(
            text(
                f"""
            SELECT uc.name, uc.description, uc.strategic_importance,
                   uc.current_maturity_level, uc.target_maturity_level, uc.status,
                   bd.name AS domain_name
            FROM unified_capabilities uc
            LEFT JOIN unified_application_capability_mapping uacm
                ON uc.id = uacm.unified_capability_id
            LEFT JOIN business_domains bd ON uc.domain_id = bd.id
            WHERE uacm.unified_capability_id IS NULL
            {_org_clause}
            ORDER BY uc.name
        """  # nosec B608 -- only the org_scope fragment is interpolated; values are bound parameters
            ),
            _org_params
        ).fetchall()

        capabilities = []
        for row in unmapped_result:
            capabilities.append(
                {
                    "name": row[0],
                    "description": row[1],
                    "strategic_importance": row[2],
                    "current_maturity_level": row[3],
                    "target_maturity_level": row[4],
                    "status": row[5],
                    "domain_name": row[6],
                }
            )

        return jsonify(
            {
                "capabilities": capabilities,
                "total_count": len(capabilities),
                "export_date": db.session.execute(text("SELECT CURRENT_TIMESTAMP")).scalar(),  # tenant-exempt: system function
            }
        )

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500

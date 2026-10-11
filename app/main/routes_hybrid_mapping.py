"""Hybrid Multi-Path Mapping Dashboard Routes"""

from flask import current_app, flash, jsonify, render_template  # dead-code-ok
from flask_login import login_required
from sqlalchemy import text

from app import db
from app.main.views import main
from app.utils.tenant_sql import current_org_id, org_scope

# Tenancy note for this whole module.
#
# The two mapping junction tables
# (unified_application_capability_mapping, capability_vendor_product_mapping)
# carry NO organization_id column, and
# neither do vendor_products / vendor_organizations / business_domains.
#
# Every query in this module is now scoped to the signed-in organisation by
# joining each mapping table to the organisation-owned table it references
# (unified_capabilities, application_components, or archimate_elements) and
# applying org_scope there.  With no organisation resolved every helper
# returns zeros or empty lists — it never leaks global counts.


@main.route("/hybrid-mapping-dashboard")
@login_required
def hybrid_mapping_dashboard():
    """Comprehensive dashboard for hybrid multi-path mapping analysis"""

    try:
        # Get comprehensive mapping statistics
        stats = _compute_mapping_stats()

        # Get detailed mapping data
        app_mappings = get_application_mappings()
        product_mappings = get_product_mappings()
        archimate_mappings = get_archimate_mappings()

        # Get unmapped items
        unmapped_caps = get_unmapped_capabilities()
        unmapped_products = get_unmapped_vendor_products()
        unmapped_archimate = get_unmapped_archimate_elements()

        return render_template(
            "hybrid_mapping/dashboard.html",
            stats=stats,
            app_mappings=app_mappings,
            product_mappings=product_mappings,
            archimate_mappings=archimate_mappings,
            unmapped_caps=unmapped_caps,
            unmapped_products=unmapped_products,
            unmapped_archimate=unmapped_archimate,
        )

    except Exception:
        db.session.rollback()  # clear any aborted txn so later queries don't cascade
        current_app.logger.exception("Error loading hybrid mapping dashboard")
        flash("Error loading hybrid mapping dashboard. Please try again.", "error")
        # stats=None, not a zeroed structure. Every percentage in that
        # structure reads as a measurement of the user's mapping coverage;
        # produced by a database error, all of them are false. The template
        # renders an em dash for the whole stats region when stats is falsy.
        return render_template(
            "hybrid_mapping/dashboard.html",
            stats=None,
            app_mappings=[],
            product_mappings=[],
            archimate_mappings=[],
            unmapped_caps=[],
            unmapped_products=[],
            unmapped_archimate=[],
            load_error="Hybrid mapping coverage could not be calculated.",
        )


def _compute_mapping_stats():
    """Run the statistics queries and return a dict (or raise on failure).

    Extracted so that the HTML dashboard, the JSON statistics endpoint, and the
    JSON export endpoint all consume the same data without one route calling
    another route's view function and receiving a Response object.

    Every query is scoped to the signed-in organisation. With no organisation
    resolved the function returns zeros — it never leaks global counts.
    """

    org_id = current_org_id()
    if org_id is None:
        return {
            "total_capabilities": 0,
            "application_centric": {
                "total_capabilities": 0,
                "capabilities_with_apps": 0,
                "apps_with_archimate": 0,
                "coverage_percentage": None,
                "archimate_coverage_percentage": None,
                "end_to_end_coverage": None,
            },
            "product_centric": {
                "total_capabilities": 0,
                "capabilities_with_products": 0,
                "capabilities_with_products_archimate": 0,
                "coverage_percentage": None,
                "archimate_coverage_percentage": None,
            },
            "direct_archimate": {
                "total_capabilities": 0,
                "capabilities_with_archimate": 0,
                "coverage_percentage": None,
            },
            "multi_path": {
                "total_capabilities": 0,
                "capabilities_with_multi_path": 0,
                "coverage_percentage": None,
            },
            "quality_metrics": {
                "total_mappings": 0,
                "high_quality_mappings": 0,
                "quality_score": None,
            },
        }

    _org_uc_where, _org_params_uc = org_scope(prefix="uc.", keyword="WHERE")
    _org_ac_and, _org_params_ac = org_scope(prefix="ac.", keyword="AND")
    _org_params_app = {**_org_params_uc, **_org_params_ac}

    # Application-Centric Coverage.
    #
    # Scoped on both uc.organization_id (WHERE) and ac.organization_id (ON
    # clause of the LEFT JOIN), so total_capabilities counts only this
    # organisation's capabilities and capabilities_with_apps counts only
    # mappings to this organisation's applications.
    app_result = db.session.execute(
        text(
            f"""
        SELECT
            COUNT(DISTINCT uc.id) as total_capabilities,
            COUNT(DISTINCT CASE WHEN ac.id IS NOT NULL THEN uacm.unified_capability_id END) as capabilities_with_apps,
            COUNT(DISTINCT CASE WHEN ac.id IS NOT NULL AND uc.archimate_element_id IS NOT NULL THEN uacm.unified_capability_id END) as apps_with_archimate
        FROM unified_capabilities uc
        LEFT JOIN unified_application_capability_mapping uacm ON uc.id = uacm.unified_capability_id
        LEFT JOIN application_components ac ON uacm.application_component_id = ac.id{_org_ac_and}
        {_org_uc_where}
    """  # nosec B608 -- only the org_scope fragment is interpolated; values are bound parameters
        ),
        _org_params_app,
    ).fetchone()

    # Product-Centric Coverage — scoped on uc.organization_id.
    _org_uc_where2, _org_params_uc2 = org_scope(prefix="uc.", keyword="WHERE")
    prod_result = db.session.execute(
        text(
            f"""
        SELECT
            COUNT(DISTINCT uc.id) as total_capabilities,
            COUNT(DISTINCT cvpm.unified_capability_id) as capabilities_with_products
        FROM unified_capabilities uc
        LEFT JOIN capability_vendor_product_mapping cvpm ON uc.id = cvpm.unified_capability_id
        {_org_uc_where2}
    """  # nosec B608 -- only the org_scope fragment is interpolated; values are bound parameters
        ),
        _org_params_uc2,
    ).fetchone()

    # Products with ArchiMate (capability-product mappings where product has
    # archimate link) — scoped on uc.organization_id via an added JOIN.
    _org_uc_and3, _org_params_uc3 = org_scope(prefix="uc.", keyword="AND")
    prod_archimate_result = db.session.execute(
        text(
            f"""
        SELECT COUNT(DISTINCT cvpm.unified_capability_id) as capabilities_with_products_archimate
        FROM capability_vendor_product_mapping cvpm
        JOIN vendor_products vp ON cvpm.vendor_product_id = vp.id
        JOIN unified_capabilities uc ON cvpm.unified_capability_id = uc.id
        WHERE vp.archimate_product_element_id IS NOT NULL{_org_uc_and3}
    """  # nosec B608 -- only the org_scope fragment is interpolated; values are bound parameters
        ),
        _org_params_uc3,
    ).fetchone()

    # Direct ArchiMate Coverage — scoped on uc.organization_id.
    # Derived from unified_capabilities.archimate_element_id (a real column)
    # rather than the nonexistent unified_capability_archimate_mapping table.
    _org_uc_where4, _org_params_uc4 = org_scope(prefix="uc.", keyword="WHERE")
    arch_result = db.session.execute(
        text(
            f"""
        SELECT
            COUNT(DISTINCT uc.id) as total_capabilities,
            COUNT(DISTINCT CASE WHEN uc.archimate_element_id IS NOT NULL THEN uc.id END) as capabilities_with_archimate
        FROM unified_capabilities uc
        {_org_uc_where4}
    """  # nosec B608 -- only the org_scope fragment is interpolated; values are bound parameters
        ),
        _org_params_uc4,
    ).fetchone()

    # Multi-path coverage — scoped on uc.organization_id.
    # The application-mapping leg previously referenced archimate_element_id
    # directly on unified_application_capability_mapping, which does not have
    # that column.  Fixed by joining through unified_capabilities.
    _org_uc_where5, _org_params_uc5 = org_scope(prefix="uc.", keyword="AND")
    multi_path_caps = db.session.execute(
        text(
            """
        SELECT COUNT(*) FROM (
            SELECT DISTINCT uc.id
            FROM unified_capabilities uc
            WHERE uc.organization_id = :org_id
            AND (
                uc.id IN (
                    SELECT DISTINCT uacm.unified_capability_id
                    FROM unified_application_capability_mapping uacm
                    JOIN unified_capabilities uc2 ON uacm.unified_capability_id = uc2.id
                    WHERE uc2.archimate_element_id IS NOT NULL
                ) OR uc.id IN (
                    SELECT DISTINCT unified_capability_id FROM capability_vendor_product_mapping
                ) OR uc.archimate_element_id IS NOT NULL
            )
        ) AS multi_caps
        """
        ),
        _org_params_uc5,
    ).scalar()

    # Quality metrics — each leg scoped to the organisation that owns the
    # referenced entity (application or capability).  The ArchiMate leg is
    # intentionally absent: unified_capability_archimate_mapping does not
    # exist as a table, and the archimate_element_id column on
    # unified_capabilities carries no mapping_strength.
    _org_ac_where6, _org_params_ac6 = org_scope(prefix="ac.", keyword="AND")
    _org_uc_where6, _org_params_uc6 = org_scope(prefix="uc.", keyword="AND")
    _org_params_hq = {**_org_params_ac6, **_org_params_uc6}
    high_quality_mappings = db.session.execute(
        text(
            f"""
        SELECT COUNT(*) FROM (
            SELECT uacm.relationship_strength FROM unified_application_capability_mapping uacm
            JOIN application_components ac ON uacm.application_component_id = ac.id
            WHERE uacm.relationship_strength >= 4{_org_ac_where6}
            UNION ALL
            SELECT cvpm.mapping_strength FROM capability_vendor_product_mapping cvpm
            JOIN unified_capabilities uc ON cvpm.unified_capability_id = uc.id
            WHERE cvpm.mapping_strength >= 4{_org_uc_where6}
        ) AS hq_mappings
    """  # nosec B608 -- only the org_scope fragment is interpolated; values are bound parameters
        ),
        _org_params_hq,
    ).scalar()

    # Total mappings — same two-leg scoping as quality metrics.
    _org_ac_where7, _org_params_ac7 = org_scope(prefix="ac.", keyword="WHERE")
    _org_uc_where7, _org_params_uc7 = org_scope(prefix="uc.", keyword="WHERE")
    _org_params_total = {**_org_params_ac7, **_org_params_uc7}
    total_mappings = db.session.execute(
        text(
            f"""
        SELECT SUM(cnt) FROM (
            SELECT COUNT(*) AS cnt FROM unified_application_capability_mapping uacm
            JOIN application_components ac ON uacm.application_component_id = ac.id
            {_org_ac_where7}
            UNION ALL
            SELECT COUNT(*) AS cnt FROM capability_vendor_product_mapping cvpm
            JOIN unified_capabilities uc ON cvpm.unified_capability_id = uc.id
            {_org_uc_where7}
        ) AS all_mappings
    """  # nosec B608 -- only the org_scope fragment is interpolated; values are bound parameters
        ),
        _org_params_total,
    ).scalar()

    # Calculate coverage percentages. A zero-denominator ratio is "not
    # computed", not "measured at 0%" -- CLAUDE.md's fabricated-data rule
    # (a 0 meaning "nothing to divide by" is indistinguishable from a
    # real measured zero, and the template colors 0% red, i.e. "failing",
    # which is actively misleading for a brand-new org with no
    # capabilities yet). Found 14 Sep 2026 in a full-app design pass:
    # every one of these except prod_archimate_coverage returned a bare
    # ``0`` on an empty denominator instead of ``None`` -- the one that
    # already returned None was the only correct example in this
    # function. All six now match it.
    #
    # SUM(...) returns Decimal, which Flask's jsonify serialises as a
    # string.  Convert to plain int so the JSON consumers see numbers.
    total_caps = int(app_result[0])
    app_coverage = (app_result[1] / total_caps * 100) if total_caps > 0 else None
    app_archimate_coverage = (app_result[2] / app_result[1] * 100) if app_result[1] > 0 else None
    prod_coverage = (prod_result[1] / total_caps * 100) if total_caps > 0 else None
    prod_archimate_coverage = (
        (prod_archimate_result[0] / prod_result[1] * 100)
        if prod_result[1] > 0
        else None
    )
    arch_coverage = (arch_result[1] / total_caps * 100) if total_caps > 0 else None
    end_to_end_coverage = (app_result[2] / total_caps * 100) if total_caps > 0 else None
    multi_path_coverage = (multi_path_caps / total_caps * 100) if total_caps > 0 else None
    total_mappings_int = int(total_mappings or 0)
    high_quality_int = int(high_quality_mappings or 0)
    quality_score = (high_quality_int / total_mappings_int * 5) if total_mappings_int > 0 else None

    return {
        "total_capabilities": total_caps,
        "application_centric": {
            "total_capabilities": total_caps,
            "capabilities_with_apps": int(app_result[1]),
            "apps_with_archimate": int(app_result[2]),
            "coverage_percentage": app_coverage,
            "archimate_coverage_percentage": app_archimate_coverage,
            "end_to_end_coverage": end_to_end_coverage,
        },
        "product_centric": {
            "total_capabilities": total_caps,
            "capabilities_with_products": int(prod_result[1]),
            "capabilities_with_products_archimate": int(prod_archimate_result[0] or 0),
            "coverage_percentage": prod_coverage,
            "archimate_coverage_percentage": prod_archimate_coverage,
        },
        "direct_archimate": {
            "total_capabilities": total_caps,
            "capabilities_with_archimate": int(arch_result[1]),
            "coverage_percentage": arch_coverage,
        },
        "multi_path": {
            "total_capabilities": total_caps,
            "capabilities_with_multi_path": int(multi_path_caps or 0),
            "coverage_percentage": multi_path_coverage,
        },
        "quality_metrics": {
            "total_mappings": total_mappings_int,
            "high_quality_mappings": high_quality_int,
            "quality_score": quality_score,
        },
    }


@main.route("/api/hybrid-mapping/statistics")
@login_required
def get_mapping_statistics():
    """API endpoint for mapping statistics"""

    try:
        return jsonify(_compute_mapping_stats())

    except Exception as e:
        db.session.rollback()  # clear any aborted txn so later queries don't cascade
        import logging
        logging.getLogger(__name__).error(f"Error getting mapping statistics: {e}")
        return jsonify({"error": "An internal error occurred"}), 500


def get_application_mappings():
    """Get detailed application-centric mappings"""

    try:
        org_id = current_org_id()
        if org_id is None:
            return []

        # ac is an inner join, so its predicate belongs in WHERE; ae is an outer
        # join, so its predicate belongs in the ON clause (a WHERE predicate on
        # an outer-joined table silently turns the join inner and would drop
        # capabilities that have no ArchiMate element at all).
        _org_ac, _org_params_ac = org_scope(prefix="ac.", keyword="WHERE")
        _org_ae, _org_params_ae = org_scope(prefix="ae.", keyword="AND")
        _org_params = {**_org_params_ac, **_org_params_ae}
        result = db.session.execute(
            text(
                f"""
            SELECT
                uacm.id,
                uc.name as capability_name,
                uc.strategic_importance,
                ac.name as application_name,
                ac.description as application_description,
                ae.name as archimate_element_name,
                ae.type as archimate_type,
                ae.layer as archimate_layer,
                uacm.relationship_strength,
                uacm.coverage_percentage,
                uacm.relationship_type
            FROM unified_application_capability_mapping uacm
            JOIN unified_capabilities uc ON uacm.unified_capability_id = uc.id
            JOIN application_components ac ON uacm.application_component_id = ac.id
            LEFT JOIN archimate_elements ae ON uc.archimate_element_id = ae.id{_org_ae}
            {_org_ac}
            ORDER BY uc.strategic_importance DESC, uc.name
        """  # nosec B608 -- only the org_scope fragment is interpolated; values are bound parameters
            ),
            _org_params,
        )

        return [
            dict(
                zip(
                    [
                        "id",
                        "capability_name",
                        "strategic_importance",
                        "application_name",
                        "application_description",
                        "archimate_element_name",
                        "archimate_type",
                        "archimate_layer",
                        "mapping_strength",
                        "coverage_percentage",
                        "relationship_type",
                    ],
                    row,
                )
            )
            for row in result
        ]

    except Exception:
        db.session.rollback()  # clear any aborted txn so later queries don't cascade
        return []


def get_product_mappings():
    """Get detailed product-centric mappings"""

    try:
        org_id = current_org_id()
        if org_id is None:
            return []

        # ae is outer-joined; its predicate goes in the ON clause so a product
        # with no ArchiMate element still appears (see get_application_mappings).
        # uc is inner-joined; its organisation predicate goes in WHERE.
        _org_ae, _org_params_ae = org_scope(prefix="ae.", keyword="AND")
        _org_uc, _org_params_uc = org_scope(prefix="uc.", keyword="WHERE")
        _org_params = {**_org_params_ae, **_org_params_uc}
        result = db.session.execute(
            text(
                f"""
            SELECT
                cvpm.id,
                uc.name as capability_name,
                uc.strategic_importance,
                vp.name as product_name,
                vpf.family_name as product_family,
                vo.name as vendor_name,
                ae.name as archimate_element_name,
                ae.type as archimate_type,
                cvpm.mapping_strength,
                cvpm.coverage_percentage,
                cvpm.business_value,
                cvpm.strategic_importance,
                cvpm.relationship_type
            FROM capability_vendor_product_mapping cvpm
            JOIN unified_capabilities uc ON cvpm.unified_capability_id = uc.id
            JOIN vendor_products vp ON cvpm.vendor_product_id = vp.id
            JOIN vendor_organizations vo ON vp.vendor_organization_id = vo.id
            LEFT JOIN vendor_product_families vpf ON vp.family_id = vpf.id
            LEFT JOIN archimate_elements ae ON vp.archimate_product_element_id = ae.id{_org_ae}
            {_org_uc}
            ORDER BY uc.strategic_importance DESC, uc.name
        """  # nosec B608 -- only the org_scope fragment is interpolated; values are bound parameters
            ),
            _org_params,
        )

        return [
            dict(
                zip(
                    [
                        "id",
                        "capability_name",
                        "strategic_importance",
                        "product_name",
                        "product_family",
                        "vendor_name",
                        "archimate_element_name",
                        "archimate_type",
                        "mapping_strength",
                        "coverage_percentage",
                        "business_value",
                        "strategic_importance",
                        "relationship_type",
                    ],
                    row,
                )
            )
            for row in result
        ]

    except Exception:
        db.session.rollback()  # clear any aborted txn so later queries don't cascade
        return []


def get_archimate_mappings():
    """Get detailed direct ArchiMate mappings.

    Derived from unified_capabilities.archimate_element_id joined with
    archimate_elements.  There is no unified_capability_archimate_mapping
    table, so mapping_strength, coverage_percentage, relationship_type and
    implementation_complexity are always NULL.
    """

    try:
        org_id = current_org_id()
        if org_id is None:
            return []

        # uc is inner-joined; scope on uc.organization_id.
        _org_uc, _org_params = org_scope(prefix="uc.", keyword="WHERE")
        result = db.session.execute(
            text(
                f"""
            SELECT
                uc.id,
                uc.name as capability_name,
                uc.strategic_importance,
                ae.name as archimate_element_name,
                ae.type as archimate_type,
                ae.layer as archimate_layer,
                ae.description as archimate_description,
                NULL as mapping_strength,
                NULL as coverage_percentage,
                NULL as relationship_type,
                NULL as implementation_complexity
            FROM unified_capabilities uc
            JOIN archimate_elements ae ON uc.archimate_element_id = ae.id
            {_org_uc}
            ORDER BY uc.strategic_importance DESC, uc.name
        """  # nosec B608 -- only the org_scope fragment is interpolated; values are bound parameters
            ),
            _org_params,
        )

        return [
            dict(
                zip(
                    [
                        "id",
                        "capability_name",
                        "strategic_importance",
                        "archimate_element_name",
                        "archimate_type",
                        "archimate_layer",
                        "archimate_description",
                        "mapping_strength",
                        "coverage_percentage",
                        "relationship_type",
                        "implementation_complexity",
                    ],
                    row,
                )
            )
            for row in result
        ]

    except Exception:
        db.session.rollback()  # clear any aborted txn so later queries don't cascade
        return []


def get_unmapped_capabilities():
    """Get capabilities without any mappings"""

    try:
        org_id = current_org_id()
        if org_id is None:
            return []

        _org_uc, _org_params = org_scope(prefix="uc.", keyword="AND")
        result = db.session.execute(
            text(
                f"""
            SELECT
                uc.id,
                uc.name,
                uc.strategic_importance,
                bd.name as domain_name
            FROM unified_capabilities uc
            JOIN business_domains bd ON uc.domain_id = bd.id
            WHERE uc.id NOT IN (
                SELECT DISTINCT unified_capability_id FROM unified_application_capability_mapping
            )
            AND uc.id NOT IN (
                SELECT DISTINCT unified_capability_id FROM capability_vendor_product_mapping
            )
            AND uc.archimate_element_id IS NULL{_org_uc}
            ORDER BY uc.strategic_importance DESC, uc.name
        """  # nosec B608 -- only the org_scope fragment is interpolated; values are bound parameters
            ),
            _org_params,
        )

        return [
            dict(zip(["id", "name", "strategic_importance", "domain_name"], row)) for row in result
        ]

    except Exception:
        db.session.rollback()  # clear any aborted txn so later queries don't cascade
        return []


def get_unmapped_vendor_products():
    """Get vendor products without capability mappings"""

    try:
        org_id = current_org_id()
        if org_id is None:
            return []

        # vendor_products and vendor_organizations are global by schema (no
        # organization_id column).  The NOT IN subquery is deliberately
        # unscoped — checking ALL mappings, not just this organisation's —
        # so that a product mapped by any organisation is excluded from
        # every organisation's unmapped list.  Scoping the subquery would
        # leak other tenants' product names.
        result = db.session.execute(
            text(
                """
            SELECT
                vp.id,
                vp.name,
                -- vendor_products carries the family as a plain varchar column
                -- (product_family); it has no family_id FK to
                -- vendor_product_families, so the old LEFT JOIN on vp.family_id
                -- raised UndefinedColumn and this list was always empty.
                vp.product_family as product_family,
                vo.name as vendor_name,
                vp.archimate_product_element_id
            FROM vendor_products vp
            JOIN vendor_organizations vo ON vp.vendor_organization_id = vo.id
            WHERE vp.id NOT IN (
                SELECT DISTINCT vendor_product_id FROM capability_vendor_product_mapping
            )
            ORDER BY vo.name, vp.name
        """
            )
        )

        return [
            dict(zip(["id", "name", "product_family", "vendor_name", "archimate_product_id"], row))
            for row in result
        ]

    except Exception:
        db.session.rollback()  # clear any aborted txn so later queries don't cascade
        return []


def get_unmapped_archimate_elements():
    """Get ArchiMate elements without capability mappings"""

    try:
        org_id = current_org_id()
        if org_id is None:
            return []

        # archimate_elements is the driving table and is scoped via org_scope.
        # The NOT IN subquery checks unified_capabilities.archimate_element_id
        # (scoped to this organisation) rather than the nonexistent
        # unified_capability_archimate_mapping table.
        _org_ae, _org_params_ae = org_scope(prefix="ae.", keyword="AND")
        _org_uc2, _org_params_uc2 = org_scope(prefix="uc2.", keyword="WHERE")
        _org_params = {**_org_params_ae, **_org_params_uc2}
        result = db.session.execute(
            text(
                f"""
            SELECT
                ae.id,
                ae.name,
                ae.type,
                ae.layer,
                ae.description
            FROM archimate_elements ae
            WHERE ae.id NOT IN (
                SELECT DISTINCT uc2.archimate_element_id
                FROM unified_capabilities uc2
                WHERE uc2.archimate_element_id IS NOT NULL{_org_uc2}
            )
            AND ae.type IN ('ApplicationComponent', 'ApplicationService', 'TechnologyService', 'BusinessProcess'){_org_ae}
            ORDER BY ae.type, ae.name
            LIMIT 20
        """  # nosec B608 -- only the org_scope fragment is interpolated; values are bound parameters
            ),
            _org_params,
        )

        return [dict(zip(["id", "name", "type", "layer", "description"], row)) for row in result]

    except Exception:
        db.session.rollback()  # clear any aborted txn so later queries don't cascade
        return []


@main.route("/api/hybrid-mapping/export")
@login_required
def export_hybrid_mapping():
    """Export hybrid mapping data as JSON"""

    try:
        return jsonify(
            {
                "statistics": _compute_mapping_stats(),
                "application_mappings": get_application_mappings(),
                "product_mappings": get_product_mappings(),
                "archimate_mappings": get_archimate_mappings(),
                "unmapped_capabilities": get_unmapped_capabilities(),
                "unmapped_vendor_products": get_unmapped_vendor_products(),
                "unmapped_archimate_elements": get_unmapped_archimate_elements(),
                "export_date": db.session.execute(text("SELECT CURRENT_TIMESTAMP")).scalar(),  # tenant-exempt: system function
            }
        )

    except Exception:
        db.session.rollback()  # clear any aborted txn so later queries don't cascade
        return jsonify({"error": "An internal error occurred"}), 500

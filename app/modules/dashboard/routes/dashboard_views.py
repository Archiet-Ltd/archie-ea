"""
Dashboard Views (migrated).

Overview dashboards, operations dashboards, and admin dashboard clone API endpoints.

Migrated from: app/dashboard/views.py
Blueprint name: "dashboard" (exact match)
URL prefix: /dashboard (applied via register() in __init__.py, NOT baked in here)

Endpoints (17 routes — url_prefix="/dashboard" applied by __init__.py):
- /                          -> index (redirect to executive dashboard)
- /overview                  -> overview
- /api/overview/chart        -> api_overview_chart
- /api/overview/table        -> api_overview_table
- /api/operations/chart      -> api_operations_chart
- /api/operations/table      -> api_operations_table
- /api/colvis                -> api_colvis
- /api/colorder              -> api_colorder
- /api/sort                  -> api_sort
- /api/edit                  -> api_edit
- /api/filters               -> api_filters
- /api/tab                   -> api_tab
- /api/duplicate             -> api_duplicate
- /api/delete                -> api_delete
- /api/bulk-delete           -> api_bulk_delete
- /api/table/<table_name>    -> api_table_get
"""

from flask import (
    Blueprint,
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required

from app import db
from app.decorators import audit_log

dashboard_bp = Blueprint("dashboard", __name__, template_folder="templates")


@dashboard_bp.route("/")
@login_required
def index():
    """Dashboard index - redirects to overview landing page."""
    return redirect(url_for("dashboard.overview"))


def _build_overview_context():
    from app.config.navigation_registry import get_navigation_sections
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.user import User
    from app.models.vendor.vendor_organization import VendorOrganization

    metrics = {
        "applications": db.session.query(db.func.count(ApplicationComponent.id)).scalar() or 0,
        "vendors": db.session.query(db.func.count(VendorOrganization.id)).scalar() or 0,
        "users": (
            db.session.query(db.func.count(User.id))
            .filter(User.organization_id == g.current_org_id)
            .scalar()
            or 0
        ),
        "active_sessions": 0,
    }

    nav_stats = {
        "elements": 0,
        "consolidation": 0,
        "solutions": 0,
        "capabilities": 0,
    }
    try:
        from app.models.archimate_core import ArchiMateElement

        nav_stats["elements"] = (
            db.session.query(db.func.count(ArchiMateElement.id)).scalar() or 0
        )
    except Exception:
        db.session.rollback()
    try:
        from app.models.consolidation_list import ConsolidationListEntry

        nav_stats["consolidation"] = (
            db.session.query(db.func.count(ConsolidationListEntry.id)).scalar() or 0
        )
    except Exception:
        db.session.rollback()
    try:
        from app.models.solution_models import Solution

        nav_stats["solutions"] = db.session.query(db.func.count(Solution.id)).scalar() or 0
    except Exception:
        db.session.rollback()
    try:
        nav_stats["capabilities"] = (
            db.session.query(db.func.count(BusinessCapability.id)).scalar() or 0
        )
    except Exception:
        db.session.rollback()

    applications = ApplicationComponent.query.order_by(ApplicationComponent.name).limit(10).all()
    vendors = VendorOrganization.query.order_by(VendorOrganization.name).limit(10).all()
    feature_sections = [
        {
            **section,
            "items": [
                item
                for item in section.get("items", [])
                if not item.get("disabled") and item.get("url") and item.get("url") != "#"
            ],
        }
        for section in get_navigation_sections(
            current_endpoint=request.endpoint,
            view_args=request.view_args,
            applications=applications,
            vendors=vendors,
        )
    ]
    feature_sections = [section for section in feature_sections if section["items"]]

    # Data coverage — raw SQL avoids ORM-level tenant filtering and version quirks
    total_apps = metrics["applications"] or 1  # avoid division by zero
    data_coverage = {"owner": 0, "vendor": 0, "cost": 0, "risk": 0, "criticality": 0}
    try:
        from sqlalchemy import text
        # Scope to the caller's org so coverage matches the (org-scoped) app count
        # and never aggregates other tenants' data. No-op in system contexts.
        _org = getattr(g, "current_org_id", None)
        _cov_where = "WHERE organization_id = :org" if _org is not None else ""
        row = db.session.execute(text(f"""
            SELECT
                COUNT(*) FILTER (WHERE application_owner IS NOT NULL OR business_owner IS NOT NULL) AS owner_n,
                COUNT(*) FILTER (WHERE vendor_product_id IS NOT NULL OR vendor_name IS NOT NULL) AS vendor_n,
                COUNT(*) FILTER (WHERE total_cost_of_ownership IS NOT NULL OR license_cost IS NOT NULL OR maintenance_cost IS NOT NULL) AS cost_n,
                COUNT(*) FILTER (WHERE business_criticality IS NOT NULL) AS crit_n
            FROM application_components
            {_cov_where}
        """), ({"org": _org} if _org is not None else {})).fetchone()
        data_coverage["owner"] = round(row[0] / total_apps * 100)
        data_coverage["vendor"] = round(row[1] / total_apps * 100)
        data_coverage["cost"] = round(row[2] / total_apps * 100)
        data_coverage["criticality"] = round(row[3] / total_apps * 100)
        if _org is not None:
            # An owner is recorded as an ApplicationOwner row; the owner text
            # columns counted above only hold a name that matched no user.
            from app.models.application_portfolio import ApplicationComponent
            from app.services.element_ownership_service import application_is_owned_expression

            owned = db.session.execute(
                db.select(db.func.count(ApplicationComponent.id))
                .where(ApplicationComponent.organization_id == _org)
                .where(application_is_owned_expression(ApplicationComponent))
            ).scalar_one()
            data_coverage["owner"] = round(owned / total_apps * 100)
    except Exception as e:
        db.session.rollback()
        data_coverage["query_error"] = str(e)

    # PROG-019: AI-governance alerts strip — cheap read of the latest EA briefing
    # only. Lives HERE (the handler that actually serves /dashboard/overview);
    # the v2 module carries the same context for its own overview variant.
    governance_alerts = None
    try:
        from app.models.strategic import EnterpriseBriefing
        briefing = (
            EnterpriseBriefing.query
            .order_by(EnterpriseBriefing.generated_at.desc(), EnterpriseBriefing.id.desc())
            .first()
        )
        if briefing is not None:
            governance_alerts = {
                "briefing_flagged": briefing.flagged_count or 0,
                "briefing_findings": briefing.finding_count or 0,
                "generated_at": briefing.generated_at.isoformat() if briefing.generated_at else None,
                "headline": briefing.headline,
            }
    except Exception:
        db.session.rollback()

    return {
        "metrics": metrics,
        "nav_stats": nav_stats,
        "feature_sections": feature_sections,
        "persona_metrics": None,
        "data_coverage": data_coverage,
        "governance_alerts": governance_alerts,
    }


@dashboard_bp.route("/overview")
@login_required
def overview():
    return render_template("dashboards/overview.html", **_build_overview_context())


@dashboard_bp.route("/api/overview/chart")
@login_required
def api_overview_chart():
    return jsonify(
        {
            "success": False,
            "labels": [],
            "datasets": [],
            "message": "No live overview chart data is configured.",
        }
    )


@dashboard_bp.route("/api/coverage-debug")
@login_required
def api_coverage_debug():
    """Temp debug: returns raw coverage query result or error."""
    try:
        from sqlalchemy import text
        # Raw SQL bypasses the tenant listener, so these coverage counts spanned
        # every organisation's portfolio.
        from flask import g as _g
        _org = getattr(_g, "current_org_id", None)
        _org_where = " WHERE organization_id = :org" if _org is not None else ""
        row = db.session.execute(text(f"""
            SELECT
                COUNT(*) FILTER (WHERE application_owner IS NOT NULL OR business_owner IS NOT NULL) AS owner_n,
                COUNT(*) FILTER (WHERE vendor_product_id IS NOT NULL OR vendor_name IS NOT NULL) AS vendor_n,
                COUNT(*) FILTER (WHERE total_cost_of_ownership IS NOT NULL OR license_cost IS NOT NULL OR maintenance_cost IS NOT NULL) AS cost_n,
                COUNT(*) FILTER (WHERE business_criticality IS NOT NULL) AS crit_n,
                COUNT(*) AS total_n
            FROM application_components{_org_where}
        """), ({"org": _org} if _org is not None else {})).fetchone()
        return jsonify({"ok": True, "owner": row[0], "vendor": row[1], "cost": row[2], "crit": row[3], "total": row[4]})
    except Exception:
        db.session.rollback()
        # 500, not 200. The body already said ok:false, but the STATUS said
        # fine, so the client's !response.ok never fired and the counts stayed
        # at whatever they were. str(e) is dropped with it: a raw exception
        # message can carry SQL and column names to the browser.
        current_app.logger.exception("dashboard coverage counts query failed")
        return jsonify({"ok": False, "error": "Could not load coverage counts"}), 500


@dashboard_bp.route("/api/overview/table")
@login_required
def api_overview_table():
    return jsonify({"rows": []})


@dashboard_bp.route("/api/operations/chart")
@login_required
def api_operations_chart():
    return jsonify(
        {
            "success": False,
            "labels": [],
            "datasets": [],
            "message": "No live operations chart data is configured.",
        }
    )


@dashboard_bp.route("/api/operations/table")
@login_required
def api_operations_table():
    return jsonify({"rows": []})


# Removed duplicate admin-dashboard-clone route - use /admin/ instead


def _pref_key(pref_name: str) -> str:
    """Return a per-user session key for a dashboard preference."""
    uid = getattr(current_user, "id", "anon")
    return f"dashboard_pref_{uid}_{pref_name}"


# API endpoints for admin dashboard clone interactive features
@dashboard_bp.route("/api/colvis", methods=["GET", "POST"])
@login_required
@audit_log("update_column_visibility")
def api_colvis():
    """Save/get column visibility preferences (session-backed)."""
    key = _pref_key("colvis")
    if request.method == "POST":
        data = request.get_json() or {}
        session[key] = data.get("colvis", data)
        session.modified = True
        return jsonify({"success": True})
    return jsonify({"success": True, "colvis": session.get(key, {})})


@dashboard_bp.route("/api/colorder", methods=["GET", "POST"])
@login_required
@audit_log("update_column_order")
def api_colorder():
    """Save/get column order preferences (session-backed)."""
    key = _pref_key("colorder")
    if request.method == "POST":
        data = request.get_json() or {}
        session[key] = data.get("order", data)
        session.modified = True
        return jsonify({"success": True})
    return jsonify({"success": True, "order": session.get(key, [])})


@dashboard_bp.route("/api/sort", methods=["GET", "POST"])
@login_required
@audit_log("update_sort_preference")
def api_sort():
    """Save/get sort preferences (session-backed)."""
    key = _pref_key("sort")
    if request.method == "POST":
        data = request.get_json() or {}
        session[key] = data.get("sort", data)
        session.modified = True
        return jsonify({"success": True})
    return jsonify({"success": True, "sort": session.get(key)})


@dashboard_bp.route("/api/edit", methods=["POST"])
@login_required
@audit_log("dashboard_edit")
def api_edit():
    """Cell edit — requires model-specific wiring; not yet implemented."""
    return jsonify({
        "success": False,
        "error": "Inline cell edit is not yet wired to a data model. Use the dedicated edit form.",
    }), 501


@dashboard_bp.route("/api/filters", methods=["GET", "POST"])
@login_required
@audit_log("update_filters")
def api_filters():
    """Save/get filter preferences (session-backed)."""
    key = _pref_key("filters")
    if request.method == "POST":
        data = request.get_json() or {}
        session[key] = data.get("filters", data)
        session.modified = True
        return jsonify({"success": True})
    return jsonify({"success": True, "filters": session.get(key, {})})


@dashboard_bp.route("/api/tab", methods=["POST", "GET"])
@login_required
@audit_log("update_tab")
def api_tab():
    """Save/load tab preferences (session-backed)."""
    key = _pref_key("tab")
    if request.method == "POST":
        data = request.get_json() or {}
        session[key] = data.get("tab", data)
        session.modified = True
        return jsonify({"success": True})
    return jsonify({"success": True, "tab": session.get(key, "outline")})


@dashboard_bp.route("/api/duplicate", methods=["POST"])
@login_required
@audit_log("dashboard_duplicate")
def api_duplicate():
    """Row duplication — requires model-specific wiring; not yet implemented."""
    return jsonify({
        "success": False,
        "error": "Row duplication is not yet wired to a data model. Use the dedicated create form.",
    }), 501


@dashboard_bp.route("/api/delete", methods=["POST"])
@login_required
@audit_log("dashboard_delete")
def api_delete():
    """Row deletion — requires model-specific wiring; not yet implemented."""
    return jsonify({
        "success": False,
        "error": "Row deletion is not yet wired to a data model. Use the dedicated delete action.",
    }), 501


@dashboard_bp.route("/api/bulk-delete", methods=["POST"])
@login_required
@audit_log("dashboard_bulk_delete")
def api_bulk_delete():
    """Bulk row deletion — requires model-specific wiring; not yet implemented."""
    return jsonify({
        "success": False,
        "error": "Bulk delete is not yet wired to a data model.",
    }), 501


@dashboard_bp.route("/api/table/<table_name>", methods=["GET"])
@login_required
def api_table_get(table_name):
    """Get table data (generic scaffold — no model wired)."""
    return jsonify({"success": True, "edits": {}, "rows": []})

"""
Dashboard Views v2 — guardrail-enabled.

Uses the new architecture:
- @timed_route for automatic metrics collection on all endpoints
- Observability (request_id in response headers)

URL prefix preserved: /dashboard (applied via register() in v2/__init__.py)
Blueprint name: dashboard (same as v1 — no cross-module url_for refs found)

All 17 routes preserved exactly from v1 dashboard_views.py.
The dashboard_api blueprint (9 routes) is nested inside this blueprint.
URL prefix /dashboard applied via register() in v2/__init__.py — route decorators must not repeat it.
"""

import logging

from flask import Blueprint, g, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app import db
from app.core.compat import mark_blueprint_guardrailed
from app.core.decorators import timed_route
from app.decorators import audit_log

logger = logging.getLogger(__name__)

dashboard_bp_v2 = Blueprint("dashboard", __name__, template_folder="templates")
mark_blueprint_guardrailed(dashboard_bp_v2)


@dashboard_bp_v2.route("/")
@timed_route
@login_required
def index():
    """Dashboard index - redirects to overview landing page."""
    return redirect(url_for("dashboard.overview"))


@dashboard_bp_v2.route("/overview")
@timed_route
@login_required
def overview():
    """The Overview dashboard: the same page, with the organisation's own figures, for every role."""
    from app.modules.dashboard.services.overview_figures import overview_context

    return render_template("dashboards/overview.html", **overview_context(current_user, g.current_org_id))



@dashboard_bp_v2.route("/api/overview/chart")
@timed_route
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


@dashboard_bp_v2.route("/api/onboarding-complete", methods=["POST"])
@timed_route
@login_required
def api_onboarding_complete():
    """Mark onboarding complete. Never writes a role.

    ``enterprise_role`` carries permissions and is set only by an administrator
    (role page, invitation, single sign-on mapping). This route used to write
    it from the request body, so any signed-in account could make itself an
    ARB member, enterprise architect or platform admin persona. A person's own
    choice is a lens preference (``User.persona_lens``), which no permission
    check reads; either key in the body is taken as that preference only.
    """
    import datetime

    from app.models.user import User

    data = request.get_json(silent=True) or {}
    lens = data.get("persona_lens") or data.get("enterprise_role")
    if lens and lens in User.PERSONA_LENSES:
        current_user.persona_lens = lens
    current_user.onboarding_completed_at = datetime.datetime.utcnow()
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error("PLT-040: onboarding-complete failed: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500
    return jsonify({"success": True})


@dashboard_bp_v2.route("/api/welcome-dismiss", methods=["POST"])
@timed_route
@login_required
def api_welcome_dismiss():
    """Dismiss the one-line "Welcome to Entelim" banner, once per user,
    forever (shell-wave-1 Task 5). Deliberately separate from
    /api/onboarding-complete, which also rewrites enterprise_role -- this
    endpoint has exactly one side effect: setting the dismiss timestamp."""
    import datetime

    if current_user.welcome_banner_dismissed_at is None:
        current_user.welcome_banner_dismissed_at = datetime.datetime.utcnow()
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.error("welcome-dismiss failed: %s", exc)
            return jsonify({"success": False, "error": str(exc)}), 500
    return jsonify({"success": True})


@dashboard_bp_v2.route("/api/company-kind", methods=["POST"])
@timed_route
@login_required
def api_save_company_kind():
    """T-onb-03: Save the organisation's company kind (Startup, Scale-up, Enterprise).

    Stores in Organization.settings["company_context"]["kind"]. The kind decides
    which canvas and suggestions the guided steps use. Choosing nothing leaves
    'startup' as the default.
    """
    from app.models.organization import Organization

    data = request.get_json(silent=True) or {}
    kind = data.get("kind")

    # Valid kinds: startup, scaleup, enterprise, or null/"" for "Not now"
    if kind and kind not in ("startup", "scaleup", "enterprise"):
        return jsonify({"success": False, "error": "Invalid company kind"}), 400

    try:
        org = Organization.query.get(g.current_org_id)
        if not org:
            return jsonify({"success": False, "error": "Organization not found"}), 404

        # Initialize settings if needed
        if org.settings is None:
            org.settings = {}

        # Initialize company_context if needed
        if "company_context" not in org.settings:
            org.settings["company_context"] = {}

        # Save the kind (or None for "Not now")
        org.settings["company_context"]["kind"] = kind if kind else "startup"

        # Mark as modified so SQLAlchemy knows to update the JSON column
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(org, "settings")

        db.session.commit()
        logger.info("T-onb-03: company kind set to %s for org %s", kind or "startup", g.current_org_id)
    except Exception as exc:
        db.session.rollback()
        logger.error("T-onb-03: save company kind failed: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500

    return jsonify({"success": True, "kind": org.settings["company_context"]["kind"]})


@dashboard_bp_v2.route("/api/overview/table")
@timed_route
@login_required
def api_overview_table():
    return jsonify({"rows": []})


@dashboard_bp_v2.route("/api/operations/chart")
@timed_route
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


@dashboard_bp_v2.route("/api/operations/table")
@timed_route
@login_required
def api_operations_table():
    return jsonify({"rows": []})


def _pref_key(pref_name: str) -> str:
    """Return a per-user session key for a dashboard preference."""
    uid = getattr(current_user, "id", "anon")
    return f"dashboard_pref_{uid}_{pref_name}"


@dashboard_bp_v2.route("/api/colvis", methods=["GET", "POST"])
@timed_route
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


@dashboard_bp_v2.route("/api/colorder", methods=["GET", "POST"])
@timed_route
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


@dashboard_bp_v2.route("/api/sort", methods=["GET", "POST"])
@timed_route
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


@dashboard_bp_v2.route("/api/edit", methods=["POST"])
@timed_route
@login_required
@audit_log("dashboard_edit")
def api_edit():
    """Cell edit — requires model-specific wiring; not yet implemented."""
    return jsonify({
        "success": False,
        "error": "Inline cell edit is not yet wired to a data model. Use the dedicated edit form.",
    }), 501


@dashboard_bp_v2.route("/api/filters", methods=["GET", "POST"])
@timed_route
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


@dashboard_bp_v2.route("/api/tab", methods=["POST", "GET"])
@timed_route
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


@dashboard_bp_v2.route("/api/duplicate", methods=["POST"])
@timed_route
@login_required
@audit_log("dashboard_duplicate")
def api_duplicate():
    """Row duplication — requires model-specific wiring; not yet implemented."""
    return jsonify({
        "success": False,
        "error": "Row duplication is not yet wired to a data model. Use the dedicated create form.",
    }), 501


@dashboard_bp_v2.route("/api/delete", methods=["POST"])
@timed_route
@login_required
@audit_log("dashboard_delete")
def api_delete():
    """Row deletion — requires model-specific wiring; not yet implemented."""
    return jsonify({
        "success": False,
        "error": "Row deletion is not yet wired to a data model. Use the dedicated delete action.",
    }), 501


@dashboard_bp_v2.route("/api/bulk-delete", methods=["POST"])
@timed_route
@login_required
@audit_log("dashboard_bulk_delete")
def api_bulk_delete():
    """Bulk row deletion — requires model-specific wiring; not yet implemented."""
    return jsonify({
        "success": False,
        "error": "Bulk delete is not yet wired to a data model.",
    }), 501


@dashboard_bp_v2.route("/api/table/<table_name>", methods=["GET"])
@timed_route
@login_required
def api_table_get(table_name):
    """Get table data (generic scaffold — no model wired)."""
    return jsonify({"success": True, "edits": {}, "rows": []})


def _assemble_health_scorecard_metrics():
    """Assemble the Architecture Health Scorecard's real metrics: solution
    risk summary, ARB pipeline, ArchiMate coverage by layer, and ADM phase
    distribution. Factored out of health_scorecard() so the AI executive
    briefing endpoint can be handed the exact same, already-computed
    metrics dict rather than re-querying (or worse, inventing numbers)."""
    # 1. Solution risk summary by impact level
    # Was querying SolutionRisk, a different, essentially unused table from
    # the `risks` model the Risk Register and /api/risks actually read - a
    # 6 Sep 2026 QA pass caught this labeled "Live from the solution risk
    # register" while showing zero against a register with real open,
    # critical, solution-linked risks. Risk.impact is an int 1-5, not a
    # stored severity string; risk_level derives the same bucket the
    # register itself renders.
    risk_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    try:
        from app.models.risk import Risk, RiskStatus
        for r in Risk.query.filter(Risk.status == RiskStatus.OPEN).all():
            risk_counts[r.risk_level] = risk_counts.get(r.risk_level, 0) + 1
    except Exception as exc:
        logger.warning("health_scorecard: Risk unavailable: %s", exc)
        # Query failed: report unknown (em dash), not a fabricated zero-risk summary.
        risk_counts = {"critical": None, "high": None, "medium": None, "low": None}

    # 2. ARB pipeline from ARBReviewItem
    arb_pipeline = {"pending": 0, "approved": 0, "rejected": 0, "under_review": 0, "deferred": 0}
    try:
        from app.models.architecture_review_board import ARBReviewItem
        for (status,) in ARBReviewItem.query.with_entities(ARBReviewItem.status).all():
            s = (status or "pending").lower()
            if s in ("draft", "submitted"):
                arb_pipeline["pending"] += 1
            elif s == "under_review":
                arb_pipeline["under_review"] += 1
            elif s == "approved":
                arb_pipeline["approved"] += 1
            elif s == "rejected":
                arb_pipeline["rejected"] += 1
            else:
                arb_pipeline["pending"] += 1
        for (decision,) in ARBReviewItem.query.with_entities(ARBReviewItem.decision).filter(
            ARBReviewItem.decision.isnot(None)
        ).all():
            if decision and decision.lower() == "deferred":
                arb_pipeline["deferred"] += 1
    except Exception as exc:
        logger.warning("health_scorecard: ARBReviewItem unavailable: %s", exc)

    # 3. ArchiMate element count grouped by layer
    # LAYER_TYPES / LAYER_TYPE_TO_LAYER are the single system of record for
    # this mapping (ADR 0008) -- imported from archimate_viewpoint_service,
    # same as the by-layer card query above. Do not reintroduce a local copy.
    from app.services.archimate_viewpoint_service import LAYER_TYPES as _scorecard_layer_types
    from app.services.archimate_viewpoint_service import (
        LAYER_TYPE_TO_LAYER as _scorecard_type_to_layer,
    )
    archimate_by_layer = {layer: 0 for layer in _scorecard_layer_types}
    archimate_by_layer["other"] = 0
    total_archimate = 0
    try:
        from app.models.archimate_core import ArchiMateElement
        for (elem_type,) in ArchiMateElement.query.with_entities(ArchiMateElement.type).all():
            total_archimate += 1
            t = (elem_type or "").lower()
            layer = _scorecard_type_to_layer.get(t, "other")
            archimate_by_layer[layer] = archimate_by_layer.get(layer, 0) + 1
    except Exception as exc:
        logger.warning("health_scorecard: ArchiMateElement unavailable: %s", exc)

    # 4. ADM phase distribution and average maturity
    # Read through the same phase summary as the Health Score's phase maturity,
    # so the two maturity figures count the same solutions.
    from app.modules.dashboard.v2.services.solution_phase_measures import (
        average_phase_progress,
        recorded_phase_summary,
    )

    adm_distribution = {p: 0 for p in "ABCDEFGH"}
    avg_maturity = None
    total_solutions = None
    try:
        phase_summary = recorded_phase_summary()
        total_solutions = phase_summary["total"]
        adm_distribution.update(phase_summary["counts"])
        if phase_summary["unclassified"]:
            adm_distribution["Unclassified"] = phase_summary["unclassified"]
        avg_maturity = average_phase_progress(phase_summary)
    except Exception as exc:
        adm_distribution = None
        total_solutions = None
        avg_maturity = None
        logger.warning("health_scorecard: Solution unavailable: %s", exc)
        db.session.rollback()

    return {
        "risk_counts": risk_counts,
        "arb_pipeline": arb_pipeline,
        "archimate_by_layer": archimate_by_layer,
        "total_archimate": total_archimate,
        "adm_distribution": adm_distribution,
        "avg_maturity": avg_maturity,
        "total_solutions": total_solutions,
    }


@dashboard_bp_v2.route("/health")
@timed_route
@login_required
def health_scorecard():
    """Architecture Health Scorecard — real metrics from SolutionRisk, ARBReviewItem,
    ArchiMateElement and Solution ADM phase distribution."""
    from app.modules.dashboard.v2.services.executive_dashboard_service import (
        ExecutiveDashboardService,
        format_health_score,
        health_score_basis,
    )

    metrics = _assemble_health_scorecard_metrics()
    health_score = None
    unavailable = None
    try:
        health = ExecutiveDashboardService()._get_health_score()
        health_score = health["composite_score"]
        unavailable = health["unavailable_components"]
    except Exception as exc:
        logger.warning("health_scorecard: health score unavailable: %s", exc)
        db.session.rollback()
    return render_template(
        "dashboards/health.html",
        health_score_text=format_health_score(health_score),
        health_basis=health_score_basis(health_score, unavailable),
        # The chart series colour is a token, passed in as the Overview passes its own.
        chart_colour="hsl(var(--chart-1))",
        **metrics,
    )


@dashboard_bp_v2.route("/api/ai-executive-briefing", methods=["POST"])
@timed_route
@login_required
def ai_executive_briefing():
    """POST /dashboard/api/ai-executive-briefing

    Generates an AI executive briefing for the CTO from the Architecture
    Health Scorecard's own metrics: a headline, what changed, risks, and
    recommended focus areas. Advisory only — nothing here is persisted.
    """
    from app.services.feature_flag_service import FeatureFlagService

    feature_guard = FeatureFlagService.require_ai_for_route(
        FeatureFlagService.FEATURE_SUGGESTIONS,
        endpoint_name="dashboard.ai_executive_briefing",
    )
    if feature_guard:
        return feature_guard

    from app.modules.dashboard.v2.services.executive_briefing_service import (
        ExecutiveBriefingAIError,
        generate_executive_briefing,
    )

    metrics = _assemble_health_scorecard_metrics()

    try:
        briefing = generate_executive_briefing(metrics)
    except ExecutiveBriefingAIError as e:
        logger.warning("Executive briefing unparseable: %s", e)
        return jsonify({"error": f"AI executive briefing failed: {e}"}), 502
    except Exception as e:
        logger.exception("Executive briefing generation failed")
        return jsonify({"error": f"AI executive briefing failed: {e}"}), 502

    return jsonify({"briefing": briefing})

"""
Industry APQC Routes - UI Views

Provides web interface routes for managing industry-specific APQC process frameworks.

Migrated from: app/main/routes_industry_apqc.py
"""

from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.decorators import audit_log, require_roles
from app.jobs.tenant_safe_job import platform_scope
from app.middleware.tenant_decorators import platform_admin_required

from app.services.industry_apqc_service import IndustryAPQCService
from app.utils.plain_errors import render_plain_error

industry_apqc_bp = Blueprint("industry_apqc", __name__, url_prefix="/industry-apqc")


# =========================================================================
# UI VIEWS
# =========================================================================


@industry_apqc_bp.route("/")
@login_required
def industry_apqc_dashboard():
    """Every industry framework, one card each with its process counts."""
    try:
        summaries = IndustryAPQCService().framework_summaries()
        return render_template("industry_apqc/dashboard.html", summaries=summaries)
    except Exception as e:
        from app import db

        db.session.rollback()
        current_app.logger.exception("Error loading industry APQC dashboard: %s", e)
        # summaries=None, not []: an empty list says "no frameworks", which is a
        # statement about the data, not about a failed query.
        return render_template(
            "industry_apqc/dashboard.html",
            summaries=None,
            load_error="The industry frameworks could not be read.",
        )


@industry_apqc_bp.route("/framework/<industry_code>")
@login_required
def industry_apqc_framework_detail(industry_code):
    """A framework as the APQC hierarchy, with Recommendations as a tab."""
    service = IndustryAPQCService()
    framework = service.get_framework_by_code(industry_code)
    if not framework:
        return render_plain_error(404)

    tab = "recommendations" if request.args.get("tab") == "recommendations" else "hierarchy"
    org_id = getattr(current_user, "organization_id", None)
    summary = next(
        (x for x in service.framework_summaries() if x["framework"].id == framework.id),
        None,
    )
    return render_template(
        "industry_apqc/framework_detail.html",
        framework=framework,
        summary=summary,
        tree=service.build_hierarchy(framework.industry_code),
        recommended=service.recommended_processes(framework.industry_code, org_id),
        pending=service.get_pending_recommendations(industry_code=framework.industry_code),
        tab=tab,
    )


@industry_apqc_bp.route("/recommendations")
@login_required
def industry_apqc_recommendations():
    """Recommendations are a tab of the framework page; this address keeps working."""
    code = request.args.get("industry")
    service = IndustryAPQCService()
    framework = service.get_framework_by_code(code) if code else None
    if framework is None:
        frameworks = service.get_all_frameworks()
        if not frameworks:
            return redirect(url_for("industry_apqc.industry_apqc_dashboard"))
        framework = frameworks[0]
    return redirect(
        url_for(
            "industry_apqc.industry_apqc_framework_detail",
            industry_code=framework.industry_code,
            tab="recommendations",
        )
    )


@industry_apqc_bp.route(
    "/api/frameworks/<industry_code>/processes/<int:process_id>/adopt", methods=["POST"]
)
@login_required
@require_roles("admin", "architect")
@audit_log("apqc_process_adopt")
def api_adopt_process(industry_code, process_id):
    """Add an industry process to the capability model."""
    try:
        capability, created = IndustryAPQCService().adopt_process(
            industry_code, process_id, current_user.organization_id
        )
    except Exception:
        current_app.logger.error("api_adopt_process failed", exc_info=True)
        return jsonify({"success": False, "error": "The process could not be adopted."}), 500
    if capability is None:
        return jsonify({"success": False, "error": "Process not found"}), 404
    return jsonify(
        {
            "success": True,
            "created": created,
            "capability": {"id": capability.id, "name": capability.name},
        }
    )


# =========================================================================
# API ENDPOINTS (Industry-specific)
# =========================================================================


@industry_apqc_bp.route("/api/frameworks")
@login_required
def api_industry_apqc_frameworks():
    """API: Get all industry frameworks."""
    try:
        service = IndustryAPQCService()
        frameworks = service.get_all_frameworks()
        return jsonify(
            {"success": True, "frameworks": [fw.to_dict() for fw in frameworks]}
        )
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@industry_apqc_bp.route("/api/frameworks/<industry_code>")
@login_required
def api_industry_apqc_framework(industry_code):
    """API: Get specific framework details."""
    try:
        service = IndustryAPQCService()
        framework = service.get_framework_by_code(industry_code)

        if not framework:
            return jsonify({"success": False, "error": "Framework not found"}), 404

        return jsonify({"success": True, "framework": framework.to_dict()})
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@industry_apqc_bp.route("/api/frameworks/<industry_code>/processes")
@login_required
def api_industry_apqc_processes(industry_code):
    """API: Get processes for an industry framework."""
    try:
        service = IndustryAPQCService()
        processes = service.get_industry_processes(industry_code)

        return jsonify(
            {
                "success": True,
                "industry_code": industry_code,
                "processes": [p.to_dict() for p in processes],
                "total": len(processes),
            }
        )
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@industry_apqc_bp.route("/api/frameworks/<industry_code>/statistics")
@login_required
def api_industry_apqc_statistics(industry_code):
    """API: Get statistics for an industry framework."""
    try:
        service = IndustryAPQCService()
        stats = service.get_industry_statistics(industry_code)

        if not stats:
            return jsonify({"success": False, "error": "Framework not found"}), 404

        return jsonify({"success": True, "statistics": stats})
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@industry_apqc_bp.route("/api/recommendations", methods=["POST"])
@login_required
@audit_log("apqc_recommendations_generate")
def api_generate_recommendations():
    """API: Generate recommendations based on maturity assessment."""
    try:
        data = request.get_json()

        industry_code = data.get("industry_code")
        maturity_data = data.get("maturity_data", {})
        automation_data = data.get("automation_data")
        focus_areas = data.get("focus_areas")
        max_recommendations = data.get("max_recommendations", 10)

        if not industry_code:
            return jsonify(
                {"success": False, "error": "industry_code is required"}
            ), 400

        service = IndustryAPQCService()
        recommendations = service.generate_recommendations(
            industry_code=industry_code,
            current_maturity_data=maturity_data,
            current_automation_data=automation_data,
            focus_areas=focus_areas,
            max_recommendations=max_recommendations,
        )

        return jsonify(
            {
                "success": True,
                "recommendations": [
                    r.to_dict() if hasattr(r, "to_dict") else str(r)
                    for r in recommendations
                ],
                "total": len(recommendations),
            }
        )
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@industry_apqc_bp.route(
    "/api/recommendations/<int:recommendation_id>/accept", methods=["POST"]
)
@login_required
@audit_log("apqc_recommendation_accept")
def api_accept_recommendation(recommendation_id):
    """API: Accept a recommendation."""
    try:
        service = IndustryAPQCService()
        result = service.accept_recommendation(
            recommendation_id=recommendation_id, user_id=current_user.id
        )
        return jsonify(result)
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@industry_apqc_bp.route(
    "/api/recommendations/<int:recommendation_id>/reject", methods=["POST"]
)
@login_required
@audit_log("apqc_recommendation_reject")
def api_reject_recommendation(recommendation_id):
    """API: Reject a recommendation."""
    try:
        data = request.get_json()
        reason = data.get("reason", "No reason provided")

        service = IndustryAPQCService()
        result = service.reject_recommendation(
            recommendation_id=recommendation_id, user_id=current_user.id, reason=reason
        )
        return jsonify(result)
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@industry_apqc_bp.route("/api/seed-frameworks", methods=["POST"])
@platform_admin_required
@audit_log("apqc_frameworks_seed")
def api_seed_frameworks():
    """API: Seed default industry frameworks (platform admin only).

    R2-3 (PR 428 round 3): this writes platform-wide rows -- "admin
    anywhere" let any self-registered org admin seed them with no
    invitation needed. Scoped to platform admins.
    """
    try:
        service = IndustryAPQCService()
        with platform_scope("platform administrator seeds the shared industry frameworks"):
            created = service.seed_default_frameworks()

        return jsonify(
            {
                "success": True,
                "created_frameworks": [fw.to_dict() for fw in created],
                "total_created": len(created),
            }
        )
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500

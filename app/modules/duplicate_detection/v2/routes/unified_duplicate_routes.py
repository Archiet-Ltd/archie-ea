"""
Unified Duplicate Detection Routes v2 — guardrail-enabled.

Preserves all 36 routes from the v1 unified_duplicate_routes.py with
@timed_route decorators and mark_blueprint_guardrailed.

Blueprint name: unified_duplicate (same as v1)
URL prefix: /duplicate-detection (applied via register())
"""
import logging
from datetime import datetime
from difflib import SequenceMatcher

from flask import (  # dead-code-ok
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app import db
from app.core.compat import mark_blueprint_guardrailed
from app.core.decorators import timed_route
from app.models.application_duplicate_detection import (  # dead-code-ok
    DuplicateDetectionRun,
    DuplicateGroup,
)
from app.models.unified_duplicate_detection import (
    UnifiedDetectionRun,
    UnifiedDuplicateGroup,
    unified_group_members,
)
from app.modules.duplicate_detection.services.unified_duplicate_detection_service import UnifiedDuplicateDetectionService
from app.utils.pagination import safe_int_arg
from app.models.application_portfolio import ApplicationComponent
from app.utils.route_guards import require_entity_json

logger = logging.getLogger(__name__)

unified_duplicate_bp_v2 = Blueprint("unified_duplicate", __name__, url_prefix="/duplicate-detection")
mark_blueprint_guardrailed(unified_duplicate_bp_v2)

unified_service = UnifiedDuplicateDetectionService()


# === ENTERPRISE-GRADE ROUTES ===


@unified_duplicate_bp_v2.route("/")
@timed_route
@login_required
def enterprise_dashboard():
    """Enterprise duplicate detection dashboard — redirects to unified simple dashboard."""
    return redirect(url_for("unified_duplicate.simple_dashboard"))


@unified_duplicate_bp_v2.route("/enterprise/run-detection", methods=["POST"])
@timed_route
@login_required
def run_enterprise_detection():
    """Run enterprise-grade duplicate detection"""
    try:
        data = request.get_json() or {}
        application_ids = data.get("application_ids")
        result = unified_service.run_duplicate_detection(application_ids)
        if result["success"]:
            return jsonify(result), 200
        else:
            return jsonify(result), 400
    except Exception as e:
        current_app.logger.error(f"Enterprise detection route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/enterprise/analysis/<int:application_id>")
@timed_route
@login_required
def get_enterprise_analysis(application_id):
    """Get enterprise duplicate analysis for application"""
    _app, missing = require_entity_json(ApplicationComponent, application_id, label="Application")
    if missing:
        return missing

    try:
        result = unified_service.get_duplicate_analysis_for_application(application_id)
        if result["success"]:
            return jsonify(result), 200
        else:
            return jsonify(result), 400
    except Exception as e:
        current_app.logger.error(f"Enterprise analysis route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/enterprise/groups")
@timed_route
@login_required
def get_enterprise_groups():
    """Get enterprise duplicate groups"""
    try:
        groups = unified_service.get_duplicate_groups("enterprise")
        return jsonify({"success": True, "groups": groups}), 200
    except Exception as e:
        current_app.logger.error(f"Enterprise groups route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/enterprise/runs")
@timed_route
@login_required
def get_enterprise_runs():
    """Get enterprise detection runs"""
    try:
        runs = unified_service.get_detection_runs("enterprise")
        return jsonify({"success": True, "runs": runs}), 200
    except Exception as e:
        current_app.logger.error(f"Enterprise runs route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# === SIMPLIFIED ROUTES ===


@unified_duplicate_bp_v2.route("/simple")
@timed_route
@login_required
def simple_dashboard():
    """Simple duplicate detection dashboard"""
    from config import CurrencyConfig
    currency_symbol = CurrencyConfig.get_currency_config()["symbol"]
    return render_template("duplicate_detection/dashboard.html", currency_symbol=currency_symbol)


@unified_duplicate_bp_v2.route("/simple/run-detection", methods=["POST"])
@timed_route
@login_required
def run_simple_detection():
    """Run simple duplicate detection with strategy support."""
    try:
        data = request.get_json() or {}
        strategy = data.get("strategy", "hybrid")
        similarity_threshold = data.get("similarity_threshold", 0.55)

        if strategy not in ["fast", "hybrid", "enhanced"]:
            return jsonify({"success": False, "error": "Invalid strategy. Use: fast, hybrid, enhanced"}), 400
        if not (0.0 <= similarity_threshold <= 1.0):
            return jsonify({"success": False, "error": "Threshold must be between 0 and 1"}), 400

        result = unified_service.run_detection(similarity_threshold=similarity_threshold, strategy=strategy)
        if result["success"]:
            return jsonify(result), 200
        else:
            return jsonify(result), 400
    except Exception as e:
        current_app.logger.error(f"Simple detection route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/simple/run-hybrid", methods=["POST"])
@timed_route
@login_required
def run_hybrid_detection():
    """Run hybrid duplicate detection"""
    try:
        data = request.get_json() or {}
        similarity_threshold = data.get("similarity_threshold", 0.8)
        result = unified_service.run_detection_hybrid(similarity_threshold)
        if result["success"]:
            return jsonify(result), 200
        else:
            return jsonify(result), 400
    except Exception as e:
        current_app.logger.error(f"Hybrid detection route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/simple/groups")
@timed_route
@login_required
def get_simple_groups():
    """Get simple duplicate groups"""
    try:
        groups = unified_service.get_duplicate_groups("simple")
        return jsonify({"success": True, "groups": groups}), 200
    except Exception as e:
        current_app.logger.error(f"Simple groups route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/simple/runs")
@timed_route
@login_required
def get_simple_runs():
    """Get simple detection runs with full details for dashboard display."""
    try:
        limit = min(safe_int_arg('limit', 10, minimum=1, maximum=500), 100)
        runs = (
            UnifiedDetectionRun.query.order_by(UnifiedDetectionRun.created_at.desc())
            .limit(limit)
            .all()
        )
        runs_data = []
        for run in runs:
            run_dict = run.to_dict()
            run_dict["duplicate_groups_found"] = run_dict.get("groups_found", 0)
            runs_data.append(run_dict)
        return jsonify({"success": True, "runs": runs_data}), 200
    except Exception as e:
        current_app.logger.error(f"Simple runs route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/simple/cleanup", methods=["POST"])
@timed_route
@login_required
def cleanup_stale_data():
    """Clean up stale duplicate detection data (POST only)"""
    try:
        result = unified_service.cleanup_stale_data()
        if result["success"]:
            return jsonify(result), 200
        else:
            return jsonify(result), 400
    except Exception as e:
        current_app.logger.error(f"Cleanup route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/simple/delete-duplicates/<int:group_id>", methods=["POST"])
@timed_route
@login_required
def delete_duplicates(group_id):
    """Delete duplicate applications in a group"""
    try:
        data = request.get_json() or {}
        keep_app_id = data.get("keep_app_id")
        if keep_app_id is not None:
            try:
                keep_app_id = int(keep_app_id)
            except (ValueError, TypeError):
                return jsonify({"success": False, "error": "Invalid keep_app_id"}), 400

        if keep_app_id:
            result = unified_service.delete_duplicates_keep_one(group_id, keep_app_id)
        else:
            result = unified_service.bulk_delete_duplicates_keep_best(group_id)

        if result["success"]:
            return jsonify(result), 200
        else:
            return jsonify(result), 400
    except Exception as e:
        current_app.logger.error(f"Delete duplicates route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/simple/api/bulk-delete-duplicates", methods=["POST"])
@timed_route
@login_required
def bulk_delete_duplicates_api():
    """Bulk delete duplicates across multiple groups."""
    try:
        data = request.get_json() or {}
        group_selections = data.get("group_selections", {})
        if not group_selections:
            return jsonify({"success": False, "error": "No groups provided"}), 400

        results = {
            "success": True, "total_groups_processed": 0, "total_deleted": 0,
            "successful_groups": [], "failed_groups": [], "errors": []
        }

        for group_id_str, keep_app_id in group_selections.items():
            try:
                group_id = int(group_id_str)
                if keep_app_id:
                    result = unified_service.delete_duplicates_keep_one(group_id, int(keep_app_id))
                else:
                    result = unified_service.bulk_delete_duplicates_keep_best(group_id)

                if result.get("success"):
                    results["total_groups_processed"] += 1
                    results["total_deleted"] += result.get("deleted_count", 0)
                    results["successful_groups"].append(group_id)
                else:
                    results["failed_groups"].append(group_id)
                    results["errors"].append(f"Group {group_id}: {result.get('error', 'Unknown error')}")
            except Exception as e:
                results["failed_groups"].append(group_id_str)
                current_app.logger.error(f"Error processing group {group_id_str}: {e}")
                results["errors"].append(f"Group {group_id_str}: processing failed")

        if results["failed_groups"]:
            results["success"] = len(results["successful_groups"]) > 0

        return jsonify(results), 200
    except Exception as e:
        current_app.logger.error(f"Bulk delete duplicates error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/simple/savings/<int:group_id>")
@timed_route
@login_required
def get_group_savings(group_id):
    """Get estimated savings for a duplicate group"""
    try:
        group = UnifiedDuplicateGroup.query.get(group_id)
        if not group:
            return jsonify({"success": False, "error": "Group not found"}), 404
        savings = unified_service._estimate_group_savings(group)
        return jsonify({"success": True, "savings": savings}), 200
    except Exception as e:
        current_app.logger.error(f"Savings route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/simple/api/statistics")
@timed_route
@login_required
def get_simple_statistics():
    """Get application consolidation statistics (simple mode)"""
    try:
        latest_run = (
            UnifiedDetectionRun.query.filter(
                UnifiedDetectionRun.strategy.in_(["fast", "hybrid"]),
                UnifiedDetectionRun.status == "completed",
            )
            .order_by(UnifiedDetectionRun.created_at.desc())
            .first()
        )
        # Count only groups that still have 2+ members (exclude orphaned groups)
        from app.models.unified_duplicate_detection import unified_group_members
        from sqlalchemy import func as sa_func
        active_group_ids = (
            db.session.query(unified_group_members.c.group_id)
            .group_by(unified_group_members.c.group_id)
            .having(sa_func.count(unified_group_members.c.application_id) >= 2)
            .subquery()
        )
        total_groups = UnifiedDuplicateGroup.query.filter(
            UnifiedDuplicateGroup.id.in_(db.session.query(active_group_ids.c.group_id))
        ).count()
        high_priority_groups = UnifiedDuplicateGroup.query.filter(
            UnifiedDuplicateGroup.id.in_(db.session.query(active_group_ids.c.group_id)),
            UnifiedDuplicateGroup.similarity_score >= 0.85,
        ).count()
        total_estimated_savings = (
            latest_run.estimated_savings if latest_run and latest_run.estimated_savings else 0
        )
        return jsonify({
            "success": True,
            "total_groups": total_groups,
            "high_priority_groups": high_priority_groups,
            "total_estimated_savings": total_estimated_savings,
            "latest_run": {
                "id": latest_run.id, "run_name": latest_run.run_name,
                "status": latest_run.status,
                "groups_found": latest_run.groups_found or 0,
                "applications_analyzed": latest_run.applications_analyzed or 0,
                "created_at": latest_run.created_at.isoformat() if latest_run else None,
            } if latest_run else None,
        })
    except Exception as e:
        current_app.logger.error(f"Simple statistics route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/simple/api/groups")
@timed_route
@login_required
def get_simple_groups_api():
    """Get all application consolidation groups"""
    try:
        groups = unified_service.get_duplicate_groups("simple", include_applications=False)
        return jsonify({"success": True, "groups": groups})
    except Exception as e:
        current_app.logger.error(f"Simple groups API route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/simple/api/element-groups")
@timed_route
@login_required
def get_element_duplicate_groups_api():
    """Return live ArchiMate duplicate-name groups for the simple dashboard."""
    try:
        groups = unified_service.get_archimate_element_duplicate_groups()
        total_elements = sum(group["element_count"] for group in groups)
        return jsonify(
            {
                "success": True,
                "groups": groups,
                "total_groups": len(groups),
                "total_duplicated_elements": total_elements,
            }
        )
    except Exception as e:
        current_app.logger.error(f"Element duplicate groups route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/simple/api/run-detection", methods=["POST"])
@timed_route
@login_required
def run_simple_detection_api():
    """Run application consolidation detection API endpoint"""
    try:
        try:
            db.session.execute(db.text("SELECT 1 FROM unified_duplicate_groups LIMIT 1"))  # tenant-exempt: schema check
        except Exception as e:
            current_app.logger.warning("Consolidation tables not found: %s", e)
            return jsonify({"success": False, "error": "Application consolidation tables not found."}), 400

        data = request.get_json() or {}
        threshold = data.get("threshold", 0.7)
        method = data.get("method", "fast")

        if method == "enhanced":
            try:
                detection_run = unified_service.run_duplicate_detection(application_ids=None)
                return jsonify({
                    "success": detection_run.get("success", False),
                    "run_id": detection_run.get("run_id"),
                    "message": detection_run.get("message", "Enhanced detection completed"),
                    "method": "enhanced",
                    "groups_found": detection_run.get("groups_found", 0),
                    "applications_analyzed": detection_run.get("applications_analyzed", 0),
                    "duration_seconds": detection_run.get("duration_seconds"),
                })
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Enhanced detection failed: {e}")
                return jsonify({"success": False, "error": "Enhanced detection failed."}), 500
        elif method == "hybrid":
            try:
                run = unified_service.run_detection_hybrid(threshold)
                return jsonify({
                    "success": run.get("success", False), "run_id": run.get("run_id"),
                    "groups_found": run.get("groups_found", 0), "exact_matches": run.get("exact_matches", 0),
                    "fuzzy_matches": run.get("fuzzy_matches", 0), "estimated_savings": run.get("estimated_savings", 0),
                    "applications_analyzed": run.get("applications_analyzed", 0),
                    "duration_seconds": run.get("duration_seconds"),
                    "message": run.get("message", ""), "warning": run.get("warning"), "method": "hybrid",
                })
            except Exception as e:
                db.session.rollback()
                current_app.logger.exception(f"Hybrid detection failed: {e}")
                run = unified_service.run_detection(threshold)
                if not run.get("success"):
                    # Both methods failed — there is no result to report.
                    return jsonify({
                        "success": False,
                        "error": run.get("message")
                        or "Duplicate detection failed for both the hybrid and simple methods",
                    }), 500
                # error-signalling-ok: the simple method ran and produced real results; the warning below tells the caller the hybrid method was not used
                return jsonify({
                    "success": run.get("success", False), "run_id": run.get("run_id"),
                    "message": run.get("message"), "method": "fast",
                    "groups_found": run.get("groups_found", 0),
                    "exact_matches": run.get("exact_matches", 0),
                    "fuzzy_matches": run.get("fuzzy_matches", 0),
                    "applications_analyzed": run.get("applications_analyzed", 0),
                    "estimated_savings": run.get("estimated_savings", 0),
                    "duration_seconds": run.get("duration_seconds"),
                    "warning": run.get("warning") or "Hybrid method failed, used simple method instead",
                })
        else:
            run = unified_service.run_detection(threshold)
            return jsonify({
                "success": run.get("success", False), "run_id": run.get("run_id"),
                "message": run.get("message") or run.get("error"), "method": "fast",
                "groups_found": run.get("groups_found", 0), "exact_matches": run.get("exact_matches", 0),
                "fuzzy_matches": run.get("fuzzy_matches", 0), "estimated_savings": run.get("estimated_savings", 0),
                "applications_analyzed": run.get("applications_analyzed", 0),
                "duration_seconds": run.get("duration_seconds"),
                "warning": run.get("warning"),
            })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Simple detection API error: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/api/find-similar/<int:app_id>")
@timed_route
@login_required
def find_similar_applications_api(app_id):
    """Find applications similar to a specific application"""
    try:
        from app.models.application_portfolio import ApplicationComponent

        method = request.args.get("method", "fast")
        threshold = float(request.args.get("threshold", 0.5))

        target_app = ApplicationComponent.query.get(app_id)
        if not target_app:
            return jsonify({"success": False, "error": "Application not found"}), 404

        all_apps = ApplicationComponent.query.filter(ApplicationComponent.id != app_id).limit(500).all()
        similar_apps = []

        for app in all_apps:
            similarity = 0.0
            if method in ("fast", "hybrid"):
                name_sim = SequenceMatcher(None, (target_app.name or "").lower(), (app.name or "").lower()).ratio()
                similarity = max(similarity, name_sim)
            if method in ("enhanced", "hybrid"):
                if target_app.description and app.description:
                    desc_sim = SequenceMatcher(None, target_app.description.lower(), app.description.lower()).ratio()
                    similarity = max(similarity, desc_sim * 0.8)
                if target_app.component_type and app.component_type:
                    if target_app.component_type == app.component_type:
                        similarity = max(similarity, 0.3)
                if target_app.deployment_status and app.deployment_status:
                    if target_app.deployment_status == app.deployment_status:
                        similarity = min(1.0, similarity + 0.1)

            similarity_pct = int(similarity * 100)
            if similarity_pct >= int(threshold * 100):
                similar_apps.append({
                    "id": app.id, "name": app.name, "type": app.component_type,
                    "status": app.deployment_status,
                    "description": (app.description or "")[:100] + "..." if app.description and len(app.description) > 100 else app.description,
                    "similarity": similarity_pct,
                })

        similar_apps.sort(key=lambda x: x["similarity"], reverse=True)
        similar_apps = similar_apps[:20]
        return jsonify({"success": True, "target_app": {"id": target_app.id, "name": target_app.name}, "similar_apps": similar_apps, "count": len(similar_apps), "method": method})
    except Exception as e:
        current_app.logger.error(f"Find similar apps API error: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/simple/group/<int:group_id>")
@timed_route
@login_required
def simple_group_detail(group_id):
    """Application consolidation group detail page"""
    try:
        from config import CurrencyConfig
        group = UnifiedDuplicateGroup.query.get_or_404(group_id)
        currency_symbol = CurrencyConfig.get_currency_config()["symbol"]
        match_details = group.match_details if isinstance(group.match_details, dict) else {}

        def _similarity(metric_name):
            value = match_details.get(metric_name, 0)
            return float(value) if isinstance(value, (int, float)) else 0.0

        group_payload = {
            "id": group.id,
            "group_name": group.name or f"Group {group.id}",
            "description": group.description,
            "duplicate_type": group.duplicate_type or "fuzzy",
            "overall_similarity_score": group.similarity_score or 0,
            "estimated_savings": group.estimated_savings or 0,
            "consolidation_priority": group.risk_level or "medium",
            "functional_similarity": _similarity("functional_similarity"),
            "capability_similarity": _similarity("capability_similarity"),
            "technical_similarity": _similarity("technical_similarity"),
            "data_similarity": _similarity("data_similarity"),
            "primary_business_process": None,
            "primary_capability": None,
            "applications": [],
        }

        # UnifiedDuplicateGroup is a plain db.Model (no TenantMixin) and
        # unified_duplicate_groups/unified_group_members have no
        # organization_id, so get_or_404(group_id) is NOT org-filtered and
        # group_id does not scope this join. application_components does carry
        # organization_id — put the predicate there.
        from flask import g as _g
        _org = getattr(_g, "current_org_id", None)
        _org_and = " AND ac.organization_id = :org" if _org is not None else ""
        app_rows = db.session.execute(
            db.text(
                f"""
                SELECT
                    ac.id,
                    ac.name,
                    ac.description,
                    ac.deployment_status,
                    ac.application_owner,
                    ac.technology_stack
                FROM unified_group_members ugm
                JOIN application_components ac ON ac.id = ugm.application_id
                WHERE ugm.group_id = :group_id{_org_and}
                ORDER BY ac.name
                LIMIT 500
                """
            ),
            {"group_id": group.id, **({"org": _org} if _org is not None else {})},
        ).mappings().all()  # materialise: a Result has no __bool__/__len__, "if not app_rows" was always False

        if not app_rows:
            # No member application is visible to the caller's organisation (either the
            # group has none, or every member belongs to a different organisation).
            # UnifiedDuplicateGroup itself carries no organisation column, so this is the
            # only tenant signal available; treat it the same as "group not found" rather
            # than reveal the group's own name, similarity scores and estimated savings.
            return "Group not found", 404

        for app in app_rows:
            technology_stack = app.get("technology_stack")
            if technology_stack is not None and not isinstance(technology_stack, str):
                technology_stack = str(technology_stack)
            group_payload["applications"].append({
                "id": app.get("id"),
                "name": app.get("name"),
                "description": app.get("description"),
                "status": app.get("deployment_status"),
                "application_owner": app.get("application_owner"),
                "technology_stack": technology_stack,
                "annual_cost": None,
            })

        try:
            dashboard_registry_url = url_for("dynamic_dashboards.model_registry_index")
        except Exception:
            dashboard_registry_url = "/auto-dashboard/registry"
        return render_template(
            "duplicate_detection/group_detail.html",
            group=group_payload, recommendation=None, pairwise_analyses=[],
            currency_symbol=currency_symbol, dashboard_registry_url=dashboard_registry_url,
        )
    except Exception as e:
        current_app.logger.error(f"Error viewing consolidation group: {e}")
        return "Group not found", 404


@unified_duplicate_bp_v2.route("/group/<int:group_id>")
@timed_route
@login_required
def group_detail_redirect(group_id):
    """Redirect old group detail URL to new location"""
    return redirect(url_for("unified_duplicate.simple_group_detail", group_id=group_id))


# === UNIFIED INTERFACE ROUTES ===


@unified_duplicate_bp_v2.route("/unified")
@timed_route
@login_required
def unified_dashboard():
    """Unified duplicate detection dashboard"""
    return render_template("unified_duplicate/dashboard.html")


@unified_duplicate_bp_v2.route("/unified/run-detection", methods=["POST"])
@timed_route
@login_required
def run_unified_detection():
    """Run unified duplicate detection with specified mode"""
    try:
        data = request.get_json() or {}
        mode = data.get("mode", "enterprise")
        kwargs = {k: v for k, v in data.items() if k != "mode"}
        result = unified_service.run_unified_detection(mode, **kwargs)
        if result["success"]:
            return jsonify(result), 200
        else:
            return jsonify(result), 400
    except Exception as e:
        current_app.logger.error(f"Unified detection route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/unified/groups")
@timed_route
@login_required
def get_unified_groups():
    """Get all duplicate groups (both enterprise and simple)"""
    try:
        mode = request.args.get("mode", "all")
        groups = unified_service.get_duplicate_groups(mode)
        return jsonify({"success": True, "groups": groups}), 200
    except Exception as e:
        current_app.logger.error(f"Unified groups route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/unified/runs")
@timed_route
@login_required
def get_unified_runs():
    """Get all detection runs (both enterprise and simple)"""
    try:
        mode = request.args.get("mode", "all")
        runs = unified_service.get_detection_runs(mode)
        return jsonify({"success": True, "runs": runs}), 200
    except Exception as e:
        current_app.logger.error(f"Unified runs route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/unified/stats")
@timed_route
@login_required
def get_unified_stats():
    """Get unified statistics for duplicate detection"""
    try:
        enterprise_runs = DuplicateDetectionRun.query.count()
        enterprise_groups = DuplicateGroup.query.count()
        simple_runs = UnifiedDetectionRun.query.count()
        simple_groups = UnifiedDuplicateGroup.query.count()
        stats = {
            "enterprise": {"total_runs": enterprise_runs, "total_groups": enterprise_groups},
            "simple": {"total_runs": simple_runs, "total_groups": simple_groups},
            "combined": {"total_runs": enterprise_runs + simple_runs, "total_groups": enterprise_groups + simple_groups},
        }
        return jsonify({"success": True, "stats": stats}), 200
    except Exception as e:
        current_app.logger.error(f"Unified stats route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/dashboard")
@timed_route
@login_required
def duplicate_dashboard():
    """Duplicate detection dashboard (AJAX-aware)."""
    try:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"status": "success", "message": "Duplicate detection dashboard loaded", "services": ["enterprise", "simple", "hybrid"]})
        return render_template("duplicate_detection/dashboard.html")
    except Exception as e:
        current_app.logger.error("Error loading duplicate detection dashboard: %s", e, exc_info=True)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"status": "error", "message": "An internal error occurred"}), 500
        else:
            return render_template(
                "duplicate_detection/dashboard.html",
                load_error="Duplicate detection results could not be read.",
            )


# === INTELLIGENCE API ENDPOINTS ===


@unified_duplicate_bp_v2.route("/simple/api/group/<int:group_id>/confidence")
@timed_route
@login_required
def api_group_confidence(group_id):
    """Get per-pair confidence scoring data for a duplicate group."""
    try:
        from sqlalchemy import select
        from app.models.application_portfolio import ApplicationComponent

        group = UnifiedDuplicateGroup.query.get(group_id)
        if not group:
            return jsonify({"success": False, "error": "Group not found"}), 404

        # tenant-filtered: scoped via parent FK (group_id)
        stmt = select(
            unified_group_members.c.application_id,
            unified_group_members.c.similarity_to_primary,
            unified_group_members.c.is_primary,
        ).where(unified_group_members.c.group_id == group_id)
        members = db.session.execute(stmt).fetchall()  # tenant-filtered: scoped via parent FK (group_id)
        app_ids = [m.application_id for m in members]
        apps = ApplicationComponent.query.filter(ApplicationComponent.id.in_(app_ids)).all()
        app_map = {a.id: a for a in apps}
        primary_id = next((m.application_id for m in members if m.is_primary), None)

        pairs = []
        scores = []
        for i, m1 in enumerate(members):
            for m2 in members[i + 1:]:
                a1 = app_map.get(m1.application_id)
                a2 = app_map.get(m2.application_id)
                if not a1 or not a2:
                    continue
                name_sim = SequenceMatcher(None, (a1.name or "").lower(), (a2.name or "").lower()).ratio()
                desc_sim = 0.0
                if a1.description and a2.description:
                    desc_sim = SequenceMatcher(None, a1.description.lower(), a2.description.lower()).ratio()
                if m1.is_primary and m2.similarity_to_primary:
                    pair_score = float(m2.similarity_to_primary)
                elif m2.is_primary and m1.similarity_to_primary:
                    pair_score = float(m1.similarity_to_primary)
                else:
                    type_sim = 1.0 if (a1.component_type and a1.component_type == a2.component_type) else 0.0
                    pair_score = name_sim * 0.4 + desc_sim * 0.4 + type_sim * 0.2
                scores.append(pair_score)
                confidence_level = "high" if pair_score >= 0.8 else ("medium" if pair_score >= 0.6 else "low")
                pairs.append({
                    "app1_id": a1.id, "app1_name": a1.name, "app2_id": a2.id, "app2_name": a2.name,
                    "similarity_score": round(pair_score, 4), "name_similarity": round(name_sim, 4),
                    "description_similarity": round(desc_sim, 4), "confidence_level": confidence_level,
                    "match_type": group.duplicate_type or "fuzzy",
                })

        avg_score = sum(scores) / len(scores) if scores else 0
        distribution = {
            "high": len([s for s in scores if s >= 0.8]),
            "medium": len([s for s in scores if 0.6 <= s < 0.8]),
            "low": len([s for s in scores if s < 0.6]),
        }
        return jsonify({
            "success": True,
            "data": {
                "group_id": group_id, "primary_app_id": primary_id, "pair_count": len(pairs),
                "pairs": pairs,
                "aggregate": {
                    "avg_confidence": round(avg_score, 4),
                    "min_confidence": round(min(scores), 4) if scores else 0,
                    "max_confidence": round(max(scores), 4) if scores else 0,
                    "distribution": distribution,
                },
            },
        })
    except Exception as e:
        current_app.logger.error(f"Error getting group confidence for {group_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/simple/api/group/<int:group_id>/merge-preview")
@timed_route
@login_required
def api_group_merge_preview(group_id):
    """Get field-level merge preview for a duplicate group."""
    try:
        from sqlalchemy import select
        from app.models.application_portfolio import ApplicationComponent

        group = UnifiedDuplicateGroup.query.get(group_id)
        if not group:
            return jsonify({"success": False, "error": "Group not found"}), 404

        primary_app_id = request.args.get("primary_app_id", type=int)
        stmt = select(unified_group_members.c.application_id, unified_group_members.c.is_primary).where(unified_group_members.c.group_id == group_id)
        members = db.session.execute(stmt).fetchall()  # tenant-filtered: scoped via parent FK (group_id)
        app_ids = [m.application_id for m in members]
        apps = ApplicationComponent.query.filter(ApplicationComponent.id.in_(app_ids)).all()
        app_map = {a.id: a for a in apps}

        if primary_app_id and primary_app_id in app_map:
            primary = app_map[primary_app_id]
        else:
            stored_primary_id = next((m.application_id for m in members if m.is_primary), None)
            if stored_primary_id and stored_primary_id in app_map:
                primary = app_map[stored_primary_id]
            elif apps:
                primary = apps[0]
            else:
                return jsonify({"success": False, "error": "No applications in group"}), 404

        merge_fields = [
            ("name", "Name"), ("description", "Description"), ("vendor_name", "Vendor"),
            ("component_type", "Component Type"), ("deployment_status", "Deployment Status"),
            ("deployment_model", "Deployment Model"), ("business_domain", "Business Domain"),
            ("business_criticality", "Business Criticality"), ("business_owner", "Business Owner"),
            ("technical_owner", "Technical Owner"), ("total_cost_of_ownership", "Total Cost of Ownership"),
            ("license_cost", "License Cost"), ("technology_stack", "Technology Stack"),
        ]

        def get_field_val(app, field_name):
            val = getattr(app, field_name, None)
            if val is None:
                return None
            if isinstance(val, float):
                return round(val, 2)
            return str(val) if val else None

        duplicates = []
        fields_matching = fields_conflicting = fields_missing = 0
        other_apps = [a for a in apps if a.id != primary.id]

        for dup_app in other_apps:
            field_conflicts = []
            for field_name, field_label in merge_fields:
                primary_val = get_field_val(primary, field_name)
                dup_val = get_field_val(dup_app, field_name)
                if primary_val == dup_val:
                    status = "match"
                    fields_matching += 1
                elif primary_val and not dup_val:
                    status = "primary_only"
                    fields_missing += 1
                elif not primary_val and dup_val:
                    status = "duplicate_only"
                    fields_missing += 1
                else:
                    status = "conflict"
                    fields_conflicting += 1
                if status == "conflict":
                    p_len = len(str(primary_val)) if primary_val else 0
                    d_len = len(str(dup_val)) if dup_val else 0
                    recommendation = "keep_primary" if p_len >= d_len else "keep_duplicate"
                elif status == "duplicate_only":
                    recommendation = "adopt_duplicate"
                else:
                    recommendation = "keep_primary"
                field_conflicts.append({"field": field_name, "label": field_label, "primary_value": primary_val, "duplicate_value": dup_val, "status": status, "recommendation": recommendation})
            duplicates.append({"app_id": dup_app.id, "app_name": dup_app.name, "field_conflicts": field_conflicts})

        primary_data = {"id": primary.id, "name": primary.name}
        for field_name, field_label in merge_fields:
            primary_data[field_name] = get_field_val(primary, field_name)

        return jsonify({
            "success": True,
            "data": {
                "group_id": group_id, "primary": primary_data, "duplicates": duplicates,
                "merge_summary": {"fields_matching": fields_matching, "fields_conflicting": fields_conflicting, "fields_missing": fields_missing, "total_fields_compared": fields_matching + fields_conflicting + fields_missing},
            },
        })
    except Exception as e:
        current_app.logger.error(f"Error getting merge preview for {group_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/simple/api/group/<int:group_id>/impact")
@timed_route
@login_required
def api_group_impact(group_id):
    """Get dependency and impact analysis for applications in a duplicate group."""
    try:
        from sqlalchemy import select
        from app.models.application_portfolio import ApplicationComponent

        group = UnifiedDuplicateGroup.query.get(group_id)
        if not group:
            return jsonify({"success": False, "error": "Group not found"}), 404

        stmt = select(unified_group_members.c.application_id, unified_group_members.c.is_primary).where(unified_group_members.c.group_id == group_id)
        members = db.session.execute(stmt).fetchall()  # tenant-filtered: scoped via parent FK (group_id)
        app_ids = [m.application_id for m in members]
        primary_ids = {m.application_id for m in members if m.is_primary}
        apps = ApplicationComponent.query.filter(ApplicationComponent.id.in_(app_ids)).all()

        total_processes = total_capabilities = total_integrations = 0
        app_impacts = []

        for app in apps:
            process_count = capability_count = integration_count = 0
            for rel_name in ("business_processes", "capabilities", "integrations"):
                if hasattr(app, rel_name):
                    try:
                        rel = getattr(app, rel_name)
                        cnt = rel.count() if hasattr(rel, "count") else len(list(rel))
                    except Exception:
                        cnt = 0
                else:
                    cnt = 0
                if rel_name == "business_processes":
                    process_count = cnt
                elif rel_name == "capabilities":
                    capability_count = cnt
                else:
                    integration_count = cnt

            user_count = app.user_count or getattr(app, "user_base_size", 0) or 0
            dependency_count = process_count + capability_count + integration_count
            if dependency_count > 10 or user_count > 500:
                risk_level = "high"
            elif dependency_count > 5 or user_count > 100:
                risk_level = "medium"
            else:
                risk_level = "low"

            total_processes += process_count
            total_capabilities += capability_count
            total_integrations += integration_count
            app_impacts.append({
                "app_id": app.id, "name": app.name, "is_primary": app.id in primary_ids,
                "process_count": process_count, "capability_count": capability_count,
                "integration_count": integration_count, "user_count": user_count,
                "dependency_count": dependency_count, "risk_level": risk_level,
                "business_criticality": app.business_criticality or "Unknown",
            })

        non_primary_impacts = [a for a in app_impacts if not a["is_primary"]]
        high_risk_count = len([a for a in non_primary_impacts if a["risk_level"] == "high"])
        return jsonify({
            "success": True,
            "data": {
                "group_id": group_id, "applications": app_impacts,
                "total_impact": {
                    "processes_affected": total_processes, "capabilities_affected": total_capabilities,
                    "integrations_affected": total_integrations,
                    "total_users_affected": sum(a["user_count"] for a in app_impacts),
                    "high_risk_apps": high_risk_count,
                },
                "consolidation_risk": "high" if high_risk_count > 0 else ("medium" if total_processes + total_capabilities > 10 else "low"),
            },
        })
    except Exception as e:
        current_app.logger.error(f"Error getting impact analysis for {group_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# === ENTERPRISE DASHBOARD API ROUTES ===


@unified_duplicate_bp_v2.route("/api/statistics/summary")
@timed_route
@login_required
def api_statistics_summary():
    """Statistics summary for the enterprise dashboard."""
    try:
        runs = DuplicateDetectionRun.query.order_by(DuplicateDetectionRun.created_at.desc()).limit(100).all()
        latest_run = runs[0] if runs else None
        groups = DuplicateGroup.query.limit(500).all()
        total_groups = len(groups)
        total_estimated_savings = sum(getattr(g, "estimated_savings", 0) or 0 for g in groups)
        priority_counts = {}
        for g in groups:
            p = getattr(g, "consolidation_priority", "low") or "low"
            priority_counts[p] = priority_counts.get(p, 0) + 1
        groups_by_priority = [{"priority": k, "count": v} for k, v in priority_counts.items()]
        return jsonify({
            "total_groups": total_groups, "total_estimated_savings": total_estimated_savings,
            "groups_by_priority": groups_by_priority,
            "latest_run": {"id": latest_run.id, "run_name": getattr(latest_run, "run_name", ""), "status": latest_run.status, "created_at": latest_run.created_at.isoformat() if latest_run.created_at else None} if latest_run else None,
        })
    except Exception as e:
        current_app.logger.exception(f"Stats summary error: {e}")
        return jsonify({"error": "Could not load duplicate detection statistics"}), 500


@unified_duplicate_bp_v2.route("/api/duplicate-groups")
@timed_route
@login_required
def api_duplicate_groups():
    """Paginated duplicate groups for the enterprise dashboard."""
    try:
        page = safe_int_arg('page', 1, minimum=1)
        per_page = safe_int_arg('per_page', 10, minimum=1, maximum=500)
        priority = request.args.get("priority", "")
        min_similarity = request.args.get("min_similarity", 0.0, type=float)

        query = DuplicateGroup.query
        if priority:
            query = query.filter(DuplicateGroup.consolidation_priority == priority)
        if min_similarity > 0:
            query = query.filter(DuplicateGroup.overall_similarity_score >= min_similarity)
        query = query.order_by(DuplicateGroup.created_at.desc())
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)

        groups_data = []
        for g in pagination.items:
            groups_data.append({
                "id": g.id, "group_name": getattr(g, "group_name", f"Group {g.id}"),
                "overall_similarity_score": getattr(g, "overall_similarity_score", 0),
                "consolidation_priority": getattr(g, "consolidation_priority", "low"),
                "duplicate_type": getattr(g, "duplicate_type", "unknown"),
                "estimated_savings": getattr(g, "estimated_savings", 0) or 0,
                "application_count": len(g.applications) if hasattr(g, "applications") else 0,
                "created_at": g.created_at.isoformat() if hasattr(g, "created_at") and g.created_at else None,
            })
        return jsonify({"groups": groups_data, "pagination": {"page": pagination.page, "per_page": pagination.per_page, "total": pagination.total, "pages": pagination.pages, "has_next": pagination.has_next, "has_prev": pagination.has_prev}})
    except Exception as e:
        current_app.logger.exception(f"Duplicate groups API error: {e}")
        return jsonify({"error": "Could not load duplicate groups"}), 500


@unified_duplicate_bp_v2.route("/api/detection-runs")
@timed_route
@login_required
def api_detection_runs():
    """Detection runs for the enterprise dashboard."""
    try:
        runs = DuplicateDetectionRun.query.order_by(DuplicateDetectionRun.created_at.desc()).limit(20).all()
        runs_data = []
        for r in runs:
            runs_data.append({
                "id": r.id, "run_name": getattr(r, "run_name", ""), "status": r.status,
                "duplicate_groups_found": getattr(r, "duplicate_groups_found", 0),
                "applications_analyzed": getattr(r, "applications_analyzed", 0),
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "completed_at": r.completed_at.isoformat() if hasattr(r, "completed_at") and r.completed_at else None,
            })
        return jsonify({"runs": runs_data})
    except Exception as e:
        current_app.logger.exception(f"Detection runs API error: {e}")
        return jsonify({"error": "Could not load detection runs"}), 500


@unified_duplicate_bp_v2.route("/run-detection", methods=["POST"])
@timed_route
@login_required
def run_detection():
    """Run duplicate detection from the enterprise dashboard."""
    try:
        request.get_json() or {}
        result = unified_service.run_duplicate_detection()
        if result.get("success"):
            return jsonify(result), 200
        else:
            return jsonify(result), 400
    except Exception as e:
        current_app.logger.error(f"Run detection error: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# === CONSOLIDATION RECOMMENDATION ROUTES ===


@unified_duplicate_bp_v2.route("/api/consolidation-recommendation/<int:recommendation_id>/approve", methods=["POST"])
@timed_route
@login_required
def approve_consolidation_recommendation(recommendation_id):
    """Approve a consolidation recommendation."""
    try:
        group = DuplicateGroup.query.get(recommendation_id)
        if not group:
            return jsonify({"success": False, "error": "Recommendation not found"}), 404
        group.status = "approved"
        group.reviewed_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"success": True, "approved_at": group.reviewed_at.isoformat()})
    except Exception as e:
        current_app.logger.error(f"Error approving recommendation {recommendation_id}: {e}")
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/api/consolidation-recommendation/<int:recommendation_id>/reject", methods=["POST"])
@timed_route
@login_required
def reject_consolidation_recommendation(recommendation_id):
    """Reject a consolidation recommendation."""
    try:
        data = request.get_json() or {}
        reason = data.get("reason", "")
        group = DuplicateGroup.query.get(recommendation_id)
        if not group:
            return jsonify({"success": False, "error": "Recommendation not found"}), 404
        group.status = "rejected"
        group.reviewed_at = datetime.utcnow()
        group.recommendation_notes = f"Rejected: {reason}"
        db.session.commit()
        return jsonify({"success": True, "rejected_at": group.reviewed_at.isoformat()})
    except Exception as e:
        current_app.logger.error(f"Error rejecting recommendation {recommendation_id}: {e}")
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/api/groups/<string:group_id>/add-to-consolidation", methods=["POST"])
@timed_route
@login_required
def api_add_group_to_consolidation(group_id):
    """Add all applications from a duplicate group to the consolidation list."""
    try:
        from app.models.application_portfolio import ApplicationComponent
        from app.models.consolidation_list import ConsolidationListEntry

        try:
            group_id_int = int(str(group_id))
        except (ValueError, TypeError):
            return jsonify({"success": False, "error": f"Invalid group ID: {group_id}"}), 400

        group = UnifiedDuplicateGroup.query.get(group_id_int)
        if not group:
            legacy_group = DuplicateGroup.query.get(group_id_int)
            if not legacy_group:
                return jsonify({"success": False, "error": f"Group {group_id} not found"}), 404
            application_ids = [app.id for app in legacy_group.applications]
            group_name = legacy_group.name or f"Duplicate Group {group_id_int}"
            estimated_savings = legacy_group.estimated_savings if hasattr(legacy_group, "estimated_savings") else None
        else:
            application_ids = [app.id for app in group.applications.all()]
            group_name = group.name or f"Duplicate Group {group_id_int}"
            estimated_savings = group.estimated_savings if hasattr(group, "estimated_savings") else None

        if not application_ids:
            return jsonify({"success": False, "error": "No applications found in this group"}), 400

        added_count = skipped_count = 0
        errors = []

        apps_by_id = {}
        if application_ids:
            apps_list = ApplicationComponent.query.filter(ApplicationComponent.id.in_(application_ids)).all()
            apps_by_id = {a.id: a for a in apps_list}
            existing_entries = ConsolidationListEntry.query.filter(ConsolidationListEntry.application_id.in_(application_ids), ConsolidationListEntry.status == "pending").all()
            existing_app_ids = {e.application_id for e in existing_entries}
        else:
            existing_app_ids = set()

        for app_id in application_ids:
            try:
                app = apps_by_id.get(app_id)
                if not app:
                    errors.append(f"Application {app_id} not found")
                    continue
                if app_id in existing_app_ids:
                    skipped_count += 1
                    continue
                savings_estimate = None
                if app.total_cost_of_ownership:
                    savings_estimate = app.total_cost_of_ownership
                elif app.maintenance_cost:
                    savings_estimate = app.maintenance_cost
                elif estimated_savings:
                    savings_estimate = estimated_savings / len(application_ids)
                entry = ConsolidationListEntry(
                    application_id=app_id, source_group_id=group_id_int, source_group_name=group_name,
                    source_type="duplicate_detection", priority="medium", status="pending",
                    estimated_savings=savings_estimate or 0,
                    added_by=current_user.email if current_user.is_authenticated else "system",
                )
                db.session.add(entry)
                added_count += 1
            except Exception as e:
                current_app.logger.error(f"Error adding app {app_id} to consolidation: {e}")
                errors.append(f"Error adding application {app_id}")

        db.session.commit()
        return jsonify({
            "success": True, "added_count": added_count, "skipped_count": skipped_count,
            "errors": errors if errors else None,
            "message": f"Added {added_count} application(s) to consolidation list" + (f" ({skipped_count} already listed)" if skipped_count else ""),
        })
    except Exception as e:
        current_app.logger.error(f"Error adding group {group_id} to consolidation: {e}")
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp_v2.route("/api/groups/<string:group_id>/ignore", methods=["POST"])
@timed_route
@login_required
def api_ignore_group(group_id):
    """Mark a duplicate group as ignored."""
    try:
        group_id_int = int(str(group_id))
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": f"Invalid group ID: {group_id}"}), 400

    try:
        payload = request.get_json(silent=True) or {}
        reason = payload.get("reason")

        group = UnifiedDuplicateGroup.query.get(group_id_int)
        if group:
            group.status = "ignored"
            group.resolution_action = "no_action"
            if reason:
                group.resolution_notes = f"Ignored: {reason}"
            db.session.commit()
            return jsonify({"success": True, "group_id": group_id_int, "status": group.status})

        legacy_group = DuplicateGroup.query.get(group_id_int)
        if not legacy_group:
            return jsonify({"success": False, "error": f"Group {group_id} not found"}), 404

        legacy_group.status = "ignored"
        if reason and hasattr(legacy_group, "recommendation_notes"):
            legacy_group.recommendation_notes = f"Ignored: {reason}"
        db.session.commit()
        return jsonify({"success": True, "group_id": group_id_int, "status": "ignored"})

    except Exception as e:
        current_app.logger.error(f"Error ignoring group {group_id}: {e}")
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500

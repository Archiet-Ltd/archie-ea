"""
Unified Duplicate Detection Routes

Core duplicate detection functionality consolidating enterprise-grade and simplified
duplicate detection into a single route set while preserving all existing endpoints.

DISTINCT ROLE:
- This module provides CORE duplicate detection functionality (REQUIRED)
- Contains enterprise and simple dashboard routes
- Provides duplicate group management and analysis
- Registered at: /duplicate-detection

RELATIONSHIP:
- unified_duplicate_routes.py = Core duplicate detection (this file, REQUIRED)
- ai_dedupe_routes.py = AI enhancements (OPTIONAL, extends this module)

Phase 2: Route consolidation (2 → 1) with full preservation
"""

from werkzeug.exceptions import HTTPException
import json
import logging  # dead-code-ok
from datetime import datetime

from flask import (  # dead-code-ok
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import login_required
from app.middleware.tenant_decorators import platform_admin_required

from .. import db
from ..models.application_duplicate_detection import (  # dead-code-ok
    DuplicateDetectionRun,
    DuplicateGroup,
)
from ..models.unified_duplicate_detection import (
    UnifiedDetectionRun,
    UnifiedDuplicateGroup,
    unified_group_members,
)
from ..services.unified_duplicate_detection_service import (
    UnifiedDuplicateDetectionService,
)
from ..services.unified_duplicate_service import UnifiedDuplicateService
from app.utils.api_response import error_response
from app.utils.pagination import safe_int_arg
logger = logging.getLogger(__name__)

# Create unified blueprint
unified_duplicate_bp = Blueprint(
    "unified_duplicate", __name__, url_prefix="/duplicate-detection"
)

# Service instance
unified_service = UnifiedDuplicateDetectionService()

# Try to import AI service (optional - requires sentence_transformers)
try:
    from ..services.ai_duplicate_detection_service import ai_detection_service

    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False
    ai_detection_service = None


# === ENTERPRISE-GRADE ROUTES (from duplicate_detection_routes.py) ===


@unified_duplicate_bp.route("/")
@login_required
def enterprise_dashboard():
    """Enterprise duplicate detection dashboard — redirects to unified simple dashboard."""
    return redirect(url_for("unified_duplicate.simple_dashboard"))


@unified_duplicate_bp.route("/enterprise/run-detection", methods=["POST"])
@login_required
@platform_admin_required
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

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Enterprise detection route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/enterprise/analysis/<int:application_id>")
@login_required
def get_enterprise_analysis(application_id):
    """Get enterprise duplicate analysis for application"""
    try:
        result = unified_service.get_duplicate_analysis_for_application(application_id)

        if result["success"]:
            return jsonify(result), 200
        else:
            return jsonify(result), 400

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Enterprise analysis route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/enterprise/groups")
@login_required
def get_enterprise_groups():
    """Get enterprise duplicate groups"""
    try:
        groups = unified_service.get_duplicate_groups("enterprise")
        return jsonify({"success": True, "groups": groups}), 200

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Enterprise groups route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/enterprise/runs")
@login_required
def get_enterprise_runs():
    """Get enterprise detection runs"""
    try:
        runs = unified_service.get_detection_runs("enterprise")
        return jsonify({"success": True, "runs": runs}), 200

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Enterprise runs route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# === SIMPLIFIED ROUTES (from simple_duplicate_routes.py) ===


@unified_duplicate_bp.route("/simple")
@login_required
def simple_dashboard():
    """Simple duplicate detection dashboard"""
    from config import CurrencyConfig

    currency_symbol = CurrencyConfig.get_currency_config()["symbol"]
    return render_template(
        "duplicate_detection/dashboard.html", currency_symbol=currency_symbol
    )


@unified_duplicate_bp.route("/simple/run-detection", methods=["POST"])
@login_required
@platform_admin_required
def run_simple_detection():
    """Run simple duplicate detection with strategy support."""
    try:
        data = request.get_json() or {}
        strategy = data.get("strategy", "hybrid")
        similarity_threshold = data.get("similarity_threshold", 0.55)

        # Validate strategy
        if strategy not in ["fast", "hybrid", "enhanced"]:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Invalid strategy. Use: fast, hybrid, enhanced",
                    }
                ),
                400,
            )

        # Validate threshold
        if not (0.0 <= similarity_threshold <= 1.0):
            return jsonify(
                {"success": False, "error": "Threshold must be between 0 and 1"}
            ), 400

        result = unified_service.run_detection(
            similarity_threshold=similarity_threshold, strategy=strategy
        )

        if result["success"]:
            return jsonify(result), 200
        else:
            return jsonify(result), 400

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Simple detection route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/simple/run-hybrid", methods=["POST"])
@login_required
@platform_admin_required
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

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Hybrid detection route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/simple/groups")
@login_required
def get_simple_groups():
    """Get simple duplicate groups"""
    try:
        groups = unified_service.get_duplicate_groups("simple")
        return jsonify({"success": True, "groups": groups}), 200

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Simple groups route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/simple/runs")
@login_required
@platform_admin_required
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
            # Alias for dashboard template compatibility
            run_dict["duplicate_groups_found"] = run_dict.get("groups_found", 0)
            runs_data.append(run_dict)
        return jsonify({"success": True, "runs": runs_data}), 200

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Simple runs route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/simple/cleanup", methods=["POST"])
@login_required
@platform_admin_required
def cleanup_stale_data():
    """Clean up stale duplicate detection data (POST only)"""
    try:
        result = unified_service.cleanup_stale_data()

        if result["success"]:
            return jsonify(result), 200
        else:
            return jsonify(result), 400

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Cleanup route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route(
    "/simple/delete-duplicates/<int:group_id>", methods=["POST"]
)
@login_required
def delete_duplicates(group_id):
    """Delete duplicate applications in a group"""
    try:
        data = request.get_json() or {}
        keep_app_id = data.get("keep_app_id")

        # Ensure keep_app_id is int (frontend may send string from dataset)
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

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Delete duplicates route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/simple/api/bulk-delete-duplicates", methods=["POST"])
@login_required
def bulk_delete_duplicates_api():
    """
    Bulk delete duplicates across multiple groups.
    Expects: { group_selections: { group_id: keep_app_id, ... } }
    """
    try:
        data = request.get_json() or {}
        group_selections = data.get("group_selections", {})

        if not group_selections:
            return jsonify({"success": False, "error": "No groups provided"}), 400

        results = {
            "success": True,
            "total_groups_processed": 0,
            "total_deleted": 0,
            "successful_groups": [],
            "failed_groups": [],
            "errors": [],
        }

        for group_id_str, keep_app_id in group_selections.items():
            try:
                group_id = int(group_id_str)
                if keep_app_id:
                    result = unified_service.delete_duplicates_keep_one(
                        group_id, int(keep_app_id)
                    )
                else:
                    result = unified_service.bulk_delete_duplicates_keep_best(group_id)

                if result.get("success"):
                    results["total_groups_processed"] += 1
                    results["total_deleted"] += result.get("deleted_count", 0)
                    results["successful_groups"].append(group_id)
                else:
                    results["failed_groups"].append(group_id)
                    results["errors"].append(
                        f"Group {group_id}: {result.get('error', 'Unknown error')}"
                    )

            except HTTPException:

                raise

            except Exception as e:
                results["failed_groups"].append(group_id_str)
                current_app.logger.error(f"Error processing group {group_id_str}: {e}")
                results["errors"].append(f"Group {group_id_str}: processing failed")

        if results["failed_groups"]:
            results["success"] = len(results["successful_groups"]) > 0

        return jsonify(results), 200

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Bulk delete duplicates error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/simple/savings/<int:group_id>")
@login_required
def get_group_savings(group_id):
    """Get estimated savings for a duplicate group"""
    try:
        group = UnifiedDuplicateGroup.query.get(group_id)
        if not group:
            return jsonify({"success": False, "error": "Group not found"}), 404

        savings = unified_service._estimate_group_savings(group)
        return jsonify({"success": True, "savings": savings}), 200

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Savings route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/simple/api/statistics")
@login_required
def get_simple_statistics():
    """Get application consolidation statistics (simple mode)"""
    try:
        # Latest run for simple/fast strategies
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
            latest_run.estimated_savings
            if latest_run and latest_run.estimated_savings
            else 0
        )

        return jsonify(
            {
                "success": True,
                "total_groups": total_groups,
                "high_priority_groups": high_priority_groups,
                "total_estimated_savings": total_estimated_savings,
                "latest_run": {
                    "id": latest_run.id,
                    "run_name": latest_run.run_name,
                    "status": latest_run.status,
                    "groups_found": latest_run.groups_found or 0,
                    "applications_analyzed": latest_run.applications_analyzed or 0,
                    "created_at": latest_run.created_at.isoformat()
                    if latest_run
                    else None,
                }
                if latest_run
                else None,
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Simple statistics route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/simple/api/groups")
@login_required
def get_simple_groups_api():
    """Get all application consolidation groups"""
    try:
        groups = unified_service.get_duplicate_groups(
            "simple", include_applications=False
        )
        return jsonify({"success": True, "groups": groups})

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Simple groups API route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/simple/api/element-groups")
@login_required
def get_element_duplicate_groups_api():
    """H-01: ArchiMate element duplicate groups (exact-name, per layer/type).

    Computed live on every call — unlike application detection, this needs no
    stored 'run' because it's a cheap exact-match scan over the ArchiMate
    catalogue, so there is no latest_run/never-run state for it to get stuck in.
    """
    try:
        groups = unified_service.get_archimate_element_duplicate_groups()
        total_elements = sum(g["element_count"] for g in groups)
        return jsonify(
            {
                "success": True,
                "groups": groups,
                "total_groups": len(groups),
                "total_duplicated_elements": total_elements,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        current_app.logger.error(f"Element duplicate groups route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/simple/api/run-detection", methods=["POST"])
@login_required
@platform_admin_required
def run_simple_detection_api():
    """Run application consolidation detection API endpoint"""
    try:
        # Check if tables exist first
        try:
            db.session.execute(  # tenant-exempt: system table (existence check)
                db.text("SELECT 1 FROM unified_duplicate_groups LIMIT 1")  # tenant-exempt
            )
        except HTTPException:
            raise
        except Exception as e:
            current_app.logger.warning("Consolidation tables not found: %s", e)
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Application consolidation tables not found. Please run the setup script first: python scripts/setup/setup_simple_duplicate.py",
                    }
                ),
                400,
            )

        data = request.get_json() or {}
        threshold = data.get("threshold", 0.7)
        method = data.get("method", "fast")  # fast, enhanced, hybrid

        # Route to appropriate detection method
        if method == "enhanced":
            # Use enhanced consolidation detection service (different table structure)
            try:
                detection_run = unified_service.run_duplicate_detection(
                    application_ids=None  # Use all applications
                )
                # Convert enhanced groups to simple format for display
                # For now, return success and let user know to check enhanced dashboard
                return jsonify(
                    {
                        "success": detection_run.get("success", False),
                        "run_id": detection_run.get("run_id"),
                        "message": detection_run.get(
                            "message", "Enhanced detection completed"
                        ),
                        "method": "enhanced",
                        "groups_found": detection_run.get("groups_found", 0),
                        "applications_analyzed": detection_run.get(
                            "applications_analyzed", 0
                        ),
                        "duration_seconds": detection_run.get("duration_seconds"),
                        "note": "Enhanced results are stored in duplicate_groups table. Use /duplicate-detection/ to view.",
                    }
                )
            except HTTPException:
                raise
            except Exception as e:
                db.session.rollback()  # Ensure session is clean
                current_app.logger.error(f"Enhanced detection failed: {e}")
                import traceback

                traceback.print_exc()
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Enhanced detection failed. Please ensure business processes and capabilities are mapped to applications.",
                        }
                    ),
                    500,
                )

        elif method == "hybrid":
            # Hybrid approach: hash-based exact + fuzzy + simple
            try:
                run = unified_service.run_detection_hybrid(threshold)

                return jsonify(
                    {
                        "success": run.get("success", False),
                        "run_id": run.get("run_id"),
                        "groups_found": run.get("groups_found", 0),
                        "exact_matches": run.get("exact_matches", 0),
                        "fuzzy_matches": run.get("fuzzy_matches", 0),
                        "applications_analyzed": run.get("applications_analyzed", 0),
                        "estimated_savings": run.get("estimated_savings", 0),
                        "duration_seconds": run.get("duration_seconds"),
                        "message": run.get("message", ""),
                        "warning": run.get("warning"),
                        "method": "hybrid",
                    }
                )
            except HTTPException:
                raise
            except Exception as e:
                db.session.rollback()  # Ensure session is clean
                current_app.logger.exception(f"Hybrid detection failed: {e}")
                # Fall back to simple
                run = unified_service.run_detection(threshold)
                if not run.get("success"):
                    # Both methods failed — there is no result to report.
                    return jsonify(
                        {
                            "success": False,
                            "error": run.get("message")
                            or "Duplicate detection failed for both the hybrid and simple methods",
                        }
                    ), 500
                # error-signalling-ok: the simple method ran and produced real results; the warning below tells the caller the hybrid method was not used
                return jsonify(
                    {
                        "success": run.get("success", False),
                        "run_id": run.get("run_id"),
                        "message": run.get("message"),
                        "method": "fast",
                        "groups_found": run.get("groups_found", 0),
                        "exact_matches": run.get("exact_matches", 0),
                        "fuzzy_matches": run.get("fuzzy_matches", 0),
                        "applications_analyzed": run.get("applications_analyzed", 0),
                        "estimated_savings": run.get("estimated_savings", 0),
                        "duration_seconds": run.get("duration_seconds"),
                        "warning": run.get("warning")
                        or "Hybrid method failed, used simple method instead",
                    }
                )
        else:
            # Fast/Simple method (default)
            run = unified_service.run_detection(threshold)

            return jsonify(
                {
                    "success": run.get("success", False),
                    "run_id": run.get("run_id"),
                    "message": run.get("message") or run.get("error"),
                    "method": "fast",
                    "groups_found": run.get("groups_found", 0),
                    "exact_matches": run.get("exact_matches", 0),
                    "fuzzy_matches": run.get("fuzzy_matches", 0),
                    "applications_analyzed": run.get("applications_analyzed", 0),
                    "estimated_savings": run.get("estimated_savings", 0),
                    "duration_seconds": run.get("duration_seconds"),
                    "warning": run.get("warning"),
                }
            )

    except HTTPException:

        raise

    except Exception as e:
        db.session.rollback()  # Ensure session is clean
        current_app.logger.error(f"Simple detection API error: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/api/find-similar/<int:app_id>")
@login_required
def find_similar_applications_api(app_id):
    """Find applications similar to a specific application"""
    try:
        from difflib import SequenceMatcher

        from app.models.application_portfolio import ApplicationComponent

        method = request.args.get("method", "fast")
        threshold = float(request.args.get("threshold", 0.5))

        # Get the target application
        target_app = ApplicationComponent.query.get(app_id)
        if not target_app:
            return jsonify({"success": False, "error": "Application not found"}), 404

        # Get other applications (capped to prevent unbounded queries)
        all_apps = (
            ApplicationComponent.query.filter(ApplicationComponent.id != app_id)
            .limit(500)
            .all()
        )

        similar_apps = []

        for app in all_apps:
            similarity = 0.0

            if method in ("fast", "hybrid"):
                # Name-based similarity
                name_sim = SequenceMatcher(
                    None, (target_app.name or "").lower(), (app.name or "").lower()
                ).ratio()
                similarity = max(similarity, name_sim)

            if method in ("enhanced", "hybrid"):
                # Description similarity
                if target_app.description and app.description:
                    desc_sim = SequenceMatcher(
                        None, target_app.description.lower(), app.description.lower()
                    ).ratio()
                    similarity = max(similarity, desc_sim * 0.8)

                # Type matching
                if target_app.component_type and app.component_type:
                    if target_app.component_type == app.component_type:
                        similarity = max(similarity, 0.3)

                # Status matching
                if target_app.deployment_status and app.deployment_status:
                    if target_app.deployment_status == app.deployment_status:
                        similarity = min(1.0, similarity + 0.1)

            # Convert to percentage
            similarity_pct = int(similarity * 100)

            if similarity_pct >= int(threshold * 100):
                similar_apps.append(
                    {
                        "id": app.id,
                        "name": app.name,
                        "type": app.component_type,
                        "status": app.deployment_status,
                        "description": (app.description or "")[:100] + "..."
                        if app.description and len(app.description) > 100
                        else app.description,
                        "similarity": similarity_pct,
                    }
                )

        # Sort by similarity descending
        similar_apps.sort(key=lambda x: x["similarity"], reverse=True)

        # Limit to top 20
        similar_apps = similar_apps[:20]

        return jsonify(
            {
                "success": True,
                "target_app": {"id": target_app.id, "name": target_app.name},
                "similar_apps": similar_apps,
                "count": len(similar_apps),
                "method": method,
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Find similar apps API error: {e}")
        import traceback

        traceback.print_exc()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/simple/group/<int:group_id>")
@login_required
def simple_group_detail(group_id):
    """Application consolidation group detail page"""
    try:
        from config import CurrencyConfig
        from ..models.unified_duplicate_detection import UnifiedDuplicateGroup
        from app.models.application_duplicate_detection import DuplicateGroup

        # Get the unified group
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
        ).mappings()

        for app in app_rows:
            technology_stack = app.get("technology_stack")
            if technology_stack is not None and not isinstance(technology_stack, str):
                technology_stack = str(technology_stack)
            group_payload["applications"].append(
                {
                    "id": app.get("id"),
                    "name": app.get("name"),
                    "description": app.get("description"),
                    "status": app.get("deployment_status"),
                    "application_owner": app.get("application_owner"),
                    "technology_stack": technology_stack,
                    "annual_cost": None,
                }
            )

        # Provide dashboard_registry_url to prevent template errors
        try:
            from flask import url_for

            dashboard_registry_url = url_for("dynamic_dashboards.model_registry_index")
        except HTTPException:
            raise
        except Exception as e:
            current_app.logger.debug("Could not resolve dashboard registry URL: %s", e)
            dashboard_registry_url = "/auto-dashboard/registry"

        # Load recommendation and pairwise analyses from the legacy DuplicateGroup model
        recommendation = None
        pairwise_analyses = []
        legacy_group = DuplicateGroup.query.get(group_id)
        if legacy_group:
            rec = legacy_group.consolidation_recommendations.first()
            if rec:
                recommendation = {
                    "id": rec.id,
                    "recommendation_type": rec.recommendation_type,
                    "status": rec.status,
                    "target_justification": rec.target_justification,
                    "target_application": (
                        {
                            "name": rec.target_application.name,
                            "description": rec.target_application.description,
                        }
                        if rec.target_application_id
                        and hasattr(rec, "target_application")
                        and rec.target_application
                        else None
                    ),
                    "source_applications": rec.source_applications or [],
                    "implementation_approach": rec.implementation_approach,
                    "estimated_timeline_months": rec.estimated_timeline_months,
                    "implementation_cost": rec.implementation_cost,
                    "annual_savings": rec.annual_savings,
                    "payback_period_months": rec.payback_period_months,
                    "roi_percentage": rec.roi_percentage,
                    "overall_risk_level": rec.overall_risk_level,
                    "confidence_score": rec.confidence_score,
                    "implementation_phases": rec.implementation_phases or [],
                }
            pairwise_analyses = [
                {
                    "id": pa.id,
                    "application_1_id": pa.application_1_id,
                    "application_2_id": pa.application_2_id,
                    "overall_similarity_score": pa.overall_similarity_score,
                    "confidence_level": pa.confidence_level,
                    "name_similarity": pa.name_similarity,
                    "functional_similarity": pa.functional_similarity,
                    "capability_similarity": pa.capability_similarity,
                    "technical_similarity": pa.technical_similarity,
                    "data_similarity": pa.data_similarity,
                    "shared_processes": pa.shared_processes or [],
                }
                for pa in legacy_group.pairwise_analyses.order_by(
                    db.text("overall_similarity_score DESC")  # tenant-filtered: scoped via parent FK
                ).limit(100).all()
            ]

        return render_template(
            "duplicate_detection/group_detail.html",
            group=group_payload,
            recommendation=recommendation,
            pairwise_analyses=pairwise_analyses,
            currency_symbol=currency_symbol,
            dashboard_registry_url=dashboard_registry_url,
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error viewing consolidation group: {e}")
        return "Group not found", 404


# Backward compatibility redirect for old URL pattern
@unified_duplicate_bp.route("/group/<int:group_id>")
@login_required
def group_detail_redirect(group_id):
    """Redirect old group detail URL to new location"""
    from flask import redirect, url_for

    return redirect(url_for("unified_duplicate.simple_group_detail", group_id=group_id))


# === UNIFIED INTERFACE ROUTES ===


@unified_duplicate_bp.route("/unified")
@login_required
def unified_dashboard():
    """Unified duplicate detection dashboard"""
    return render_template("unified_duplicate/dashboard.html")


@unified_duplicate_bp.route("/unified/run-detection", methods=["POST"])
@login_required
@platform_admin_required
def run_unified_detection():
    """Run unified duplicate detection with specified mode"""
    try:
        data = request.get_json() or {}
        mode = data.get("mode", "enterprise")

        # Remove mode from kwargs for the service call
        kwargs = {k: v for k, v in data.items() if k != "mode"}

        result = unified_service.run_unified_detection(mode, **kwargs)

        if result["success"]:
            return jsonify(result), 200
        else:
            return jsonify(result), 400

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Unified detection route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/unified/groups")
@login_required
def get_unified_groups():
    """Get all duplicate groups (both enterprise and simple)"""
    try:
        mode = request.args.get("mode", "all")
        groups = unified_service.get_duplicate_groups(mode)
        return jsonify({"success": True, "groups": groups}), 200

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Unified groups route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/unified/runs")
@login_required
def get_unified_runs():
    """Get all detection runs (both enterprise and simple)"""
    try:
        mode = request.args.get("mode", "all")
        runs = unified_service.get_detection_runs(mode)
        return jsonify({"success": True, "runs": runs}), 200

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Unified runs route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/unified/stats")
@login_required
def get_unified_stats():
    """Get unified statistics for duplicate detection"""
    try:
        # Get enterprise stats
        enterprise_runs = DuplicateDetectionRun.query.count()
        enterprise_groups = DuplicateGroup.query.count()

        # Get simple stats
        simple_runs = UnifiedDetectionRun.query.count()
        simple_groups = UnifiedDuplicateGroup.query.count()

        stats = {
            "enterprise": {
                "total_runs": enterprise_runs,
                "total_groups": enterprise_groups,
            },
            "simple": {"total_runs": simple_runs, "total_groups": simple_groups},
            "combined": {
                "total_runs": enterprise_runs + simple_runs,
                "total_groups": enterprise_groups + simple_groups,
            },
        }

        return jsonify({"success": True, "stats": stats}), 200

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Unified stats route error: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/dashboard")
@login_required
def duplicate_dashboard():
    """Duplicate detection dashboard (AJAX-aware)."""
    try:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(
                {
                    "status": "success",
                    "message": "Duplicate detection dashboard loaded",
                    "services": ["enterprise", "simple", "hybrid"],
                }
            )

        return render_template("duplicate_detection/dashboard.html")

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(
            "Error loading duplicate detection dashboard: %s", e, exc_info=True
        )
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(
                {"status": "error", "message": "An internal error occurred"}
            ), 500
        else:
            return render_template(
                "duplicate_detection/dashboard.html",
                load_error="Duplicate detection results could not be read.",
            )


# === AI DETECTION ROUTES (from ai_dedupe_routes.py) ===


@unified_duplicate_bp.route("/ai/dashboard")
@login_required
def ai_dashboard():
    """AI-powered duplicate detection dashboard"""
    if not AI_AVAILABLE:
        # Show installation instructions when AI is not available
        return render_template(
            "dedupe/ai_insights.html",
            ai_stats={"detections_count": 0, "average_processing_time": 0},
            recent_runs=[],
            global_stats={},
            ai_strategies=["ai_enhanced", "semantic_only", "business_aware"],
            performance_metrics={},
            ai_unavailable=True,
            error="AI features require additional dependencies. Please install with: pip install -r requirements_ai.txt",
        )

    try:
        # Get AI-specific statistics
        ai_stats = ai_detection_service.get_performance_metrics()

        # Get recent AI runs
        recent_ai_runs = UnifiedDuplicateService.get_runs(
            strategy="ai_enhanced", limit=5
        )

        # Get global statistics
        global_stats = UnifiedDuplicateService.get_statistics()

        return render_template(
            "dedupe/ai_insights.html",
            ai_stats=ai_stats,
            recent_runs=recent_ai_runs,
            global_stats=global_stats,
            ai_strategies=["ai_enhanced", "semantic_only", "business_aware"],
            performance_metrics=ai_stats,
            ai_unavailable=False,
        )
    except HTTPException:
        raise
    except Exception as e:
        current_app.logger.error(f"Error loading AI dashboard: {e}")
        # No zeroed ai_stats: "0 detections, 0s average processing time" is a
        # measurement, and this path measured nothing.
        return render_template(
            "dedupe/ai_insights.html",
            ai_stats=None,
            ai_insights=[],
            run=None,
            recent_runs=[],
            global_stats=None,
            ai_strategies=["ai_enhanced", "semantic_only", "business_aware"],
            performance_metrics=None,
            ai_unavailable=True,
            error="An error occurred loading the AI dashboard.",
            load_error="AI deduplication insights could not be read.",
        )


@unified_duplicate_bp.route("/ai/analyze", methods=["GET", "POST"])
@login_required
def ai_analyze():
    """AI-powered duplicate analysis interface"""
    if request.method == "GET":
        return render_template(
            "dedupe/ai_insights.html",
            strategies=["ai_enhanced", "semantic_only", "business_aware"],
            default_threshold=0.65,
        )

    # Handle POST request for analysis
    try:
        data = request.get_json() or {}
        strategy = data.get("strategy", "ai_enhanced")
        threshold = float(data.get("threshold", 0.65))
        config = data.get("config", {})

        if not AI_AVAILABLE:
            return jsonify({"success": False, "error": "AI service not available"}), 503

        # Run AI detection
        result = ai_detection_service.detect_duplicates(
            strategy=strategy, threshold=threshold, config=config
        )

        if result["success"]:
            return jsonify(
                {
                    "success": True,
                    "run_id": result["run_id"],
                    "duplicates": result["duplicates"],
                    "statistics": result["statistics"],
                    "insights": result["ai_insights"],
                    "processing_time": result["processing_time"],
                }
            )
        else:
            return jsonify(result), 500

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"AI analysis failed: {e}")
        return jsonify(
            {"success": False, "error": "AI analysis failed. Please try again."}
        ), 500


@unified_duplicate_bp.route("/ai/insights/<int:run_id>")
@login_required
def ai_insights(run_id):
    """View AI insights for a specific detection run"""
    try:
        run = UnifiedDetectionRun.query.get(run_id)
        if run is None:
            return render_template("errors/404.html"), 404

        # Get AI-specific insights
        if run.algorithm_version.startswith("ai_"):
            # Load AI metadata from groups
            groups = UnifiedDuplicateGroup.query.filter_by(run_id=run_id).all()

            ai_insights = {
                "algorithm_version": run.algorithm_version,
                "total_groups": len(groups),
                "high_confidence_groups": len(
                    [g for g in groups if g.similarity_score > 0.8]
                ),
                "medium_confidence_groups": len(
                    [g for g in groups if 0.6 <= g.similarity_score <= 0.8]
                ),
                "low_confidence_groups": len(
                    [g for g in groups if g.similarity_score < 0.6]
                ),
                "average_confidence": sum(g.similarity_score for g in groups)
                / len(groups)
                if groups
                else 0,
                "groups_with_ai_insights": len(
                    [g for g in groups if g.metadata and "ai_insights" in g.metadata]
                ),
            }

            # Extract global insights
            if groups and groups[0].metadata:
                try:
                    metadata = json.loads(groups[0].metadata)
                    if "ai_insights" in metadata:
                        ai_insights["global_insights"] = metadata["ai_insights"]
                except (json.JSONDecodeError, TypeError, KeyError):
                    logger.exception("Failed to JSON parsing")
                    pass
        else:
            ai_insights = {"error": "Not an AI detection run"}

        return render_template(
            "dedupe/ai_insights.html", run=run, ai_insights=ai_insights
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error loading AI insights: {e}")
        return render_template("errors/500.html"), 500


# ===== AI API Endpoints =====


@unified_duplicate_bp.route("/ai/api/detect", methods=["POST"])
@login_required
def api_ai_detect():
    """
    Run AI-powered duplicate detection.

    POST JSON:
    {
        "strategy": "ai_enhanced|semantic_only|business_aware",
        "threshold": 0.65,
        "config": {
            "include_semantic": true,
            "include_business": true,
            "include_structural": true
        }
    }
    """
    try:
        if not AI_AVAILABLE:
            return jsonify({"success": False, "error": "AI service not available"}), 503

        data = request.get_json() or {}
        strategy = data.get("strategy", "ai_enhanced")
        threshold = float(data.get("threshold", 0.65))
        config = data.get("config", {})

        # Validate strategy
        valid_strategies = ["ai_enhanced", "semantic_only", "business_aware"]
        if strategy not in valid_strategies:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f"Invalid strategy. Must be one of: {valid_strategies}",
                    }
                ),
                400,
            )

        # Validate threshold
        if not 0.0 <= threshold <= 1.0:
            return (
                jsonify(
                    {"success": False, "error": "Threshold must be between 0.0 and 1.0"}
                ),
                400,
            )

        # Run AI detection
        result = ai_detection_service.detect_duplicates(
            strategy=strategy, threshold=threshold, config=config
        )

        if not result.get("success"):
            # A failed run is not a 200. The service has already logged the
            # internal detail; the client gets a product-level message only.
            current_app.logger.error(
                "AI duplicate detection reported failure: %s", result.get("error")
            )
            return error_response(
                "Duplicate detection could not be completed.",
                code="DUPLICATE_DETECTION_FAILED",
                status_code=502,
            )

        return jsonify(result)

    except ValueError as e:
        current_app.logger.warning(f"Invalid AI detection parameters: {e}")
        return jsonify({"success": False, "error": "Invalid detection parameters"}), 400
    except HTTPException:
        raise
    except Exception as e:
        current_app.logger.error(f"AI detection API failed: {e}")
        return jsonify(
            {"success": False, "error": "AI detection failed. Please try again."}
        ), 500


@unified_duplicate_bp.route("/ai/api/feedback", methods=["POST"])
@login_required
def api_feedback():
    """
    Submit user feedback for adaptive learning.

    POST JSON:
    {
        "duplicate_id": 123,
        "action": "accept|reject|modify",
        "confidence": 85,
        "notes": "User feedback notes"
    }
    """
    try:
        if not AI_AVAILABLE:
            return jsonify({"success": False, "error": "AI service not available"}), 503

        data = request.get_json() or {}
        duplicate_id = data.get("duplicate_id")
        action = data.get("action")
        confidence = data.get("confidence")
        data.get("notes", "")

        if not duplicate_id or not action or confidence is None:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "duplicate_id, action, and confidence are required",
                    }
                ),
                400,
            )

        # Validate action
        valid_actions = ["accept", "reject", "modify"]
        if action not in valid_actions:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f"Invalid action. Must be one of: {valid_actions}",
                    }
                ),
                400,
            )

        # Validate confidence
        if not 1 <= confidence <= 100:
            return jsonify(
                {"success": False, "error": "Confidence must be between 1 and 100"}
            ), 400

        # Process feedback
        ai_detection_service.process_user_feedback(
            duplicate_id=duplicate_id, user_action=action, confidence=confidence
        )

        return jsonify({"success": True, "message": "Feedback processed successfully"})

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Feedback processing failed: {e}")
        return jsonify(
            {"success": False, "error": "Feedback processing failed. Please try again."}
        ), 500


@unified_duplicate_bp.route("/ai/api/performance", methods=["GET"])
@login_required
def api_performance():
    """Get AI detection performance metrics"""
    try:
        if not AI_AVAILABLE:
            return jsonify({"success": False, "error": "AI service not available"}), 503

        metrics = ai_detection_service.get_performance_metrics()
        return jsonify({"success": True, "metrics": metrics})
    except HTTPException:
        raise
    except Exception as e:
        current_app.logger.error(f"Error getting performance metrics: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/ai/api/compare", methods=["POST"])
@login_required
def api_compare_strategies():
    """
    Compare different AI detection strategies.

    POST JSON:
    {
        "threshold": 0.65,
        "strategies": ["ai_enhanced", "semantic_only", "business_aware"]
    }
    """
    try:
        if not AI_AVAILABLE:
            return jsonify({"success": False, "error": "AI service not available"}), 503

        data = request.get_json() or {}
        threshold = float(data.get("threshold", 0.65))
        strategies = data.get(
            "strategies", ["ai_enhanced", "semantic_only", "business_aware"]
        )

        comparison_results = {}

        for strategy in strategies:
            try:
                result = ai_detection_service.detect_duplicates(
                    strategy=strategy, threshold=threshold
                )

                if result.get("success"):
                    comparison_results[strategy] = {
                        "duplicates_found": result["statistics"]["total_duplicates"],
                        "high_confidence": result["statistics"]["high_confidence"],
                        "medium_confidence": result["statistics"]["medium_confidence"],
                        "low_confidence": result["statistics"]["low_confidence"],
                        "average_confidence": result["statistics"][
                            "average_confidence"
                        ],
                        "total_savings": result["statistics"]["total_savings"],
                        "processing_time": result["processing_time"],
                        "quality_score": result["ai_insights"]["quality_score"],
                    }
                else:
                    current_app.logger.error(
                        "Strategy %s failed: %s", strategy, result.get("error")
                    )
                    comparison_results[strategy] = {
                        "error": "Strategy could not be evaluated."
                    }

            except HTTPException:

                raise

            except Exception as e:
                current_app.logger.error(f"Strategy {strategy} comparison failed: {e}")
                comparison_results[strategy] = {"error": "Strategy comparison failed"}

        if comparison_results and all(
            "error" in r for r in comparison_results.values()
        ):
            # Every strategy failed - that is a server-side failure, not a 200.
            return error_response(
                "Strategy comparison could not be completed.",
                code="STRATEGY_COMPARISON_FAILED",
                status_code=502,
            )

        return jsonify(
            {"success": True, "threshold": threshold, "comparison": comparison_results}
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Strategy comparison failed: {e}")
        return jsonify(
            {"success": False, "error": "Strategy comparison failed. Please try again."}
        ), 500


@unified_duplicate_bp.route("/ai/api/optimize-threshold", methods=["POST"])
@login_required
def api_optimize_threshold():
    """
    Get optimized threshold for a strategy based on historical feedback.

    POST JSON:
    {
        "strategy": "ai_enhanced"
    }
    """
    try:
        if not AI_AVAILABLE:
            return jsonify({"success": False, "error": "AI service not available"}), 503

        data = request.get_json() or {}
        strategy = data.get("strategy", "ai_enhanced")

        # Get optimized threshold from learning engine
        optimized_threshold = (
            ai_detection_service.learning_engine.get_optimized_threshold(strategy)
        )

        return jsonify(
            {
                "success": True,
                "strategy": strategy,
                "optimized_threshold": optimized_threshold,
                "default_threshold": 0.65,
                "recommendation": _get_threshold_recommendation(optimized_threshold),
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Threshold optimization failed: {e}")
        return jsonify(
            {
                "success": False,
                "error": "Threshold optimization failed. Please try again.",
            }
        ), 500


def _get_threshold_recommendation(threshold: float) -> str:
    """Get threshold recommendation based on value"""
    if threshold > 0.8:
        return "High threshold - very conservative, only strong duplicates"
    elif threshold > 0.6:
        return "Medium threshold - balanced approach"
    else:
        return "Low threshold - more aggressive, may include false positives"


# === INTELLIGENCE API ENDPOINTS (confidence, merge preview, impact) ===


@unified_duplicate_bp.route("/simple/api/group/<int:group_id>/confidence")
@login_required
def api_group_confidence(group_id):
    """
    Get per-pair confidence scoring data for a duplicate group.

    Returns similarity scores between each pair of applications in the group,
    plus aggregate confidence statistics.
    """
    try:
        from difflib import SequenceMatcher

        from sqlalchemy import select

        from app.models.application_portfolio import ApplicationComponent

        group = UnifiedDuplicateGroup.query.get(group_id)
        if not group:
            return jsonify({"success": False, "error": "Group not found"}), 404

        # Get all member apps with their similarity_to_primary from the association table
        stmt = select(
            unified_group_members.c.application_id,
            unified_group_members.c.similarity_to_primary,
            unified_group_members.c.is_primary,
        ).where(unified_group_members.c.group_id == group_id)
        members = db.session.execute(stmt).fetchall()  # tenant-filtered: scoped via parent FK (group_id)

        app_ids = [m.application_id for m in members]
        apps = ApplicationComponent.query.filter(
            ApplicationComponent.id.in_(app_ids)
        ).all()
        app_map = {a.id: a for a in apps}
        primary_id = next((m.application_id for m in members if m.is_primary), None)

        # Build per-pair similarity data
        pairs = []
        scores = []
        for i, m1 in enumerate(members):
            for m2 in members[i + 1 :]:
                a1 = app_map.get(m1.application_id)
                a2 = app_map.get(m2.application_id)
                if not a1 or not a2:
                    continue

                # Compute name similarity
                name_sim = SequenceMatcher(
                    None, (a1.name or "").lower(), (a2.name or "").lower()
                ).ratio()

                # Compute description similarity
                desc_sim = 0.0
                if a1.description and a2.description:
                    desc_sim = SequenceMatcher(
                        None, a1.description.lower(), a2.description.lower()
                    ).ratio()

                # Use stored similarity_to_primary where available, else compute
                if m1.is_primary and m2.similarity_to_primary:
                    pair_score = float(m2.similarity_to_primary)
                elif m2.is_primary and m1.similarity_to_primary:
                    pair_score = float(m1.similarity_to_primary)
                else:
                    # Weighted combination: name 0.4, description 0.4, type match 0.2
                    type_sim = (
                        1.0
                        if (
                            a1.component_type and a1.component_type == a2.component_type
                        )
                        else 0.0
                    )
                    pair_score = name_sim * 0.4 + desc_sim * 0.4 + type_sim * 0.2

                scores.append(pair_score)
                confidence_level = (
                    "high"
                    if pair_score >= 0.8
                    else ("medium" if pair_score >= 0.6 else "low")
                )
                pairs.append(
                    {
                        "app1_id": a1.id,
                        "app1_name": a1.name,
                        "app2_id": a2.id,
                        "app2_name": a2.name,
                        "similarity_score": round(pair_score, 4),
                        "name_similarity": round(name_sim, 4),
                        "description_similarity": round(desc_sim, 4),
                        "confidence_level": confidence_level,
                        "match_type": group.duplicate_type or "fuzzy",
                    }
                )

        # Aggregate stats
        avg_score = sum(scores) / len(scores) if scores else 0
        distribution = {
            "high": len([s for s in scores if s >= 0.8]),
            "medium": len([s for s in scores if 0.6 <= s < 0.8]),
            "low": len([s for s in scores if s < 0.6]),
        }

        return jsonify(
            {
                "success": True,
                "data": {
                    "group_id": group_id,
                    "primary_app_id": primary_id,
                    "pair_count": len(pairs),
                    "pairs": pairs,
                    "aggregate": {
                        "avg_confidence": round(avg_score, 4),
                        "min_confidence": round(min(scores), 4) if scores else 0,
                        "max_confidence": round(max(scores), 4) if scores else 0,
                        "distribution": distribution,
                    },
                },
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error getting group confidence for {group_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/simple/api/group/<int:group_id>/merge-preview")
@login_required
def api_group_merge_preview(group_id):
    """
    Get field-level merge preview for a duplicate group.

    Shows which fields conflict between the primary app and duplicates,
    and provides a recommendation for each conflicting field.
    """
    try:
        from sqlalchemy import select

        from app.models.application_portfolio import ApplicationComponent

        group = UnifiedDuplicateGroup.query.get(group_id)
        if not group:
            return jsonify({"success": False, "error": "Group not found"}), 404

        primary_app_id = request.args.get("primary_app_id", type=int)

        # Get all member apps
        stmt = select(
            unified_group_members.c.application_id,
            unified_group_members.c.is_primary,
        ).where(unified_group_members.c.group_id == group_id)
        members = db.session.execute(stmt).fetchall()  # tenant-filtered: scoped via parent FK (group_id)
        app_ids = [m.application_id for m in members]
        apps = ApplicationComponent.query.filter(
            ApplicationComponent.id.in_(app_ids)
        ).all()
        app_map = {a.id: a for a in apps}

        # Determine primary
        if primary_app_id and primary_app_id in app_map:
            primary = app_map[primary_app_id]
        else:
            stored_primary_id = next(
                (m.application_id for m in members if m.is_primary), None
            )
            if stored_primary_id and stored_primary_id in app_map:
                primary = app_map[stored_primary_id]
            elif apps:
                primary = apps[0]
            else:
                return jsonify(
                    {"success": False, "error": "No applications in group"}
                ), 404

        # Fields to compare for merge preview
        merge_fields = [
            ("name", "Name"),
            ("description", "Description"),
            ("vendor_name", "Vendor"),
            ("component_type", "Component Type"),
            ("deployment_status", "Deployment Status"),
            ("deployment_model", "Deployment Model"),
            ("business_domain", "Business Domain"),
            ("business_criticality", "Business Criticality"),
            ("business_owner", "Business Owner"),
            ("technical_owner", "Technical Owner"),
            ("total_cost_of_ownership", "Total Cost of Ownership"),
            ("license_cost", "License Cost"),
            ("technology_stack", "Technology Stack"),
        ]

        def get_field_val(app, field_name):
            val = getattr(app, field_name, None)
            if val is None:
                return None
            if isinstance(val, float):
                return round(val, 2)
            return str(val) if val else None

        # Build per-duplicate comparison
        duplicates = []
        fields_matching = 0
        fields_conflicting = 0
        fields_missing = 0

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

                # Recommend keeping the longer/more complete value for text fields
                if status == "conflict":
                    p_len = len(str(primary_val)) if primary_val else 0
                    d_len = len(str(dup_val)) if dup_val else 0
                    recommendation = (
                        "keep_primary" if p_len >= d_len else "keep_duplicate"
                    )
                elif status == "duplicate_only":
                    recommendation = "adopt_duplicate"
                else:
                    recommendation = "keep_primary"

                field_conflicts.append(
                    {
                        "field": field_name,
                        "label": field_label,
                        "primary_value": primary_val,
                        "duplicate_value": dup_val,
                        "status": status,
                        "recommendation": recommendation,
                    }
                )

            duplicates.append(
                {
                    "app_id": dup_app.id,
                    "app_name": dup_app.name,
                    "field_conflicts": field_conflicts,
                }
            )

        primary_data = {
            "id": primary.id,
            "name": primary.name,
        }
        for field_name, field_label in merge_fields:
            primary_data[field_name] = get_field_val(primary, field_name)

        return jsonify(
            {
                "success": True,
                "data": {
                    "group_id": group_id,
                    "primary": primary_data,
                    "duplicates": duplicates,
                    "merge_summary": {
                        "fields_matching": fields_matching,
                        "fields_conflicting": fields_conflicting,
                        "fields_missing": fields_missing,
                        "total_fields_compared": fields_matching
                        + fields_conflicting
                        + fields_missing,
                    },
                },
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error getting merge preview for {group_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/simple/api/group/<int:group_id>/impact")
@login_required
def api_group_impact(group_id):
    """
    Get dependency and impact analysis for applications in a duplicate group.

    Shows what business processes, capabilities, and integrations each application
    supports, to assess the risk and scope of consolidation.
    """
    try:
        from sqlalchemy import select

        from app.models.application_portfolio import ApplicationComponent

        group = UnifiedDuplicateGroup.query.get(group_id)
        if not group:
            return jsonify({"success": False, "error": "Group not found"}), 404

        # Get all member apps
        stmt = select(
            unified_group_members.c.application_id,
            unified_group_members.c.is_primary,
        ).where(unified_group_members.c.group_id == group_id)
        members = db.session.execute(stmt).fetchall()  # tenant-filtered: scoped via parent FK (group_id)
        app_ids = [m.application_id for m in members]
        primary_ids = {m.application_id for m in members if m.is_primary}
        apps = ApplicationComponent.query.filter(
            ApplicationComponent.id.in_(app_ids)
        ).all()

        total_processes = 0
        total_capabilities = 0
        total_integrations = 0
        app_impacts = []

        for app in (
            apps
        ):  # model-safety-ok: small fixed set (duplicate group typically 2-5 apps)
            # Count linked business processes
            process_count = 0
            if hasattr(
                app, "business_processes"
            ):  # model-safety-ok: optional relationship not guaranteed on ApplicationComponent
                try:
                    process_count = (
                        app.business_processes.count()  # model-safety-ok: small fixed set
                        if hasattr(
                            app.business_processes, "count"
                        )  # model-safety-ok: checking dynamic query vs list interface
                        else len(list(app.business_processes))
                    )
                except HTTPException:
                    raise
                except Exception as e:
                    current_app.logger.debug(
                        "Could not count processes for app %s: %s", app.id, e
                    )
                    process_count = 0

            # Count linked capabilities
            capability_count = 0
            if hasattr(
                app, "capabilities"
            ):  # model-safety-ok: optional relationship not guaranteed on ApplicationComponent
                try:
                    capability_count = (
                        app.capabilities.count()  # model-safety-ok: small fixed set
                        if hasattr(
                            app.capabilities, "count"
                        )  # model-safety-ok: checking dynamic query vs list interface
                        else len(list(app.capabilities))
                    )
                except HTTPException:
                    raise
                except Exception as e:
                    current_app.logger.debug(
                        "Could not count capabilities for app %s: %s", app.id, e
                    )
                    capability_count = 0

            # Count integrations (check for integration relationships)
            integration_count = 0
            if hasattr(
                app, "integrations"
            ):  # model-safety-ok: optional relationship not guaranteed on ApplicationComponent
                try:
                    integration_count = (
                        app.integrations.count()  # model-safety-ok: small fixed set
                        if hasattr(
                            app.integrations, "count"
                        )  # model-safety-ok: checking dynamic query vs list interface
                        else len(list(app.integrations))
                    )
                except HTTPException:
                    raise
                except Exception as e:
                    current_app.logger.debug(
                        "Could not count integrations for app %s: %s", app.id, e
                    )
                    integration_count = 0

            # User count
            user_count = app.user_count or app.user_base_size or 0

            # Dependency count: processes + capabilities + integrations
            dependency_count = process_count + capability_count + integration_count

            # Risk assessment
            if dependency_count > 10 or user_count > 500:
                risk_level = "high"
            elif dependency_count > 5 or user_count > 100:
                risk_level = "medium"
            else:
                risk_level = "low"

            total_processes += process_count
            total_capabilities += capability_count
            total_integrations += integration_count

            app_impacts.append(
                {
                    "app_id": app.id,
                    "name": app.name,
                    "is_primary": app.id in primary_ids,
                    "process_count": process_count,
                    "capability_count": capability_count,
                    "integration_count": integration_count,
                    "user_count": user_count,
                    "dependency_count": dependency_count,
                    "risk_level": risk_level,
                    "business_criticality": app.business_criticality or "Unknown",
                }
            )

        # Overall disruption estimate
        non_primary_impacts = [a for a in app_impacts if not a["is_primary"]]
        high_risk_count = len(
            [a for a in non_primary_impacts if a["risk_level"] == "high"]
        )

        return jsonify(
            {
                "success": True,
                "data": {
                    "group_id": group_id,
                    "applications": app_impacts,
                    "total_impact": {
                        "processes_affected": total_processes,
                        "capabilities_affected": total_capabilities,
                        "integrations_affected": total_integrations,
                        "total_users_affected": sum(
                            a["user_count"] for a in app_impacts
                        ),
                        "high_risk_apps": high_risk_count,
                    },
                    "consolidation_risk": "high"
                    if high_risk_count > 0
                    else (
                        "medium" if total_processes + total_capabilities > 10 else "low"
                    ),
                },
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error getting impact analysis for {group_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# === ENTERPRISE DASHBOARD API ROUTES ===
# These serve the enterprise dashboard template (duplicate_detection/dashboard.html)


@unified_duplicate_bp.route("/api/statistics/summary")
@login_required
def api_statistics_summary():
    """Statistics summary for the enterprise dashboard."""
    try:
        # Get enterprise detection runs
        runs = (
            DuplicateDetectionRun.query.order_by(
                DuplicateDetectionRun.created_at.desc()
            )
            .limit(100)
            .all()
        )
        latest_run = runs[0] if runs else None

        # Get enterprise groups (capped)
        groups = DuplicateGroup.query.limit(500).all()
        total_groups = len(groups)

        # Calculate estimated savings
        total_estimated_savings = sum(
            getattr(g, "estimated_savings", 0) or 0 for g in groups
        )

        # Count groups by priority
        priority_counts = {}
        for g in groups:
            p = getattr(g, "consolidation_priority", "low") or "low"
            priority_counts[p] = priority_counts.get(p, 0) + 1
        groups_by_priority = [
            {"priority": k, "count": v} for k, v in priority_counts.items()
        ]

        return jsonify(
            {
                "total_groups": total_groups,
                "total_estimated_savings": total_estimated_savings,
                "groups_by_priority": groups_by_priority,
                "latest_run": {
                    "id": latest_run.id,
                    "run_name": getattr(latest_run, "run_name", ""),
                    "status": latest_run.status,
                    "created_at": latest_run.created_at.isoformat()
                    if latest_run.created_at
                    else None,
                }
                if latest_run
                else None,
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.exception(f"Stats summary error: {e}")
        return jsonify({"error": "Could not load duplicate detection statistics"}), 500


@unified_duplicate_bp.route("/api/duplicate-groups")
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
            query = query.filter(
                DuplicateGroup.overall_similarity_score >= min_similarity
            )

        query = query.order_by(DuplicateGroup.created_at.desc())
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)

        groups_data = []
        for g in pagination.items:
            groups_data.append(
                {
                    "id": g.id,
                    "group_name": getattr(g, "group_name", f"Group {g.id}"),
                    "overall_similarity_score": getattr(
                        g, "overall_similarity_score", 0
                    ),
                    "consolidation_priority": getattr(
                        g, "consolidation_priority", "low"
                    ),
                    "duplicate_type": getattr(g, "duplicate_type", "unknown"),
                    "estimated_savings": getattr(g, "estimated_savings", 0) or 0,
                    "application_count": len(g.applications)
                    if hasattr(g, "applications")
                    else 0,
                    "created_at": g.created_at.isoformat()
                    if hasattr(g, "created_at") and g.created_at
                    else None,
                }
            )

        return jsonify(
            {
                "groups": groups_data,
                "pagination": {
                    "page": pagination.page,
                    "per_page": pagination.per_page,
                    "total": pagination.total,
                    "pages": pagination.pages,
                    "has_next": pagination.has_next,
                    "has_prev": pagination.has_prev,
                },
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.exception(f"Duplicate groups API error: {e}")
        return jsonify({"error": "Could not load duplicate groups"}), 500


@unified_duplicate_bp.route("/api/detection-runs")
@login_required
def api_detection_runs():
    """Detection runs for the enterprise dashboard."""
    try:
        runs = (
            DuplicateDetectionRun.query.order_by(
                DuplicateDetectionRun.created_at.desc()
            )
            .limit(20)
            .all()
        )

        runs_data = []
        for r in runs:
            runs_data.append(
                {
                    "id": r.id,
                    "run_name": getattr(r, "run_name", ""),
                    "status": r.status,
                    "duplicate_groups_found": getattr(r, "duplicate_groups_found", 0),
                    "applications_analyzed": getattr(r, "applications_analyzed", 0),
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "completed_at": r.completed_at.isoformat()
                    if hasattr(r, "completed_at") and r.completed_at
                    else None,
                }
            )

        return jsonify({"runs": runs_data})

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.exception(f"Detection runs API error: {e}")
        return jsonify({"error": "Could not load detection runs"}), 500


@unified_duplicate_bp.route("/run-detection", methods=["POST"])
@login_required
@platform_admin_required
def run_detection():
    """Run duplicate detection from the enterprise dashboard."""
    try:
        data = request.get_json() or {}
        data.get("similarity_threshold", 0.70)

        result = unified_service.run_duplicate_detection()

        if result.get("success"):
            return jsonify(result), 200
        else:
            return jsonify(result), 400

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Run detection error: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# === CONSOLIDATION RECOMMENDATION ROUTES ===
# These serve the group_detail template (duplicate_detection/group_detail.html)


@unified_duplicate_bp.route(
    "/api/consolidation-recommendation/<int:recommendation_id>/approve",
    methods=["POST"],
)
@login_required
def approve_consolidation_recommendation(recommendation_id):
    """Approve a consolidation recommendation."""
    try:
        from ..models.application_duplicate_detection import DuplicateGroup

        group = DuplicateGroup.query.get(recommendation_id)
        if not group:
            return jsonify({"success": False, "error": "Recommendation not found"}), 404

        group.status = "approved"
        group.reviewed_at = datetime.utcnow()
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "approved_at": group.reviewed_at.isoformat(),
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(
            f"Error approving recommendation {recommendation_id}: {e}"
        )
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route(
    "/api/consolidation-recommendation/<int:recommendation_id>/reject",
    methods=["POST"],
)
@login_required
def reject_consolidation_recommendation(recommendation_id):
    """Reject a consolidation recommendation."""
    try:
        from ..models.application_duplicate_detection import DuplicateGroup

        data = request.get_json() or {}
        reason = data.get("reason", "")

        group = DuplicateGroup.query.get(recommendation_id)
        if not group:
            return jsonify({"success": False, "error": "Recommendation not found"}), 404

        group.status = "rejected"
        group.reviewed_at = datetime.utcnow()
        group.recommendation_notes = f"Rejected: {reason}"
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "rejected_at": group.reviewed_at.isoformat(),
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(
            f"Error rejecting recommendation {recommendation_id}: {e}"
        )
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route(
    "/api/groups/<string:group_id>/add-to-consolidation", methods=["POST"]
)
@login_required
def api_add_group_to_consolidation(group_id):
    """Add applications from a duplicate group to the consolidation list.

    Accepts an optional JSON body with ``app_ids`` (list of ints) to add only
    a subset of the group's applications.  When omitted, all members are added.

    Migrated from dedupe_routes.py to unified_duplicate_routes.py to use the
    registered /duplicate-detection blueprint prefix.
    """
    try:
        from flask_login import current_user

        from app.models.application_portfolio import ApplicationComponent
        from app.models.consolidation_list import ConsolidationListEntry

        # Convert group_id string to integer safely
        try:
            group_id_int = int(str(group_id))
        except (ValueError, TypeError):
            return jsonify(
                {"success": False, "error": f"Invalid group ID: {group_id}"}
            ), 400

        # Optional subset of app IDs from the request body
        body = request.get_json(silent=True) or {}
        requested_ids = body.get("app_ids")  # list[int] or None

        # Get the group directly from database
        group = UnifiedDuplicateGroup.query.get(group_id_int)
        if not group:
            # Try legacy DuplicateGroup as fallback
            legacy_group = DuplicateGroup.query.get(group_id_int)
            if not legacy_group:
                return jsonify(
                    {"success": False, "error": f"Group {group_id} not found"}
                ), 404
            # Use legacy group applications
            application_ids = [app.id for app in legacy_group.applications]
            group_name = legacy_group.name or f"Duplicate Group {group_id_int}"
            estimated_savings = (
                legacy_group.estimated_savings
                if hasattr(legacy_group, "estimated_savings")
                else None
            )  # model-safety-ok
        else:
            # Use unified group members
            application_ids = [app.id for app in group.applications.all()]
            group_name = group.name or f"Duplicate Group {group_id_int}"
            estimated_savings = (
                group.estimated_savings if hasattr(group, "estimated_savings") else None
            )  # model-safety-ok

        # Narrow to caller-selected subset when provided
        if requested_ids is not None:
            allowed = set(application_ids)
            application_ids = [int(i) for i in requested_ids if int(i) in allowed]

        if not application_ids:
            return jsonify(
                {"success": False, "error": "No applications found in this group"}
            ), 400

        added_count = 0
        skipped_count = 0
        errors = []

        # Batch prefetch: load all apps and existing consolidation entries
        apps_by_id = {}
        if application_ids:
            apps_list = ApplicationComponent.query.filter(
                ApplicationComponent.id.in_(application_ids)
            ).all()
            apps_by_id = {a.id: a for a in apps_list}

            existing_entries = ConsolidationListEntry.query.filter(
                ConsolidationListEntry.application_id.in_(application_ids),
                ConsolidationListEntry.status == "pending",
            ).all()
            existing_app_ids = {e.application_id for e in existing_entries}
        else:
            existing_app_ids = set()

        for app_id in application_ids:
            try:
                app = apps_by_id.get(app_id)
                if not app:
                    errors.append(f"Application {app_id} not found")
                    continue

                # Check if already in consolidation list (pre-fetched)
                if app_id in existing_app_ids:
                    skipped_count += 1
                    continue

                # Calculate estimated savings if available
                savings_estimate = None
                if (
                    app.total_cost_of_ownership
                ):  # model-safety-ok: checked attribute exists
                    savings_estimate = app.total_cost_of_ownership
                elif app.maintenance_cost:  # model-safety-ok: checked attribute exists
                    savings_estimate = app.maintenance_cost
                elif estimated_savings:
                    savings_estimate = estimated_savings / len(application_ids)

                # Add to consolidation list
                entry = ConsolidationListEntry(
                    application_id=app_id,
                    source_group_id=group_id_int,
                    source_group_name=group_name,
                    source_type="duplicate_detection",
                    priority="medium",
                    status="pending",
                    estimated_savings=savings_estimate or 0,
                    added_by=current_user.email
                    if current_user.is_authenticated
                    else "system",
                )
                db.session.add(entry)
                added_count += 1

            except HTTPException:

                raise

            except Exception as e:
                current_app.logger.error(
                    f"Error adding app {app_id} to consolidation: {e}"
                )
                errors.append(f"Error adding application {app_id}")

        db.session.commit()

        return jsonify(
            {
                "success": True,
                "added_count": added_count,
                "skipped_count": skipped_count,
                "errors": errors if errors else None,
                "message": f"Added {added_count} application(s) to consolidation list"
                + (f" ({skipped_count} already listed)" if skipped_count else ""),
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error adding group {group_id} to consolidation: {e}")
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_duplicate_bp.route("/api/groups/<string:group_id>/ignore", methods=["POST"])
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

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error ignoring group {group_id}: {e}")
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500

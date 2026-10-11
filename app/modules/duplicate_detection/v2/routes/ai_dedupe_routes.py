"""
AI Dedupe Routes v2 — guardrail-enabled.

Preserves all 8 routes from the v1 ai_dedupe_routes.py with
@timed_route decorators and mark_blueprint_guardrailed.

Blueprint name: ai (same as v1)
URL prefix: /ai (nested under /duplicate-detection via register())
"""

from werkzeug.exceptions import HTTPException
import json
import logging

from flask import Blueprint, jsonify, render_template, request
from app.middleware.tenant_decorators import platform_admin_required
from flask_login import login_required

from app.core.compat import mark_blueprint_guardrailed
from app.core.decorators import timed_route
from app.utils.api_response import error_response

try:
    from app.modules.duplicate_detection.services.ai_duplicate_detection_service import (
        ai_detection_service,
    )

    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False
    ai_detection_service = None

from app.models.unified_duplicate_detection import (
    UnifiedDetectionRun,
    UnifiedDuplicateGroup,
)
from app.modules.duplicate_detection.services.unified_duplicate_service import (
    UnifiedDuplicateService,
)

logger = logging.getLogger(__name__)

ai_dedupe_bp_v2 = Blueprint("ai", __name__, url_prefix="/ai")
mark_blueprint_guardrailed(ai_dedupe_bp_v2)


@ai_dedupe_bp_v2.route("/dashboard")
@timed_route
@login_required
def ai_dashboard():
    """AI-powered duplicate detection dashboard"""
    if not AI_AVAILABLE:
        return render_template(
            "dedupe/ai_insights.html",
            ai_stats={"detections_count": 0, "average_processing_time": 0},
            recent_runs=[],
            global_stats={},
            ai_strategies=["ai_enhanced", "semantic_only", "business_aware"],
            performance_metrics={},
            ai_unavailable=True,
            error="AI features require additional dependencies.",
        )
    try:
        ai_stats = ai_detection_service.get_performance_metrics()
        recent_ai_runs = UnifiedDuplicateService.get_runs(
            strategy="ai_enhanced", limit=5
        )
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
        logger.error(f"Error loading AI dashboard: {e}")
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


@ai_dedupe_bp_v2.route("/analyze", methods=["GET", "POST"])
@timed_route
@login_required
def ai_analyze():
    """AI-powered duplicate analysis interface"""
    if request.method == "GET":
        return render_template(
            "dedupe/ai_insights.html",
            strategies=["ai_enhanced", "semantic_only", "business_aware"],
            default_threshold=0.65,
        )
    try:
        data = request.get_json() or {}
        strategy = data.get("strategy", "ai_enhanced")
        threshold = float(data.get("threshold", 0.65))
        config = data.get("config", {})
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
        logger.error(f"AI analysis failed: {e}")
        return jsonify(
            {"success": False, "error": "AI analysis failed. Please try again."}
        ), 500


@ai_dedupe_bp_v2.route("/insights/<int:run_id>")
@timed_route
@login_required
@platform_admin_required
def ai_insights(run_id):
    """View AI insights for a specific detection run"""
    try:
        run = UnifiedDetectionRun.query.get_or_404(run_id)
        if (getattr(run, "strategy", "") or "").startswith("ai_"):
            groups = UnifiedDuplicateGroup.query.filter_by(detection_run_id=run_id).all()
            ai_insights_data = {
                "algorithm_version": getattr(run, "strategy", None),
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
            if groups and groups[0].metadata:
                try:
                    metadata = json.loads(groups[0].metadata)
                    if "ai_insights" in metadata:
                        ai_insights_data["global_insights"] = metadata["ai_insights"]
                except (json.JSONDecodeError, TypeError, KeyError):
                    logger.exception("Failed to JSON parsing")
                    pass
        else:
            ai_insights_data = {"error": "Not an AI detection run"}
        return render_template(
            "dedupe/ai_insights.html", run=run, ai_insights=ai_insights_data
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading AI insights: {e}")
        return render_template("errors/500.html"), 500


@ai_dedupe_bp_v2.route("/api/detect", methods=["POST"])
@timed_route
@login_required
def api_ai_detect():
    """Run AI-powered duplicate detection."""
    try:
        data = request.get_json() or {}
        strategy = data.get("strategy", "ai_enhanced")
        threshold = float(data.get("threshold", 0.65))
        config = data.get("config", {})
        valid_strategies = ["ai_enhanced", "semantic_only", "business_aware"]
        if strategy not in valid_strategies:
            return jsonify(
                {
                    "success": False,
                    "error": f"Invalid strategy. Must be one of: {valid_strategies}",
                }
            ), 400
        if not 0.0 <= threshold <= 1.0:
            return jsonify(
                {"success": False, "error": "Threshold must be between 0.0 and 1.0"}
            ), 400
        if not AI_AVAILABLE or ai_detection_service is None:
            return error_response(
                "AI duplicate detection is not available on this deployment.",
                code="AI_UNAVAILABLE",
                status_code=503,
            )

        result = ai_detection_service.detect_duplicates(
            strategy=strategy, threshold=threshold, config=config
        )

        if not result.get("success"):
            # A failed run is not a 200. The service has already logged the
            # internal detail; the client gets a product-level message only.
            logger.error(
                "AI duplicate detection reported failure: %s", result.get("error")
            )
            return error_response(
                "Duplicate detection could not be completed.",
                code="DUPLICATE_DETECTION_FAILED",
                status_code=502,
            )

        return jsonify(result)
    except ValueError as e:
        logger.warning(f"Invalid AI detection parameters: {e}")
        return jsonify({"success": False, "error": "Invalid detection parameters"}), 400
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI detection API failed: {e}")
        return jsonify(
            {"success": False, "error": "AI detection failed. Please try again."}
        ), 500


@ai_dedupe_bp_v2.route("/api/feedback", methods=["POST"])
@timed_route
@login_required
def api_feedback():
    """Submit user feedback for adaptive learning."""
    try:
        data = request.get_json() or {}
        duplicate_id = data.get("duplicate_id")
        action = data.get("action")
        confidence = data.get("confidence")
        data.get("notes", "")
        if not duplicate_id or not action or confidence is None:
            return jsonify(
                {
                    "success": False,
                    "error": "duplicate_id, action, and confidence are required",
                }
            ), 400
        valid_actions = ["accept", "reject", "modify"]
        if action not in valid_actions:
            return jsonify(
                {
                    "success": False,
                    "error": f"Invalid action. Must be one of: {valid_actions}",
                }
            ), 400
        if not 1 <= confidence <= 100:
            return jsonify(
                {"success": False, "error": "Confidence must be between 1 and 100"}
            ), 400
        ai_detection_service.process_user_feedback(
            duplicate_id=duplicate_id, user_action=action, confidence=confidence
        )
        return jsonify({"success": True, "message": "Feedback processed successfully"})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Feedback processing failed: {e}")
        return jsonify(
            {"success": False, "error": "Feedback processing failed. Please try again."}
        ), 500


@ai_dedupe_bp_v2.route("/api/performance", methods=["GET"])
@timed_route
@login_required
def api_performance():
    """Get AI detection performance metrics"""
    try:
        metrics = ai_detection_service.get_performance_metrics()
        return jsonify({"success": True, "metrics": metrics})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting performance metrics: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@ai_dedupe_bp_v2.route("/api/compare", methods=["POST"])
@timed_route
@login_required
def api_compare_strategies():
    """Compare different AI detection strategies."""
    try:
        data = request.get_json() or {}
        threshold = float(data.get("threshold", 0.65))
        strategies = data.get(
            "strategies", ["ai_enhanced", "semantic_only", "business_aware"]
        )
        if not AI_AVAILABLE or ai_detection_service is None:
            return error_response(
                "AI duplicate detection is not available on this deployment.",
                code="AI_UNAVAILABLE",
                status_code=503,
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
                    logger.error(
                        "Strategy %s failed: %s", strategy, result.get("error")
                    )
                    comparison_results[strategy] = {
                        "error": "Strategy could not be evaluated."
                    }
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Strategy {strategy} comparison failed: {e}")
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
    except Exception:
        logger.error("Strategy comparison failed", exc_info=True)
        return jsonify(
            {"success": False, "error": "Strategy comparison failed. Please try again."}
        ), 500


@ai_dedupe_bp_v2.route("/api/optimize-threshold", methods=["POST"])
@timed_route
@login_required
def api_optimize_threshold():
    """Get optimized threshold for a strategy based on historical feedback."""
    try:
        data = request.get_json() or {}
        strategy = data.get("strategy", "ai_enhanced")
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
        logger.error(f"Threshold optimization failed: {e}")
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

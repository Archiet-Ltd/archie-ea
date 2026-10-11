"""
Confidence Threshold Controls & Review Queue API Routes

Provides REST API endpoints for confidence threshold management,
review queue operations, and human-in-the-loop validation workflows.
"""

from datetime import datetime
import logging

from flask import Blueprint, jsonify, request

from app.decorators import audit_log
from app.services.confidence_review_service import (
    ConfidenceReviewService,
    ConfidenceThresholdConfig,
    ReviewDecisionData,
    ReviewQueueItemData,
)
from flask_login import login_required
from werkzeug.exceptions import HTTPException

from app.utils.pagination import safe_int_arg

logger = logging.getLogger(__name__)

# Create blueprint
confidence_review_bp = Blueprint("confidence_review", __name__, url_prefix="/api/confidence")

# Initialize service
confidence_service = ConfidenceReviewService()


@confidence_review_bp.route("/thresholds", methods=["POST"])
@login_required
@audit_log("confidence_create_threshold")
def create_confidence_threshold():
    """
    Create a new confidence threshold configuration.

    Request Body:
        {
            "threshold_name": "Custom Capability Threshold",
            "threshold_type": "capability",
            "minimum_confidence": 0.7,
            "auto_approval_threshold": 0.85,
            "rejection_threshold": 0.4,
            "context_type": "capability_level",
            "context_value": "strategic",
            "domain_filter": "business",
            "requires_human_review": true,
            "auto_review_enabled": false,
            "review_queue_priority": 3,
            "validation_rules": {},
            "quality_gates": {},
            "user_id": 789
        }

    Returns:
        JSON with threshold creation result
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body is required"}), 400

        # Validate required fields
        required_fields = ["threshold_name", "threshold_type", "user_id"]
        for field in required_fields:
            if not data.get(field):
                return jsonify({"success": False, "error": f"Required field missing: {field}"}), 400

        # Create threshold configuration
        config = ConfidenceThresholdConfig(
            threshold_name=data["threshold_name"],
            threshold_type=data["threshold_type"],
            minimum_confidence=data.get("minimum_confidence", 0.6),
            auto_approval_threshold=data.get("auto_approval_threshold", 0.8),
            rejection_threshold=data.get("rejection_threshold", 0.3),
            context_type=data.get("context_type"),
            context_value=data.get("context_value"),
            domain_filter=data.get("domain_filter"),
            requires_human_review=data.get("requires_human_review", True),
            auto_review_enabled=data.get("auto_review_enabled", False),
            review_queue_priority=data.get("review_queue_priority", 5),
            validation_rules=data.get("validation_rules", {}),
            quality_gates=data.get("quality_gates", {}),
        )

        # Create threshold
        result = confidence_service.create_confidence_threshold(config, data["user_id"])

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error creating confidence threshold: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@confidence_review_bp.route("/thresholds", methods=["GET"])
@login_required
def get_confidence_thresholds():
    """
    Get confidence thresholds with optional filtering.

    Query Parameters:
        threshold_type (str): Optional threshold type filter
        is_active (bool): Optional active status filter

    Returns:
        JSON with confidence thresholds
    """
    try:
        threshold_type = request.args.get("threshold_type")
        is_active = (
            request.args.get("is_active", type=bool) if request.args.get("is_active") else None
        )

        thresholds = confidence_service.get_confidence_thresholds(threshold_type, is_active)

        return jsonify(
            {"success": True, "thresholds": thresholds, "total_thresholds": len(thresholds)}
        )

    except Exception as e:
        logger.error(f"Error getting confidence thresholds: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@confidence_review_bp.route("/thresholds/<int:threshold_id>", methods=["PUT"])
@login_required
@audit_log("confidence_update_threshold")
def update_confidence_threshold(threshold_id: int):
    """
    Update a confidence threshold configuration.

    Path Parameters:
        threshold_id: Threshold ID

    Request Body:
        {
            "minimum_confidence": 0.7,
            "auto_approval_threshold": 0.85,
            "requires_human_review": true,
            "review_queue_priority": 3
        }

    Returns:
        JSON with update result
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body is required"}), 400

        result = confidence_service.update_confidence_threshold(threshold_id, data)

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error updating confidence threshold {threshold_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@confidence_review_bp.route("/evaluate", methods=["POST"])
@login_required
@audit_log("confidence_evaluate_threshold")
def evaluate_confidence_threshold():
    """
    Evaluate an item against confidence thresholds.

    Request Body:
        {
            "item_type": "capability_mapping",
            "item_id": 123,
            "item_name": "Strategic Customer Management",
            "item_data": {"capability_level": "strategic", "domain": "business"},
            "confidence_score": 0.75,
            "confidence_factors": {
                "name_similarity": 0.8,
                "description_match": 0.7,
                "context_alignment": 0.75
            },
            "ai_model_used": "gpt - 4",
            "generation_timestamp": "2023 - 01 - 01T12:00:00Z",
            "threshold_name": "Strategic Capability Mapping",
            "context_type": "capability_level",
            "context_value": "strategic",
            "domain": "business"
        }

    Returns:
        JSON with evaluation result and action
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body is required"}), 400

        # Validate required fields
        required_fields = ["item_type", "item_id", "item_name", "confidence_score"]
        for field in required_fields:
            if not data.get(field):
                return jsonify({"success": False, "error": f"Required field missing: {field}"}), 400

        # Create review queue item data
        item_data = ReviewQueueItemData(
            item_type=data["item_type"],
            item_id=data["item_id"],
            item_name=data["item_name"],
            item_data=data.get("item_data", {}),
            confidence_score=data["confidence_score"],
            confidence_factors=data.get("confidence_factors", {}),
            ai_model_used=data.get("ai_model_used", "unknown"),
            generation_timestamp=datetime.fromisoformat(data["generation_timestamp"])
            if data.get("generation_timestamp")
            else datetime.utcnow(),
            threshold_name=data.get("threshold_name"),
            context_type=data.get("context_type"),
            context_value=data.get("context_value"),
            domain=data.get("domain"),
        )

        # Evaluate confidence threshold
        result = confidence_service.evaluate_confidence_threshold(item_data)

        # If requires review, add to queue
        if result["success"] and result["requires_review"]:
            queue_result = confidence_service.add_to_review_queue(item_data, result)
            result["queue_result"] = queue_result

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error evaluating confidence threshold: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@confidence_review_bp.route("/queue", methods=["GET"])
@login_required
def get_review_queue():
    """
    Get review queue items with optional filtering.

    Query Parameters:
        status (str): Optional status filter (pending, in_review, approved, rejected, etc.)
        item_type (str): Optional item type filter
        assigned_to_id (int): Optional assigned reviewer filter
        limit (int): Maximum results (default: 50)

    Returns:
        JSON with review queue items
    """
    try:
        status = request.args.get("status")
        item_type = request.args.get("item_type")
        assigned_to_id = request.args.get("assigned_to_id", type=int)
        limit = min(safe_int_arg('limit', 50, minimum=1, maximum=500), 100)

        result = confidence_service.get_review_queue(status, item_type, assigned_to_id, limit)

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error getting review queue: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@confidence_review_bp.route("/queue/<int:review_item_id>/assign", methods=["POST"])
@login_required
@audit_log("confidence_assign_review_item")
def assign_review_item(review_item_id: int):
    """
    Assign a review queue item to a reviewer.

    Path Parameters:
        review_item_id: Review item ID

    Request Body:
        {
            "reviewer_id": 789
        }

    Returns:
        JSON with assignment result
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body is required"}), 400

        reviewer_id = data.get("reviewer_id")
        if not reviewer_id:
            return jsonify({"success": False, "error": "reviewer_id is required"}), 400

        result = confidence_service.assign_review_item(review_item_id, reviewer_id)

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error assigning review item {review_item_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@confidence_review_bp.route("/queue/<int:review_item_id>/review", methods=["POST"])
@login_required
@audit_log("confidence_submit_review_decision")
def submit_review_decision(review_item_id: int):
    """
    Submit a review decision for a queue item.

    Path Parameters:
        review_item_id: Review item ID

    Request Body:
        {
            "decision_type": "approve",
            "decision_reason": "High confidence and good quality",
            "reviewer_id": 789,
            "reviewer_role": "architect",
            "reviewer_experience_level": "senior",
            "quality_assessment": {
                "accuracy": {"score": 0.9, "weight": 0.4},
                "completeness": {"score": 0.8, "weight": 0.3},
                "relevance": {"score": 0.85, "weight": 0.3}
            },
            "identified_issues": [],
            "suggested_improvements": ["Add more context information"],
            "human_confidence_estimate": 0.85,
            "ai_accuracy_assessment": 4,
            "correction_made": false,
            "corrected_data": {},
            "review_duration_seconds": 300
        }

    Returns:
        JSON with decision submission result
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body is required"}), 400

        # Validate required fields
        required_fields = ["decision_type", "decision_reason", "reviewer_id", "reviewer_role"]
        for field in required_fields:
            if not data.get(field):
                return jsonify({"success": False, "error": f"Required field missing: {field}"}), 400

        # Create review decision data
        decision_data = ReviewDecisionData(
            review_item_id=review_item_id,
            decision_type=data["decision_type"],
            decision_reason=data["decision_reason"],
            reviewer_id=data["reviewer_id"],
            reviewer_role=data["reviewer_role"],
            reviewer_experience_level=data.get("reviewer_experience_level", "intermediate"),
            quality_assessment=data.get("quality_assessment", {}),
            identified_issues=data.get("identified_issues", []),
            suggested_improvements=data.get("suggested_improvements", []),
            human_confidence_estimate=data.get("human_confidence_estimate", 0.5),
            ai_accuracy_assessment=data.get("ai_accuracy_assessment", 3),
            correction_made=data.get("correction_made", False),
            corrected_data=data.get("corrected_data", {}),
            review_duration_seconds=data.get("review_duration_seconds", 0),
        )

        result = confidence_service.submit_review_decision(decision_data)

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error submitting review decision for {review_item_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@confidence_review_bp.route("/statistics", methods=["GET"])
@login_required
def get_review_statistics():
    """
    Get review queue statistics with optional date filtering.

    Query Parameters:
        date_from (str): Optional start date filter (YYYY-MM-DD)
        date_to (str): Optional end date filter (YYYY-MM-DD)

    Returns:
        JSON with review statistics
    """
    try:
        date_from_str = request.args.get("date_from")
        date_to_str = request.args.get("date_to")

        date_from = None
        date_to = None

        if date_from_str:
            try:
                date_from = datetime.strptime(date_from_str, "%Y-%m-%d")
            except ValueError:
                return (
                    jsonify(
                        {"success": False, "error": "Invalid date_from format, use YYYY-MM-DD"}
                    ),
                    400,
                )

        if date_to_str:
            try:
                date_to = datetime.strptime(date_to_str, "%Y-%m-%d")
            except ValueError:
                return (
                    jsonify({"success": False, "error": "Invalid date_to format, use YYYY-MM-DD"}),
                    400,
                )

        statistics = confidence_service.get_review_statistics(date_from, date_to)

        if "error" in statistics:
            return jsonify({"success": False, "error": statistics["error"]}), 500

        return jsonify({"success": True, "statistics": statistics})

    except Exception as e:
        logger.error(f"Error getting review statistics: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@confidence_review_bp.route("/quality-criteria", methods=["GET"])
@login_required
def get_quality_criteria():
    """
    Get quality assessment criteria for different item types.

    Query Parameters:
        item_type (str): Optional item type filter

    Returns:
        JSON with quality criteria
    """
    try:
        item_type = request.args.get("item_type")

        if item_type:
            criteria = confidence_service.quality_criteria.get(item_type, {})
        else:
            criteria = confidence_service.quality_criteria

        return jsonify({"success": True, "quality_criteria": criteria})

    except Exception as e:
        logger.error(f"Error getting quality criteria: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@confidence_review_bp.route("/escalation-rules", methods=["GET"])
@login_required
def get_escalation_rules():
    """
    Get escalation rules for review queue management.

    Returns:
        JSON with escalation rules
    """
    try:
        return jsonify({"success": True, "escalation_rules": confidence_service.escalation_rules})

    except Exception as e:
        logger.error(f"Error getting escalation rules: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@confidence_review_bp.route("/thresholds/default", methods=["GET"])
@login_required
def get_default_thresholds():
    """
    Get default confidence thresholds for all item types.

    Returns:
        JSON with default thresholds
    """
    try:
        default_thresholds = {}
        for name, config in confidence_service.default_thresholds.items():
            default_thresholds[name] = {
                "threshold_name": config.threshold_name,
                "threshold_type": config.threshold_type,
                "minimum_confidence": config.minimum_confidence,
                "auto_approval_threshold": config.auto_approval_threshold,
                "rejection_threshold": config.rejection_threshold,
                "context_type": config.context_type,
                "context_value": config.context_value,
                "domain_filter": config.domain_filter,
                "requires_human_review": config.requires_human_review,
                "review_queue_priority": config.review_queue_priority,
            }

        return jsonify({"success": True, "default_thresholds": default_thresholds})

    except Exception as e:
        logger.error(f"Error getting default thresholds: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@confidence_review_bp.route("/queue/<int:review_item_id>", methods=["GET"])
@login_required
def get_review_item_details(review_item_id: int):
    """
    Get detailed information about a specific review queue item.

    Path Parameters:
        review_item_id: Review item ID

    Returns:
        JSON with review item details
    """
    try:
        from app.models.confidence_review import ReviewQueueItem
        from app.utils.route_guards import load_entity

        review_item = load_entity(ReviewQueueItem, review_item_id)
        if not review_item:
            return jsonify({"success": False, "error": "Review item not found"}), 404

        return jsonify({"success": True, "review_item": review_item.to_dict()})

    except Exception as e:
        logger.error(f"Error getting review item details {review_item_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@confidence_review_bp.route("/queue/<int:review_item_id>/decisions", methods=["GET"])
@login_required
def get_review_item_decisions(review_item_id: int):
    """
    Get all decisions for a specific review queue item.

    Path Parameters:
        review_item_id: Review item ID

    Returns:
        JSON with review decisions
    """
    try:
        from app.models.confidence_review import ReviewDecision, ReviewQueueItem
        from app.utils.route_guards import require_entity

        # A nonexistent review item must 404, not return an empty decision list:
        # "no decisions" and "no such item" are otherwise indistinguishable.
        require_entity(ReviewQueueItem, review_item_id, description="Review item not found")

        decisions = (
            ReviewDecision.query.filter_by(review_item_id=review_item_id)
            .order_by(ReviewDecision.decision_timestamp.desc())
            .all()
        )

        return jsonify(
            {
                "success": True,
                "decisions": [decision.to_dict() for decision in decisions],
                "total_decisions": len(decisions),
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting review item decisions {review_item_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@confidence_review_bp.route("/batch-evaluate", methods=["POST"])
@login_required
@audit_log("confidence_batch_evaluate")
def batch_evaluate_confidence():
    """
    Evaluate multiple items against confidence thresholds.

    Request Body:
        {
            "items": [
                {
                    "item_type": "capability_mapping",
                    "item_id": 123,
                    "item_name": "Strategic Customer Management",
                    "confidence_score": 0.75,
                    "confidence_factors": {},
                    "ai_model_used": "gpt - 4"
                }
            ]
        }

    Returns:
        JSON with batch evaluation results
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body is required"}), 400

        items = data.get("items", [])
        if not items:
            return jsonify({"success": False, "error": "items array is required"}), 400

        results = []
        for item_data in items:
            try:
                # Create review queue item data
                review_item_data = ReviewQueueItemData(
                    item_type=item_data["item_type"],
                    item_id=item_data["item_id"],
                    item_name=item_data["item_name"],
                    item_data=item_data.get("item_data", {}),
                    confidence_score=item_data["confidence_score"],
                    confidence_factors=item_data.get("confidence_factors", {}),
                    ai_model_used=item_data.get("ai_model_used", "unknown"),
                    generation_timestamp=datetime.fromisoformat(item_data["generation_timestamp"])
                    if item_data.get("generation_timestamp")
                    else datetime.utcnow(),
                    threshold_name=item_data.get("threshold_name"),
                    context_type=item_data.get("context_type"),
                    context_value=item_data.get("context_value"),
                    domain=item_data.get("domain"),
                )

                # Evaluate confidence threshold
                result = confidence_service.evaluate_confidence_threshold(review_item_data)
                results.append(result)

            except Exception as e:
                logger.error(f"Error evaluating item {item_data.get('item_id', 'unknown')}: {e}")
                results.append(
                    {"success": False, "error": "An internal error occurred", "item_id": item_data.get("item_id")}
                )

        return jsonify({"success": True, "total_items": len(items), "evaluations": results})

    except Exception as e:
        logger.error(f"Error in batch confidence evaluation: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


def register_confidence_review_routes(app):
    """Register confidence review blueprint with Flask app."""
    app.register_blueprint(confidence_review_bp)
    logger.info("Confidence review API routes registered")

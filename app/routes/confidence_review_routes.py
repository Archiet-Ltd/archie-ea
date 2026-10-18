"""Confidence Review Dashboard Routes.

Provides UI for reviewing low-confidence mappings before they are committed.
"""

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from app.decorators import audit_log
from app.middleware.tenant_decorators import platform_admin_required

confidence_review_bp = Blueprint(
    "confidence_review_dashboard", __name__, url_prefix="/reviews"
)


@confidence_review_bp.route("/confidence")
@login_required
def confidence_dashboard():
    """
    Render the confidence review dashboard.

    Shows all mappings that require human review due to low confidence scores.
    """
    return render_template("reviews/confidence_review_dashboard.html")


@confidence_review_bp.route("/api/pending")
@login_required
def api_pending_reviews():
    """
    API endpoint to fetch pending confidence reviews.

    Returns low-confidence mappings that need human approval.
    """
    from app.services.confidence_review_service import ConfidenceReviewService

    review_service = ConfidenceReviewService()
    pending_items = review_service.get_pending_reviews()

    return jsonify(
        {"success": True, "pending_count": len(pending_items), "items": pending_items}
    )


@confidence_review_bp.route("/api/approve/<int:item_id>", methods=["POST"])
@login_required
@audit_log("review_approve")
def api_approve_review(item_id):
    """Approve a pending confidence review item."""
    from app.services.confidence_review_service import ConfidenceReviewService

    review_service = ConfidenceReviewService()
    result = review_service.approve_item(
        item_id, approved_by=request.json.get("notes", "")
    )

    return jsonify(result)


@confidence_review_bp.route("/api/reject/<int:item_id>", methods=["POST"])
@login_required
@audit_log("review_reject")
def api_reject_review(item_id):
    """Reject a pending confidence review item."""
    from app.services.confidence_review_service import ConfidenceReviewService

    review_service = ConfidenceReviewService()
    result = review_service.reject_item(
        item_id,
        rejected_by=request.json.get("notes", ""),
        reason=request.json.get("reason", "Manually rejected"),
    )

    return jsonify(result)


@confidence_review_bp.route("/api/bulk-approve", methods=["POST"])
@login_required
@audit_log("reviews_bulk_approve")
def api_bulk_approve():
    """Bulk approve multiple pending review items."""
    from app.services.confidence_review_service import ConfidenceReviewService

    review_service = ConfidenceReviewService()
    item_ids = request.json.get("item_ids", [])

    successful_count = 0
    failed_count = 0

    for item_id in item_ids:
        result = review_service.approve_item(
            item_id, approved_by=request.json.get("decision_reason", "")
        )
        if result.get("success"):
            successful_count += 1
        else:
            failed_count += 1

    return jsonify(
        {
            "success": True,
            "successful_count": successful_count,
            "failed_count": failed_count,
        }
    )


@confidence_review_bp.route("/api/bulk-reject", methods=["POST"])
@login_required
@audit_log("reviews_bulk_reject")
def api_bulk_reject():
    """Bulk reject multiple pending review items."""
    from app.services.confidence_review_service import ConfidenceReviewService

    review_service = ConfidenceReviewService()
    item_ids = request.json.get("item_ids", [])

    successful_count = 0
    failed_count = 0

    for item_id in item_ids:
        result = review_service.reject_item(
            item_id,
            rejected_by=request.json.get("decision_reason", ""),
            reason=request.json.get("decision_reason", "Bulk rejected"),
        )
        if result.get("success"):
            successful_count += 1
        else:
            failed_count += 1

    return jsonify(
        {
            "success": True,
            "successful_count": successful_count,
            "failed_count": failed_count,
        }
    )


@confidence_review_bp.route("/api/thresholds", methods=["POST"])
@login_required
@platform_admin_required
@audit_log("review_thresholds_save")
def api_save_thresholds():
    """Save confidence threshold configuration.

    Platform-admin-only (lead review of PR 430, item 6): writes the global,
    platform-wide ``ConfidenceThreshold`` table and was ``login_required``
    only -- any signed-in user could rewrite platform confidence thresholds.
    ``created_by_id`` is also taken from ``current_user.id`` now, not from
    the request body: a caller could previously claim the row was created by
    anyone.
    """
    from app.models.confidence_review import ConfidenceThreshold
    from app import db

    data = request.json

    # Create or update threshold configuration
    threshold = ConfidenceThreshold(
        threshold_name=data.get("threshold_name", "User Configured Thresholds"),
        threshold_type=data.get("threshold_type", "global"),
        minimum_confidence=data.get("minimum_confidence", 0.5),
        auto_approval_threshold=data.get("auto_approval_threshold", 0.8),
        rejection_threshold=data.get("rejection_threshold", 0.3),
        requires_human_review=data.get("requires_human_review", True),
        created_by_id=current_user.id,
    )

    db.session.add(threshold)
    db.session.commit()

    return jsonify({"success": True, "threshold_id": threshold.id})

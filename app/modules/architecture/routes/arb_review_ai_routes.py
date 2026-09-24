"""ARB reviewer-side AI assist: pre-brief generation.

Entelim's ARB flow already lets a submitter draft a submission with AI
(app/modules/architecture/routes/architecture_assistant_routes.py). The
reviewer side had no equivalent: opening a review gave a reviewer nothing —
no pre-brief, no conflict surfacing, no draft disposition. This endpoint is
purely advisory: it returns a suggested pre-brief and writes nothing to the
review. The reviewer's actual decision still goes through the existing
POST /arb/reviews/<id>/decision flow.

Registered onto arb_bp via a side-effect import in arb_routes.py, matching
the existing arb_decision_routes.py / arb_document_routes.py pattern.
"""
from __future__ import annotations

import logging

from flask import jsonify
from flask_login import login_required

from app.modules.architecture.routes.arb_routes import arb_bp
from app.models.architecture_review_board import ARBReviewItem
from app.modules.architecture.services.arb_review_ai_service import (
    ARBReviewAIPrebriefError,
    generate_review_prebrief,
)
from app.services.feature_flag_service import FeatureFlagService

logger = logging.getLogger(__name__)


@arb_bp.route("/api/reviews/<int:review_id>/ai-prebrief", methods=["POST"])
@login_required
def ai_review_prebrief(review_id):
    """POST /arb/api/reviews/<id>/ai-prebrief

    Generates an AI pre-brief for an ARB reviewer: a summary, key risks,
    questions for the submitter, possible conflicts/overlaps, and a draft
    disposition. Nothing here is persisted — it is advisory input for the
    reviewer, who still records their decision via the existing decision
    controls.
    """
    feature_guard = FeatureFlagService.require_ai_for_route(
        FeatureFlagService.FEATURE_SUGGESTIONS,
        endpoint_name="arb.ai_review_prebrief",
    )
    if feature_guard:
        return feature_guard

    # Loaded via the normal ORM query so TenantMixin's do_orm_execute filter
    # applies — a review belonging to another org 404s exactly like an
    # unknown id, rather than leaking its existence.
    review = ARBReviewItem.query.get_or_404(review_id)

    try:
        prebrief = generate_review_prebrief(review)
    except ARBReviewAIPrebriefError as e:
        logger.warning("ARB pre-brief unparseable for review %s: %s", review_id, e)
        return jsonify({"error": f"AI pre-brief failed: {e}"}), 502
    except Exception as e:
        logger.exception("ARB pre-brief generation failed for review %s", review_id)
        return jsonify({"error": f"AI pre-brief failed: {e}"}), 502

    return jsonify({"prebrief": prebrief})

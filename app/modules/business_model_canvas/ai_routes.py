"""Business Model Canvas AI assist: draft one block at a time.

Registered onto business_model_bp via a side-effect import at the bottom
of routes.py, matching the pattern used by
app/modules/architecture/routes/arb_review_ai_routes.py. Purely advisory:
this endpoint returns a drafted block and writes nothing — saving the
draft goes through the canvas's existing POST .../api/block write path.
"""

from __future__ import annotations

import logging

from flask import jsonify, request
from flask_login import current_user, login_required

from app.modules.business_model_canvas.ai_service import (
    BusinessModelAIDraftError,
    generate_block_draft,
)
from app.decorators import require_roles
from app.modules.business_model_canvas import service
from app.modules.business_model_canvas.routes import EDIT_ROLES, business_model_bp
from app.services.cost_visibility import costs_visible
from app.services.feature_flag_service import FeatureFlagService

logger = logging.getLogger(__name__)

DRAFT_FAILED = "Suggestions could not be drafted just now. Your entries are unchanged. Try again in a moment."


@business_model_bp.route("/api/<int:canvas_id>/ai-draft-block", methods=["POST"])
@login_required
@require_roles(*EDIT_ROLES)
def ai_draft_block(canvas_id):
    """POST /business-model/api/<id>/ai-draft-block

    Body: {"block": <a box key of this canvas>}. Proposes entries for that
    box from the canvas's other boxes and the organisation's own model,
    each with the source it came from. Nothing is written — an accepted
    proposal goes through the canvas's add-entry route. Only the people
    who may edit the canvas get this.
    """
    feature_guard = FeatureFlagService.require_ai_for_route(
        FeatureFlagService.FEATURE_SUGGESTIONS,
        endpoint_name="business_model.ai_draft_block",
    )
    if feature_guard:
        return feature_guard

    # ORM query so TenantMixin's do_orm_execute filter applies — a canvas
    # belonging to another org 404s exactly like an unknown id.
    canvas = service.get_canvas_or_none(canvas_id)
    if canvas is None:
        return jsonify({"error": "Canvas not found"}), 404

    payload = request.get_json(silent=True) or {}
    block_key = payload.get("block")

    try:
        draft = generate_block_draft(canvas, block_key, show_costs=costs_visible(current_user))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except BusinessModelAIDraftError as e:
        logger.warning("BMC block draft unparseable for canvas %s: %s", canvas_id, e)
        return jsonify({"error": DRAFT_FAILED}), 502
    except Exception as e:
        logger.exception("BMC block draft generation failed for canvas %s", canvas_id)
        return jsonify({"error": DRAFT_FAILED}), 502

    from app.modules.ai_chat.tools.executor import GENOME_NOTICE

    return jsonify({"draft": draft, "notice": GENOME_NOTICE})

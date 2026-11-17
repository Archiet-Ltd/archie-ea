"""Product roadmap outcome routes — TPM-011.

Routes:
  GET  /api/product-roadmap            → JSON roadmap (now/next/later)
  PATCH /api/product-roadmap/epics/<id>/horizon → assign horizon
  GET  /product-roadmap                → render template
"""
import logging

from flask import Blueprint, jsonify, render_template, request

from app.services import product_roadmap_service
from flask_login import current_user, login_required

from app.core.auth.permissions import may_change_model_element

logger = logging.getLogger(__name__)

roadmap_outcome_bp = Blueprint("roadmap_outcome", __name__)


@roadmap_outcome_bp.route("/api/product-roadmap", methods=["GET"])
@login_required
def get_product_roadmap():
    """GET /api/product-roadmap?solution_id=N&board_id=M"""
    solution_id = request.args.get("solution_id", type=int)
    board_id = request.args.get("board_id", type=int)
    try:
        data = product_roadmap_service.get_outcome_roadmap(
            solution_id=solution_id, board_id=board_id
        )
    except Exception as exc:
        logger.exception("Failed to build product roadmap")
        return jsonify({"error": str(exc)}), 500
    return jsonify(data), 200


@roadmap_outcome_bp.route("/api/product-roadmap/epics/<int:epic_id>/horizon", methods=["PATCH"])
@login_required
def assign_horizon(epic_id: int):
    """PATCH /api/product-roadmap/epics/<id>/horizon — body: {"horizon": "now|next|later"}"""
    if not may_change_model_element(current_user, "edit"):
        return jsonify({"error": "Your role can view the roadmap but not change it."}), 403
    data = request.get_json(force=True) or {}
    horizon = (data.get("horizon") or "").strip()
    try:
        result = product_roadmap_service.assign_epic_to_horizon(epic_id, horizon)
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("Failed to assign horizon")
        return jsonify({"error": str(exc)}), 500
    return jsonify(result), 200


@roadmap_outcome_bp.route("/product-roadmap", methods=["GET"])
@login_required
def product_roadmap_page():
    """GET /product-roadmap — render the product roadmap template."""
    return render_template(
        "roadmap/product_roadmap.html",
        can_write=may_change_model_element(current_user, "edit"),
    )

"""Prioritisation routes — MoSCoW / WSJF / RICE backlog (TPM-006)."""
import logging

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from app.core.auth.permissions import may_change_model_element

from app.services import prioritisation_service

logger = logging.getLogger(__name__)

prioritisation_bp = Blueprint(
    "prioritisation",
    __name__,
    url_prefix="",
)


@prioritisation_bp.route("/api/backlog/prioritised", methods=["GET"])
@login_required
def get_prioritised_backlog():
    """GET /api/backlog/prioritised?method=wsjf&solution_id=N"""
    method = request.args.get("method", "wsjf")
    solution_id = request.args.get("solution_id", type=int)
    items = prioritisation_service.get_backlog_prioritised(solution_id=solution_id, method=method)
    return jsonify(items), 200


@prioritisation_bp.route("/api/backlog/<int:req_id>/moscow", methods=["PATCH"])
@login_required
def patch_moscow(req_id: int):
    """PATCH /api/backlog/<req_id>/moscow — set MoSCoW priority."""
    if not may_change_model_element(current_user, "edit"):
        return jsonify({"error": "Your role can view the backlog but not change it."}), 403
    data = request.get_json(force=True) or {}
    priority = data.get("priority", "")
    try:
        result = prioritisation_service.set_moscow(req_id, priority)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result), 200


@prioritisation_bp.route("/api/backlog/<int:req_id>/wsjf", methods=["PATCH"])
@login_required
def patch_wsjf(req_id: int):
    """PATCH /api/backlog/<req_id>/wsjf — set WSJF components."""
    if not may_change_model_element(current_user, "edit"):
        return jsonify({"error": "Your role can view the backlog but not change it."}), 403
    data = request.get_json(force=True) or {}
    try:
        result = prioritisation_service.set_wsjf_components(
            req_id,
            business_value=int(data.get("business_value", 1)),
            time_criticality=int(data.get("time_criticality", 1)),
            risk_reduction=int(data.get("risk_reduction", 1)),
            job_size=int(data.get("job_size", 1)),
        )
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result), 200


@prioritisation_bp.route("/api/backlog/<int:req_id>/rice", methods=["PATCH"])
@login_required
def patch_rice(req_id: int):
    """PATCH /api/backlog/<req_id>/rice — set RICE components."""
    if not may_change_model_element(current_user, "edit"):
        return jsonify({"error": "Your role can view the backlog but not change it."}), 403
    data = request.get_json(force=True) or {}
    try:
        result = prioritisation_service.set_rice_components(
            req_id,
            reach=int(data.get("reach", 0)),
            impact=int(data.get("impact", 1)),
            confidence=int(data.get("confidence", 100)),
        )
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result), 200


@prioritisation_bp.route("/backlog/prioritisation", methods=["GET"])
@login_required
def prioritisation_view():
    """GET /backlog/prioritisation — render backlog prioritisation UI."""
    return render_template("backlog/prioritisation.html", can_write=may_change_model_element(current_user, "edit"))

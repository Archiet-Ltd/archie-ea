"""Prioritisation routes — MoSCoW / WSJF / RICE backlog."""
import logging

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from app import db
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
    from app.core.auth.permissions import is_read_only_member

    method = request.args.get("method", "wsjf")
    solution_id = request.args.get("solution_id", type=int)
    # A backlog requirement written before it was ranked has no ranked copy yet: give it one, so the board
    # holds every requirement the backlog holds. Idempotent, and fenced to the caller's own organisation.
    # Reading never stores anything for a person whose organisation role is viewer: the ranking is computed
    # for them without writing the backfill.
    if not is_read_only_member(current_user):
        if prioritisation_service.project_backlog_requirements(getattr(current_user, "organization_id", None)):
            db.session.commit()
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


_WSJF_INPUTS = (
    ("business_value", "business value"), ("time_criticality", "time criticality"),
    ("risk_reduction", "risk reduction"), ("job_size", "job size"),
)
_RICE_INPUTS = (
    ("reach", "reach"), ("impact", "impact"), ("confidence", "confidence"), ("story_points", "story points"),
)


def _missing_inputs(data: dict, inputs) -> str:
    """The inputs a save left out, in words: a missing input is never stored as a stand-in value."""
    return ", ".join(label for key, label in inputs if data.get(key) in (None, ""))


@prioritisation_bp.route("/api/backlog/<int:req_id>/wsjf", methods=["PATCH"])
@login_required
def patch_wsjf(req_id: int):
    """PATCH /api/backlog/<req_id>/wsjf — set WSJF components."""
    if not may_change_model_element(current_user, "edit"):
        return jsonify({"error": "Your role can view the backlog but not change it."}), 403
    data = request.get_json(force=True) or {}
    if data.get("clear") is True:
        return jsonify(prioritisation_service.clear_wsjf_components(req_id)), 200
    missing = _missing_inputs(data, _WSJF_INPUTS)
    if missing:
        return jsonify({"error": "Record " + missing + " to save a WSJF score."}), 400
    try:
        result = prioritisation_service.set_wsjf_components(
            req_id,
            business_value=int(data["business_value"]),
            time_criticality=int(data["time_criticality"]),
            risk_reduction=int(data["risk_reduction"]),
            job_size=int(data["job_size"]),
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
    if data.get("clear") is True:
        return jsonify(prioritisation_service.clear_rice_components(req_id)), 200
    missing = _missing_inputs(data, _RICE_INPUTS)
    if missing:
        return jsonify({"error": "Record " + missing + " to save a RICE score."}), 400
    try:
        result = prioritisation_service.set_rice_components(
            req_id,
            reach=int(data["reach"]),
            impact=int(data["impact"]),
            confidence=int(data["confidence"]),
            story_points=int(data["story_points"]),
        )
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result), 200


@prioritisation_bp.route("/backlog/prioritisation", methods=["GET"])
@login_required
def prioritisation_view():
    """GET /backlog/prioritisation — render backlog prioritisation UI."""
    return render_template("backlog/prioritisation.html", can_write=may_change_model_element(current_user, "edit"))

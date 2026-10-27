"""Communication log routes for TPM-012.

Endpoints:
  POST /api/communications           — log a new communication entry (201)
  GET  /api/communications           — list entries (?solution_id=N&stakeholder_id=M)
  GET  /solutions/<id>/communications — render timeline view
"""

import logging

from flask import Blueprint, jsonify, redirect, request

from app.services import communication_log_service
from flask_login import login_required

logger = logging.getLogger(__name__)

communication_bp = Blueprint("communication_log", __name__)


@communication_bp.route("/api/communications", methods=["POST"])
@login_required
def create_communication():
    """POST /api/communications — log a communication entry. Returns 201."""
    data = request.get_json(force=True) or {}
    solution_id = data.get("solution_id")
    comm_type = data.get("comm_type")
    subject = data.get("subject")

    if not comm_type:
        return jsonify({"error": "comm_type is required"}), 400
    if not subject:
        return jsonify({"error": "subject is required"}), 400

    try:
        entry = communication_log_service.log_communication(
            solution_id=solution_id,
            stakeholder_id=data.get("stakeholder_id"),
            comm_type=comm_type,
            subject=subject,
            summary=data.get("summary"),
            outcome=data.get("outcome"),
            action_items=data.get("action_items", []),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(entry.to_dict()), 201


@communication_bp.route("/api/communications", methods=["GET"])
@login_required
def list_communications():
    """GET /api/communications?solution_id=N&stakeholder_id=M — return log."""
    solution_id = request.args.get("solution_id", type=int)
    stakeholder_id = request.args.get("stakeholder_id", type=int)

    if not solution_id:
        return jsonify({"error": "solution_id query parameter is required"}), 400

    entries = communication_log_service.get_communication_log(solution_id, stakeholder_id)
    return jsonify(entries), 200


@communication_bp.route("/solutions/<int:solution_id>/communications", methods=["GET"])
@login_required
def communication_log_view(solution_id: int):
    """GET /solutions/<id>/communications: the old communication log page. The notes
    live on the solution record now (the solutions rethink, slice 5b)."""
    from app.models.solution_models import Solution
    from app.utils.route_guards import require_entity
    require_entity(Solution, solution_id, description="Solution not found")
    return redirect("/solutions/%d#record-notes" % solution_id)

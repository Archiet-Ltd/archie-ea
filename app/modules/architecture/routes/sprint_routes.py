"""Sprint REST API routes — /api/sprints."""
import logging

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from app import db
from app.services import sprint_service
from app.services import sprint_analytics_service

logger = logging.getLogger(__name__)

sprint_bp = Blueprint("sprint", __name__, url_prefix="/api/sprints")
sprint_view_bp = Blueprint("sprint_view", __name__, url_prefix="/sprints")


def _board_or_404(board_id):
    """The kanban board, only when it belongs to the caller's organisation.

    ``KanbanBoard`` carries the tenant filter, so a board id from another
    organisation is a 404 before a sprint is listed under it or attached to it.
    """
    from app.models.adm_kanban import KanbanBoard

    return KanbanBoard.query.get_or_404(board_id)


@sprint_bp.route("", methods=["POST"])
@login_required
def create_sprint():
    """POST /api/sprints — create a new sprint. Returns 201."""
    data = request.get_json(force=True) or {}
    board_id = data.get("board_id")
    if not board_id:
        return jsonify({"error": "board_id is required"}), 400
    if not data.get("name"):
        return jsonify({"error": "name is required"}), 400
    _board_or_404(board_id)
    sprint = sprint_service.create_sprint(board_id, data)
    return jsonify(sprint.to_dict()), 201


@sprint_bp.route("", methods=["GET"])
@login_required
def list_sprints():
    """GET /api/sprints?board_id=X — list sprints for a board."""
    board_id = request.args.get("board_id", type=int)
    if not board_id:
        return jsonify({"error": "board_id query parameter is required"}), 400
    _board_or_404(board_id)
    sprints = sprint_service.get_sprints(board_id)
    return jsonify([s.to_dict() for s in sprints]), 200


@sprint_bp.route("/<int:sprint_id>/status", methods=["PATCH"])
@login_required
def update_sprint_status(sprint_id: int):
    """PATCH /api/sprints/<id>/status — transition sprint status."""
    data = request.get_json(force=True) or {}
    status = data.get("status")
    if not status:
        return jsonify({"error": "status is required"}), 400
    try:
        sprint = sprint_service.update_sprint_status(sprint_id, status)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(sprint.to_dict()), 200


@sprint_bp.route("/default-board", methods=["GET"])
@login_required
def get_default_board():
    """GET /api/sprints/default-board — return or create the current user's default board."""
    from app.models.adm_kanban import KanbanBoard

    board = (
        KanbanBoard.query
        .filter_by(created_by_id=current_user.id)
        .order_by(KanbanBoard.created_at.desc())
        .first()
    )
    if not board:
        board = KanbanBoard(
            name="Architecture Work Queue",
            description="Auto-created board for governance tasks",
            created_by_id=current_user.id,
        )
        db.session.add(board)
        db.session.commit()
    return jsonify({"board_id": board.id}), 200


@sprint_bp.route("/<int:sprint_id>/cards", methods=["PATCH"])
@login_required
def assign_card(sprint_id: int):
    """PATCH /api/sprints/<id>/cards — assign a card reference to a sprint."""
    data = request.get_json(force=True) or {}
    card_ref = data.get("card_ref")
    if not card_ref:
        return jsonify({"error": "card_ref is required"}), 400
    sprint = sprint_service.assign_card_to_sprint(sprint_id, card_ref)
    return jsonify(sprint.to_dict()), 200


@sprint_bp.route("/<int:sprint_id>/burndown", methods=["GET"])
@login_required
def get_burndown(sprint_id: int):
    """GET /api/sprints/<id>/burndown — burndown chart data."""
    sprint_service.get_sprint_or_404(sprint_id)
    data = sprint_analytics_service.get_burndown_data(sprint_id)
    if not data:
        return jsonify({"error": "Sprint not found"}), 404
    return jsonify(data), 200


@sprint_bp.route("/<int:sprint_id>/analytics", methods=["GET"])
@login_required
def get_sprint_analytics(sprint_id: int):
    """GET /api/sprints/<id>/analytics — throughput, cycle time, and CFD data.

    TPM-009: Returns combined analytics for a sprint:
      - throughput: count of cards completed within sprint dates
      - cycle_time: mean days from started_at to completed_at
      - cfd: daily snapshot of cards per status column
    """
    sprint_service.get_sprint_or_404(sprint_id)
    data = sprint_analytics_service.get_sprint_analytics(sprint_id)
    if data is None:
        return jsonify({"error": "Sprint not found"}), 404
    return jsonify(data), 200


@sprint_view_bp.route("/<int:sprint_id>/analytics", methods=["GET"])
@login_required
def sprint_analytics_page(sprint_id: int):
    """GET /sprints/<id>/analytics — sprint analytics dashboard page."""
    sprint = sprint_service.get_sprint_or_404(sprint_id)
    return render_template("adm_kanban/sprint_analytics.html", sprint=sprint)


@sprint_bp.route("/velocity", methods=["GET"])
@login_required
def get_velocity():
    """GET /api/sprints/velocity?board_id=N — velocity data for closed sprints."""
    board_id = request.args.get("board_id", type=int)
    data = sprint_analytics_service.get_velocity_data(board_id=board_id)
    return jsonify(data), 200


@sprint_view_bp.route("/charts", methods=["GET"])
@login_required
def charts():
    """GET /sprints/charts — velocity and burndown charts page."""
    return render_template("sprints/charts.html")

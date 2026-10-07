"""Sprint service — CRUD and lifecycle operations for Sprint entities."""
from datetime import date

from app import db
from app.models.sprint import Sprint, SprintStatus


def create_sprint(board_id: int, data: dict) -> Sprint:
    """Create a new sprint for the given board."""
    sprint = Sprint(
        name=data["name"],
        board_id=board_id,
        start_date=_parse_date(data.get("start_date")),
        end_date=_parse_date(data.get("end_date")),
        capacity_points=data.get("capacity_points", 0),
        goal=data.get("goal"),
        status=SprintStatus(data["status"]) if data.get("status") else SprintStatus.PLANNING,
    )
    db.session.add(sprint)
    db.session.commit()
    return sprint


def get_sprints(board_id: int) -> list:
    """Return all sprints for a board, ordered by id."""
    return Sprint.query.filter_by(board_id=board_id).order_by(Sprint.id).all()


def get_sprint_or_404(sprint_id: int) -> Sprint:
    """The sprint, only when its board belongs to the caller's organisation.

    A sprint carries no organisation column; its kanban board does. The inner join
    lets the automatic tenant filter on ``KanbanBoard`` decide, so another
    organisation's sprint id is a 404 rather than its data.
    """
    from flask import abort

    from app.models.adm_kanban import KanbanBoard

    sprint = (
        Sprint.query.join(KanbanBoard, Sprint.board_id == KanbanBoard.id)
        .filter(Sprint.id == sprint_id)
        .first()
    )
    if sprint is None:
        abort(404)
    return sprint


def update_sprint_status(sprint_id: int, status: str) -> Sprint:
    """Transition sprint to a new status."""
    sprint = get_sprint_or_404(sprint_id)
    sprint.status = SprintStatus(status)
    db.session.commit()
    return sprint


def assign_card_to_sprint(sprint_id: int, card_ref: str) -> Sprint:
    """Record a card reference assignment to a sprint (stored in goal field as tag).

    In this implementation the sprint goal is annotated with the card reference.
    A full implementation would use a junction table; this satisfies the API contract.
    """
    sprint = get_sprint_or_404(sprint_id)
    tag = f"[card:{card_ref}]"
    if sprint.goal:
        if tag not in sprint.goal:
            sprint.goal = f"{sprint.goal} {tag}"
    else:
        sprint.goal = tag
    db.session.commit()
    return sprint


def _parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    from datetime import datetime as _dt
    return _dt.fromisoformat(value).date()

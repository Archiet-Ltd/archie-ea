"""Communication log service for TPM-012.

Provides CRUD operations for StakeholderCommunication records.
"""

from datetime import datetime

from app import db
from app.models.stakeholder_communication import CommunicationType, StakeholderCommunication


def log_communication(
    solution_id,
    stakeholder_id,
    comm_type: str,
    subject: str,
    summary: str = None,
    outcome: str = None,
    action_items: list = None,
    logged_by: int = None,
    organization_id: int = None,
) -> StakeholderCommunication:
    """Create and persist a communication log entry.

    The entry belongs to the solution's organisation, read from the solution itself, so a
    caller outside a request (a seed, a job) still writes a fenced row. A caller that names
    a different organisation is refused rather than filing the note under the wrong one."""
    if solution_id is not None:
        from app.models.solution_models import Solution

        solution = db.session.get(Solution, solution_id)
        if solution is None:
            raise ValueError("Solution %s not found" % solution_id)
        if organization_id is not None and organization_id != solution.organization_id:
            raise ValueError("Solution %s belongs to another organisation" % solution_id)
        organization_id = solution.organization_id
    entry = StakeholderCommunication(
        solution_id=solution_id,
        stakeholder_id=stakeholder_id,
        comm_type=CommunicationType(comm_type),
        subject=subject,
        summary=summary,
        outcome=outcome,
        action_items=action_items or [],
        logged_at=datetime.utcnow(),
        logged_by=logged_by,
        organization_id=organization_id,
    )
    db.session.add(entry)
    db.session.commit()
    return entry


def get_communication_log(solution_id: int, stakeholder_id: int = None, organization_id: int = None) -> list:
    """Return chronological log entries for a solution, optionally filtered by stakeholder."""
    query = StakeholderCommunication.query.filter_by(solution_id=solution_id)
    if organization_id is not None:
        query = query.filter_by(organization_id=organization_id)
    if stakeholder_id is not None:
        query = query.filter_by(stakeholder_id=stakeholder_id)
    entries = query.order_by(StakeholderCommunication.logged_at.asc()).all()
    return [e.to_dict() for e in entries]


def add_action_item(comm_id: int, item: str, owner: str, due: str) -> dict:
    """Append an action item to an existing communication log entry."""
    entry = StakeholderCommunication.query.get_or_404(comm_id)
    items = list(entry.action_items or [])
    new_item = {"item": item, "owner": owner, "due": due}
    items.append(new_item)
    entry.action_items = items
    db.session.commit()
    return new_item

"""Which strategic goals an initiative serves.

The link is a row in ``initiative_goals`` (initiative, goal, contribution
level); the goal trace reads the same rows, so a link made on the initiative
page shows up on the goal's trace without a second record. An initiative also
counts as serving an objective when the transformation programme it is tied to
has a row in ``strategic_initiative_goals`` -- one question, both junctions.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app import db
from app.models.motivation import Goal, initiative_goals
from app.models.strategic import strategic_initiative_goals
from app.models.vendor.vendor_organization import EnterpriseInitiative

CONTRIBUTION_LEVELS = ("primary", "supporting", "indirect")


class InitiativeGoalLinkError(Exception):
    """A refused link, with the HTTP status and the sentence the person sees."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _load(initiative_id: int, goal_id: Optional[int] = None):
    # A select, not Session.get: get() returns an identity-map hit without
    # emitting SQL, which skips the organisation filter.
    initiative = db.session.execute(
        db.select(EnterpriseInitiative).where(EnterpriseInitiative.id == initiative_id)
    ).scalar_one_or_none()
    if initiative is None:
        raise InitiativeGoalLinkError("Initiative not found.", 404)
    goal = None
    if goal_id is not None:
        goal = db.session.execute(
            db.select(Goal).where(
                Goal.id == goal_id, Goal.organization_id == initiative.organization_id
            )
        ).scalar_one_or_none()
        if goal is None:
            raise InitiativeGoalLinkError("That goal could not be found.", 404)
    return initiative, goal


def list_links(initiative_id: int) -> List[Dict[str, Any]]:
    """The goals this initiative is linked to, with the recorded contribution."""
    initiative, _ = _load(initiative_id)
    rows = db.session.execute(
        db.select(Goal, initiative_goals.c.contribution_level)
        .join(initiative_goals, initiative_goals.c.goal_id == Goal.id)
        .where(
            initiative_goals.c.initiative_id == initiative.id,
            Goal.organization_id == initiative.organization_id,
        )
        .order_by(Goal.name)
    ).all()
    return [
        {"goal_id": goal.id, "name": goal.name, "contribution_level": level}
        for goal, level in rows
    ]


def add_link(initiative_id: int, goal_id: int, contribution_level: Optional[str] = None):
    """Link an initiative to a goal. Flushes; the caller commits."""
    if contribution_level not in (None, "") and contribution_level not in CONTRIBUTION_LEVELS:
        raise InitiativeGoalLinkError(
            "Contribution must be one of: %s." % ", ".join(CONTRIBUTION_LEVELS), 400
        )
    initiative, goal = _load(initiative_id, goal_id)
    existing = db.session.execute(
        db.select(initiative_goals.c.goal_id).where(
            initiative_goals.c.initiative_id == initiative.id,
            initiative_goals.c.goal_id == goal.id,
        )
    ).first()
    if existing is not None:
        raise InitiativeGoalLinkError("%s is already linked to this initiative." % goal.name, 409)
    db.session.execute(
        initiative_goals.insert().values(
            initiative_id=initiative.id,
            goal_id=goal.id,
            contribution_level=contribution_level or None,
        )
    )
    db.session.flush()
    return goal


def add_link_for_element(initiative_id: int, element_id: int, contribution_level: Optional[str] = None):
    """Link an initiative to the goal that the chosen architecture element stands for.

    The element must be the architecture element of a goal in the initiative's
    own organisation; anything else is refused with the same wording, so the
    answer does not reveal whether an element exists elsewhere.
    """
    initiative, _ = _load(initiative_id)
    goal = db.session.execute(
        db.select(Goal).where(
            Goal.archimate_element_id == element_id,
            Goal.organization_id == initiative.organization_id,
        )
    ).scalars().first()
    if goal is None:
        raise InitiativeGoalLinkError("That element is not a goal in your organisation.", 404)
    return add_link(initiative_id, goal.id, contribution_level)


def remove_link(initiative_id: int, goal_id: int) -> None:
    """Remove a link. Flushes; the caller commits."""
    initiative, goal = _load(initiative_id, goal_id)
    result = db.session.execute(
        initiative_goals.delete().where(
            initiative_goals.c.initiative_id == initiative.id,
            initiative_goals.c.goal_id == goal.id,
        )
    )
    if result.rowcount == 0:
        raise InitiativeGoalLinkError("That goal is not linked to this initiative.", 404)
    db.session.flush()


def initiatives_serving_no_objective() -> List[EnterpriseInitiative]:
    """Initiatives with no goal link in either junction.

    Tenant scoping comes from EnterpriseInitiative's own mixin; the goal rows
    are checked through the initiative ids, which are already the caller's.
    """
    linked_directly = db.select(initiative_goals.c.initiative_id)
    programmes_with_goals = db.select(strategic_initiative_goals.c.strategic_initiative_id)
    return list(
        db.session.execute(
            db.select(EnterpriseInitiative)
            .where(
                EnterpriseInitiative.id.not_in(linked_directly),
                db.or_(
                    EnterpriseInitiative.linked_strategic_initiative_id.is_(None),
                    EnterpriseInitiative.linked_strategic_initiative_id.not_in(
                        programmes_with_goals
                    ),
                ),
            )
            .order_by(EnterpriseInitiative.name)
        ).scalars()
    )

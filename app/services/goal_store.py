"""The one writer of an organisation's goals.

A goal is a ``goals`` row (target date, status) and its ArchiMate Goal element. Both are written here, with
the organisation named in every statement, so a caller with no request (the chat's worker, a command) is
fenced the same as a request. A Goal element made some other way (the element page, a generated tool) has no
row yet; it gets one on its first write here, and until then reads as "No target date recorded".
"""
from datetime import date

from app import db
from app.models.archimate_core import ArchiMateElement
from app.models.motivation import Goal
from app.services.archimate_backbone import fit_name, sync_archimate_element

NO_TARGET_DATE = "No target date recorded"

# What a caller may set beyond the name, described and the date: the promotion of a solution's goal and later
# slices pass these by keyword, so a new field needs no change to a caller.
EXTRA_FIELDS = ("driver_id", "strategic_priority", "measurable_metrics", "notes", "archimate_element_id")
EDITABLE_FIELDS = ("name", "description", "target_date", "status") + EXTRA_FIELDS[:-1]
STATUSES = ("draft", "active", "on_hold", "achieved", "cancelled")


class GoalError(ValueError):
    """A goal that cannot be written, with a sentence a person can read."""


def _as_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value.date() if hasattr(value, "date") else value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise GoalError("The target date '%s' is not a date." % value)


def _check_status(status):
    if status is not None and status not in STATUSES:
        raise GoalError("A goal's status is one of: %s." % ", ".join(STATUSES))


def goal_by_id(organization_id, goal_id):
    return db.session.execute(
        db.select(Goal).where(Goal.id == goal_id, Goal.organization_id == organization_id)
    ).scalar_one_or_none()


def goal_for_element(organization_id, element_id):
    """The goals row behind a Goal element of the organisation, or None when it has none yet."""
    return db.session.execute(
        db.select(Goal).where(Goal.archimate_element_id == element_id,
                              Goal.organization_id == organization_id)
    ).scalar_one_or_none()


def goal_element(organization_id, element_id):
    return db.session.execute(
        db.select(ArchiMateElement).where(ArchiMateElement.id == element_id,
                                          ArchiMateElement.organization_id == organization_id,
                                          ArchiMateElement.type == "Goal")
    ).scalar_one_or_none()


def create_goal(organization_id, name, description=None, target_date=None, status=None, *, commit=True,
                **extra):
    """Create a goal of the organisation with its element. Returns the saved Goal.

    ``commit=False`` flushes only, for a caller that commits a larger piece of work itself."""
    if organization_id is None:
        raise GoalError("A goal belongs to an organisation.")
    name = (name or "").strip()
    if not name:
        raise GoalError("A goal needs a name.")
    _check_status(status)
    unknown = set(extra) - set(EXTRA_FIELDS)
    if unknown:
        raise GoalError("A goal has no field called %s." % ", ".join(sorted(unknown)))
    goal = Goal(
        name=fit_name(name),
        description=description,
        target_date=_as_date(target_date),
        status=status or "active",
        organization_id=organization_id,
        **extra,
    )
    try:
        db.session.add(goal)
        db.session.flush()
        sync_archimate_element(goal)
        if commit:
            db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return goal


def update_goal(organization_id, element_id, **fields):
    """Change a goal of the organisation, found by its element. Only the editable fields are applied.

    A Goal element with no row yet gets its row here. Returns the Goal, or raises ``GoalError`` when the
    organisation has no such goal."""
    element = goal_element(organization_id, element_id)
    if element is None:
        raise GoalError("There is no such goal in this workspace.")
    unknown = set(fields) - set(EDITABLE_FIELDS)
    if unknown:
        raise GoalError("A goal has no field called %s." % ", ".join(sorted(unknown)))
    _check_status(fields.get("status"))
    goal = goal_for_element(organization_id, element_id)
    try:
        if goal is None:
            goal = Goal(name=element.name, description=element.description, status="active",
                        archimate_element_id=element.id, organization_id=organization_id)
            db.session.add(goal)
        for key, value in fields.items():
            if value is None:
                continue
            if key == "target_date":
                value = _as_date(value)
            elif key == "name":
                value = fit_name(str(value).strip())
                element.name = value
            elif key == "description":
                element.description = value
            setattr(goal, key, value)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return goal

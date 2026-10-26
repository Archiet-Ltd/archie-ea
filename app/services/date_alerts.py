"""The alerts a date raises on its own, without anyone editing anything.

A support date that comes within 180 days and a review condition that passes
its due date both happen with no change event. Once a day, per organisation,
``scan_date_alerts`` finds them and writes them through the same rules
(``app/services/change_rules.py``) and the same writer as the change-feed
consumer, so the bell, the wording and the one-alert-per-date guarantee are the
same whichever path found the date first.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from app.extensions import db
from app.services import change_rules


def _support_dates(rule: Dict[str, Any], organization_id: int, today: date) -> List[Dict[str, Any]]:
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent

    horizon = today + timedelta(days=rule["when"]["date_within_days"])
    applications = db.session.execute(
        db.select(ApplicationComponent)
        .where(ApplicationComponent.organization_id == organization_id)
        .where(ApplicationComponent.deleted_at.is_(None))
        .where(ApplicationComponent.end_of_life_date.isnot(None))
        .where(ApplicationComponent.end_of_life_date >= today)
        .where(ApplicationComponent.end_of_life_date <= horizon)
    ).scalars().all()
    if not applications:
        return []
    owners = ApplicationOwner.get_display_rows_for_applications(
        [row.id for row in applications], organization_id
    )
    found: List[Dict[str, Any]] = []
    for row in applications:
        recipients = sorted({owner["user_id"] for owner in owners.get(row.id, [])})
        if not recipients:
            continue
        found.extend(change_rules.alert_for(
            rule,
            recipients=recipients,
            fields={
                "name": row.name,
                "subject": row.name,
                "after": change_rules.day_shown(row.end_of_life_date),
                "after_raw": row.end_of_life_date.isoformat(),
                "archimate_element_id": row.archimate_element_id,
                "entity_id": row.id,
            },
            event_type=rule["event_type"],
            entity_type="application_component",
            entity_id=row.id,
        ))
    return found


# A condition is open while the person responsible still owes something; once
# evidence is in, the board owes the next step, and fulfilled or waived is done.
_CLOSED_CONDITION_STATES = ("evidence_submitted", "fulfilled", "waived")


def _overdue_conditions(rule: Dict[str, Any], organization_id: int, today: date) -> List[Dict[str, Any]]:
    from app.models.arb_decision_event import ARBCondition
    from app.models.architecture_review_board import ARBReviewItem

    rows = db.session.execute(
        db.select(ARBCondition, ARBReviewItem.title)
        .outerjoin(ARBReviewItem, ARBReviewItem.id == ARBCondition.review_item_id)
        .where(ARBCondition.organization_id == organization_id)
        .where(ARBCondition.due_date.isnot(None))
        .where(ARBCondition.due_date < today)
        .where(ARBCondition.status.notin_(_CLOSED_CONDITION_STATES))
        .where(ARBCondition.responsible_id.isnot(None))
    ).all()
    found: List[Dict[str, Any]] = []
    for condition, review_title in rows:
        found.extend(change_rules.alert_for(
            rule,
            recipients=[condition.responsible_id],
            fields={
                "subject": review_title or "you are responsible for",
                "number": condition.condition_number,
                "due": change_rules.day_shown(condition.due_date),
                "due_raw": condition.due_date.isoformat(),
                "review_item_id": condition.review_item_id,
                "entity_id": condition.id,
            },
            event_type=None,
            entity_type="arb_condition",
            entity_id=condition.id,
        ))
    return found


_SCANS = {
    "support_dates": _support_dates,
    "overdue_conditions": _overdue_conditions,
}


def scan_date_alerts(organization_id: int, *, today: Optional[date] = None) -> int:
    """Write the alerts the organisation's dates raise today; returns how many.

    Safe to run any number of times a day: an alert already written for the
    same date or condition is never written again.
    """
    from app.services.change_feed_consumer import write_notifications

    today = today or date.today()
    planned: List[Dict[str, Any]] = []
    for rule in change_rules.daily_rules():
        planned.extend(_SCANS[rule["daily_scan"]](rule, organization_id, today))
    written = write_notifications(organization_id, planned)
    db.session.commit()
    return written


__all__ = ["scan_date_alerts"]

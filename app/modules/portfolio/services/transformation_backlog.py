"""The transformation backlog: the work that has been identified but not yet planned.

One ordered list, built from the two registers that already hold the facts:

* a **gap** that is still open and that no work package resolves, and
* a **work package** that is not finished or cancelled and has no start date.

Nothing is stored for the backlog itself and no score is computed. Items are
ordered by the priority a person recorded (critical first), then by name, so
the order can be explained by reading the page.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app import db

# Recorded priority, most urgent first. A value outside this list sorts last and
# is shown as recorded rather than being mapped to something it was not.
PRIORITY_ORDER = ("critical", "high", "medium", "low")

CLOSED_GAP_STATUSES = ("resolved", "closed", "cancelled")
CLOSED_WORK_PACKAGE_STATUSES = ("completed", "cancelled")


def _rank(priority: str | None) -> int:
    value = (priority or "").strip().lower()
    return PRIORITY_ORDER.index(value) if value in PRIORITY_ORDER else len(PRIORITY_ORDER)


def open_gaps_without_work_package(organization_id: int) -> List[Dict[str, Any]]:
    """The organisation's open gaps that no work package is linked to.

    "No work package" is decided by the gap register's one reader, so this list
    and the gap screens always agree on which gaps are addressed.
    """
    from app.services.gap_register_service import get_gaps_not_addressed

    return [
        {
            "kind": "gap",
            "id": g.id,
            "name": g.name,
            "priority": g.priority,
            "detail": g.description,
            "status": g.resolution_status,
            "owner": g.owner,
            "target_date": g.target_resolution_date,
        }
        for g in get_gaps_not_addressed(organization_id)
        if g.resolution_status is None or g.resolution_status not in CLOSED_GAP_STATUSES
    ]


def unscheduled_work_packages(organization_id: int) -> List[Dict[str, Any]]:
    from app.models.unified_work_package import UnifiedWorkPackage as WP

    rows = db.session.execute(
        db.select(WP)
        .where(WP.organization_id == organization_id)
        .where(WP.start_date.is_(None))
        .where(db.or_(WP.status.is_(None), WP.status.not_in(CLOSED_WORK_PACKAGE_STATUSES)))
    ).scalars().all()
    return [
        {
            "kind": "work_package",
            "id": w.id,
            "name": w.name,
            "priority": w.priority,
            "detail": w.description,
            "status": w.status,
            "owner": w.assigned_to,
            "target_date": None,
        }
        for w in rows
    ]


def transformation_backlog(organization_id: int) -> Dict[str, Any]:
    """The backlog in order, with the number of each kind of item."""
    items = open_gaps_without_work_package(organization_id) + unscheduled_work_packages(organization_id)
    items.sort(key=lambda i: (_rank(i["priority"]), (i["name"] or "").lower(), i["kind"], i["id"]))
    return {
        "items": items,
        "gap_count": sum(1 for i in items if i["kind"] == "gap"),
        "work_package_count": sum(1 for i in items if i["kind"] == "work_package"),
    }

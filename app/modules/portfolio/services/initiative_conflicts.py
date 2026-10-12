"""Initiatives competing for the same capability or the same owner in a quarter.

Two initiatives conflict in a quarter when both are running in it (start and
completion dates overlap the quarter) and either serve the same capability, or
have the same owner. Both facts are recorded links (the initiative's capability
links and its owner), never inferred from names. An initiative with no start
date is left out and counted separately, because its quarter is unknown.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Tuple

from app import db
from app.models.business_capabilities import BusinessCapability
from app.models.strategic import StrategicInitiative, strategic_initiative_capabilities
from app.models.user import User

INACTIVE_STATUSES = ("completed", "cancelled")


def quarter_bounds(year: int, quarter: int) -> Tuple[datetime.date, datetime.date]:
    if quarter not in (1, 2, 3, 4):
        raise ValueError("quarter must be 1 to 4")
    start = datetime.date(year, 3 * (quarter - 1) + 1, 1)
    end_month = 3 * quarter
    next_start = datetime.date(year + (end_month // 12), end_month % 12 + 1, 1)
    return start, next_start - datetime.timedelta(days=1)


def next_quarter(today: Optional[datetime.date] = None) -> Tuple[int, int]:
    today = today or datetime.date.today()
    q = (today.month - 1) // 3 + 1
    return (today.year + 1, 1) if q == 4 else (today.year, q + 1)


def parse_quarter(raw: Optional[str]) -> Optional[Tuple[int, int]]:
    """'2026-Q4' -> (2026, 4); None when it is not in that form."""
    try:
        year, q = (raw or "").upper().split("-Q")
        year, q = int(year), int(q)
    except ValueError:
        return None
    return (year, q) if 1 <= q <= 4 and 1900 < year < 3000 else None


def _runs_in(initiative: StrategicInitiative, start: datetime.date, end: datetime.date) -> bool:
    if initiative.start_date is None:
        return False
    finish = initiative.target_completion_date or datetime.date.max
    return initiative.start_date <= end and finish >= start


def find_conflicts(year: int, quarter: int) -> Dict[str, Any]:
    """Conflicts for the quarter among the signed-in organisation's initiatives.

    Returns ``{"quarter", "conflicts", "undated"}``. Each conflict names the
    shared capability or owner and the initiatives involved.
    """
    start, end = quarter_bounds(year, quarter)
    initiatives = list(
        db.session.execute(
            db.select(StrategicInitiative).where(
                db.or_(
                    StrategicInitiative.status.is_(None),
                    StrategicInitiative.status.not_in(INACTIVE_STATUSES),
                ),
                StrategicInitiative.archived_at.is_(None),
            )
        ).scalars()
    )
    undated = [i for i in initiatives if i.start_date is None]
    running = [i for i in initiatives if _runs_in(i, start, end)]
    running_ids = [i.id for i in running]
    by_id = {i.id: i for i in running}

    conflicts: List[Dict[str, Any]] = []

    if running_ids:
        links = db.session.execute(
            db.select(
                strategic_initiative_capabilities.c.capability_id,
                strategic_initiative_capabilities.c.strategic_initiative_id,
            ).where(strategic_initiative_capabilities.c.strategic_initiative_id.in_(running_ids))
        ).all()
        per_capability: Dict[int, List[int]] = {}
        for capability_id, initiative_id in links:
            per_capability.setdefault(capability_id, []).append(initiative_id)
        shared = {cid: ids for cid, ids in per_capability.items() if len(ids) > 1}
        names = {}
        if shared:
            names = {
                c.id: c.name
                for c in db.session.execute(
                    db.select(BusinessCapability).where(BusinessCapability.id.in_(shared))
                ).scalars()
            }
        for capability_id, ids in sorted(shared.items(), key=lambda kv: names.get(kv[0], "")):
            conflicts.append(
                {
                    "kind": "capability",
                    "subject": names.get(capability_id, "A capability"),
                    "initiatives": [
                        {"id": i, "name": by_id[i].name} for i in sorted(ids, key=lambda i: by_id[i].name)
                    ],
                }
            )

    per_owner: Dict[int, List[StrategicInitiative]] = {}
    for initiative in running:
        if initiative.owner_id is not None:
            per_owner.setdefault(initiative.owner_id, []).append(initiative)
    shared_owners = {oid: items for oid, items in per_owner.items() if len(items) > 1}
    if shared_owners:
        users = {
            u.id: u
            for u in db.session.execute(
                db.select(User).where(User.id.in_(shared_owners))
            ).scalars()
        }
        for owner_id, items in shared_owners.items():
            user = users.get(owner_id)
            label = (
                ("%s %s" % (user.first_name or "", user.last_name or "")).strip() or user.email
                if user
                else "An owner"
            )
            conflicts.append(
                {
                    "kind": "owner",
                    "subject": label,
                    "initiatives": [
                        {"id": i.id, "name": i.name} for i in sorted(items, key=lambda i: i.name)
                    ],
                }
            )

    return {
        "quarter": {"year": year, "quarter": quarter, "start": start, "end": end},
        "conflicts": conflicts,
        "undated": [{"id": i.id, "name": i.name} for i in undated],
        "running_count": len(running),
    }

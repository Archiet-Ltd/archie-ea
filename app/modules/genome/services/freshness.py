"""Model freshness: how much of one organisation's model was confirmed or synchronised recently.

``measure_freshness(org_id)`` answers "what share of the model has someone
confirmed, or a connector synchronised, in the last 90 days", by layer and by
owner, and names the owners whose records are stalest. It is read-only,
deterministic for a given ``now`` and always scoped to one organisation.

Which dates it reads
--------------------
The element table has no confirmation or synchronisation date of its own yet,
so an element's date is the latest of the dates already recorded against it:

* ``archimate_elements.last_reviewed_date`` - when the element was last reviewed;
* ``application_components.updated_at`` - when the application record behind an
  application element was last saved;
* ``application_components.last_sync_from_abacus`` - when a connector last
  synchronised that application record.

A relationship's date is ``archimate_relationships.updated_at``.

An element with none of these dates is counted as "not recorded", never as
fresh and never as a made-up age. When no element in the organisation has a
date, the share is ``None`` (shown as a dash) with the reason, not 0.

Owners are read from the one owner record, ``application_owners``, for the
application behind an element; the assignment and the user must both belong to
the organisation. Elements with no application record have no owner record and
are counted under "no owner recorded".
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

FRESHNESS_WINDOW_DAYS = 90
STALEST_OWNER_LIMIT = 5

NO_LAYER = "Layer not recorded"
NO_DATE_REASON = (
    "No review date is recorded on these elements, and no application record "
    "behind them has been saved or synchronised."
)
NO_OWNER_REASON = (
    "Owners are recorded per application; these elements have no application "
    "record with an owner."
)

BASIS = [
    "the element's last review date",
    "the date its application record was last saved",
    "the date a connector last synchronised its application record",
]


def _as_datetime(value: Any) -> Optional[datetime]:
    """A naive datetime for a date or datetime value, or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is not None else value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return None


def _latest(values: Iterable[Any]) -> Optional[datetime]:
    dates = [d for d in (_as_datetime(v) for v in values) if d is not None]
    return max(dates) if dates else None


def _bucket() -> Dict[str, Any]:
    return {"total": 0, "fresh": 0, "stale": 0, "not_recorded": 0, "oldest": None}


def _count(bucket: Dict[str, Any], when: Optional[datetime], cutoff: datetime) -> None:
    bucket["total"] += 1
    if when is None:
        bucket["not_recorded"] += 1
        return
    if when >= cutoff:
        bucket["fresh"] += 1
    else:
        bucket["stale"] += 1
    if bucket["oldest"] is None or when < bucket["oldest"]:
        bucket["oldest"] = when


def _finish(bucket: Dict[str, Any]) -> Dict[str, Any]:
    """Add the share; None when nothing in the bucket has a recorded date."""
    measured = bucket["fresh"] + bucket["stale"]
    out = dict(bucket)
    out["share"] = (bucket["fresh"] / bucket["total"]) if measured else None
    out["oldest"] = bucket["oldest"].isoformat() if bucket["oldest"] is not None else None
    return out


def _owner_label(first_name: Optional[str], last_name: Optional[str], email: Optional[str]) -> str:
    name = " ".join(p for p in (first_name, last_name) if p)
    return name or (email or "")


def measure_freshness(
    org_id: int,
    session=None,
    *,
    window_days: int = FRESHNESS_WINDOW_DAYS,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Measure one organisation's model freshness.

    Args:
        org_id: the organisation to measure. Passed explicitly so the query is
            scoped outside a request too; every read filters on it.
        session: optional SQLAlchemy session; defaults to ``db.session``.
        window_days: how recent a date must be to count as fresh.
        now: the moment to measure at (tests pass a fixed value).

    Returns a dict with ``elements`` and ``relationships`` totals, ``by_layer``
    and ``by_owner`` rows, ``no_owner`` for elements without an owner record,
    and ``stalest_owners``. Each row carries total, fresh, stale, not_recorded,
    share (None when nothing is dated) and oldest (ISO date or None).
    """
    if session is None:
        from app.extensions import db

        session = db.session

    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.user import User

    now = _as_datetime(now) or datetime.utcnow()
    cutoff = now - timedelta(days=window_days)

    elements = (
        session.query(ArchiMateElement.id, ArchiMateElement.layer, ArchiMateElement.last_reviewed_date)
        .filter(ArchiMateElement.organization_id == org_id)
        .filter(ArchiMateElement.deleted_at.is_(None))
        .order_by(ArchiMateElement.id.asc())
        .all()
    )

    apps = (
        session.query(
            ApplicationComponent.id,
            ApplicationComponent.archimate_element_id,
            ApplicationComponent.updated_at,
            ApplicationComponent.last_sync_from_abacus,
        )
        .filter(ApplicationComponent.organization_id == org_id)
        .filter(ApplicationComponent.deleted_at.is_(None))
        .filter(ApplicationComponent.archimate_element_id.isnot(None))
        .all()
    )
    app_dates: Dict[int, List[Any]] = {}
    app_ids_by_element: Dict[int, List[int]] = {}
    for app_id, element_id, updated_at, synced_at in apps:
        app_dates.setdefault(element_id, []).extend([updated_at, synced_at])
        app_ids_by_element.setdefault(element_id, []).append(app_id)

    owners_by_app: Dict[int, Dict[int, str]] = {}
    all_app_ids = [a[0] for a in apps]
    if all_app_ids:
        owner_rows = (
            session.query(
                ApplicationOwner.application_id,
                User.id,
                User.first_name,
                User.last_name,
                User.email,
            )
            .join(User, User.id == ApplicationOwner.user_id)
            .filter(ApplicationOwner.organization_id == org_id)
            .filter(User.organization_id == org_id)
            .filter(ApplicationOwner.application_id.in_(all_app_ids))
            .all()
        )
        for app_id, user_id, first, last, email in owner_rows:
            owners_by_app.setdefault(app_id, {})[user_id] = _owner_label(first, last, email)

    totals = _bucket()
    by_layer: Dict[str, Dict[str, Any]] = {}
    by_owner: Dict[int, Dict[str, Any]] = {}
    owner_names: Dict[int, str] = {}
    no_owner = _bucket()

    for element_id, layer, reviewed_at in elements:
        when = _latest([reviewed_at, *app_dates.get(element_id, [])])
        _count(totals, when, cutoff)
        _count(by_layer.setdefault(str(layer) if layer else NO_LAYER, _bucket()), when, cutoff)

        owners: Dict[int, str] = {}
        for app_id in app_ids_by_element.get(element_id, []):
            owners.update(owners_by_app.get(app_id, {}))
        if not owners:
            _count(no_owner, when, cutoff)
        for user_id, label in owners.items():
            owner_names[user_id] = label
            _count(by_owner.setdefault(user_id, _bucket()), when, cutoff)

    rel_totals = _bucket()
    for (updated_at,) in (
        session.query(ArchiMateRelationship.updated_at)
        .filter(ArchiMateRelationship.organization_id == org_id)
        .all()
    ):
        _count(rel_totals, _as_datetime(updated_at), cutoff)

    layer_rows = [{"layer": name, **_finish(b)} for name, b in sorted(by_layer.items())]
    owner_rows_out = [
        {"user_id": uid, "owner": owner_names[uid], **_finish(b)}
        for uid, b in sorted(by_owner.items(), key=lambda kv: (owner_names[kv[0]].lower(), kv[0]))
    ]
    # Stalest first: owners with a stale record, oldest date first, then most stale.
    stalest = sorted(
        (row for row in owner_rows_out if row["stale"] > 0),
        key=lambda row: (row["oldest"], -row["stale"], row["owner"].lower()),
    )[:STALEST_OWNER_LIMIT]

    return {
        "organization_id": org_id,
        "window_days": window_days,
        "as_of": now.isoformat(),
        "basis": list(BASIS),
        "elements": _finish(totals),
        "relationships": _finish(rel_totals),
        "by_layer": layer_rows,
        "by_owner": owner_rows_out,
        "no_owner": _finish(no_owner),
        "stalest_owners": stalest,
        "not_recorded_reason": NO_DATE_REASON,
        "no_owner_reason": NO_OWNER_REASON,
    }


__all__ = ["FRESHNESS_WINDOW_DAYS", "measure_freshness"]

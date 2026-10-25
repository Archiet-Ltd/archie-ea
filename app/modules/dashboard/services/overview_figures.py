"""The figures on the Overview dashboard, read from the organisation's own records.

One function, ``build_overview``, answers every figure on the page so the page and the
lists it links to cannot disagree:

* applications are the Systems list's default set (duplicates and decommissioned left out);
* an owner is ``application_is_owned_expression``, the one definition of "owned";
* run cost is the Spend page's annual recurring cost (``get_spend_summary``);
* what was added and changed comes from the change history (``HistoryService``).

A figure that cannot be worked out is ``None`` with a sentence saying why; the template
never turns a missing figure into a zero.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func, or_

from app import db

logger = logging.getLogger(__name__)

RANGES = (("90d", "Last 3 months", 90), ("30d", "Last 30 days", 30), ("7d", "Last 7 days", 7))
CONNECTED_KINDS = ("import", "connector")


def _percent_change(now_value, before_value):
    if not before_value:
        return None
    return round((now_value - before_value) / before_value * 100)


def _signed(number):
    return "%+d" % number


def _plural(count, one, many=None):
    return "%d %s" % (count, one if count == 1 else (many or one + "s"))


def _first_name(user):
    name = (getattr(user, "first_name", "") or "").strip()
    return name or None


def _active_applications():
    """The applications the Systems list shows by default."""
    from app.models.application_portfolio import ApplicationComponent as A

    return db.select(A).where(
        ~A.name.ilike("(Duplicate)%", escape="\\"),
        or_(A.lifecycle_status.is_(None), func.lower(A.lifecycle_status) != "5. decommissioned"),
    )


def _applications(org_id, now):
    from app.models.application_portfolio import ApplicationComponent as A
    from app.services.element_ownership_service import application_is_owned_expression

    base = _active_applications().where(A.organization_id == org_id)
    ids = base.with_only_columns(A.id).subquery()
    total = db.session.execute(db.select(func.count()).select_from(ids)).scalar_one()
    since = now - timedelta(days=30)
    added = db.session.execute(
        db.select(func.count(A.id)).where(A.id.in_(db.select(ids.c.id)), A.created_at >= since)
    ).scalar_one()
    owned = application_is_owned_expression(A)
    unowned = db.session.execute(
        base.where(~owned).with_only_columns(A.id, A.name, A.created_at).order_by(A.name)
    ).all()
    return {
        "total": total,
        "added_30d": added,
        "unowned": [(row.id, row.name) for row in unowned],
        "unowned_added_30d": sum(1 for row in unowned if row.created_at and row.created_at >= since),
    }


def _sourced_share(org_id):
    """Share of model elements that record where they came from, and the least-sourced layer."""
    from app.models import ArchiMateElement as E

    rows = db.session.execute(
        db.select(E.layer, func.count(E.id), func.count(E.source_kind))
        .where(E.organization_id == org_id, E.deleted_at.is_(None))
        .group_by(E.layer)
    ).all()
    total = sum(r[1] for r in rows)
    if not total:
        return None
    sourced = sum(r[2] for r in rows)
    shares = sorted(
        ((r[2] / r[1], r[0] or "Unassigned layer", r[1]) for r in rows), key=lambda item: (item[0], item[1])
    )
    weakest = shares[0] if shares and shares[0][0] < 1 else None
    return {
        "percent": round(sourced / total * 100),
        "total": total,
        "weakest": ({"layer": weakest[1], "percent": round(weakest[0] * 100), "count": weakest[2]} if weakest else None),
    }


def _relationships(org_id, now):
    from app.models import ArchiMateElement as E
    from app.models import ArchiMateRelationship as R

    total = db.session.execute(db.select(func.count(R.id)).where(R.organization_id == org_id)).scalar_one()
    if not total:
        return {"total": 0}
    since = now - timedelta(days=30)
    added = db.session.execute(
        db.select(func.count(R.id)).where(R.organization_id == org_id, R.created_at >= since)
    ).scalar_one()
    ends = Counter()
    for source_id, target_id in db.session.execute(
        db.select(R.source_id, R.target_id).where(R.organization_id == org_id)
    ):
        ends[source_id] += 1
        if target_id != source_id:
            ends[target_id] += 1
    top = None
    if ends:
        element_id, count = max(ends.items(), key=lambda item: (item[1], -item[0]))
        name = db.session.execute(
            db.select(E.name).where(E.id == element_id, E.organization_id == org_id)
        ).scalar_one_or_none()
        if name:
            top = {"name": name, "count": count}
    return {"total": total, "added_30d": added, "top": top}


def _months(now, count=12):
    year, month = now.year, now.month
    out = []
    for _ in range(count):
        out.append(date(year, month, 1))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(out))


def _run_cost(org_id, now):
    from app.models.application_portfolio import VendorContract
    from app.modules.procurement.services import get_spend_summary

    summary = get_spend_summary()
    total = summary["total_annual_cost"]
    if total is None:
        return {"total": None, "contracts": summary["total_contracts"]}
    contracts = db.session.execute(
        db.select(VendorContract.start_date, VendorContract.end_date, VendorContract.annual_cost).where(
            VendorContract.organization_id == org_id,
            VendorContract.status == "active",
            VendorContract.annual_cost.isnot(None),
        )
    ).all()
    series = []
    for first in _months(now.date()):
        nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
        monthly = sum(
            c.annual_cost / 12
            for c in contracts
            if c.start_date < nxt and (c.end_date is None or c.end_date >= first)
        )
        series.append({"month": first.strftime("%b"), "value": round(monthly)})
    this_month, last_month = series[-1]["value"], series[-2]["value"]
    top = summary["top_vendors"][0] if summary["top_vendors"] else None
    share = None
    if top and top[1]["annual_cost"] and total:
        share = {"name": top[0], "percent": round(top[1]["annual_cost"] / total * 100)}
    return {
        "total": total,
        "contracts": summary["total_contracts"],
        "series": series,
        "this_month": this_month,
        "change": _percent_change(this_month, last_month),
        "top": share,
    }


def _history(org_id, now):
    """Every model change since the start of last year, from the change history."""
    from app.modules.intelligence.services.history_service import HistoryService

    start = datetime(now.year - 1, 1, 1)
    try:
        return HistoryService(db.session, org_id).difference(start, now).changes
    except Exception:
        db.session.rollback()
        logger.exception("overview: change history could not be read")
        return None


def _name_of(change):
    snapshot = change.snapshot or {}
    if change.table_name == "archimate_relationships":
        return "%s relationship" % (snapshot.get("type") or "A").replace("_", " ").capitalize()
    return snapshot.get("name") or "An element"


def _is_connected(change):
    return (change.snapshot or {}).get("source_kind") in CONNECTED_KINDS


def _activity(changes, now):
    if changes is None:
        return None
    today = now.date()
    inserts = [c for c in changes if c.action == "insert" and c.valid_from]
    ranges = {}
    for key, label, days in RANGES:
        first = today - timedelta(days=days - 1)
        per_day = {first + timedelta(days=i): [0, 0] for i in range(days)}
        for change in inserts:
            day = change.valid_from.date()
            if day in per_day:
                per_day[day][0 if _is_connected(change) else 1] += 1
        rows = [
            {"day": d.strftime("%d/%m"), "connected": v[0], "manual": v[1]}
            for d, v in per_day.items()
        ]
        ranges[key] = {
            "label": label,
            "rows": rows,
            "connected": sum(r["connected"] for r in rows),
            "manual": sum(r["manual"] for r in rows),
        }
        ranges[key]["total"] = ranges[key]["connected"] + ranges[key]["manual"]
    this_year = sum(1 for c in inserts if c.valid_from.year == now.year)
    last_year = sum(1 for c in inserts if c.valid_from.year == now.year - 1)
    week_start = today - timedelta(days=6)
    week = [c for c in changes if c.valid_from and c.valid_from.date() >= week_start and c.action in ("insert", "update", "delete")]
    per_weekday = defaultdict(int)
    for change in week:
        per_weekday[change.valid_from.date()] += 1
    weekly_rows = [
        {"day": (week_start + timedelta(days=i)).strftime("%a"), "changes": per_weekday[week_start + timedelta(days=i)]}
        for i in range(7)
    ]
    recent = sorted((c for c in changes if c.valid_from), key=lambda c: c.valid_from, reverse=True)[:5]
    return {
        "ranges": ranges,
        "this_year": this_year,
        "last_year": last_year,
        "year_change": _percent_change(this_year, last_year),
        "week": {
            "rows": weekly_rows,
            "total": len(week),
            "added": sum(1 for c in week if c.action == "insert"),
            "updated": sum(1 for c in week if c.action == "update"),
            "removed": sum(1 for c in week if c.action == "delete"),
        },
        "recent": [
            {
                "name": _name_of(c),
                "action": {"insert": "Added", "update": "Updated", "delete": "Removed"}.get(c.action, c.action),
                "by": c.changed_by_name,
                "when": c.valid_from,
                "kind": "relationship" if c.table_name == "archimate_relationships" else "element",
            }
            for c in recent
        ],
    }


def _areas(org_id):
    from app.models.application_portfolio import ApplicationComponent as A

    ids = _active_applications().where(A.organization_id == org_id).with_only_columns(A.id).subquery()
    area = func.coalesce(func.nullif(func.trim(A.business_domain), ""), "No area recorded").label("area")
    rows = db.session.execute(
        db.select(area, func.count(A.id))
        .where(A.id.in_(db.select(ids.c.id)))
        .group_by(area)
        .order_by(func.count(A.id).desc(), area)
        .limit(6)
    ).all()
    return [{"area": r[0], "applications": r[1]} for r in rows]


def _table(org_id):
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent as A
    from app.services.application_cost_accessor import get_annual_cost_float
    from app.services.element_ownership_service import application_is_owned

    apps = db.session.execute(_active_applications().where(A.organization_id == org_id).order_by(A.name)).scalars().all()
    owners = ApplicationOwner.get_display_rows_for_applications([a.id for a in apps], org_id) if apps else {}
    rows = []
    for app in apps:
        names = []
        if application_is_owned(app):
            names = [o["user_name"] for o in owners.get(app.id, []) if o.get("user_name") and o["user_name"] != "—"]
            if not names:
                legacy = (app.legacy_business_owner or app.legacy_application_owner or "").strip()
                names = [legacy] if legacy else ["Name not recorded"]
        rows.append({
            "id": app.id,
            "name": app.name,
            "type": (app.component_type or "").strip() or None,
            "status": (app.lifecycle_status or "").strip() or None,
            "owner": ", ".join(names) if names else None,
            "cost": get_annual_cost_float(app),
        })
    rows.sort(key=lambda r: (r["cost"] is None, -(r["cost"] or 0), r["name"].lower()))
    return {"rows": rows[:10], "total": len(rows)}


def build_overview(org_id, user, now=None):
    """Every figure the Overview page shows for one organisation."""
    now = now or datetime.utcnow()
    apps = _applications(org_id, now)
    sourced = _sourced_share(org_id)
    relationships = _relationships(org_id, now)
    run_cost = _run_cost(org_id, now)
    changes = _history(org_id, now)
    activity = _activity(changes, now)
    empty = not apps["total"] and not relationships["total"] and sourced is None
    return {
        "empty": empty,
        "first_name": _first_name(user),
        "applications": apps,
        "sourced": sourced,
        "relationships": relationships,
        "run_cost": run_cost,
        "activity": activity,
        "areas": _areas(org_id),
        "table": _table(org_id),
        "ranges": RANGES,
    }


def _link(endpoint, **values):
    from flask import current_app, url_for

    return url_for(endpoint, **values) if endpoint in current_app.view_functions else None


def overview_context(user, org_id):
    """The template context for the Overview page: the figures and where each card leads."""
    from app.decorators.requires_role import may_open_spend

    figures = build_overview(org_id, user)
    figures["links"] = {
        "applications": _link("unified_applications.application_list"),
        "no_owner": _link("unified_applications.application_list", no_owner=1),
        "spend": _link("procurement.spend_analytics") if may_open_spend(user) else None,
        "model": _link("archimate_crud.dashboard"),
        "changes": _link("intelligence_ui.history_changes_page"),
        "connect": _link("m365_connector.github_connect"),
    }
    return {"overview": figures}

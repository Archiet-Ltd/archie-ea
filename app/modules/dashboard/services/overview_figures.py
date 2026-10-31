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
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func

from app import db

logger = logging.getLogger(__name__)

RANGES = (("90d", "Last 3 months", 90), ("30d", "Last 30 days", 30), ("7d", "Last 7 days", 7))
CONNECTED_KINDS = ("import", "connector")
#: Change-history tables that hold people and access, not the organisation's model. A person's own
#: account is written to the history when they sign up; it is never a record the model gained.
ACCOUNT_TABLES = frozenset({"user", "user_sessions", "invitations", "organization_settings"})


def model_record_tables():
    """The one set of change-history tables whose rows are model records (elements, relationships,
    applications, capabilities, owners, solutions ...): every audited table except the account tables.
    Every figure, bar and split on the Overview selects its change rows through this set."""
    from app.modules.intelligence.services.history_service import AUDITED_TABLES

    return frozenset(AUDITED_TABLES) - ACCOUNT_TABLES


def _currency():
    """The organisation's currency symbol and whether it comes before the amount."""
    from flask import has_request_context

    from app.middleware.tenant_context import current_org
    from config import CurrencyConfig

    try:
        organization = current_org() if has_request_context() else None
    except Exception:
        organization = None
    cfg = CurrencyConfig.get_org_currency_config(organization)
    return cfg["symbol"], cfg["position"] == "prefix", cfg["code"]


def _currency_code():
    """The organisation's currency code (e.g., 'GBP', 'USD')."""
    from flask import has_request_context

    from app.middleware.tenant_context import current_org
    from config import CurrencyConfig

    try:
        organization = current_org() if has_request_context() else None
    except Exception:
        organization = None
    cfg = CurrencyConfig.get_org_currency_config(organization)
    return cfg["code"]


def _money(amount, compact=False):
    """An amount in whole pounds, or short ("£250k", "£20.8k") for a card; the table keeps the whole figure."""
    if amount is None:
        return None
    symbol, prefix, _code = _currency()
    amount = float(amount)
    if compact and abs(amount) >= 1000:
        for size, suffix in ((1_000_000, "m"), (1_000, "k")):
            if abs(amount) >= size:
                number = ("%.1f" % (amount / size)).rstrip("0").rstrip(".")
                text = number + suffix
                break
    else:
        text = "{:,.0f}".format(amount)
    return symbol + text if prefix else text + symbol


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


def _is_first_visit(user):
    """True when the signed-in user has no ``UserSession`` row created before the
    current one -- the current session is the first one this user has ever had."""
    from flask import session

    from app.models.user_session import UserSession

    sid = session.get("_sid")
    if not sid:
        return False
    # tenant-scoping-ok: UserSession is deliberately NOT TenantMixin (see its module
    # docstring); scoped here by user_id, which is the one scope that applies to it.
    current = UserSession.query.filter_by(sid=sid).first()
    if current is None or current.user_id != user.id:
        return False
    # tenant-scoping-ok: UserSession is deliberately NOT TenantMixin (see its module
    # docstring); scoped here by user_id, which is the one scope that applies to it.
    earlier = UserSession.query.filter(
        UserSession.user_id == user.id,
        UserSession.created_at < current.created_at,
    ).first()
    return earlier is None


def _active_applications():
    """The applications the Systems list shows by default (the rule every count and cost total uses)."""
    from app.services.application_cost_accessor import counted_applications_query

    return counted_applications_query()


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
    layers = [
        {"layer": name, "key": (name or "").lower(), "percent": round(share * 100), "count": count}
        for share, name, count in shares
        if share < 1
    ][:4]
    return {
        "percent": round(sourced / total * 100),
        "total": total,
        "weakest": ({"layer": weakest[1], "percent": round(weakest[0] * 100), "count": weakest[2]} if weakest else None),
        "layers": layers,
    }


def _relationships(org_id, now):
    from app.models import ArchiMateElement as E
    from app.models import ArchiMateRelationship as R
    from app.models.audit_log import AuditLog

    total = db.session.execute(db.select(func.count(R.id)).where(R.organization_id == org_id)).scalar_one()
    if not total:
        return {"total": 0}
    since = now - timedelta(days=30)
    added = db.session.execute(
        db.select(func.count(AuditLog.id)).where(
            AuditLog.organization_id == org_id,
            AuditLog.table_name == "archimate_relationships",
            AuditLog.action == "create",
            AuditLog.created_at >= since,
        )
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
    """The annual run cost: what the organisation's applications cost, read through the one cost accessor."""
    from app.models.application_portfolio import ApplicationComponent as A
    from app.services.application_cost_accessor import organisation_annual_cost

    summary = organisation_annual_cost(org_id)
    total = summary["total"]
    if total is None:
        return {"total": None, "applications": summary["applications"], "with_cost": 0}
    month = total / 12
    top = None
    ranked = sorted(
        ((app_id, f["value"]) for app_id, f in summary["figures"].items() if f["value"] is not None),
        key=lambda item: (-item[1], item[0]),
    )
    if ranked:
        name = db.session.execute(
            db.select(A.name).where(A.id == ranked[0][0], A.organization_id == org_id)
        ).scalar_one_or_none()
        if name:
            top = {"name": name, "percent": round(ranked[0][1] / total * 100)}
    return {
        "total": total,
        "total_short": _money(total, compact=True),
        "total_exact": _money(total),
        "applications": summary["applications"],
        "with_cost": summary["applications_with_cost"],
        "series": [],
        "this_month": month,
        "this_month_short": _money(month, compact=True),
        "this_month_exact": _money(month),
        "change": None,
        "top": top,
    }


def _history(org_id, now):
    """Every model change since the start of last year, from the change history."""
    from app.modules.intelligence.services.history_service import HistoryService

    start = datetime(now.year - 1, 1, 1)
    try:
        tables = model_record_tables()
        return [c for c in HistoryService(db.session, org_id).difference(start, now).changes if c.table_name in tables]
    except Exception:
        db.session.rollback()
        logger.exception("overview: change history could not be read")
        return None


def _application_names(changes):
    """Names of the applications that ownership changes refer to, by application id."""
    from app.models.application_portfolio import ApplicationComponent

    ids = {
        (c.snapshot or c.previous_snapshot or {}).get("application_id")
        for c in changes
        if c.table_name in ("application_owners", "application_ownership")
    }
    ids = {i for i in ids if isinstance(i, int)}
    if not ids:
        return {}
    rows = db.session.execute(
        db.select(ApplicationComponent.id, ApplicationComponent.name).where(ApplicationComponent.id.in_(ids))
    ).all()
    return dict(rows)


#: What a relationship reads as between the names of the two elements it joins.
_RELATION_VERBS = {
    "serving": "serves", "serves": "serves", "uses": "serves",
    "realization": "realises", "realisation": "realises", "realizes": "realises", "realises": "realises",
    "assignment": "is assigned to", "assigns": "is assigned to",
    "flow": "flows to", "flows": "flows to",
    "triggering": "triggers", "triggers": "triggers",
    "access": "accesses", "accesses": "accesses",
    "association": "is associated with", "associates": "is associated with",
    "aggregation": "aggregates", "aggregates": "aggregates",
    "composition": "is composed of", "composes": "is composed of",
    "influence": "influences", "influences": "influences",
    "specialization": "is a kind of", "specialisation": "is a kind of",
    "specializes": "is a kind of", "specialises": "is a kind of",
}


def _relation_verb(relation_type):
    key = (relation_type or "").strip().lower().replace("_", "").replace(" ", "")
    if key.endswith("relationship"):
        key = key[: -len("relationship")]
    return _RELATION_VERBS.get(key, "is connected to")


def _relationship_phrases(org_id, changes):
    """What each relationship the changes are about reads as, by relationship id: "Order management system serves Checkout".

    The relationship and the two elements it joins are read from this organisation's model. A relationship
    that has since been deleted is read from the version the change history kept.
    """
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    rows = [c for c in changes if c.table_name == "archimate_relationships"]
    if not rows:
        return {}
    ids = {c.record_id for c in rows}
    current = {
        r[0]: (r[1], r[2], r[3])
        for r in db.session.execute(
            db.select(
                ArchiMateRelationship.id, ArchiMateRelationship.type,
                ArchiMateRelationship.source_id, ArchiMateRelationship.target_id,
            ).where(ArchiMateRelationship.id.in_(ids), ArchiMateRelationship.organization_id == org_id)
        ).all()
    }
    joined = {}
    for change in rows:
        if change.record_id in current:
            joined[change.record_id] = current[change.record_id]
            continue
        for values in (change.snapshot, change.previous_snapshot):
            if values and values.get("source_id") and values.get("target_id"):
                joined[change.record_id] = (values.get("type"), values["source_id"], values["target_id"])
                break
    element_ids = {i for _t, source, target in joined.values() for i in (source, target)}
    names = {}
    if element_ids:
        names = {
            r[0]: r[1]
            for r in db.session.execute(
                db.select(ArchiMateElement.id, ArchiMateElement.name).where(
                    ArchiMateElement.id.in_(element_ids), ArchiMateElement.organization_id == org_id
                )
            ).all()
            if r[1]
        }
    phrases = {}
    for record_id, (kind, source, target) in joined.items():
        if names.get(source) and names.get(target):
            phrases[record_id] = "%s %s %s" % (names[source], _relation_verb(kind), names[target])
    return phrases


def _name_of(change, applications=None, relationships=None):
    """What a change row is about: the record's name, or its type and name once the record is deleted.

    A relationship reads as the two elements it joins: "Order management system serves Checkout".
    """
    snapshot = change.snapshot or {}
    before = change.previous_snapshot or {}
    kind = change.kind or "Record"
    if change.table_name == "archimate_relationships":
        phrase = (relationships or {}).get(change.record_id)
        if phrase:
            return phrase
        # the history's own label for it, "serving: Order management system to Checkout"
        match = re.fullmatch(r"(\w+): (.+) to (.+)", change.label or "")
        if match:
            return "%s %s %s" % (match.group(2), _relation_verb(match.group(1)), match.group(3))
        return "Relationship between two elements"
    if change.table_name in ("application_owners", "application_ownership"):
        app_id = snapshot.get("application_id") or before.get("application_id")
        app_name = (applications or {}).get(app_id)
        return "Owner of %s" % app_name if app_name else kind
    name = change.label or snapshot.get("name") or before.get("name")
    if not name:
        return kind
    return "%s %s" % (kind, name) if change.action == "delete" else name


def _person_names(org_id, changes):
    """The names of the people who made the changes, as the change history knows them (never an email address)."""
    from app.modules.intelligence.services.history_service import HistoryService

    return HistoryService(db.session, org_id).person_names(c.changed_by_id for c in changes)


def _is_connected(change):
    return (change.snapshot or {}).get("source_kind") in CONNECTED_KINDS


def _bars(inserts, today, days):
    """Records added, split into connected and by hand: one bar a day, or a week when the range is long."""
    size = 7 if days > 31 else 1
    first = today - timedelta(days=days - 1)
    buckets = []
    end = today
    while end >= first:
        buckets.append([max(end - timedelta(days=size - 1), first), end, 0, 0])
        end -= timedelta(days=size)
    for change in inserts:
        day = change.valid_from.date()
        if first <= day <= today:
            for bucket in buckets:
                if bucket[0] <= day <= bucket[1]:
                    bucket[2 if _is_connected(change) else 3] += 1
                    break
    return [
        {"day": "%d %s" % (b[0].day, b[0].strftime("%b")), "connected": b[2], "manual": b[3]}
        for b in reversed(buckets)
    ]


def _activity(changes, now, org_id):
    if changes is None:
        return None
    today = now.date()
    inserts = [c for c in changes if c.action == "insert" and c.valid_from]
    ranges = {}
    for key, label, days in RANGES:
        rows = _bars(inserts, today, days)
        ranges[key] = {
            "label": label,
            "rows": rows,
            "per": "week" if days > 31 else "day",
            "connected": sum(r["connected"] for r in rows),
            "manual": sum(r["manual"] for r in rows),
        }
        ranges[key]["total"] = ranges[key]["connected"] + ranges[key]["manual"]
    # the shortest range that holds any record is the one shown first
    default_range = next((key for key in ("7d", "30d") if ranges[key]["total"]), "90d")
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
    recent_all = sorted((c for c in changes if c.valid_from), key=lambda c: c.valid_from, reverse=True)
    # Collapse changes to the same element on the same day into one row, keeping the latest.
    seen = {}
    recent = []
    for c in recent_all:
        key = (c.table_name, c.record_id, c.valid_from.date())
        if key not in seen:
            seen[key] = c
            recent.append(c)
    recent = recent[:5]
    applications = _application_names(recent)
    relationships = _relationship_phrases(org_id, recent)
    people = _person_names(org_id, recent)
    return {
        "ranges": ranges,
        "default_range": default_range,
        "this_year": this_year,
        "last_year": last_year,
        "year_change": _percent_change(this_year, last_year),
        "year_rows": [
            {"year": "Last year", "last": last_year, "this": 0},
            {"year": "This year", "last": 0, "this": this_year},
        ],
        "week": {
            "rows": weekly_rows,
            "total": len(week),
            "added": sum(1 for c in week if c.action == "insert"),
            "updated": sum(1 for c in week if c.action == "update"),
            "removed": sum(1 for c in week if c.action == "delete"),
        },
        "recent": [
            {
                "name": _name_of(c, applications, relationships),
                "action": {"insert": "Added", "update": "Updated", "delete": "Removed"}.get(c.action, c.action),
                "by": people.get(c.changed_by_id),
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


def _once(names):
    """The names with each person listed once, in the order first met."""
    seen, kept = set(), []
    for name in names:
        key = " ".join(name.split()).casefold()
        if key not in seen:
            seen.add(key)
            kept.append(name)
    return kept


def _table(org_id, with_cost=True):
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import lifecycle_label
    from app.models.application_portfolio import ApplicationComponent as A
    from app.services.application_cost_accessor import annual_cost_figures
    from app.services.element_ownership_service import application_is_owned

    apps = db.session.execute(_active_applications().where(A.organization_id == org_id).order_by(A.name)).scalars().all()
    owners = ApplicationOwner.get_display_rows_for_applications([a.id for a in apps], org_id) if apps else {}
    figures = annual_cost_figures(apps) if with_cost else {}
    rows = []
    for app in apps:
        names = []
        if application_is_owned(app):
            names = _once([o["user_name"] for o in owners.get(app.id, []) if o.get("user_name") and o["user_name"] != "—"])
            if not names:
                legacy = (app.legacy_business_owner or app.legacy_application_owner or "").strip()
                names = [legacy] if legacy else ["Name not recorded"]
        rows.append({
            "id": app.id,
            "name": app.name,
            "type": (app.component_type or "").strip() or None,
            "status": lifecycle_label(app.lifecycle_status),
            "owners": names,
            "owner": ", ".join(names) if names else None,
            "cost": figures.get(app.id, {}).get("value"),
            "cost_source": figures.get(app.id, {}).get("source"),
        })
        rows[-1]["cost_label"] = _money(rows[-1]["cost"])
    rows.sort(key=lambda r: (r["cost"] is None, -(r["cost"] or 0), r["name"].lower()))
    costed = [r["cost"] for r in rows if r["cost"] is not None]
    return {"rows": rows[:10], "total": len(rows), "cost_total": sum(costed) if costed else None}


def _name_the_basis(run_cost, applications_cost):
    """Say where the run cost comes from."""
    if run_cost["total"] is None:
        return
    count = run_cost["with_cost"]
    run_cost["basis"] = "from the annual cost of %d %s" % (count, "application" if count == 1 else "applications")
    run_cost["difference"] = None
    missing = run_cost["applications"] - count
    if missing > 0:
        run_cost["difference"] = "%d %s no cost recorded." % (missing, "application has" if missing == 1 else "applications have")


def build_overview(org_id, user, now=None, with_cost=True):
    """Every figure the Overview page shows for one organisation.

    ``with_cost`` is the one rule's answer (``may_see_spend_pages``): when it is false no cost is read, and the
    run cost and the applications' costs are withheld in the shape the page renders as not shown.
    """
    now = now or datetime.utcnow()
    apps = _applications(org_id, now)
    sourced = _sourced_share(org_id)
    relationships = _relationships(org_id, now)
    run_cost = _run_cost(org_id, now) if with_cost else {"total": None, "applications": 0, "with_cost": 0, "withheld": True}
    changes = _history(org_id, now)
    activity = _activity(changes, now, org_id)
    table = _table(org_id, with_cost)
    _name_the_basis(run_cost, table["cost_total"])
    empty = not apps["total"] and not relationships["total"] and sourced is None
    return {
        "empty": empty,
        "first_name": _first_name(user),
        "first_visit": _is_first_visit(user),
        "applications": apps,
        "sourced": sourced,
        "relationships": relationships,
        "run_cost": run_cost,
        "may_see_spend": with_cost,
        "activity": activity,
        "areas": _areas(org_id),
        "table": table,
        "ranges": RANGES,
        "currency_code": _currency_code(),
        # the chart ramp tokens as colours a chart can draw with
        "chart_colours": ["hsl(var(--chart-1))", "hsl(var(--chart-tint))"],
    }


def _link(endpoint, **values):
    from flask import current_app, url_for

    return url_for(endpoint, **values) if endpoint in current_app.view_functions else None


def _has_endpoint(endpoint):
    from flask import current_app

    return endpoint in current_app.view_functions


def _layer_keys():
    from app.modules.architecture.routes.archimate_crud.routes import LAYER_CONFIG

    return LAYER_CONFIG.keys()


def overview_context(user, org_id):
    """The template context for the Overview page: the figures and where each card leads."""
    from app.decorators.requires_role import may_open_spend, may_see_spend_pages

    figures = build_overview(org_id, user, with_cost=may_see_spend_pages(user))
    figures["links"] = {
        "applications": _link("unified_applications.application_list"),
        "no_owner": _link("unified_applications.application_list", no_owner=1),
        "spend": _link("procurement.spend_analytics") if may_open_spend(user) else None,
        "model": _link("archimate_crud.dashboard"),
        "changes": _link("intelligence_ui.history_changes_page"),
        "connect": _link("m365_connector.github_connect"),
    }
    if figures["sourced"] and _has_endpoint("archimate_crud.dashboard"):
        for row in figures["sourced"]["layers"]:
            row["href"] = _link("archimate_crud.dashboard", layer=row["key"]) if row["key"] in _layer_keys() else None
    return {"overview": figures}

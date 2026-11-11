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
from datetime import datetime, timedelta

from sqlalchemy import func

from app import db

logger = logging.getLogger(__name__)

RANGES = (("90d", "Last 3 months", 90), ("30d", "Last 30 days", 30), ("7d", "Last 7 days", 7))
CONNECTED_KINDS = ("import", "connector")


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


def _money_in(amount, code):
    """A whole amount under the symbol of the currency it was recorded in, never the organisation's."""
    from config import CurrencyConfig

    cfg = CurrencyConfig.SUPPORTED_CURRENCIES.get(code.upper())
    if cfg is None:
        return "%s %s" % (code.upper(), "{:,.0f}".format(float(amount)))
    text = "{:,.0f}".format(float(amount))
    return cfg["symbol"] + text if cfg["position"] == "prefix" else text + cfg["symbol"]


def _other_currency_cost(stacked):
    """(label, code, amount) for a system whose only stated cost is in another currency, else None:
    \"€1,200 a year, from 1 active contract\". The amount is in that currency, never the organisation's."""
    parts = []
    for code, other in sorted(stacked.items()):
        n = other["contracts"]
        parts.append("%s a year, from %d active contract%s" % (_money_in(other["total"], code), n, "" if n == 1 else "s"))
    if not parts:
        return None
    first = sorted(stacked)[0]
    return "; ".join(parts), first, stacked[first]["total"]


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


def _is_duplicate(A):
    return A.name.ilike("(Duplicate)%", escape="\\")


def _is_decommissioned(A):
    return db.and_(A.lifecycle_status.isnot(None), func.lower(A.lifecycle_status) == "5. decommissioned")


def _active_applications():
    """The applications the Systems list shows by default. A repository a connector brought in and
    did not classify as an application is not one until a person promotes it by naming its owner."""
    from app.connectors.github import unpromoted_import_expression
    from app.models.application_portfolio import ApplicationComponent as A

    return db.select(A).where(~unpromoted_import_expression(A), ~_is_duplicate(A), ~_is_decommissioned(A))


def left_out_applications(org_id) -> dict:
    """How many of the organisation's application records the count leaves out, by the one reason
    each has: a duplicate, a decommissioned application, or a repository nobody owns yet."""
    from app.connectors.github import unpromoted_import_expression
    from app.models.application_portfolio import ApplicationComponent as A

    def count(*conditions):
        return db.session.execute(
            db.select(func.count(A.id)).where(A.organization_id == org_id, *conditions)
        ).scalar_one()

    return {
        "duplicate": count(_is_duplicate(A)),
        "decommissioned": count(~_is_duplicate(A), _is_decommissioned(A)),
        "repository": count(~_is_duplicate(A), ~_is_decommissioned(A), unpromoted_import_expression(A)),
    }


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
    # the card names the first of these, so an application a person recorded comes before one a connector derived
    # from a repository: the person's record is the gap they can act on first
    from app.models.archimate_core import ArchiMateElement

    derived = db.select(ArchiMateElement.id).where(
        ArchiMateElement.id == A.archimate_element_id, ArchiMateElement.source_kind == "connector"
    ).exists()
    unowned = db.session.execute(
        base.where(~owned).with_only_columns(A.id, A.name, A.created_at).order_by(derived, A.name)
    ).all()
    return {
        "total": total,
        "added_30d": added,
        "unowned": [(row.id, row.name) for row in unowned],
        "unowned_added_30d": sum(1 for row in unowned if row.created_at and row.created_at >= since),
    }


def _sourced_share(org_id):
    """Share of model elements that record where they came from, and the least-sourced layer.
    Elements loaded as a starting point at sign-up are not counted: they are not what the person brought."""
    from app.models import ArchiMateElement as E

    rows = db.session.execute(
        db.select(E.layer, func.count(E.id), func.count(E.source_kind))
        .where(E.organization_id == org_id, E.deleted_at.is_(None), E.source_kind.is_distinct_from("starter"))
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


def relationship_ends(org_id):
    """How many recorded relationships touch each element of the organisation (an element at
    both ends of one relationship counts it once). The one count the overview's "most
    connected" line and the chat's first questions both read."""
    from app.models import ArchiMateRelationship as R

    ends = Counter()
    for source_id, target_id in db.session.execute(
        db.select(R.source_id, R.target_id).where(R.organization_id == org_id)
    ):
        ends[source_id] += 1
        if target_id != source_id:
            ends[target_id] += 1
    return ends


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
    ends = relationship_ends(org_id)
    top = None
    if ends:
        element_id, count = max(ends.items(), key=lambda item: (item[1], -item[0]))
        name = db.session.execute(
            db.select(E.name).where(E.id == element_id, E.organization_id == org_id)
        ).scalar_one_or_none()
        if name:
            top = {"name": name, "count": count}
    return {"total": total, "added_30d": added, "top": top}


RUN_COST_MONTHS = 6


def _monthly_run_cost(org_id, now, this_month):
    """The monthly run cost for each of the last months, as the contracts' dates stood at each month's end.

    A contract counts in a month once its start date has come and until its end date passes. A month before
    the first contract started is left out rather than drawn as a zero: nothing was recorded then.
    """
    from app.services import vendor_register

    series = []
    month_start = now.date().replace(day=1)
    for back in range(RUN_COST_MONTHS - 1, 0, -1):
        first = month_start
        for _ in range(back):
            first = (first - timedelta(days=1)).replace(day=1)
        as_at = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        counted = [
            c for c in vendor_register.contract_ledger(org_id, as_at)["counted"] if c.annual_cost is not None
        ]
        started = [c.annual_cost for c in counted if c.start_date <= as_at]
        if started or series:
            series.append({"month": first.strftime("%b"), "value": round(sum(started) / 12)})
    return series + [{"month": now.strftime("%b"), "value": this_month}]


def _run_cost(org_id, now):
    from app.modules.procurement.services import get_spend_summary

    summary = get_spend_summary(org_id)
    total = summary["total_annual_cost"]
    if total is None:
        return {"total": None, "contracts": summary["total_contracts"]}
    from app.services import vendor_register

    ledger = vendor_register.contract_ledger(org_id, now.date())
    stated = vendor_register.contracts_in_total(ledger)
    # the monthly figure sits under "Active contracts, per month": the annual cost every active contract in the
    # organisation's currency states, over twelve, including those on a system that records its own cost. The
    # costs recorded on systems are not contracts and have no month-by-month history, so the chart draws the
    # months the contracts' own start and end dates can stand behind
    # with no active contract the sum is nothing, so the card reads zero: a computed figure, not a missing one
    contract_annual = vendor_register.contracts_annual_value(ledger) or 0
    this_month = round(contract_annual / 12)
    series = _monthly_run_cost(org_id, now, this_month)
    top = summary["top_vendors"][0] if summary["top_vendors"] else None
    share = None
    if top and top[1]["annual_cost"] and total:
        share = {"name": top[0], "percent": round(top[1]["annual_cost"] / total * 100)}
    return {
        "total": total,
        "total_short": _money(total, compact=True),
        "total_exact": _money(total),
        "contracts": summary["total_contracts"],
        "stated_contracts": stated,
        "left_apart": vendor_register.active_left_apart(ledger),
        "recorded_on_systems": summary["recorded_on_systems"],
        "series": series,
        "this_month": this_month,
        "this_month_short": _money(this_month, compact=True),
        "this_month_exact": _money(this_month),
        "change": None,
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


def _without_applications_counted_as_elements(inserts, org_id):
    """The added records with each application counted once, and no owner counted as a record.

    An application is stored as an inventory row and as the model element it is linked to, and both
    are written as one action, so the two history records are one record added. The element's record
    is kept (it names where the record came from); the inventory row's is dropped.
    """
    from app.models.application_portfolio import ApplicationComponent as A

    # who owns an application is a fact about it, written with it; it is not a second record added
    inserts = [c for c in inserts if c.table_name not in ("application_owners", "application_ownership")]
    rows = [c for c in inserts if c.table_name == "application_component"]
    if not rows:
        return inserts
    element_of = dict(
        db.session.execute(
            db.select(A.id, A.archimate_element_id).where(
                A.organization_id == org_id, A.id.in_({c.record_id for c in rows})
            )
        ).all()
    )
    element_ids = {c.record_id for c in inserts if c.table_name == "archimate_elements"}
    return [
        c for c in inserts
        if c.table_name != "application_component" or element_of.get(c.record_id) not in element_ids
    ]


def _activity(changes, now, org_id):
    if changes is None:
        return None
    today = now.date()
    inserts = _without_applications_counted_as_elements(
        [c for c in changes if c.action == "insert" and c.valid_from], org_id
    )
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
        # with nothing to set it against the card shows this one figure alone, so the figure carries its label
        "this_year": this_year if last_year or not this_year else "{:,} added this year".format(this_year),
        "last_year": last_year,
        "year_change": _percent_change(this_year, last_year),
        "year_rows": [
            {"year": "Last year", "last": last_year, "this": 0},
            {"year": "This year", "last": 0, "this": this_year},
        ],
        "week": {
            "rows": weekly_rows,
            "total": len(week),
            # records added, counted once like "Records added": the chart above counts changes
            "added": sum(1 for c in inserts if c.valid_from.date() >= week_start),
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
    from app.services.application_cost_accessor import get_run_cost_with_source
    from app.services.element_ownership_service import application_is_owned

    from app.services import vendor_register

    apps = db.session.execute(_active_applications().where(A.organization_id == org_id).order_by(A.name)).scalars().all()
    ledger = vendor_register.contract_ledger(org_id) if with_cost else None
    on_system = vendor_register.counted_by_system(ledger) if ledger else {}
    abroad = vendor_register.other_currency_by_system(ledger) if ledger else {}
    owners = ApplicationOwner.get_display_rows_for_applications([a.id for a in apps], org_id) if apps else {}
    rows = []
    for app in apps:
        names = []
        if application_is_owned(app):
            names = _once([o["user_name"] for o in owners.get(app.id, []) if o.get("user_name") and o["user_name"] != "—"])
            if not names:
                legacy = (app.legacy_business_owner or app.legacy_application_owner or "").strip()
                names = [legacy] if legacy else ["Name not recorded"]
        cost, cost_source = get_run_cost_with_source(app, on_system.get(app.id, [])) if with_cost else (None, None)
        rows.append({
            "id": app.id,
            "name": app.name,
            "type": (app.component_type or "").strip() or None,
            "status": lifecycle_label(app.lifecycle_status),
            "owners": names,
            "owner": ", ".join(names) if names else None,
            "cost": cost,
            "cost_source": cost_source,
            "cost_currency": None,
        })
        rows[-1]["cost_label"] = _money(rows[-1]["cost"])
        other = _other_currency_cost(abroad.get(app.id, {})) if cost is None else None
        if other:
            # the page shows cost_label wherever a cost is set, so the figure is stated in the currency it was recorded in
            rows[-1]["cost_label"], rows[-1]["cost_currency"], rows[-1]["cost"] = other
    # a figure in another currency is shown beside the rest but never ranked or added with them
    rows.sort(key=lambda r: (r["cost"] is None or r["cost_currency"] is not None,
                             -(r["cost"] or 0) if r["cost_currency"] is None else 0, r["name"].lower()))
    costed = [r["cost"] for r in rows if r["cost"] is not None and r["cost_currency"] is None]
    return {"rows": rows[:10], "total": len(rows), "cost_total": sum(costed) if costed else None}


def _name_the_basis(run_cost, applications_cost):
    """Say where the run cost comes from, and why the applications' own costs may add up to another figure."""
    if run_cost["total"] is None:
        return
    count = run_cost.get("stated_contracts", run_cost["contracts"])
    run_cost["basis"] = "from %d active %s" % (count, "contract" if count == 1 else "contracts")
    if run_cost.get("recorded_on_systems"):
        run_cost["basis"] = "from the costs recorded on systems and " + run_cost["basis"][5:]
    notes = []
    if applications_cost is not None:
        gap = run_cost["total"] - applications_cost
        if round(gap) > 0:
            notes.append("%s of contract cost is not yet tied to an application." % _money(gap, compact=True))
        elif round(gap) < 0:
            notes.append("The applications' own costs add up to %s, %s more than the contracts state." % (
                _money(applications_cost, compact=True), _money(-gap, compact=True)))
    notes.extend(_active_left_apart_notes(run_cost.get("left_apart")))
    run_cost["difference"] = " ".join(notes) or None


def _active_left_apart_notes(apart):
    """One sentence for each kind of active contract the counted figure leaves out, so the contracts named
    here add up to the active contracts the contracts list shows."""
    if not apart:
        return []
    notes = []
    if apart["no_cost"]:
        n = apart["no_cost"]
        notes.append("%d more active contract%s no annual cost." % (n, " states" if n == 1 else "s state"))
    for code, other in sorted(apart["other_currency"].items()):
        n = other["contracts"]
        notes.append("%d active contract%s in %s %s not added: %s a year." % (
            n, "" if n == 1 else "s", code, "is" if n == 1 else "are", _money_in(other["total"], code)))
    if apart["on_retired_system"]:
        n = apart["on_retired_system"]
        notes.append("%d active contract%s on a decommissioned or removed system and %s not counted." % (
            n, " sits" if n == 1 else "s sit", "is" if n == 1 else "are"))
    return notes


def run_cost_line(org_id, user, now=None):
    """The run cost the Overview states, for any page that must state the same figure.

    None when ``user`` may not read cost (the one rule), and ``total`` None when no active contract
    states an annual cost, so the page can say why instead of showing a zero.
    """
    from app.decorators.requires_role import may_see_spend_pages

    if not may_see_spend_pages(user):
        return None
    run_cost = _run_cost(org_id, now or datetime.utcnow())
    _name_the_basis(run_cost, None)
    return run_cost


def build_overview(org_id, user, now=None, with_cost=True):
    """Every figure the Overview page shows for one organisation.

    ``with_cost`` is the one rule's answer (``may_see_spend_pages``): when it is false no cost is read, and the
    run cost and the applications' costs are withheld in the shape the page renders as not shown.
    """
    now = now or datetime.utcnow()
    apps = _applications(org_id, now)
    sourced = _sourced_share(org_id)
    relationships = _relationships(org_id, now)
    run_cost = _run_cost(org_id, now) if with_cost else {"total": None, "contracts": 0, "withheld": True}
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

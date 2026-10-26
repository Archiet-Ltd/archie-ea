"""What an application or a technology costs, read from the one cost fact store.

Every screen that shows an element's cost (the application's Cost tab, a technology's element page, the canvas
Cost Structure box and the organisation total under it) reads this module, so the same figure is the same on each.
Nothing here reads the legacy cost columns: they reach the store through ``application_cost_accessor``.

The rules, in the words the pages state them:
- An application's annual cost is the sum of its cost lines. Lines recorded on the application's cost fields are
  its breakdown; when its recorded total of ownership is larger than that breakdown, the difference is a line of its
  own ("Not broken down"), so the total is never counted twice. Lines a person adds here are added to it.
- An application's infrastructure (and hosting) cost is split across the technologies it runs on, evenly unless a
  percentage is recorded on the link. Licences, maintenance and the rest stay with the application. A share is part
  of the application's own cost, so the organisation total counts it once, on the application.
- A monthly figure counts twelve times a year; a one-off, a budget or a forecast is shown but not counted.
- A figure in another currency is converted at the rate for its period. With no rate on file the total is missing
  and the page says which rate; it is never converted on a guess.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Dict, Iterable, List, Optional

from app import db
from app.models.cost_fact import (
    CATEGORY_TOTAL,
    ELEMENT_APPLICATION,
    ELEMENT_TECHNOLOGY,
    KIND_ACTUAL,
    KINDS,
    PERIOD_ANNUAL,
    CostFact,
)
from app.services.cost_fact_store import list_facts, upsert_fact, year_period

PENNY = Decimal("0.01")

# Who wrote a fact, as the page says it.
SOURCE_PERSON = "person"
SOURCE_ALLOCATION = "allocation"
_COLUMN_SOURCE = "application_cost_accessor"
_SOURCE_WORDS = {
    _COLUMN_SOURCE: "Recorded on the application's cost fields",
    "backfill_cost_facts": "Imported from the application's cost fields",
    "spreadsheet_import": "Imported from a spreadsheet",
    "connector": "From a connector",
}

# The categories a person picks when adding a line, in the order offered.
PERSON_CATEGORIES = (
    ("licences", "Licences"),
    ("infrastructure", "Infrastructure"),
    ("hosting", "Hosting"),
    ("maintenance", "Maintenance"),
    ("support", "Support"),
    ("development", "Development"),
    ("other", "Other"),
)
_CATEGORY_WORDS = dict(PERSON_CATEGORIES)
_CATEGORY_WORDS.update({
    "license_cost": "Licences",
    "license_cost_annual": "Licences",
    "maintenance_cost": "Maintenance",
    "infrastructure_cost": "Infrastructure",
    "infrastructure_cost_monthly": "Infrastructure",
    "support_cost": "Support",
    "development_cost_annual": "Development",
    "implementation_cost": "Implementation",
    CATEGORY_TOTAL: "Total cost of ownership",
})
# What is split across the technologies an application runs on.
ALLOCATED_CATEGORIES = frozenset({"infrastructure", "hosting", "infrastructure_cost", "infrastructure_cost_monthly"})
PERIODS = (("annual", "A year"), ("monthly", "A month"))
KIND_WORDS = {"actual": "Actual", "budget": "Budget", "forecast": "Forecast"}

# The neighbours an application "runs on": the element page's own group, so the two cannot disagree.
RUNS_ON_TYPES = ("Node", "Device", "SystemSoftware", "TechnologyService")
SHARE_PROPERTY = "cost_share_percent"


class CostLineError(ValueError):
    """A cost line a person typed that cannot be recorded; the message says why, in plain words."""


@dataclass
class CostLine:
    label: str
    category: str
    amount: Decimal  # a year's worth, in ``currency``
    currency: str
    source: str  # where the figure came from, in words
    as_of: Optional[date]
    kind: str = KIND_ACTUAL
    counted: bool = True  # whether it is part of the annual total
    note: str = ""
    fact_id: Optional[int] = None  # set when a person may edit or delete it here
    period: str = PERIOD_ANNUAL
    entered_amount: Optional[Decimal] = None  # what was typed, before a month became a year
    application_id: Optional[int] = None  # the application a share comes from; its Cost tab holds the figure
    source_name: str = ""


@dataclass
class CostView:
    lines: List[CostLine]
    currency: str  # the reporting currency of the total
    total: Optional[Decimal]  # None when a rate is missing or nothing is counted
    missing: List[tuple] = field(default_factory=list)  # (currency, date) with no rate on file
    as_of: Optional[date] = None

    @property
    def has_lines(self) -> bool:
        return bool(self.lines)

    @property
    def counted_lines(self) -> List[CostLine]:
        return [line for line in self.lines if line.counted]

    def by_category(self) -> List[tuple]:
        """(label, amount in the reporting currency) per category, largest first; empty when a rate is missing."""
        if self.total is None:
            return []
        sums: Dict[str, Decimal] = {}
        for line in self.counted_lines:
            rate = _rate(line.currency, self.currency, line.as_of)
            key = _CATEGORY_WORDS.get(line.category, line.label)
            sums[key] = sums.get(key, Decimal(0)) + line.amount * rate
        return sorted(((k, v.quantize(PENNY)) for k, v in sums.items()), key=lambda kv: kv[1], reverse=True)


# ------------------------------------------------------------------ reading


def reporting_currency(organization_id: int) -> str:
    from app.services.application_cost_accessor import get_reporting_currency

    return get_reporting_currency(organization_id)


def _rate(from_currency: str, to_currency: str, on: Optional[date]) -> Optional[Decimal]:
    if from_currency.upper() == to_currency.upper():
        return Decimal(1)
    from app.services.currency_service import get_exchange_rate

    rate = get_exchange_rate(from_currency, to_currency, on)
    return Decimal(str(rate)) if rate is not None else None


def _day(value) -> Optional[date]:
    if value is None:
        return None
    return value.date() if hasattr(value, "date") else value


def _annual(fact: CostFact) -> Decimal:
    amount = Decimal(fact.amount)
    return amount * 12 if fact.period == "monthly" else amount


def _source_words(fact: CostFact, people: Dict[int, str]) -> str:
    if fact.source == SOURCE_PERSON:
        user_id = (fact.source_id or "").split(":")[0]
        name = people.get(int(user_id)) if user_id.isdigit() else None
        return f"Added by {name}" if name else "Added by a person"
    return _SOURCE_WORDS.get(fact.source, "Recorded")


def _people(facts: Iterable[CostFact]) -> Dict[int, str]:
    ids = {int(f.source_id.split(":")[0]) for f in facts
           if f.source == SOURCE_PERSON and (f.source_id or "").split(":")[0].isdigit()}
    if not ids:
        return {}
    from app.models.user import User

    rows = db.session.execute(db.select(User).where(User.id.in_(ids))).scalars()
    return {u.id: (getattr(u, "full_name", None) or getattr(u, "first_name", None) or u.email) for u in rows}


def _line_from_fact(fact: CostFact, people, *, editable: bool) -> CostLine:
    kind = fact.kind or KIND_ACTUAL
    label = _CATEGORY_WORDS.get(fact.category, fact.category.replace("_", " ").capitalize())
    note = ""
    counted = kind == KIND_ACTUAL and fact.period in (PERIOD_ANNUAL, "monthly")
    if fact.period == "monthly":
        note = f"{Decimal(fact.amount).quantize(PENNY):,} {fact.currency} a month, twelve times a year"
    elif fact.period not in (PERIOD_ANNUAL, "monthly"):
        note = "One-off, not part of the annual total"
    if kind != KIND_ACTUAL:
        note = f"{KIND_WORDS.get(kind, kind)}, not part of the actual total"
    return CostLine(
        label=label, category=fact.category, amount=_annual(fact), currency=fact.currency,
        source=_source_words(fact, people), as_of=_day(fact.updated_at or fact.created_at),
        kind=kind, counted=counted, note=note, fact_id=fact.id if editable else None,
        period=fact.period, entered_amount=Decimal(fact.amount),
    )


def _application_lines(facts: List[CostFact], people) -> List[CostLine]:
    """The lines of one application from its facts: the breakdown, any remainder of its total, a person's lines."""
    column = [f for f in facts if f.source != SOURCE_PERSON]
    breakdown = [f for f in column if f.category != CATEGORY_TOTAL]
    totals = [f for f in column if f.category == CATEGORY_TOTAL]
    lines = [_line_from_fact(f, people, editable=False) for f in breakdown]
    for total in totals:
        line = _line_from_fact(total, people, editable=False)
        same = [ln for ln in lines if ln.counted and ln.currency == total.currency]
        if not same:
            lines.append(line)
            continue
        rest = Decimal(total.amount) - sum(ln.amount for ln in same)
        if rest > 0:
            line.label = "Not broken down"
            line.amount = rest
            line.note = "The recorded total of ownership less the lines above"
            lines.append(line)
    lines.extend(_line_from_fact(f, people, editable=True) for f in facts if f.source == SOURCE_PERSON)
    return lines


def _view(lines: List[CostLine], currency: str) -> CostView:
    total = Decimal(0)
    missing: List[tuple] = []
    for line in lines:
        if not line.counted:
            continue
        rate = _rate(line.currency, currency, line.as_of)
        if rate is None:
            pair = (line.currency, line.as_of)
            if pair not in missing:
                missing.append(pair)
            continue
        total += line.amount * rate
    counted = [ln for ln in lines if ln.counted]
    as_of = max((ln.as_of for ln in lines if ln.as_of), default=None)
    amount = total.quantize(PENNY, ROUND_HALF_UP) if counted and not missing else None
    return CostView(lines=lines, currency=currency, total=amount, missing=missing, as_of=as_of)


def application_cost(organization_id: int, application_id: int) -> CostView:
    """An application's cost lines and annual total, in the organisation's reporting currency."""
    facts = list_facts(organization_id, element_type=ELEMENT_APPLICATION, element_ids=[application_id])
    return _view(_application_lines(facts, _people(facts)), reporting_currency(organization_id))


def _runs_on(organization_id: int) -> Dict[int, List[tuple]]:
    """{application element id: [(technology element id, recorded percent or None)]}, from the model."""
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.archimate_element_types import base_type

    rels = db.session.execute(
        db.select(ArchiMateRelationship).where(ArchiMateRelationship.organization_id == organization_id)
    ).scalars().all()
    ends = {rid for r in rels for rid in (r.source_id, r.target_id)}
    elements = {
        e.id: e for e in db.session.execute(
            db.select(ArchiMateElement).where(
                ArchiMateElement.organization_id == organization_id, ArchiMateElement.id.in_(ends or {0}))
        ).scalars()
    }
    runs_on: Dict[int, List[tuple]] = {}
    for rel in rels:
        a, b = elements.get(rel.source_id), elements.get(rel.target_id)
        if a is None or b is None:
            continue
        for app_el, tech_el in ((a, b), (b, a)):
            if base_type(app_el.type) == "ApplicationComponent" and base_type(tech_el.type) in RUNS_ON_TYPES:
                links = runs_on.setdefault(app_el.id, [])
                if tech_el.id not in {t for t, _ in links}:
                    links.append((tech_el.id, _percent(rel)))
    return runs_on


def _percent(rel) -> Optional[Decimal]:
    value = (rel.custom_properties or {}).get(SHARE_PROPERTY)
    if isinstance(value, dict):
        value = value.get("value")
    try:
        pct = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return pct if 0 <= pct <= 100 else None


def split(amount: Decimal, links: List[tuple]) -> List[tuple]:
    """[(technology id, share amount, percent)]: recorded percentages first, the rest split evenly; to the penny.

    The shares add up exactly to the part of ``amount`` they cover (all of it unless every link records a
    percentage and they total under 100): the last share takes the rounding remainder. Recorded percentages above
    100 in total are scaled down to 100.
    """
    if not links:
        return []
    recorded = sum(pct for _, pct in links if pct is not None)
    unrecorded = [tid for tid, pct in links if pct is None]
    scale = Decimal(100) / recorded if recorded > 100 else Decimal(1)
    even = (Decimal(100) - recorded * scale) / len(unrecorded) if unrecorded else Decimal(0)
    percents = [(tid, pct * scale if pct is not None else even) for tid, pct in links]
    covered = (amount * sum(p for _, p in percents) / 100).quantize(PENNY, ROUND_HALF_UP)
    shares, given = [], Decimal(0)
    for i, (tid, pct) in enumerate(percents):
        if i == len(percents) - 1:
            share = covered - given
        else:
            share = (amount * pct / 100).quantize(PENNY, ROUND_HALF_UP)
        shares.append((tid, share, pct))
        given += share
    return shares


def _share_words(pct: Decimal, recorded: Optional[Decimal], count: int, app_name: str) -> str:
    if recorded is not None:
        return f"{pct.normalize():f}% of {app_name}'s infrastructure, as recorded on the link"
    if count == 1:
        return f"All of {app_name}'s infrastructure: it runs on this technology alone"
    return f"{app_name}'s infrastructure split evenly across the {count} technologies it runs on"


def technology_cost(organization_id: int, element_id: int) -> CostView:
    """A technology's recorded lines plus its share of the infrastructure of each application that runs on it."""
    from app.models.application_portfolio import ApplicationComponent

    recorded = list_facts(organization_id, element_type=ELEMENT_TECHNOLOGY, element_ids=[element_id])
    people = _people(recorded)
    lines = [_line_from_fact(f, people, editable=f.source == SOURCE_PERSON) for f in recorded]

    runs_on = _runs_on(organization_id)
    app_elements = [aid for aid, links in runs_on.items() if element_id in {t for t, _ in links}]
    apps = db.session.execute(
        db.select(ApplicationComponent).where(
            ApplicationComponent.organization_id == organization_id,
            ApplicationComponent.archimate_element_id.in_(app_elements or {0}),
            ApplicationComponent.deleted_at.is_(None),
        ).order_by(ApplicationComponent.name)
    ).scalars().all()
    facts = list_facts(organization_id, element_type=ELEMENT_APPLICATION, element_ids=[a.id for a in apps] or [0])
    app_people = _people(facts)
    for app in apps:
        links = runs_on[app.archimate_element_id]
        own = [f for f in facts if f.element_id == app.id]
        for line in _application_lines(own, app_people):
            if not line.counted or line.category not in ALLOCATED_CATEGORIES:
                continue
            for tid, share, pct in split(line.amount, links):
                if tid != element_id:
                    continue
                recorded_pct = dict(links)[tid]
                lines.append(CostLine(
                    label=f"{line.label}: share of {app.name}", category=line.category, amount=share,
                    currency=line.currency, source=_share_words(pct, recorded_pct, len(links), app.name),
                    as_of=line.as_of, note="Part of the application's own cost; counted once, there",
                    application_id=app.id, source_name=app.name,
                ))
    return _view(lines, reporting_currency(organization_id))


@dataclass
class OrganisationCost:
    currency: str
    total: Optional[Decimal]
    missing: List[tuple]
    as_of: Optional[date]
    applications: List[dict]  # {id, name, total, currency}, largest first; total None when a rate is missing
    categories: List[tuple]  # (label, amount) across the organisation, largest first


def organisation_cost(organization_id: int) -> OrganisationCost:
    """Every application's total and the organisation's, from the same lines the application pages show.

    Technology lines a person recorded are counted too; a technology's share of an application is not, because it
    is already part of that application.
    """
    from app.models.application_portfolio import ApplicationComponent

    currency = reporting_currency(organization_id)
    facts = list_facts(organization_id, element_type=ELEMENT_APPLICATION)
    tech_facts = list_facts(organization_id, element_type=ELEMENT_TECHNOLOGY)
    people = _people(facts + tech_facts)
    names = {
        a.id: a.name for a in db.session.execute(
            db.select(ApplicationComponent).where(
                ApplicationComponent.organization_id == organization_id,
                ApplicationComponent.id.in_({f.element_id for f in facts} or {0}),
                ApplicationComponent.deleted_at.is_(None),
            )
        ).scalars()
    }
    by_app: Dict[int, List[CostFact]] = {}
    for f in facts:
        if f.element_id in names:
            by_app.setdefault(f.element_id, []).append(f)
    applications, all_lines = [], []
    for app_id, own in by_app.items():
        view = _view(_application_lines(own, people), currency)
        if not view.counted_lines:
            continue
        all_lines.extend(view.lines)
        applications.append({"id": app_id, "name": names[app_id], "total": view.total, "currency": currency})
    all_lines.extend(_line_from_fact(f, people, editable=False) for f in tech_facts if f.source != SOURCE_ALLOCATION)
    view = _view(all_lines, currency)
    applications.sort(key=lambda a: (a["total"] is None, -(a["total"] or 0), a["name"]))
    return OrganisationCost(currency=currency, total=view.total, missing=view.missing, as_of=view.as_of,
                            applications=applications, categories=view.by_category())


# ------------------------------------------------------------------ writing a person's lines

_CURRENCY = re.compile(r"^[A-Z]{3}$")


def _element_in_org(organization_id: int, element_type: str, element_id: int):
    if element_type == ELEMENT_APPLICATION:
        from app.models.application_portfolio import ApplicationComponent

        row = db.session.get(ApplicationComponent, element_id)
        ok = row is not None and row.organization_id == organization_id and row.deleted_at is None
    elif element_type == ELEMENT_TECHNOLOGY:
        from app.models.archimate_core import ArchiMateElement

        row = db.session.get(ArchiMateElement, element_id)
        ok = row is not None and row.organization_id == organization_id and (row.layer or "").lower() == "technology"
    else:
        ok = False
    if not ok:
        raise LookupError("no such element in this organisation")


def _clean(category, amount, currency, period, kind) -> tuple:
    if category not in dict(PERSON_CATEGORIES):
        raise CostLineError("Choose what the cost is for.")
    try:
        value = Decimal(str(amount).replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        raise CostLineError("Enter the amount as a number, for example 1200 or 1200.50.") from None
    if not value.is_finite() or value <= 0:
        raise CostLineError("Enter an amount above zero.")
    if value >= Decimal("100000000000000"):
        raise CostLineError("That amount is too large to record.")
    currency = (currency or "").strip().upper()
    if not _CURRENCY.match(currency):
        raise CostLineError("Enter the currency as a three-letter code, for example GBP or USD.")
    if period not in dict(PERIODS):
        raise CostLineError("Choose whether the amount is for a year or a month.")
    if kind not in KINDS:
        raise CostLineError("Choose whether it is an actual cost, a budget or a forecast.")
    return category, value.quantize(PENNY, ROUND_HALF_UP), currency, period, kind


def add_line(organization_id: int, element_type: str, element_id: int, *, user_id: int,
             category, amount, currency, period=PERIOD_ANNUAL, kind=KIND_ACTUAL) -> CostFact:
    """Record a cost line a person typed on an application or a technology."""
    _element_in_org(organization_id, element_type, element_id)
    category, value, currency, period, kind = _clean(category, amount, currency, period, kind)
    start, end = year_period()
    fact, _ = upsert_fact(
        organization_id, element_type, element_id, value, currency, SOURCE_PERSON,
        category=category, kind=kind, period=period, period_start=start, period_end=end,
        source_table="users", source_id=f"{user_id}:{secrets.token_hex(6)}",
    )
    return fact


def _persons_fact(organization_id: int, element_type: str, element_id: int, fact_id: int) -> CostFact:
    _element_in_org(organization_id, element_type, element_id)
    fact = db.session.execute(
        db.select(CostFact).where(
            CostFact.id == fact_id, CostFact.organization_id == organization_id,
            CostFact.element_type == element_type, CostFact.element_id == element_id,
            CostFact.source == SOURCE_PERSON,
        )
    ).scalar_one_or_none()
    if fact is None:
        raise LookupError("no such cost line on this element")
    return fact


def update_line(organization_id: int, element_type: str, element_id: int, fact_id: int, *,
                category, amount, currency, period=PERIOD_ANNUAL, kind=KIND_ACTUAL) -> CostFact:
    fact = _persons_fact(organization_id, element_type, element_id, fact_id)
    fact.category, fact.amount, fact.currency, fact.period, fact.kind = _clean(category, amount, currency, period, kind)
    return fact


def delete_line(organization_id: int, element_type: str, element_id: int, fact_id: int) -> None:
    db.session.delete(_persons_fact(organization_id, element_type, element_id, fact_id))

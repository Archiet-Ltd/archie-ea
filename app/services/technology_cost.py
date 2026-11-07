"""What a technology costs and which applications carry it.

A cost recorded against a node or system software is a ``CostFact`` of element
type ``technology`` (the one cost fact store). Its share for each application is
computed here on every read, never stored: the technology's total in the
reporting currency is split evenly across the applications it serves, to the
penny, so the shares always add up to the total.

The applications a technology serves are the ones it realises: the same
``realization`` relationships the application's Technology tab writes.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Dict, List, Optional

from app import db
from app.models import ArchiMateElement, ArchiMateRelationship
from app.models.application_portfolio import ApplicationComponent
from app.models.cost_fact import ELEMENT_TECHNOLOGY, KIND_ACTUAL, PERIOD_ANNUAL
from app.modules.architecture.services.application_technology_links import (
    REALIZATION,
    is_technology_element,
)
from app.services import cost_fact_store

SOURCE = "technology_cost_form"

CATEGORIES = (
    ("licence", "Licence"),
    ("hosting", "Hosting"),
    ("support", "Support"),
    ("other", "Other"),
)

RULE = (
    "Each technology's total is split evenly across the applications it serves. "
    "A share is worked out when you open the page, so it moves when a cost, a rate "
    "or a link changes."
)

CENT = Decimal("0.01")


class TechnologyCostError(ValueError):
    """A refused write; the message is shown to the person."""


def _technology(organization_id: int, element_id: int) -> ArchiMateElement:
    element = db.session.execute(
        db.select(ArchiMateElement).where(
            ArchiMateElement.id == element_id, ArchiMateElement.organization_id == organization_id
        )
    ).scalar_one_or_none()
    if element is None or not is_technology_element(element):
        raise TechnologyCostError("That node or system software was not found.")
    return element


def currency_codes() -> List[str]:
    from config import CurrencyConfig

    return sorted(CurrencyConfig.SUPPORTED_CURRENCIES)


def record_cost(organization_id: int, element_id: int, amount, currency: str, category: str,
                period: str = PERIOD_ANNUAL):
    """Record (or change) the annual actual cost of one category for a technology.

    Flushes, never commits: the caller owns the transaction.
    """
    _technology(organization_id, element_id)
    try:
        value = Decimal(str(amount).strip().replace(",", ""))
    except Exception:
        raise TechnologyCostError("Enter the amount as a number.")
    if not value.is_finite() or value <= 0:
        raise TechnologyCostError("Enter an amount greater than zero.")
    value = value.quantize(CENT, rounding=ROUND_HALF_EVEN)
    currency = (currency or "").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise TechnologyCostError("Enter the currency as a three-letter code, such as GBP.")
    if period != PERIOD_ANNUAL:
        raise TechnologyCostError("Costs are recorded for the year.")
    if category not in dict(CATEGORIES):
        raise TechnologyCostError("Choose a cost category.")
    fact, outcome = cost_fact_store.upsert_fact(
        organization_id, ELEMENT_TECHNOLOGY, element_id, value, currency, SOURCE,
        category=category, kind=KIND_ACTUAL, period=PERIOD_ANNUAL,
        period_start=cost_fact_store.year_period()[0], period_end=cost_fact_store.year_period()[1],
    )
    db.session.flush()
    return fact, outcome


def _facts(organization_id: int, element_ids):
    return cost_fact_store.list_facts(
        organization_id, element_type=ELEMENT_TECHNOLOGY, element_ids=element_ids,
        kind=KIND_ACTUAL, period=PERIOD_ANNUAL,
    )


def served_applications(organization_id: int, element_id: int) -> List[ApplicationComponent]:
    """The applications this technology realises, by name."""
    rows = db.session.execute(
        db.select(ApplicationComponent)
        .join(ArchiMateRelationship, ArchiMateRelationship.target_id == ApplicationComponent.archimate_element_id)
        .where(
            ArchiMateRelationship.source_id == element_id,
            db.func.lower(ArchiMateRelationship.type) == REALIZATION,
            ApplicationComponent.organization_id == organization_id,
            ArchiMateRelationship.organization_id == organization_id,
        )
        .order_by(ApplicationComponent.name, ApplicationComponent.id)
    ).scalars().all()
    seen, apps = set(), []
    for row in rows:
        if row.id not in seen:
            seen.add(row.id)
            apps.append(row)
    return apps


def split_evenly(total: Decimal, count: int) -> List[Decimal]:
    """``total`` in whole pennies shared over ``count`` parts; the parts add up to the total."""
    cents = int((total.quantize(CENT, rounding=ROUND_HALF_EVEN) / CENT).to_integral_value())
    base, extra = divmod(cents, count)
    return [Decimal(base + (1 if i < extra else 0)) * CENT for i in range(count)]


def allocation(organization_id: int, element_id: int, reporting_currency: Optional[str] = None) -> Dict:
    """The technology's costs, its total and each served application's share.

    ``total`` and every share are None when a rate is missing (``missing`` says
    which); a technology with no cost has ``total`` None and no facts.
    """
    from app.services.currency_service import convert_total
    from config import CurrencyConfig

    reporting = reporting_currency or CurrencyConfig.get_org_currency_code(organization_id=organization_id)
    facts = _facts(organization_id, [element_id])
    converted = convert_total(facts, reporting)
    apps = served_applications(organization_id, element_id)
    total = None if converted.amount is None else converted.amount.quantize(CENT, rounding=ROUND_HALF_EVEN)
    shares = [None] * len(apps)
    if total is not None and apps:
        shares = split_evenly(total, len(apps))
    return {
        "element_id": element_id,
        "currency": reporting,
        "facts": facts,
        "total": total,
        "missing": list(converted.missing),
        "applications": [{"id": a.id, "name": a.name, "share": s} for a, s in zip(apps, shares)],
    }


def shares_for_application(organization_id: int, application_id: int,
                           reporting_currency: Optional[str] = None) -> List[Dict]:
    """Each technology that serves the application, with this application's share of its cost."""
    app_row = db.session.execute(
        db.select(ApplicationComponent).where(
            ApplicationComponent.id == application_id, ApplicationComponent.organization_id == organization_id)
    ).scalar_one_or_none()
    if app_row is None or not app_row.archimate_element_id:
        return []
    techs = db.session.execute(
        db.select(ArchiMateElement)
        .join(ArchiMateRelationship, ArchiMateRelationship.source_id == ArchiMateElement.id)
        .where(
            ArchiMateRelationship.target_id == app_row.archimate_element_id,
            db.func.lower(ArchiMateRelationship.type) == REALIZATION,
            ArchiMateElement.organization_id == organization_id,
            ArchiMateRelationship.organization_id == organization_id,
        )
        .order_by(ArchiMateElement.name, ArchiMateElement.id)
    ).scalars().all()
    out, seen = [], set()
    for tech in techs:
        if tech.id in seen or not is_technology_element(tech):
            continue
        seen.add(tech.id)
        alloc = allocation(organization_id, tech.id, reporting_currency)
        mine = next((a for a in alloc["applications"] if a["id"] == application_id), None)
        out.append({
            "element_id": tech.id, "name": tech.name, "type": tech.type, "currency": alloc["currency"],
            "total": alloc["total"], "share": mine["share"] if mine else None,
            "has_cost": bool(alloc["facts"]), "missing": alloc["missing"],
            "served": len(alloc["applications"]),
        })
    return out


def canvas_figures(organization_id: int, reporting_currency: Optional[str] = None) -> Dict:
    """The technology half of the canvas cost structure: each application's technology share and the total
    the canvas states, which is every application's own cost plus every technology cost an application carries.
    """
    from app.services.currency_service import convert_total
    from config import CurrencyConfig

    reporting = reporting_currency or CurrencyConfig.get_org_currency_code(organization_id=organization_id)
    tech_ids = sorted({f.element_id for f in _facts(organization_id, None)})
    per_app: Dict[int, Dict] = {}
    carried = Decimal(0)
    unavailable: List = []
    for tech_id in tech_ids:
        alloc = allocation(organization_id, tech_id, reporting)
        if alloc["total"] is None:
            unavailable.extend(alloc["missing"])
            continue
        for a in alloc["applications"]:
            carried += a["share"]
            entry = per_app.setdefault(a["id"], {"id": a["id"], "name": a["name"], "share": Decimal(0)})
            entry["share"] += a["share"]
    direct = convert_total(
        cost_fact_store.list_facts(organization_id, element_type="application", kind=KIND_ACTUAL, period=PERIOD_ANNUAL),
        reporting)
    direct_amount = Decimal(0)
    if direct.amount is not None:
        direct_amount = direct.amount
    elif direct.missing:
        unavailable.extend(direct.missing)
    return {
        "currency": reporting,
        "shares": sorted(per_app.values(), key=lambda r: (-r["share"], r["name"])),
        "total": None if unavailable else (direct_amount + carried).quantize(CENT, rounding=ROUND_HALF_EVEN),
        "missing": unavailable,
    }

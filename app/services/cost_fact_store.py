"""The one cost fact store: write, clear and read typed cost facts.

Every function takes the organisation explicitly and puts it in every
predicate, so a caller outside a request (a CLI command, a test) is scoped the
same way as a request. Inside a request an organisation other than the
signed-in one is refused.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Iterable, List, Optional

from flask import g, has_request_context

from app import db
from app.models.cost_fact import (
    CATEGORY_TOTAL,
    KIND_ACTUAL,
    PERIOD_ANNUAL,
    CostFact,
)


def _check_org(organization_id: int) -> None:
    if organization_id is None:
        raise ValueError("a cost fact belongs to an organisation")
    if has_request_context():
        current = getattr(g, "current_org_id", None)
        if current is not None and current != organization_id:
            raise PermissionError("cost facts of another organisation are not accessible")


def _identity(organization_id, element_type, element_id, category, kind, source, source_id):
    return (
        CostFact.organization_id == organization_id,
        CostFact.element_type == element_type,
        CostFact.element_id == element_id,
        CostFact.category == category,
        CostFact.kind == kind,
        CostFact.source == source,
        CostFact.source_id == (source_id or ""),
    )


def year_period(year: Optional[int] = None) -> tuple:
    year = year or date.today().year
    return date(year, 1, 1), date(year, 12, 31)


def upsert_fact(
    organization_id: int,
    element_type: str,
    element_id: int,
    amount,
    currency: str,
    source: str,
    *,
    category: str = CATEGORY_TOTAL,
    kind: str = KIND_ACTUAL,
    period: str = PERIOD_ANNUAL,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    source_table: Optional[str] = None,
    source_id: str = "",
) -> tuple:
    """Insert the fact, or update it when its amount or currency changed.

    Returns ``(fact, outcome)`` with outcome ``created``, ``updated`` or
    ``unchanged``. A second identical call changes nothing, including the
    period: an unchanged value keeps the period it was recorded for.
    """
    _check_org(organization_id)
    amount = Decimal(str(amount))
    currency = currency.upper()
    source_id = source_id or ""
    fact = db.session.execute(
        db.select(CostFact).where(
            *_identity(organization_id, element_type, element_id, category, kind, source, source_id))
    ).scalar_one_or_none()
    if fact is None:
        fact = CostFact(
            organization_id=organization_id, element_type=element_type, element_id=element_id,
            category=category, kind=kind, amount=amount, currency=currency, period=period,
            period_start=period_start, period_end=period_end, source=source,
            source_table=source_table, source_id=source_id,
        )
        db.session.add(fact)
        return fact, "created"
    if Decimal(fact.amount) == amount and fact.currency == currency:
        return fact, "unchanged"
    fact.amount = amount
    fact.currency = currency
    fact.period = period
    fact.period_start = period_start
    fact.period_end = period_end
    return fact, "updated"


def clear_facts(
    organization_id: int,
    element_type: str,
    element_id: int,
    source: str,
    *,
    category: str = CATEGORY_TOTAL,
    kind: str = KIND_ACTUAL,
    source_id: str = "",
) -> int:
    """Delete the fact a source wrote, so a cleared value leaves no stale fact."""
    _check_org(organization_id)
    facts = db.session.execute(
        db.select(CostFact).where(
            *_identity(organization_id, element_type, element_id, category, kind, source, source_id))
    ).scalars().all()
    for fact in facts:
        db.session.delete(fact)
    return len(facts)


def list_facts(
    organization_id: int,
    *,
    element_type: Optional[str] = None,
    element_ids: Optional[Iterable[int]] = None,
    category: Optional[str] = None,
    kind: Optional[str] = None,
    period: Optional[str] = None,
) -> List[CostFact]:
    """The organisation's facts, optionally narrowed. Never another organisation's."""
    _check_org(organization_id)
    stmt = db.select(CostFact).where(CostFact.organization_id == organization_id)
    if element_type:
        stmt = stmt.where(CostFact.element_type == element_type)
    if element_ids is not None:
        stmt = stmt.where(CostFact.element_id.in_(list(element_ids)))
    if category:
        stmt = stmt.where(CostFact.category == category)
    if kind:
        stmt = stmt.where(CostFact.kind == kind)
    if period:
        stmt = stmt.where(CostFact.period == period)
    return list(db.session.execute(stmt.order_by(CostFact.id)).scalars())


def total_in_reporting_currency(organization_id: int, reporting_currency: Optional[str] = None, **filters):
    """Total of the matching facts in the reporting currency.

    ``amount`` is None (shown as missing) when the facts span currencies with
    no rate on file for their period; a sum is never returned on a guess.
    """
    from app.services.application_cost_accessor import get_reporting_currency
    from app.services.currency_service import convert_total

    reporting = reporting_currency or get_reporting_currency(organization_id)
    return convert_total(list_facts(organization_id, **filters), reporting)

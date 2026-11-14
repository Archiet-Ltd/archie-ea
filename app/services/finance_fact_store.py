"""The one writer and the one reader of an organisation's money facts.

Cash, spending, revenue, funding and paying customers are stored as dated ``FinanceFact`` rows and
never overwritten. Burn and months of cash are worked out here from the latest facts, each stated with
the date it rests on, and are never stored or worked out from elapsed time. Every function takes the
organisation id and puts it in the WHERE clause.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from flask import current_app

from app import db
from app.models.archimate_core import ArchiMateElement
from app.models.finance_fact import COUNT_MEASURES, KINDS, MEASURES, SOURCE_KINDS, FinanceFact

__all__ = [
    "FinanceFactError", "MEASURE_LABELS", "STALE_AFTER_DAYS", "today", "record_fact", "supersede_fact",
    "latest", "series", "history", "derived_figures", "products", "replaceable_fact",
]

MEASURE_LABELS = {
    "cash_balance": "Cash in bank",
    "spend": "Monthly outgoings",
    "revenue": "Monthly revenue",
    "funding_in": "Funding received",
    "paying_customers": "Paying customers",
}
STALE_AFTER_DAYS = 31


class FinanceFactError(ValueError):
    """A fact that cannot be recorded; the message is a sentence a person can read."""


def today() -> date:
    """Today's date. A test configuration may fix it so a page can be read as of a later day."""
    fixed = current_app.config.get("FINANCE_FACTS_TODAY")
    if fixed:
        return date.fromisoformat(str(fixed))
    return datetime.now(timezone.utc).date()


def products(organization_id: int) -> list:
    """The organisation's Product elements: the only subjects a fact may be about."""
    return (
        ArchiMateElement.query.filter(
            ArchiMateElement.organization_id == organization_id, ArchiMateElement.type == "Product"
        )
        .order_by(ArchiMateElement.name)
        .all()
    )


def _subject_in_org(organization_id: int, subject_element_id):
    if subject_element_id is None:
        return None
    element = ArchiMateElement.query.filter(
        ArchiMateElement.id == subject_element_id, ArchiMateElement.organization_id == organization_id
    ).first()
    if element is None or element.type != "Product":
        raise FinanceFactError("Choose one of your own products, or leave the figure for the whole organisation.")
    return element


def _amount(measure: str, raw):
    try:
        amount = Decimal(str(raw).replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        raise FinanceFactError("Enter the figure as a number.")
    if not amount.is_finite() or amount < 0:
        raise FinanceFactError("Enter the figure as a number that is not negative.")
    if measure in COUNT_MEASURES and amount != amount.to_integral_value():
        raise FinanceFactError("Enter a whole number of customers.")
    return amount


def record_fact(
    organization_id: int,
    measure: str,
    amount,
    as_of: date,
    source_ref: str,
    *,
    currency: str | None = None,
    stated_by_user_id: int | None = None,
    recorded_by_id: int | None = None,
    kind: str = "actual",
    subject_element_id: int | None = None,
    source_kind: str = "person",
    note: str | None = None,
    replaces_id: int | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    assumes_element_id: int | None = None,
) -> FinanceFact:
    """Append one fact, and when ``replaces_id`` is given point that fact of this organisation at it."""
    if measure not in MEASURES:
        raise FinanceFactError("Choose what the figure is.")
    if kind not in KINDS or source_kind not in SOURCE_KINDS:
        raise FinanceFactError("Choose whether the figure is an actual or a forecast.")
    if not isinstance(as_of, date):
        raise FinanceFactError("Enter the date the figure was true.")
    source_ref = " ".join((source_ref or "").split())
    if not source_ref:
        raise FinanceFactError("Say where the figure comes from.")
    value = _amount(measure, amount)
    if measure in COUNT_MEASURES:
        currency = None
    else:
        currency = (currency or "").strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise FinanceFactError("Enter a three-letter currency such as USD.")
    _subject_in_org(organization_id, subject_element_id)
    if assumes_element_id is not None:
        assumed = ArchiMateElement.query.filter(
            ArchiMateElement.id == assumes_element_id, ArchiMateElement.organization_id == organization_id
        ).first()
        if assumed is None:
            raise FinanceFactError("The element this figure depends on is not in your organisation.")
    old = replaceable_fact(organization_id, replaces_id, measure) if replaces_id is not None else None

    fact = FinanceFact(
        organization_id=organization_id, measure=measure, kind=kind, subject_element_id=subject_element_id,
        amount=value, currency=currency, as_of=as_of, period_start=period_start, period_end=period_end,
        source_kind=source_kind, source_ref=source_ref[:512], stated_by_user_id=stated_by_user_id,
        note=(note or "").strip() or None, assumes_element_id=assumes_element_id, recorded_by_id=recorded_by_id,
    )
    db.session.add(fact)
    db.session.flush()
    if old is not None:
        old.superseded_by_id = fact.id
        db.session.flush()
    db.session.commit()
    return fact


def replaceable_fact(organization_id: int, fact_id, measure: str | None = None) -> FinanceFact:
    fact = FinanceFact.query.filter(
        FinanceFact.id == fact_id, FinanceFact.organization_id == organization_id
    ).first()
    if fact is None:
        raise FinanceFactError("That figure is not one of your organisation's.")
    if fact.superseded_by_id is not None:
        raise FinanceFactError("That figure has already been replaced.")
    if measure is not None and fact.measure != measure:
        raise FinanceFactError("A figure can only be replaced by a figure of the same kind.")
    return fact


def supersede_fact(organization_id: int, fact_id: int, replacement_id: int) -> FinanceFact:
    """Point an existing fact of this organisation at the fact that replaces it."""
    old = replaceable_fact(organization_id, fact_id)
    new = FinanceFact.query.filter(
        FinanceFact.id == replacement_id, FinanceFact.organization_id == organization_id
    ).first()
    if new is None or new.measure != old.measure or new.id == old.id:
        raise FinanceFactError("That figure cannot replace this one.")
    old.superseded_by_id = new.id
    db.session.commit()
    return old


def _current(organization_id: int, measure: str, subject_element_id, kind: str):
    query = FinanceFact.query.filter(
        FinanceFact.organization_id == organization_id,
        FinanceFact.measure == measure,
        FinanceFact.kind == kind,
        FinanceFact.superseded_by_id.is_(None),
    )
    if subject_element_id is None:
        return query.filter(FinanceFact.subject_element_id.is_(None))
    return query.filter(FinanceFact.subject_element_id == subject_element_id)


def latest(organization_id: int, measure: str, subject_element_id=None, kind: str = "actual"):
    """The newest fact of a measure that has not been replaced, or None when none is recorded."""
    return (
        _current(organization_id, measure, subject_element_id, kind)
        .order_by(FinanceFact.as_of.desc(), FinanceFact.id.desc())
        .first()
    )


def series(organization_id: int, measure: str, subject_element_id=None, kind: str = "actual", months: int = 12):
    """The facts of a measure that were true in the last ``months`` months, oldest first."""
    since = today() - timedelta(days=31 * max(int(months), 1))
    return (
        _current(organization_id, measure, subject_element_id, kind)
        .filter(FinanceFact.as_of >= since)
        .order_by(FinanceFact.as_of, FinanceFact.id)
        .all()
    )


def history(organization_id: int, page: int = 1, per_page: int = 20):
    """Every fact of this organisation, newest first, replaced ones included: (rows, total)."""
    base = FinanceFact.query.filter(FinanceFact.organization_id == organization_id)
    total = base.count()
    rows = (
        base.order_by(FinanceFact.as_of.desc(), FinanceFact.id.desc())
        .offset((max(page, 1) - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return rows, total


def _figure(fact) -> dict | None:
    if fact is None:
        return None
    return {
        "id": fact.id, "measure": fact.measure, "label": MEASURE_LABELS[fact.measure], "amount": fact.amount,
        "currency": fact.currency, "as_of": fact.as_of, "source_ref": fact.source_ref,
    }


def derived_figures(organization_id: int, on: date | None = None) -> dict:
    """Burn and months of cash from the latest facts, each stated with the dates it rests on.

    ``burn`` is the latest monthly outgoings less the latest monthly revenue; with no revenue recorded it is
    the outgoings, and the figure says so. ``months_of_cash`` is the latest cash divided by the burn.
    Neither is ever worked out from elapsed time. A figure that cannot be worked out is None with the
    reason in ``*_reason``; nothing is filled in with a zero.
    """
    on = on or today()
    cash = _figure(latest(organization_id, "cash_balance"))
    spend = _figure(latest(organization_id, "spend"))
    revenue = _figure(latest(organization_id, "revenue"))
    out = {
        "cash": cash, "spend": spend, "revenue": revenue, "burn": None, "months_of_cash": None,
        "burn_reason": None, "months_of_cash_reason": None, "cash_age_days": None, "cash_stale": False,
    }
    if cash is not None:
        out["cash_age_days"] = (on - cash["as_of"]).days
        out["cash_stale"] = out["cash_age_days"] > STALE_AFTER_DAYS

    if spend is None:
        out["burn_reason"] = "Record your monthly outgoings to see how fast cash is used."
    elif revenue is not None and revenue["currency"] != spend["currency"]:
        out["burn_reason"] = "Monthly outgoings and monthly revenue are in different currencies."
    else:
        taken = revenue["amount"] if revenue is not None else Decimal(0)
        out["burn"] = {
            "amount": spend["amount"] - taken, "currency": spend["currency"], "spend_as_of": spend["as_of"],
            "revenue_as_of": revenue["as_of"] if revenue is not None else None,
            "revenue_recorded": revenue is not None,
        }

    if cash is None:
        out["months_of_cash_reason"] = "Record the cash in your bank to see how many months it lasts."
    elif out["burn"] is None:
        out["months_of_cash_reason"] = out["burn_reason"]
    elif cash["currency"] != out["burn"]["currency"]:
        out["months_of_cash_reason"] = "Cash and monthly outgoings are in different currencies."
    elif out["burn"]["amount"] <= 0:
        out["months_of_cash_reason"] = "Monthly revenue covers monthly outgoings, so cash is not running down."
    else:
        out["months_of_cash"] = {
            "months": (cash["amount"] / out["burn"]["amount"]).quantize(Decimal("0.1")),
            "cash_as_of": cash["as_of"], "spend_as_of": spend["as_of"],
            "revenue_as_of": revenue["as_of"] if revenue is not None else None,
        }
    return out

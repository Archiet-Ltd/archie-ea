"""Putting a risk in money, based on the Open FAIR(TM) method.

A person gives four factors as ranges (smallest, most likely, largest):

* ``frequency``     how often a threat acts, per year
* ``vulnerability`` how likely it is to get through, 0 to 1
* ``direct_loss``   the direct loss of one event
* ``knock_on_loss`` the knock-on loss of one event

Each trial draws one value per factor and the loss of that trial is
``frequency x vulnerability x (direct + knock-on)``. A seeded run of
``TRIALS`` trials gives the most likely yearly loss (the median) and the
5th to 95th percentile range. The same seed is used every time, so the same
inputs give the same figure on every page load.

The result is written as cost facts through the one cost fact store, on the
risk; screens read the facts, and this module's readers are the only way they
get them. ``RiskLossAssessment`` keeps only the entered ranges, the person and
the date.
"""

from __future__ import annotations

import random
from datetime import datetime
from decimal import Decimal
from typing import Dict, Iterable, List, Optional

from app import db
from app.models.cost_fact import ELEMENT_RISK, KIND_FORECAST, PERIOD_ANNUAL
from app.models.risk import Risk
from app.models.risk_entity_link import RiskEntityLink
from app.models.risk_loss_assessment import RiskLossAssessment
from app.services import cost_fact_store

TRIALS = 10_000
SEED = 20261101
SOURCE = "open_fair"
SOURCE_TABLE = "risk_loss_assessments"

CATEGORY_LIKELY = "yearly_loss_likely"
CATEGORY_LOW = "yearly_loss_low"
CATEGORY_HIGH = "yearly_loss_high"
_CATEGORIES = (CATEGORY_LIKELY, CATEGORY_LOW, CATEGORY_HIGH)

# Factor key -> (label a person reads, what to enter)
FACTORS = {
    "frequency": ("How often a threat acts", "times a year"),
    "vulnerability": ("How likely it gets through", "a share from 0 to 1"),
    "direct_loss": ("Direct loss", "per event, in the organisation's currency"),
    "knock_on_loss": ("Knock-on loss", "per event, in the organisation's currency"),
}


class FactorError(ValueError):
    """An entered range that cannot be simulated; the message says which and why."""


def parse_factors(raw: Dict[str, Iterable]) -> Dict[str, List[float]]:
    """Validate ``raw`` ({factor: [min, likely, max]}) and return it as floats."""
    out: Dict[str, List[float]] = {}
    for key, (label, _unit) in FACTORS.items():
        values = raw.get(key)
        try:
            low, likely, high = (float(v) for v in values)
        except (TypeError, ValueError):
            raise FactorError("%s needs a smallest, a most likely and a largest number." % label)
        if min(low, likely, high) < 0:
            raise FactorError("%s cannot be negative." % label)
        if not low <= likely <= high:
            raise FactorError("%s: the most likely value must sit between the smallest and the largest." % label)
        if key == "vulnerability" and high > 1:
            raise FactorError("%s is a share from 0 to 1." % label)
        out[key] = [low, likely, high]
    return out


def simulate(factors: Dict[str, List[float]], trials: int = TRIALS, seed: int = SEED) -> Dict[str, float]:
    """Seeded Monte Carlo run: the median and the 5th and 95th percentile of the yearly loss."""
    rng = random.Random(seed)

    def draw(key):
        low, likely, high = factors[key]
        return rng.triangular(low, high, likely)

    losses = sorted(
        draw("frequency") * draw("vulnerability") * (draw("direct_loss") + draw("knock_on_loss"))
        for _ in range(trials)
    )

    def at(share):
        return losses[min(trials - 1, int(share * trials))]

    return {"low": at(0.05), "likely": at(0.50), "high": at(0.95)}


def _whole(value: float) -> Decimal:
    return Decimal(int(round(value)))


def save_assessment(organization_id: int, risk_id: int, raw_factors, user_id: Optional[int]) -> RiskLossAssessment:
    """Run the simulation for the organisation's risk and write the three cost facts.

    Raises ``FactorError`` for a bad range and ``LookupError`` when the risk is not
    this organisation's.
    """
    from config import CurrencyConfig

    risk = db.session.execute(
        db.select(Risk).where(Risk.id == risk_id, Risk.organization_id == organization_id)
    ).scalar_one_or_none()
    if risk is None:
        raise LookupError("risk not found")
    factors = parse_factors(raw_factors)
    result = simulate(factors)
    currency = CurrencyConfig.get_org_currency_code(organization_id=organization_id)

    row = db.session.execute(
        db.select(RiskLossAssessment).where(
            RiskLossAssessment.organization_id == organization_id,
            RiskLossAssessment.risk_id == risk_id,
        )
    ).scalar_one_or_none()
    if row is None:
        row = RiskLossAssessment(organization_id=organization_id, risk_id=risk_id)
        db.session.add(row)
    row.factors = factors
    row.trials = TRIALS
    row.seed = SEED
    row.currency = currency
    row.entered_by_id = user_id
    row.entered_at = datetime.utcnow()

    for category, key in ((CATEGORY_LIKELY, "likely"), (CATEGORY_LOW, "low"), (CATEGORY_HIGH, "high")):
        cost_fact_store.upsert_fact(
            organization_id, ELEMENT_RISK, risk_id, _whole(result[key]), currency, SOURCE,
            category=category, kind=KIND_FORECAST, period=PERIOD_ANNUAL,
            source_table=SOURCE_TABLE, source_id=str(risk_id),
        )
    db.session.commit()
    return row


def losses_for_risks(organization_id: int, risk_ids: Iterable[int]) -> Dict[int, Dict]:
    """The recorded yearly loss of each risk that has one, read from the cost facts.

    ``{risk_id: {likely, low, high, currency, entered_at, entered_by}}``; a risk
    nobody has put in money is absent, never zero.
    """
    risk_ids = list(risk_ids)
    if not risk_ids:
        return {}
    facts = cost_fact_store.list_facts(
        organization_id, element_type=ELEMENT_RISK, element_ids=risk_ids, kind=KIND_FORECAST,
    )
    by_risk: Dict[int, Dict] = {}
    for fact in facts:
        if fact.source != SOURCE or fact.category not in _CATEGORIES:
            continue
        entry = by_risk.setdefault(fact.element_id, {})
        entry[fact.category] = fact
    rows = db.session.execute(
        db.select(RiskLossAssessment).where(
            RiskLossAssessment.organization_id == organization_id,
            RiskLossAssessment.risk_id.in_(risk_ids),
        )
    ).scalars().all()
    assessments = {row.risk_id: row for row in rows}

    from app.models.user import User

    out: Dict[int, Dict] = {}
    for risk_id, entry in by_risk.items():
        likely, low, high = (entry.get(c) for c in (CATEGORY_LIKELY, CATEGORY_LOW, CATEGORY_HIGH))
        assessment = assessments.get(risk_id)
        if likely is None or low is None or high is None or assessment is None:
            continue
        person = None
        if assessment.entered_by_id:
            user = db.session.execute(
                db.select(User).where(User.id == assessment.entered_by_id,
                                      User.organization_id == organization_id)
            ).scalar_one_or_none()
            if user is not None:
                person = " ".join(p for p in (user.first_name, user.last_name) if p) or user.email
        out[risk_id] = {
            "likely": Decimal(likely.amount), "low": Decimal(low.amount), "high": Decimal(high.amount),
            "currency": likely.currency, "entered_at": assessment.entered_at, "entered_by": person,
            "factors": assessment.factors,
        }
    return out


def money(amount, currency: str) -> str:
    """A whole-unit amount with the currency's own symbol, e.g. ``£120,000``."""
    from config import CurrencyConfig

    cfg = CurrencyConfig.get_currency_config(currency)
    text = "{:,.0f}".format(Decimal(amount))
    return cfg["symbol"] + text if cfg.get("position") == "prefix" else text + cfg["symbol"]


def describe(loss: Dict) -> Dict[str, str]:
    """The words a screen shows for one risk's loss, so every screen says it the same way."""
    entered = loss["entered_at"]
    return {
        "likely": money(loss["likely"], loss["currency"]),
        "range": "%s to %s" % (money(loss["low"], loss["currency"]), money(loss["high"], loss["currency"])),
        "date": entered.strftime("%-d %B %Y") if entered else "",
        "by": loss.get("entered_by") or "",
        "method": "Based on the Open FAIR™ method, %s trials, 5th to 95th percentile" % format(TRIALS, ","),
    }


def losses_for_application(organization_id: int, application_id: int) -> List[Dict]:
    """Each risk linked to the application that has a yearly loss, with that risk's own figure."""
    links = db.session.execute(
        db.select(RiskEntityLink).where(
            RiskEntityLink.organization_id == organization_id,
            RiskEntityLink.entity_type == "application",
            RiskEntityLink.entity_id == application_id,
        )
    ).scalars().all()
    risk_ids = [link.risk_id for link in links]
    losses = losses_for_risks(organization_id, risk_ids)
    risks = {
        r.id: r for r in db.session.execute(
            db.select(Risk).where(Risk.organization_id == organization_id, Risk.id.in_(risk_ids or [-1]))
        ).scalars()
    }
    return [
        {"risk": risks[rid], "loss": loss, "words": describe(loss)}
        for rid, loss in sorted(losses.items()) if rid in risks
    ]

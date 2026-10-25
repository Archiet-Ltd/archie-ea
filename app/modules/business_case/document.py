"""The business case as a document: what each section shows, and where it comes from.

Costs and benefits are cost facts about each option (the one cost fact store);
risks are the risk register's rows linked to the case's work package. Nothing
here is typed twice: a total on the page is a sum of stored facts, and payback
is worked out from them on every read.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional

from app import db
from app.models.business_case import BusinessCase, BusinessCaseOption
from app.models.cost_fact import ELEMENT_BUSINESS_CASE_OPTION, KIND_FORECAST
from app.models.risk import Risk
from app.models.risk_entity_link import RiskEntityLink
from app.services import cost_fact_store
from app.services.application_cost_accessor import get_reporting_currency
from app.services.currency_service import convert_total

FACT_SOURCE = "business_case"
ONE_OFF = "one_off"
RUN_ANNUAL = "run_annual"
BENEFIT_ANNUAL = "benefit_annual"
FIGURES = (
    (ONE_OFF, "One-off cost"),
    (RUN_ANNUAL, "Annual running cost"),
    (BENEFIT_ANNUAL, "Annual benefit"),
)
MAX_OPTIONS = 3


@dataclass
class OptionView:
    option: BusinessCaseOption
    one_off: Optional[Decimal] = None
    run_annual: Optional[Decimal] = None
    benefit_annual: Optional[Decimal] = None
    three_year_cost: Optional[Decimal] = None
    payback_months: Optional[int] = None
    payback_note: str = ""
    currency: str = ""

    @property
    def recommended(self) -> bool:
        return bool(self.option.is_recommended)


@dataclass
class Document:
    case: BusinessCase
    currency: str
    options: List[OptionView] = field(default_factory=list)
    risks: list = field(default_factory=list)

    @property
    def recommended(self) -> Optional[OptionView]:
        return next((o for o in self.options if o.recommended), None)


def payback(one_off, run_annual, benefit_annual):
    """(months, note). Months are None, with the reason in words, when they cannot be worked out."""
    if one_off is None or benefit_annual is None:
        return None, "Add the one-off cost and the annual benefit to see payback."
    net = Decimal(benefit_annual) - Decimal(run_annual or 0)
    if Decimal(one_off) <= 0:
        return 0, ""
    if net <= 0:
        return None, "The annual benefit does not cover the running cost, so the investment is not paid back."
    return int(math.ceil(Decimal(one_off) / net * 12)), ""


def three_year_cost(one_off, run_annual):
    if one_off is None and run_annual is None:
        return None
    return Decimal(one_off or 0) + 3 * Decimal(run_annual or 0)


def set_option_figures(organization_id: int, option: BusinessCaseOption, **amounts) -> None:
    """Write (or clear, when blank) the option's cost facts. Amounts are in the reporting currency."""
    currency = get_reporting_currency(organization_id)
    for category, _label in FIGURES:
        value = amounts.get(category)
        if value is None or value == "":
            cost_fact_store.clear_facts(organization_id, ELEMENT_BUSINESS_CASE_OPTION, option.id,
                                        FACT_SOURCE, category=category, kind=KIND_FORECAST)
        else:
            cost_fact_store.upsert_fact(organization_id, ELEMENT_BUSINESS_CASE_OPTION, option.id,
                                        value, currency, FACT_SOURCE, category=category,
                                        kind=KIND_FORECAST)


def option_figures(organization_id: int, option_ids, currency: str) -> Dict[int, Dict[str, Optional[Decimal]]]:
    """Per option, each figure in the reporting currency; None where no fact is on file."""
    facts = cost_fact_store.list_facts(organization_id, element_type=ELEMENT_BUSINESS_CASE_OPTION,
                                       element_ids=option_ids, kind=KIND_FORECAST)
    out: Dict[int, Dict[str, Optional[Decimal]]] = {i: {c: None for c, _ in FIGURES} for i in option_ids}
    for option_id in option_ids:
        for category, _label in FIGURES:
            rows = [f for f in facts if f.element_id == option_id and f.category == category]
            if rows:
                out[option_id][category] = convert_total(rows, currency).amount
    return out


def linked_risks(organization_id: int, work_package_id) -> list:
    """The register's risks linked to the work package, highest score first."""
    if not work_package_id:
        return []
    rows = (
        db.session.query(Risk)
        .join(RiskEntityLink, RiskEntityLink.risk_id == Risk.id)
        .filter(Risk.organization_id == organization_id,
                RiskEntityLink.organization_id == organization_id,
                RiskEntityLink.entity_type == "work_package",
                RiskEntityLink.entity_id == work_package_id)
        .all()
    )
    return sorted(rows, key=lambda r: r._effective_score, reverse=True)


def build_document(case: BusinessCase) -> Document:
    org_id = case.organization_id
    currency = get_reporting_currency(org_id)
    doc = Document(case=case, currency=currency)
    options = list(case.options)
    figures = option_figures(org_id, [o.id for o in options], currency)
    for option in options:
        f = figures[option.id]
        months, note = payback(f[ONE_OFF], f[RUN_ANNUAL], f[BENEFIT_ANNUAL])
        doc.options.append(OptionView(
            option=option, one_off=f[ONE_OFF], run_annual=f[RUN_ANNUAL],
            benefit_annual=f[BENEFIT_ANNUAL],
            three_year_cost=three_year_cost(f[ONE_OFF], f[RUN_ANNUAL]),
            payback_months=months, payback_note=note, currency=currency))
    doc.risks = linked_risks(org_id, case.work_package_id)
    return doc

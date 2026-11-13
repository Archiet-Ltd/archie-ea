"""The one place a figure in pounds is withheld from a person who may not see spend.

The rule is ``may_see_spend_pages`` (app/decorators/requires_role.py): the people the Spend page is open to,
and whoever may see cost figures. ``costs_visible`` asks it. ``withhold_costs`` takes the money out of a
result before a reply is built, and the one sentence a person reads is ``COSTS_WITHHELD``. A template reads
the ``costs_withheld`` flag the masker sets and prints that sentence; it never blanks a cell itself.
"""
from typing import Any, Iterable, Optional

COSTS_WITHHELD = "Cost figures are withheld for your role."

# every key that holds a money amount, in a run's result and in a saved scenario's figures
COST_KEYS = ("estimated_financial_risk", "estimated_risk_cost", "application_cost", "annual_cost", "cost_source",
             "running_cost", "costed_applications")

# every key that holds a money amount in the investment analysis, its feed and its pages
INVESTMENT_MONEY_KEYS = (
    "estimated_cost", "budget_allocated", "budget_spent", "source", "linked_initiative_budget",
    "total_estimated_investment", "critical_investment", "high_investment", "medium_investment", "low_investment",
)

# every key that holds a money amount or a figure worked out from one in a business case
BUSINESS_CASE_MONEY_KEYS = (
    "financial_benefit_annual", "capex", "opex_annual", "tco_3yr", "roi_percentage", "payback_months", "npv",
)


def costs_visible(user) -> bool:
    """The one rule the Overview applies to cost: whoever may read the spend pages."""
    from app.decorators.requires_role import may_see_spend_pages

    return may_see_spend_pages(user)


def withhold_costs(result: Any, keys: Optional[Iterable[str]] = None) -> Any:
    """A copy of a result with every cost left out and the reason stated.

    ``keys`` names the money keys when a page's figures sit under other keys than a simulation's; the
    result and each part that held one then carries ``costs_withheld``. The stored data is not changed;
    this is applied on the way out for a person the cost rule withholds from.
    """
    money = COST_KEYS if keys is None else tuple(keys)
    out = _withhold(result, money, keys is not None)
    if keys is not None and isinstance(out, dict):
        out["costs_withheld"] = True
    return out


def withhold_reply(reply: Any, keys: Iterable[str]) -> Any:
    """A JSON reply with its money keys left out for the signed-in person the rule withholds from.

    A route calls this on the reply it is about to jsonify. For a person the rule allows the reply
    passes through unchanged; for anyone else the named money keys are removed at any depth and the
    reply carries ``costs_withheld`` with the one sentence, so a template or a fetch can say it once.
    """
    from flask import current_user, has_request_context

    if not has_request_context() or current_user is None or not current_user.is_authenticated:
        return reply
    if costs_visible(current_user):
        return reply
    out = withhold_costs(reply, keys=keys)
    if isinstance(out, dict):
        out["costs_withheld"] = COSTS_WITHHELD
    return out


import re as _re

# a field whose name says money, in a change-history row
MONEY_FIELD_RE = _re.compile(
    r"cost|tco|budget|spend|saving|invest|estimate|price|licen[cs]e|capex|opex|revenue", _re.I
)


def withhold_change_rows(rows: Any) -> Any:
    """History-style rows (``{"fields": [{"field", "before", "after"}]}``) with money-named
    fields' figures replaced by the one sentence, for the signed-in person the rule withholds from."""
    from flask import current_user, has_request_context

    if not has_request_context() or current_user is None or not current_user.is_authenticated:
        return rows
    if costs_visible(current_user):
        return rows
    out = []
    for row in rows or []:
        row = dict(row)
        fields = []
        for f in row.get("fields") or []:
            f = dict(f)
            if MONEY_FIELD_RE.search(str(f.get("field", ""))):
                f["before"] = COSTS_WITHHELD
                f["after"] = COSTS_WITHHELD
            fields.append(f)
        row["fields"] = fields
        row["costs_withheld"] = True
        out.append(row)
    return out


def _withhold(result: Any, money: tuple, flag_money: bool) -> Any:
    if isinstance(result, list):
        return [_withhold(item, money, flag_money) for item in result]
    if not isinstance(result, dict):
        return result
    out = {}
    for key, value in result.items():
        out[key] = None if key in money else _withhold(value, money, flag_money)
    if "financial_risk_reason" in result:
        out["financial_risk_reason"] = COSTS_WITHHELD
    if result.get("money") is True and "label" in result:
        out.update(before=None, after=None, change=None, note=COSTS_WITHHELD, withheld=True)
    if any(k in result for k in ("estimated_financial_risk", "estimated_risk_cost", "figures", "simulation")) \
            or (flag_money and any(k in result for k in money)):
        out["costs_withheld"] = True
    return out

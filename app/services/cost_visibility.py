"""The one place a figure in pounds is withheld from a person who may not see spend.

The rule is ``may_see_spend_pages`` (app/decorators/requires_role.py): the people the Spend page is open to,
and whoever may see cost figures. ``costs_visible`` asks it. ``withhold_costs`` takes the money out of a
result before a reply is built, and the one sentence a person reads is ``COSTS_WITHHELD``. A template reads
the ``costs_withheld`` flag the masker sets and prints that sentence; it never blanks a cell itself.
"""
import re
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

# every key that holds a money amount in a vendor analysis reply, its comparison, export and provenance
VENDOR_ANALYSIS_MONEY_KEYS = ("tco_total", "license_cost_annual", "support_cost_annual", "cost_delta")


# A currency amount written into text: a symbol then digits, or digits then a currency word.
_AMOUNT_IN_TEXT = re.compile(
    r"(?:[£$€]\s?\d[\d,]*(?:\.\d+)?(?:\s?[kKmM]\b|bn\b)?"
    r"|\b\d[\d,]*(?:\.\d+)?\s?(?:[kKmM]\s)?(?:GBP|USD|EUR|pounds|dollars|euros)\b)"
)


def without_amounts(value: Any) -> Any:
    """A copy of text, lists and dicts with every written currency amount replaced by a plain phrase."""
    if isinstance(value, str):
        return _AMOUNT_IN_TEXT.sub("a cost figure", value)
    if isinstance(value, list):
        return [without_amounts(v) for v in value]
    if isinstance(value, dict):
        return {k: without_amounts(v) for k, v in value.items()}
    return value


def costs_visible(user) -> bool:
    """The one rule the Overview applies to cost: whoever may read the spend pages."""
    from app.decorators.requires_role import may_see_spend_pages

    return may_see_spend_pages(user)


def vendor_analysis_reply(reply: Any, user) -> Any:
    """A vendor analysis reply for a person: unchanged when the cost rule allows them, otherwise with every
    money amount left out and the one withheld sentence said once. The one place the vendor analysis routes
    of both modules pass their replies through."""
    if costs_visible(user):
        return reply
    return {**withhold_costs(reply, keys=VENDOR_ANALYSIS_MONEY_KEYS), "costs_withheld_sentence": COSTS_WITHHELD}


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

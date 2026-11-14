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

# every key that holds a money amount or a figure worked out from one in a business case
BUSINESS_CASE_MONEY_KEYS = (
    "financial_benefit_annual", "capex", "opex_annual", "tco_3yr", "roi_percentage", "payback_months",
)

# the money names that carry no money word, by the kind of reply that uses them
ROLLUP_FIGURE_KEYS = ("estimated", "actual", "variance", "variance_pct")
SHARE_FIGURE_KEYS = ("total", "share", "share_range")
PIPELINE_FIGURE_KEYS = ("estimated_pipeline", "realized_ytd", "completed_total")
BOUND_FIGURE_KEYS = ("min", "max")

# The one list of money names. A key is money when it is on this list or its name says money
# (``_is_money_name``); hours, scores, weights, counts and percentages that are not worked out from money
# are not money and stay visible to everyone.
MONEY_KEYS = COST_KEYS + BUSINESS_CASE_MONEY_KEYS + VENDOR_ANALYSIS_MONEY_KEYS + (
    "estimated_cost", "budget_allocated", "budget_spent", "linked_initiative_budget",
    "total_estimated_investment", "investment_required", "amount", "allocated", "remaining", "spent", "roi",
    "estimated_total",
)

_MONEY_WORD = re.compile(
    r"cost|price|budget|spend|spent|saving|tco|revenue|invest|estimate|amount|allocated|remaining|capex|opex|npv|"
    r"payback|financial_benefit|licen[cs]e|projected|forecast", re.I)
_NOT_MONEY_WORD = re.compile(
    r"cost_cent(er|re)|cost_code|hours|days|duration|effort|points|estimate\w*date|score|weight|count|withheld|visible|_id$|"
    r"currency|status|kind|framework|supported|risk_class|plateau|_name$|label|reason|note|_type$|categories_loaded", re.I)


def _is_money_name(name: Any) -> bool:
    name = str(name)
    if name in MONEY_KEYS:
        return True
    return bool(_MONEY_WORD.search(name)) and not _NOT_MONEY_WORD.search(name)


def is_money_key(name: Any, extra: Iterable[str] = ()) -> bool:
    """True when ``name`` holds a money amount: on the one list, named in ``extra``, or says money."""
    return str(name) in tuple(extra) or _is_money_name(name)


# the evidence factors whose observed value is an amount of money
COST_EVIDENCE_FACTORS = ("Annual Cost (TCO)", "Cost per User")


# A currency amount written into text: a symbol or a currency code before the digits, or digits (with a comma,
# a space or a scale word in them) before a currency word.
_NUMBER = r"(?:\d{1,3}(?:[, \u00a0]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_SCALE = r"(?:\s?(?:[kKmM]|bn|thousand|million|billion)\b)?"
_AMOUNT_IN_TEXT = re.compile(
    rf"(?:[£$€]\s?{_NUMBER}{_SCALE}"
    rf"|\b(?:GBP|USD|EUR)\s?{_NUMBER}{_SCALE}"
    rf"|\b{_NUMBER}{_SCALE}\s?(?:GBP|USD|EUR|pounds?|dollars?|euros?)\b)"
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
    return withhold_costs(reply, keys=VENDOR_ANALYSIS_MONEY_KEYS)


def withhold_costs(result: Any, keys: Optional[Iterable[str]] = None) -> Any:
    """A copy of a result with every money amount left out and the reason stated.

    A key is money when ``is_money_key`` says so, or when it is named in ``keys`` (a page whose figures sit
    under plain names, such as a shares table, names them here from the lists in this file). The result and
    each part that held a money amount carries ``costs_withheld``. The stored data is not changed; this is
    applied on the way out for a person the cost rule withholds from.
    """
    out = _withhold(result, tuple(keys or ()))
    if isinstance(out, dict):
        out["costs_withheld"] = True
        out["costs_withheld_sentence"] = COSTS_WITHHELD
    return out


def withhold_reply(reply: Any, keys: Iterable[str] = ()) -> Any:
    """A JSON reply for the signed-in person: unchanged when the cost rule allows them, otherwise with every
    money amount left out, ``costs_withheld`` True and the one sentence under ``costs_withheld_sentence``.
    A route calls this on the reply it is about to jsonify."""
    from flask import has_request_context
    from flask_login import current_user

    if not has_request_context() or current_user is None or not current_user.is_authenticated:
        return reply
    if costs_visible(current_user):
        return reply
    return withhold_costs(reply, keys=keys)


def withhold_evidence_trail(node: Any) -> Any:
    """An evidence trail with each cost factor's observed amount left out, no amount written into any
    rationale or source, and the withheld sentence said once at the top."""
    return withhold_costs(node)


def withhold_change_rows(rows: Any) -> Any:
    """History-style rows (``{"fields": [{"field", "before", "after"}]}``) with money-named
    fields' figures replaced by the one sentence, for the signed-in person the rule withholds from."""
    from flask import has_request_context
    from flask_login import current_user

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
            if is_money_key(f.get("field", "")):
                f["before"] = COSTS_WITHHELD
                f["after"] = COSTS_WITHHELD
            fields.append(f)
        row["fields"] = fields
        row["costs_withheld"] = True
        out.append(row)
    return out


def _withhold(result: Any, extra: tuple) -> Any:
    if isinstance(result, list):
        return [_withhold(item, extra) for item in result]
    if not isinstance(result, dict):
        return without_amounts(result)
    out = {}
    masked = False
    for key, value in result.items():
        if isinstance(value, bool) or not is_money_key(key, extra):
            out[key] = _withhold(value, extra)
        else:
            out[key] = None
            masked = True
    if result.get("factor") in COST_EVIDENCE_FACTORS and "raw_value" in result:
        out["raw_value"] = None
        masked = True
    if "financial_risk_reason" in result:
        out["financial_risk_reason"] = COSTS_WITHHELD
    if result.get("money") is True and "label" in result:
        out.update(before=None, after=None, change=None, note=COSTS_WITHHELD, withheld=True)
    if masked or any(k in result for k in ("figures", "simulation")):
        out["costs_withheld"] = True
    return out

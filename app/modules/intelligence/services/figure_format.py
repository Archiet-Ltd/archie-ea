"""The one formatter that turns a declared figure's value into text.

A figure reaches a person only as the engine's rendering of a value a declared
field returned. ``render_figure`` is the only code that does that rendering, for
the chat and for the connector alike. It never abbreviates (no k, m or bn), never
converts a unit or a currency, never computes a relative date, and never shows a
dash, "N/A" or a zero for a missing value: a missing value reads as the
declaration's ``reads_null``.

Conventions: en-GB, comma thousands separator, half away from zero.
"""

from __future__ import annotations

import datetime as _dt
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Optional

_SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}
_MONTHS = (
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
)


def _round(value: Any, places: int = 0) -> Decimal:
    quantum = Decimal(1).scaleb(-places)
    return Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)


def _grouped(number: Decimal, places: int = 0) -> str:
    return "{:,.{p}f}".format(number, p=places)


def _is_whole(value: Any) -> bool:
    return Decimal(str(value)) == Decimal(str(value)).to_integral_value()


def _trim(number: Decimal, max_places: int) -> str:
    """At most *max_places* decimals, without trailing zeros."""
    text = _grouped(_round(number, max_places), max_places)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return "%d%s" % (n, suffix)


def _as_date(value: Any) -> Optional[_dt.date]:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        try:
            return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def _as_datetime(value: Any) -> Optional[_dt.datetime]:
    if isinstance(value, _dt.datetime):
        return value
    if isinstance(value, str):
        try:
            return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _day_month_year(day: _dt.date) -> str:
    return "%d %s %d" % (day.day, _MONTHS[day.month - 1], day.year)


def money(value: Any, currency: Optional[str]) -> str:
    """£470,000; $1,500,000,000; €470,000; CHF 1,200; £45.50. A whole value is shown
    whole; otherwise two decimals below 1,000 and whole units from 1,000 up."""
    code = (currency or "").upper()
    negative = Decimal(str(value)) < 0
    amount = abs(Decimal(str(value)))
    if _is_whole(amount):
        body = _grouped(_round(amount, 0))
    elif amount < 1000:
        body = _grouped(_round(amount, 2), 2)
    else:
        body = _grouped(_round(amount, 0))
    symbol = _SYMBOLS.get(code)
    text = (symbol + body) if symbol else ("%s %s" % (code, body) if code else body)
    return ("-" + text) if negative else text


def value_text(declaration: Dict[str, Any], value: Any, unit: Dict[str, Any]) -> str:
    """The text of *value* for the declared kind: what replaces ``{value}``."""
    kind = declaration["kind"]
    if kind == "count":
        return _grouped(_round(value, 0))
    if kind == "money":
        return money(value, unit.get("currency"))
    if kind == "score":
        return "%s out of %s" % (_trim(Decimal(str(value)), 1), _trim(Decimal(str(unit["out_of"])), 1))
    if kind in ("percent", "ratio"):
        number = Decimal(str(value)) * (100 if kind == "ratio" else 1)
        places = int(declaration.get("decimals", 0) or 0)
        shown = _round(number, places)
        if number != 0 and number != 100 and shown in (Decimal(0), Decimal(100)):
            places = max(places, 1)
            shown = _round(number, places)
        return _grouped(shown, places) + "%"
    if kind == "duration":
        word = str(unit["unit"])
        return "%s %s" % (_trim(Decimal(str(value)), 2), word if _is_one(value) else _plural(word))
    if kind == "date":
        day = _as_date(value)
        return _day_month_year(day) if day else str(value)
    if kind == "datetime":
        moment = _as_datetime(value)
        if moment is None:
            return str(value)
        text = _day_month_year(moment.date())
        if declaration.get("precision") == "minute":
            text += " at %02d:%02d UTC" % (moment.hour, moment.minute)
        return text
    if kind == "year":
        return str(int(value))
    if kind == "month":
        if isinstance(value, str) and "-" in value:
            day = _as_date(value + "-01" if len(value) == 7 else value)
            if day:
                return "%s %d" % (_MONTHS[day.month - 1], day.year)
        return str(value)
    if kind == "rank":
        return "%s of %s" % (_ordinal(int(value)), _grouped(_round(unit["of"], 0)))
    raise ValueError("unknown figure kind %r" % kind)


def _is_one(value: Any) -> bool:
    return Decimal(str(value)) == 1


def _plural(word: str) -> str:
    return word if word.endswith("s") else word + "s"


def render_figure(entry: Dict[str, Any]) -> str:
    """The phrase for one figure-table entry.

    *entry* holds ``declaration`` (the declared field), ``value`` (what the tool
    returned) and ``unit`` (the currency, scale, unit or total the declaration
    names, read from the same result). A null value reads as ``reads_null``."""
    declaration = entry["declaration"]
    value = entry.get("value")
    if value is None:
        return declaration["reads_null"]
    reads = declaration["reads"]
    if isinstance(reads, dict):
        reads = reads["one"] if _is_one(value) else reads["other"]
    text = reads.replace("{value}", value_text(declaration, value, entry.get("unit") or {}))
    window = declaration.get("window")
    if window and entry.get("window"):
        phrase = window["phrases"].get(entry["window"])
        if phrase:
            text += " " + phrase
            start = _as_date(entry.get("window_start"))
            if start:
                text += " (since %s)" % _day_month_year(start)
    return text


def value_only(entry: Dict[str, Any]) -> str:
    """The value without its phrase: a table cell shows this."""
    value = entry.get("value")
    declaration = entry["declaration"]
    if value is None:
        return declaration["reads_null"]
    return value_text(declaration, value, entry.get("unit") or {})

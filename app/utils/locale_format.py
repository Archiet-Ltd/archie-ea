"""One way to write a date, a number, a percentage and an amount of money.

Every value a person reads is written by one of four functions here, registered once as the template
filters ``fmt_date``, ``fmt_number``, ``fmt_percent`` and ``fmt_money``. They read the signed-in organisation's
locale (``Organization.locale``) and currency (``Organization.reporting_currency``, read through
``CurrencyConfig``), so a company in Berlin reads ``1.234,56 €`` and ``25.10.2026`` where a company in London
reads ``£1,234.56`` and ``25 Oct 2026``.

A missing value is returned as ``None`` by the python functions and as an em dash by the filters, never as a
fabricated zero. The compact form of an amount ("£1.7m") comes from the same function as the full form, with
``compact=True``, so the two always agree.
"""
from __future__ import annotations

import datetime as _dt
import re
from decimal import Decimal
from typing import Any, Optional

from babel import Locale, UnknownLocaleError
from babel import dates as _dates
from babel import numbers as _numbers

EM_DASH = "—"
DEFAULT_LOCALE = "en-GB"

# Locales an organisation can pick. The tag is the one stored in ``Organization.locale``.
SUPPORTED_LOCALES = {
    "en-GB": "English (United Kingdom)",
    "en-US": "English (United States)",
    "de-DE": "Deutsch (Deutschland)",
    "fr-FR": "Francais (France)",
    "es-ES": "Espanol (Espana)",
}


def normalise_locale(tag: Optional[str]) -> str:
    """The supported tag for ``tag`` ("de_DE", "de-de", "de" all give "de-DE"), else the default."""
    if not tag:
        return DEFAULT_LOCALE
    text = str(tag).strip().replace("_", "-")
    for supported in SUPPORTED_LOCALES:
        if supported.lower() == text.lower():
            return supported
    language = text.split("-")[0].lower()
    for supported in SUPPORTED_LOCALES:
        if supported.split("-")[0].lower() == language:
            return supported
    return DEFAULT_LOCALE


def _babel_locale(tag: Optional[str]) -> Locale:
    try:
        return Locale.parse(normalise_locale(tag), sep="-")
    except (UnknownLocaleError, ValueError):
        return Locale.parse(DEFAULT_LOCALE, sep="-")


def current_locale() -> str:
    """The signed-in organisation's locale tag, else the default. Read once per request."""
    from flask import g, has_request_context

    if not has_request_context():
        return DEFAULT_LOCALE
    cached = g.get("_locale_format_tag")
    if cached:
        return cached
    tag = DEFAULT_LOCALE
    org_id = getattr(g, "current_org_id", None)
    if org_id is not None:
        try:
            from app import db
            from app.models.organization import Organization

            stored = db.session.execute(
                db.select(Organization.locale).where(Organization.id == org_id)
            ).scalar_one_or_none()
            tag = normalise_locale(stored)
        except Exception:
            tag = DEFAULT_LOCALE
    g._locale_format_tag = tag
    return tag


def current_currency() -> str:
    """The signed-in organisation's currency code (the one stored value, see billing03)."""
    from config import CurrencyConfig

    return CurrencyConfig.get_org_currency_code()


def _number(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip())
    except Exception:
        return None


# ---- numbers --------------------------------------------------------------------------------------------

def fmt_number(value: Any, decimals: Optional[int] = None, locale: Optional[str] = None) -> Optional[str]:
    """1234.5 -> "1,234.5" (en-GB), "1.234,5" (de-DE). ``decimals`` fixes the places; None keeps what is there."""
    number = _number(value)
    if number is None:
        return None
    loc = _babel_locale(locale or current_locale())
    if decimals is None:
        return _numbers.format_decimal(number, locale=loc)
    pattern = "#,##0" + ("." + "0" * decimals if decimals > 0 else "")
    return _numbers.format_decimal(number, format=pattern, locale=loc)


def fmt_percent(value: Any, decimals: int = 0, locale: Optional[str] = None) -> Optional[str]:
    """A fraction as a percentage: 0.256 -> "26%" (en-GB), "26 %" (de-DE)."""
    number = _number(value)
    if number is None:
        return None
    loc = _babel_locale(locale or current_locale())
    pattern = re.sub(r"#,##0(\.0+)?", "#,##0" + ("." + "0" * decimals if decimals > 0 else ""), loc.percent_formats[None].pattern)
    return _numbers.format_percent(number, format=pattern, locale=loc)


# ---- money ----------------------------------------------------------------------------------------------

_COMPACT_ENGLISH = {"K": "k", "M": "m", "B": "bn", "T": "tn"}


def fmt_money(
    value: Any,
    currency: Optional[str] = None,
    compact: bool = False,
    decimals: Optional[int] = None,
    code: bool = False,
    locale: Optional[str] = None,
) -> Optional[str]:
    """An amount of money in the organisation's currency.

    ``compact`` gives the headline form ("£1.7m", "1,7 Mio. €") for amounts of a thousand and over; a smaller amount
    is written in full. ``decimals`` fixes the places of the full form (0 for whole pounds); None uses the
    currency's own. ``code`` writes the ISO code instead of the symbol ("USD 490", "490 USD"), for prices that must
    name their currency.
    """
    number = _number(value)
    if number is None:
        return None
    tag = locale or current_locale()
    loc = _babel_locale(tag)
    cur = (currency or current_currency() or "GBP").upper()
    if code:
        text = fmt_number(number, decimals if decimals is not None else (0 if number == number.to_integral() else 2), tag)
        return "%s %s" % (cur, text) if loc.language == "en" else "%s %s" % (text, cur)
    if compact and abs(number) >= 1000:
        out = _numbers.format_compact_currency(number, cur, locale=loc, fraction_digits=1)
        if loc.language == "en":
            out = re.sub(r"(?<=\d)([KMBT])\b", lambda m: _COMPACT_ENGLISH[m.group(1)], out)
        return out
    if decimals is None:
        return _numbers.format_currency(number, cur, locale=loc)
    pattern = loc.currency_formats["standard"].pattern
    pattern = re.sub(r"0\.0+", "0" + ("." + "0" * decimals if decimals else ""), pattern)
    return _numbers.format_currency(number, cur, format=pattern, locale=loc, currency_digits=False)


# ---- dates ----------------------------------------------------------------------------------------------

def _as_datetime(value: Any) -> Optional[_dt.datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, _dt.datetime):
        return value
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day)
    try:
        return _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_date(value: Any, style: str = "medium", locale: Optional[str] = None) -> Optional[str]:
    """A date as the organisation's locale writes it: "25 Oct 2026" (en-GB), "25.10.2026" (de-DE).

    ``style`` is "short", "medium", "long", or a skeleton such as "MMM" or "d MMM" for a chart label. A datetime
    gets its time as well ("25 Oct 2026, 19:32") when ``style`` is "medium", "short" or "long" and the value
    carries a time; use ``fmt_datetime`` to always include it.
    """
    when = _as_datetime(value)
    if when is None:
        return None
    loc = _babel_locale(locale or current_locale())
    if style in ("short", "medium", "long", "full"):
        return _dates.format_date(when.date(), format=style, locale=loc)
    return _dates.format_skeleton(style, when.date(), locale=loc)


def fmt_datetime(value: Any, locale: Optional[str] = None) -> Optional[str]:
    """A date and time: "25 Oct 2026, 19:32" (en-GB), "25.10.2026, 19:32" (de-DE)."""
    when = _as_datetime(value)
    if when is None:
        return None
    loc = _babel_locale(locale or current_locale())
    day = _dates.format_date(when.date(), format="medium", locale=loc)
    clock = _dates.format_time(when.time(), format="short", locale=loc)
    return "%s, %s" % (day, clock)


def fmt_date_range(start: Any, end: Any, locale: Optional[str] = None) -> Optional[str]:
    """"19 Oct to 25 Oct 2026" (en-GB), "19.10. - 25.10.2026" style for other locales: the start without its year."""
    first, last = _as_datetime(start), _as_datetime(end)
    if first is None or last is None:
        return None
    tag = locale or current_locale()
    loc = _babel_locale(tag)
    head = fmt_date(first, "MMMd", tag) if first.year == last.year else fmt_date(first, "medium", tag)
    joiner = "to" if loc.language == "en" else "\u2013"
    return "%s %s %s" % (head, joiner, fmt_date(last, "medium", tag))


# ---- registration ---------------------------------------------------------------------------------------

def _or_dash(text: Optional[str]) -> str:
    return EM_DASH if text is None else text


def register_locale_filters(app) -> None:
    """Register the four filters (and ``fmt_datetime``) once. Called from the context-processor bootstrap."""
    filters = app.jinja_env.filters
    filters["fmt_number"] = lambda v, decimals=None: _or_dash(fmt_number(v, decimals))
    filters["fmt_percent"] = lambda v, decimals=0: _or_dash(fmt_percent(v, decimals))
    filters["fmt_money"] = lambda v, currency=None, compact=False, decimals=None, code=False: _or_dash(
        fmt_money(v, currency, compact, decimals, code)
    )
    filters["fmt_date"] = lambda v, style="medium": _or_dash(fmt_date(v, style))
    filters["fmt_datetime"] = lambda v: _or_dash(fmt_datetime(v))
    filters["fmt_date_range"] = lambda start, end: _or_dash(fmt_date_range(start, end))

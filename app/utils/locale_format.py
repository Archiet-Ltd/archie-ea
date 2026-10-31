"""One way to write a date, a number, a percentage and an amount of money, following the organisation's locale.

Templates call the four filters ``fmt_date``, ``fmt_number``, ``fmt_percent`` and ``fmt_money``; nothing else turns a
figure into text. The locale lives on the organisation (``Organization.settings["locale"]``) and the currency on
``Organization.reporting_currency``, so a company in Berlin reads ``1.234,56 €`` and ``25.10.2026`` where one in London
reads ``£1,234.56`` and ``25 Oct 2026``. A missing value is an em dash, never ``0`` or blank.

The table below is deliberately small and written out by hand: Babel is not a dependency, and three locales are what
the first customers need. A locale is added by adding a row; nothing else changes.
"""

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional

DEFAULT_LOCALE = "en-GB"
DEFAULT_CURRENCY = "GBP"
NULL_DISPLAY = "—"
_NBSP = " "

_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# decimal / group: the separators. date: how a calendar date is written. money_after: the symbol follows the number.
# percent_gap: what sits between the number and the % sign. compact: the suffixes for thousands, millions, billions.
LOCALES = {
    "en-GB": {
        "label": "English (United Kingdom)", "decimal": ".", "group": ",", "date": "dmy_text",
        "money_after": False, "percent_gap": "", "compact": ("k", "m", "bn"), "compact_gap": "",
    },
    "en-US": {
        "label": "English (United States)", "decimal": ".", "group": ",", "date": "mdy_text",
        "money_after": False, "percent_gap": "", "compact": ("k", "m", "bn"), "compact_gap": "",
    },
    "de-DE": {
        "label": "Deutsch (Deutschland)", "decimal": ",", "group": ".", "date": "dmy_dots",
        "money_after": True, "percent_gap": _NBSP, "compact": ("Tsd.", "Mio.", "Mrd."), "compact_gap": _NBSP,
    },
}

CURRENCY_SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}
CURRENCIES = (("GBP", "Pound sterling (£)"), ("USD", "US dollar ($)"), ("EUR", "Euro (€)"))


def locale_options():
    return [(code, row["label"]) for code, row in LOCALES.items()]


def _rules(locale: Optional[str]) -> dict:
    return LOCALES.get(locale or "") or LOCALES[DEFAULT_LOCALE]


def _organisation():
    """The signed-in person's active organisation, or None outside a request."""
    try:
        from flask import g, has_request_context
        from flask_login import current_user

        if not has_request_context():
            return None
        cached = getattr(g, "_locale_org", None)
        if cached is not None:
            return cached
        if not getattr(current_user, "is_authenticated", False):
            return None
        org = None
        active_id = getattr(g, "current_org_id", None)
        if active_id:
            from app.extensions import db
            from app.models.organization import Organization

            org = db.session.get(Organization, active_id)
        if org is None:
            org = getattr(current_user, "organization", None)
        g._locale_org = org
        return org
    except Exception:
        return None


def current_locale() -> str:
    org = _organisation()
    code = ((getattr(org, "settings", None) or {}).get("locale") if org is not None else None)
    return code if code in LOCALES else DEFAULT_LOCALE


def current_currency() -> str:
    org = _organisation()
    code = (getattr(org, "reporting_currency", None) or "").upper() if org is not None else ""
    return code or DEFAULT_CURRENCY


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _group(digits: str, separator: str) -> str:
    out = []
    while len(digits) > 3:
        out.insert(0, digits[-3:])
        digits = digits[:-3]
    out.insert(0, digits)
    return separator.join(out)


def _plain(number: Decimal, decimals: int, rules: dict) -> str:
    """The unsigned digits of ``number`` at a fixed number of decimals, with this locale's separators."""
    quantum = Decimal(1).scaleb(-decimals)
    rounded = abs(number).quantize(quantum, rounding=ROUND_HALF_UP)
    whole, _, fraction = ("%f" % rounded).partition(".")
    text = _group(whole, rules["group"])
    return text + (rules["decimal"] + fraction[:decimals] if decimals else "")


def _signed(number: Decimal, text: str) -> str:
    return ("-" + text) if number < 0 and Decimal(text.replace(".", "").replace(",", "").replace(" ", "") or 0) != 0 else text


def fmt_number(value: Any, decimals: Optional[int] = None, locale: Optional[str] = None) -> str:
    """1234.5 -> ``1,234.5`` (en) / ``1.234,5`` (de). Whole numbers show no decimals unless ``decimals`` says so."""
    number = _decimal(value)
    if number is None:
        return NULL_DISPLAY
    if decimals is None:
        decimals = 0 if number == number.to_integral_value() else 2
    text = _plain(number, decimals, _rules(locale or current_locale()))
    return ("-" + text) if number < 0 and any(c in "123456789" for c in text) else text


def fmt_percent(value: Any, decimals: int = 0, locale: Optional[str] = None, scale: bool = True) -> str:
    """0.125 -> ``12.5%`` / ``12,5 %``. ``scale=False`` for a value that is already 0 to 100."""
    number = _decimal(value)
    if number is None:
        return NULL_DISPLAY
    rules = _rules(locale or current_locale())
    if scale:
        number = number * 100
    return fmt_number(number, decimals, locale or current_locale()) + rules["percent_gap"] + "%"


def fmt_money(value: Any, currency: Optional[str] = None, compact: bool = False, decimals: Optional[int] = None,
              locale: Optional[str] = None) -> str:
    """1234.56 GBP -> ``£1,234.56``; the same in de-DE EUR -> ``1.234,56 €``.

    ``compact`` is the headline form on a card (``£1.7m``, ``137,7 Tsd. €``). It comes from this same function, so the
    short form and the sentence beside it can never disagree.
    """
    number = _decimal(value)
    if number is None:
        return NULL_DISPLAY
    code = (currency or current_currency()).upper()
    symbol = CURRENCY_SYMBOLS.get(code, code + _NBSP)
    loc = locale or current_locale()
    rules = _rules(loc)
    suffix = ""
    shown = number
    if compact:
        for limit, index in ((Decimal(10) ** 9, 2), (Decimal(10) ** 6, 1), (Decimal(10) ** 3, 0)):
            if abs(number) >= limit:
                shown, suffix = number / limit, rules["compact_gap"] + rules["compact"][index]
                break
        places = 1 if suffix else (0 if number == number.to_integral_value() else 2)
        if decimals is not None:
            places = decimals
    else:
        places = decimals if decimals is not None else 2
    digits = _plain(shown, places, rules)
    if suffix and places:
        # 1.0m reads as 1m; 1.7m stays 1.7m
        fraction = rules["decimal"] + "0" * places
        digits = digits[: -len(fraction)] if digits.endswith(fraction) else digits
    sign = "-" if shown < 0 and any(c in "123456789" for c in digits) else ""
    if rules["money_after"]:
        return "%s%s%s%s%s" % (sign, digits, suffix, _NBSP, symbol.strip())
    return "%s%s%s%s" % (sign, symbol, digits, suffix)


def _as_date(value: Any):
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def fmt_date(value: Any, time: bool = False, locale: Optional[str] = None) -> str:
    """2026-10-25 -> ``25 Oct 2026`` (en-GB), ``Oct 25, 2026`` (en-US), ``25.10.2026`` (de-DE). ``time=True`` adds ``, 19:32``."""
    moment = _as_date(value)
    if moment is None:
        return NULL_DISPLAY
    style = _rules(locale or current_locale())["date"]
    if style == "dmy_dots":
        text = "%02d.%02d.%d" % (moment.day, moment.month, moment.year)
    elif style == "mdy_text":
        text = "%s %d, %d" % (_MONTHS_EN[moment.month - 1], moment.day, moment.year)
    else:
        text = "%d %s %d" % (moment.day, _MONTHS_EN[moment.month - 1], moment.year)
    return text + (", %02d:%02d" % (moment.hour, moment.minute) if time else "")


def register_locale_filters(app) -> None:
    """Register the four filters once. Everything that formats a figure for a person goes through these."""
    app.add_template_filter(fmt_date, "fmt_date")
    app.add_template_filter(fmt_number, "fmt_number")
    app.add_template_filter(fmt_percent, "fmt_percent")
    app.add_template_filter(fmt_money, "fmt_money")

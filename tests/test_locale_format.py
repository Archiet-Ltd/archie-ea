"""The one formatter writes dates, numbers, percentages and money by locale."""
import datetime as dt

import pytest

from app.utils.locale_format import fmt_date, fmt_datetime, fmt_money, fmt_number, fmt_percent

NB = " "


@pytest.mark.parametrize("loc,expected", [("en-GB", "1,234.56"), ("en-US", "1,234.56"), ("de-DE", "1.234,56")])
def test_numbers(loc, expected):
    assert fmt_number(1234.56, 2, loc) == expected


@pytest.mark.parametrize("loc,expected", [("en-GB", "26%"), ("en-US", "26%"), ("de-DE", "26" + NB + "%")])
def test_percent(loc, expected):
    assert fmt_percent(0.256, 0, loc) == expected


@pytest.mark.parametrize("loc,expected", [("en-GB", "25 Oct 2026"), ("en-US", "Oct 25, 2026"), ("de-DE", "25.10.2026")])
def test_date(loc, expected):
    assert fmt_date(dt.date(2026, 10, 25), "medium", loc) == expected


def test_datetime():
    when = dt.datetime(2026, 10, 25, 19, 32)
    assert fmt_datetime(when, "en-GB") == "25 Oct 2026, 19:32"
    assert fmt_datetime(when, "de-DE") == "25.10.2026, 19:32"


def test_full_money():
    assert fmt_money(1234.56, "GBP", locale="en-GB") == "£1,234.56"
    assert fmt_money(1234.56, "USD", locale="en-US") == "$1,234.56"
    assert fmt_money(1234.56, "EUR", locale="de-DE") == "1.234,56" + NB + "€"
    assert fmt_money(137700, "GBP", decimals=0, locale="en-GB") == "£137,700"
    assert fmt_money(137700, "EUR", decimals=0, locale="de-DE") == "137.700" + NB + "€"


def test_compact_money():
    assert fmt_money(1_700_000, "GBP", compact=True, locale="en-GB") == "£1.7m"
    assert fmt_money(137_700, "GBP", compact=True, locale="en-GB") == "£137.7k"
    assert fmt_money(1_700_000, "EUR", compact=True, locale="de-DE") == "1,7" + NB + "Mio." + NB + "€"
    assert fmt_money(250, "GBP", compact=True, decimals=0, locale="en-GB") == "£250"


def test_named_currency():
    assert fmt_money(490, "USD", code=True, locale="en-GB") == "USD 490"
    assert fmt_money(490, "USD", code=True, locale="de-DE") == "490" + NB + "USD"


def test_missing_is_none():
    assert fmt_money(None) is None and fmt_date(None) is None and fmt_number(None) is None

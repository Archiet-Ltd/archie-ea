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


def test_billing_day_keeps_two_digits_in_english_only():
    assert fmt_date(dt.date(2030, 1, 1), "medium0", "en-GB") == "01 Jan 2030"
    assert fmt_date(dt.date(2030, 1, 1), "medium0", "de-DE") == "01.01.2030"


def test_compact_money_without_a_short_word_is_written_in_full():
    assert fmt_money(154130, "EUR", compact=True, decimals=0, locale="de-DE") == "154.130" + NB + "€"
    assert fmt_money(154130, "GBP", compact=True, decimals=0, locale="en-GB") == "£154.1k"


def test_september_keeps_its_three_letters_in_english():
    assert fmt_date(dt.date(2026, 9, 1), "MMM", "en-GB") == "Sep"


def test_the_older_filters_are_aliases_of_the_one_formatter(app, monkeypatch):
    """number_format, percent, format_number, format_percent, format_date, format_datetime, currency and
    format_currency all follow the organisation's locale because they call the one formatter."""
    from app.utils import locale_format

    monkeypatch.setattr(locale_format, "current_locale", lambda: "de-DE")
    monkeypatch.setattr(locale_format, "current_currency", lambda: "EUR")
    f = app.jinja_env.filters
    assert f["number_format"](1234.5) == "1.234,50"
    assert f["format_number"](1234.5, 2) == "1.234,50"
    assert f["percent"](0.256, 1) == "25,6" + NB + "%"
    assert f["format_percent"](0.256, 1) == "25,6" + NB + "%"
    assert f["format_date"](dt.date(2026, 10, 25)) == "25.10.2026"
    assert f["format_datetime"](dt.datetime(2026, 10, 25, 19, 32)) == "25.10.2026, 19:32"
    assert f["format_datetime"](dt.datetime(2026, 10, 25, 19, 32), "d MMM yyyy") == "25 Okt. 2026"
    assert f["currency"](1234.56) == "1.234,56" + NB + "€"
    assert f["format_currency"](1234.56) == "1.234,56" + NB + "€"
    assert f["format_currency"](None) == "—"

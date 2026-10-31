"""One formatter for dates, numbers, percentages and money, reading the organisation's locale."""
from datetime import date, datetime

import pytest

from app.utils import locale_format as lf

NB = " "


@pytest.mark.parametrize("locale, expected", [
    ("en-GB", "£1,234.56"),
    ("en-US", "£1,234.56"),
    ("de-DE", "1.234,56" + NB + "£"),
])
def test_full_money_follows_the_locale(locale, expected):
    assert lf.fmt_money(1234.56, "GBP", locale=locale) == expected


def test_a_german_organisation_in_euros_reads_the_brief_example():
    assert lf.fmt_money(1234.56, "EUR", locale="de-DE") == "1.234,56" + NB + "€"
    assert lf.fmt_money(1234.56, "GBP", locale="en-GB") == "£1,234.56"
    assert lf.fmt_money(1234.56, "USD", locale="en-US") == "$1,234.56"


@pytest.mark.parametrize("locale, value, expected", [
    ("en-GB", 1_700_000, "£1.7m"),
    ("en-GB", 137_700, "£137.7k"),
    ("en-GB", 2_000_000, "£2m"),
    ("en-GB", 1_250_000_000, "£1.3bn"),
    ("en-GB", 950, "£950"),
    ("de-DE", 1_700_000, "1,7" + NB + "Mio." + NB + "£"),
    ("de-DE", 137_700, "137,7" + NB + "Tsd." + NB + "£"),
])
def test_compact_money_is_the_same_function_with_a_flag(locale, value, expected):
    assert lf.fmt_money(value, "GBP", compact=True, locale=locale) == expected


def test_compact_and_full_agree_on_the_figure():
    assert lf.fmt_money(1_700_000, "GBP", locale="en-GB").startswith("£1,700,000")
    assert lf.fmt_money(1_700_000, "GBP", compact=True, locale="en-GB") == "£1.7m"


def test_negative_money_keeps_the_sign_first():
    assert lf.fmt_money(-1234.5, "GBP", locale="en-GB") == "-£1,234.50"
    assert lf.fmt_money(-1234.5, "EUR", locale="de-DE") == "-1.234,50" + NB + "€"
    assert lf.fmt_money(-0.001, "GBP", locale="en-GB") == "£0.00"  # no "-0.00"


@pytest.mark.parametrize("locale, expected", [
    ("en-GB", "25 Oct 2026"), ("en-US", "Oct 25, 2026"), ("de-DE", "25.10.2026"),
])
def test_dates_follow_the_locale(locale, expected):
    assert lf.fmt_date(date(2026, 10, 25), locale=locale) == expected
    assert lf.fmt_date(datetime(2026, 10, 25, 19, 32), locale=locale) == expected
    assert lf.fmt_date("2026-10-25T19:32:00", locale=locale) == expected


def test_a_time_is_added_on_request():
    assert lf.fmt_date(datetime(2026, 10, 25, 19, 32), time=True, locale="en-GB") == "25 Oct 2026, 19:32"
    assert lf.fmt_date(datetime(2026, 10, 25, 9, 5), time=True, locale="de-DE") == "25.10.2026, 09:05"


@pytest.mark.parametrize("locale, expected", [("en-GB", "1,234,567"), ("de-DE", "1.234.567")])
def test_numbers_group_by_locale(locale, expected):
    assert lf.fmt_number(1234567, locale=locale) == expected


def test_numbers_show_decimals_only_when_they_are_there_or_asked_for():
    assert lf.fmt_number(3, locale="en-GB") == "3"
    assert lf.fmt_number(3.5, locale="en-GB") == "3.50"
    assert lf.fmt_number(3.5, 1, locale="de-DE") == "3,5"
    assert lf.fmt_number(2.5, 0, locale="en-GB") == "3"  # half rounds up, not to even


@pytest.mark.parametrize("locale, expected", [("en-GB", "12.5%"), ("de-DE", "12,5" + NB + "%")])
def test_percentages(locale, expected):
    assert lf.fmt_percent(0.125, 1, locale=locale) == expected
    assert lf.fmt_percent(12.5, 1, locale=locale, scale=False) == expected


@pytest.mark.parametrize("function", [lf.fmt_date, lf.fmt_number, lf.fmt_percent, lf.fmt_money])
@pytest.mark.parametrize("missing", [None, "", "not a number"])
def test_a_missing_or_unreadable_value_is_a_dash_never_zero(function, missing):
    assert function(missing) == "—"


def test_an_unknown_locale_falls_back_to_the_default():
    assert lf.fmt_date(date(2026, 10, 25), locale="xx-XX") == "25 Oct 2026"


def test_the_filters_are_registered_once_on_the_app(app):
    for name in ("fmt_date", "fmt_number", "fmt_percent", "fmt_money"):
        assert name in app.jinja_env.filters
    assert app.jinja_env.filters["fmt_money"] is lf.fmt_money

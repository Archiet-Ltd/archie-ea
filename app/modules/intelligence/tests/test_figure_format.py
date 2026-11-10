"""One formatter turns a declared figure's value into text, for every kind.

No abbreviation (no k, m or bn), no unit or currency conversion, no relative date,
and a missing value reads as the declaration's own words, never a dash or a zero.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.modules.intelligence.services.figure_format import money, render_figure, value_only


def _entry(kind, value, reads="{value}", unit=None, **extra):
    declaration = {"kind": kind, "reads": reads, "reads_null": "nothing recorded", **extra}
    return {"declaration": declaration, "value": value, "unit": unit or {}}


COUNT = {"one": "{value} connection downstream", "other": "{value} connections downstream"}


@pytest.mark.parametrize("value,text", [
    (13, "13 connections downstream"),
    (1, "1 connection downstream"),
    (0, "0 connections downstream"),
    (1204, "1,204 connections downstream"),
])
def test_a_count_is_whole_with_a_singular_phrase_at_one(value, text):
    assert render_figure(_entry("count", value, COUNT)) == text


@pytest.mark.parametrize("value,currency,text", [
    (470000, "GBP", "£470,000"),
    (1500000000, "USD", "$1,500,000,000"),
    (470000, "EUR", "€470,000"),
    (1200, "CHF", "CHF 1,200"),
    (45.5, "GBP", "£45.50"),
    (1500.75, "GBP", "£1,501"),
    (-250, "GBP", "-£250"),
    (110000.0, "GBP", "£110,000"),
])
def test_money_is_shown_whole_in_its_currency_symbol_or_code(value, currency, text):
    assert money(value, currency) == text


def test_engine_money_never_abbreviates():
    for value in (1200, 2_000_000, 1_500_000_000):
        text = money(value, "USD")
        assert not any(ch in text.lower() for ch in "kmb"), text
        assert text.replace("$", "").replace(",", "").isdigit()


def test_a_score_renders_with_its_declared_scale():
    entry = _entry("score", 80, "a priority score of {value}", {"out_of": 100})
    assert render_figure(entry) == "a priority score of 80 out of 100"
    assert render_figure(_entry("score", 16, "a risk score of {value}", {"out_of": 25})) == "a risk score of 16 out of 25"
    assert render_figure(_entry("score", 3.2, "{value}", {"out_of": 5})) == "3.2 out of 5"
    assert render_figure(_entry("score", 3.25, "{value}", {"out_of": 5})) == "3.3 out of 5"


@pytest.mark.parametrize("kind,value,decimals,text", [
    ("percent", 50, 0, "50%"),
    ("percent", 66.666, 1, "66.7%"),
    ("ratio", 0.5, 0, "50%"),
    ("ratio", 0.996, 0, "99.6%"),
    ("ratio", 0.004, 0, "0.4%"),
    ("percent", 100, 0, "100%"),
    ("percent", 0, 0, "0%"),
])
def test_a_percent_or_ratio_is_a_percentage_and_never_rounds_to_zero_or_all(kind, value, decimals, text):
    assert render_figure(_entry(kind, value, "{value}", decimals=decimals)) == text


def test_a_duration_carries_its_unit_singular_at_one():
    months = {"unit": "month"}
    assert render_figure(_entry("duration", 18, "{value}", months)) == "18 months"
    assert render_figure(_entry("duration", 1, "{value}", {"unit": "day"})) == "1 day"
    assert render_figure(_entry("duration", 3, "{value}", {"unit": "week"})) == "3 weeks"


def test_a_date_and_a_datetime_read_day_month_year():
    assert render_figure(_entry("date", "2027-03-14", "due on {value}")) == "due on 14 March 2027"
    assert render_figure(_entry("date", dt.date(2027, 3, 4), "{value}")) == "4 March 2027"
    assert render_figure(_entry("datetime", "2027-03-14T09:30:00Z", "{value}", precision="minute")) == (
        "14 March 2027 at 09:30 UTC")
    assert render_figure(_entry("datetime", "2027-03-14T09:30:00Z", "{value}")) == "14 March 2027"


def test_a_year_a_month_and_a_rank_read_as_stored_or_ordinal():
    assert render_figure(_entry("year", 2027, "in {value}")) == "in 2027"
    assert render_figure(_entry("month", "2027-03", "in {value}")) == "in March 2027"
    assert render_figure(_entry("rank", 3, "{value}", {"of": 12})) == "3rd of 12"
    assert render_figure(_entry("rank", 11, "{value}", {"of": 12})) == "11th of 12"
    assert render_figure(_entry("rank", 22, "{value}", {"of": 30})) == "22nd of 30"


def test_a_date_bound_quantity_names_its_window_and_start_date():
    declaration = {"kind": "count", "reads": {"one": "{value} change merged", "other": "{value} changes merged"},
                   "reads_null": "no merges counted",
                   "window": {"field": "window", "start_field": "since",
                              "phrases": {"week": "this week", "30d": "in the last 30 days"}}}
    entry = {"declaration": declaration, "value": 12, "unit": {}, "window": "week", "window_start": "2026-11-09"}
    assert render_figure(entry) == "12 changes merged this week (since 9 November 2026)"
    entry = {"declaration": declaration, "value": 4, "unit": {}, "window": "30d", "window_start": "2026-10-11"}
    assert render_figure(entry) == "4 changes merged in the last 30 days (since 11 October 2026)"


def test_a_missing_value_reads_its_own_words_never_a_dash_or_a_zero():
    entry = _entry("money", None, "an estimated cost of {value}", {"currency": "GBP"})
    assert render_figure(entry) == "nothing recorded"
    assert value_only(entry) == "nothing recorded"
    for text in (render_figure(entry),):
        assert text not in ("—", "-", "N/A", "0", "£0")

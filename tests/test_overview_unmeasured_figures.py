"""The overview never prints 0 for a figure it did not measure.

A printed 0 is indistinguishable from a measured zero; an unmeasured figure
renders as an em dash (the `dash` filter), and a measured zero stays 0.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from flask import render_template_string

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "app/templates/dashboards/overview.html").read_text(encoding="utf-8")
HEALTH = (ROOT / "app/templates/dashboards/health.html").read_text(encoding="utf-8")
DASH = "—"


def _section(start_marker: str, end_marker: str) -> str:
    """The health scorecard's markup from one card comment to the next."""
    start = HEALTH.index(start_marker)
    return HEALTH[start:HEALTH.index(end_marker, start)]


TABLE_MACROS = "{% import 'components/card.html' as card %}"


def _render(app, fragment, **ctx):
    with app.test_request_context("/"):
        return render_template_string(TABLE_MACROS + fragment, **ctx)


@pytest.fixture
def risk_table():
    return _section("{# ── P1: Solution Risk Summary", "{# ── P3: ArchiMate Coverage")


def test_risk_and_arb_counts_show_a_dash_when_the_query_failed(app, risk_table):
    ctx = {"risk_counts": {"critical": None, "high": None, "medium": None, "low": None},
           "arb_pipeline": {"pending": None, "approved": None, "rejected": None,
                            "under_review": None, "deferred": None}}
    html = _render(app, risk_table, **ctx)
    cells = html.split('data-testid="risk-by-impact"', 1)[1]
    assert DASH in cells
    assert ">None<" not in html
    assert ">0<" not in html


def test_risk_and_arb_counts_show_a_dash_when_the_counts_are_missing(app, risk_table):
    html = _render(app, risk_table)
    assert DASH in html and ">0<" not in html


def test_measured_zero_risk_counts_stay_zero(app, risk_table):
    ctx = {"risk_counts": {"critical": 0, "high": 2, "medium": 0, "low": 0},
           "arb_pipeline": {"pending": 0, "approved": 1, "rejected": 0,
                            "under_review": 0, "deferred": 0}}
    html = _render(app, risk_table, **ctx)
    assert ">0<" in "".join(html.split())
    assert DASH not in html


def test_no_figure_in_the_template_falls_back_to_zero():
    assert "|default(0)" not in SOURCE
    assert "else '0'" not in SOURCE
    assert "or {}).get(level, 0)" not in SOURCE and "or {}).get(status_key, 0)" not in SOURCE
    assert "{count:0}" not in SOURCE


def test_a_phase_label_without_a_letter_does_not_break_the_page(app):
    start = HEALTH.index("{% if phase == 'Unclassified' %}")
    line = HEALTH[start: HEALTH.index("{% endif %}", start) + len("{% endif %}")]
    adm = "{% set adm_labels = {'C': 'Info Systems'} %}"
    html = _render(app, adm + "[" + line + "]", phase="Unclassified")
    assert "[Unclassified]" in html
    html = _render(app, adm + "[" + line + "]", phase="C")
    assert "[C: Info Systems]" in html

"""DG-033: the application Cost tab never shows two unexplained totals.

The recorded total cost of ownership (typed or imported) and the sum of the
four component fields are different things. The tab labels each for what it
is and states the difference, shows one total when they agree, and says
"Cost not calculated" once when neither exists. The currency is stated once.
"""

from __future__ import annotations

import re

import pytest

from tests.test_application_detail_shell import _get_detail_html

pytestmark = pytest.mark.usefixtures("db_session")


def _cost_panel(html):
    start = html.index('id="panel-cost"')
    end = html.index('id="panel-dependencies"', start)
    return html[start:end]


def _text(panel):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", panel))


def test_recorded_total_and_partial_components_are_labelled(app, db_session, make_org):
    html, _ = _get_detail_html(
        app, db_session, make_org, "cost-partial",
        total_cost_of_ownership=85000, license_cost_annual=42000,
    )
    text = _text(_cost_panel(html))
    assert "Recorded total cost of ownership" in text
    assert "Sum of recorded components" in text
    assert "85,000" in text and "42,000" in text
    assert "not broken down" in text
    # no bare duplicate total
    assert "Calculated Total" not in text
    assert text.count("Total Cost of Ownership") == 0
    assert text.count("Recorded total cost of ownership") == 1


def test_currency_stated_once(app, db_session, make_org):
    html, _ = _get_detail_html(
        app, db_session, make_org, "cost-currency",
        total_cost_of_ownership=85000, license_cost_annual=42000,
    )
    text = _text(_cost_panel(html))
    assert len(re.findall(r"Amounts in [A-Z]{3}", text)) == 1
    assert "£" not in text and "$" not in text


def test_agreeing_figures_show_one_total(app, db_session, make_org):
    html, _ = _get_detail_html(
        app, db_session, make_org, "cost-agree",
        total_cost_of_ownership=42000, license_cost_annual=42000,
    )
    text = _text(_cost_panel(html))
    assert text.count("Total cost of ownership") == 1
    assert "Sum of recorded components" not in text
    assert "Recorded total cost of ownership" not in text


def test_neither_available_says_not_calculated_once(app, db_session, make_org):
    html, _ = _get_detail_html(app, db_session, make_org, "cost-none")
    text = _text(_cost_panel(html))
    assert text.count("Cost not calculated") == 1
    assert "Total" not in text

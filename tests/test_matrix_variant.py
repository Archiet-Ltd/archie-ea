"""The matrix variant of the dense table: cell states, row headers and the legend."""
from __future__ import annotations

import re

from flask import render_template_string

IMPORT = "{% from 'components/dense_table.html' import dense_table, matrix_row_header, matrix_cell %}"


def _render(app, body, **ctx):
    with app.test_request_context("/"):
        return render_template_string(IMPORT + body, **ctx)


def test_every_filled_cell_state_renders_a_styled_chip_not_an_undefined_class(app):
    for state, value in (("single-value", "1"), ("multiple-count", "3"), ("not-recorded", None)):
        html = _render(app, "{{ matrix_cell(state=s, value=v) }}", s=state, v=value)
        assert f'data-state="{state}"' in html
        assert "badge badge" not in html
        chip = re.search(r'<span class="([^"]*rounded-md border[^"]*)"', html)
        assert chip, f"{state} has no chip"
        assert re.search(r"\bbg-(muted|secondary|warning/10)\b", chip.group(1))


def test_not_recorded_cell_says_so_to_assistive_technology(app):
    html = _render(app, "{{ matrix_cell(state='not-recorded') }}")
    assert '<span class="sr-only">not recorded</span>' in html


def test_empty_cell_is_a_quiet_dot_with_a_spoken_none(app):
    html = _render(app, "{{ matrix_cell(state='empty') }}")
    assert '<span class="sr-only">none</span>' in html
    assert "·" in html


def test_row_header_is_sentence_case_with_normal_tracking(app):
    html = _render(app, "{{ matrix_row_header('Calibration Ledger') }}")
    tag = re.search(r'<th scope="row" class="([^"]*)"', html).group(1)
    assert "normal-case" in tag and "tracking-normal" in tag and "sticky" in tag


def test_default_legend_names_all_four_states_with_their_chips(app):
    html = _render(
        app,
        "{% call dense_table(columns=[('Element', ''), ('Business', '')], rows=1, variant='matrix', "
        "caption='Matrix') %}<tr>{{ matrix_row_header('A') }}{{ matrix_cell(state='single-value', value='1') }}</tr>{% endcall %}",
    )
    legend = html[html.index("matrix-legend"):]
    for words in ("none", "one", "several, with the count", "not recorded"):
        assert words in legend
    assert "badge badge" not in legend


def test_row_headers_outrank_the_legacy_th_rule_in_the_stylesheet():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    legacy_th_rule = "#main-content th {" in (root / "app/static/styles/shadcn-components.css").read_text()
    css = (root / "app/static/css/tailwind-input.css").read_text()
    rule = css[css.index("#main-content .dense-table.matrix tbody th[scope=\"row\"]"):]
    rule = rule[: rule.index("}")]
    assert legacy_th_rule, "the legacy rule this override answers is gone; drop the override with it"
    assert "text-transform: none" in rule and "letter-spacing: normal" in rule

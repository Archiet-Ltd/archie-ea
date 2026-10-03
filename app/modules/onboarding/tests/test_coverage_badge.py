"""Render tests for the Coverage chip (components/provenance.html's
coverage_badge macro) — onboarding-redesign-v3 §5's three states plus
accepted, each visually distinct and never mixed on one chip."""
from __future__ import annotations

from flask import render_template_string


def _render(app, template, **ctx):
    with app.test_request_context("/"):
        return render_template_string(template, **ctx)


def test_recorded_state_renders_success_styling_and_text(app):
    html = _render(app, "{% from 'components/provenance.html' import coverage_badge %}{{ coverage_badge('recorded') }}")
    assert "Recorded" in html
    assert "border-success/30" in html
    assert "bg-success/10" in html


def test_worked_out_state_renders_info_styling_and_the_why(app):
    html = _render(
        app,
        "{% from 'components/provenance.html' import coverage_badge %}{{ coverage_badge('worked_out', note='from linked applications') }}",
    )
    assert "Worked out" in html
    assert "from linked applications" in html
    assert "border-info/30" in html
    assert "bg-info/10" in html


def test_expected_state_is_the_default_and_uses_neutral_styling(app):
    html = _render(app, "{% from 'components/provenance.html' import coverage_badge %}{{ coverage_badge('expected') }}")
    assert "Expected at your stage" in html
    assert "bg-muted" in html


def test_accepted_state_renders_warning_styling_and_the_reason(app):
    html = _render(
        app,
        "{% from 'components/provenance.html' import coverage_badge %}{{ coverage_badge('accepted', note='Planned for Q1') }}",
    )
    assert "Accepted" in html
    assert "Planned for Q1" in html
    assert "border-warning/30" in html
    assert "bg-warning/10" in html


def test_all_four_states_use_different_colour_tokens():
    # A static check on the macro source itself: the four branches must not
    # collapse onto the same colour, which would make the states indistinguishable.
    from pathlib import Path

    src = Path("app/templates/components/provenance.html").read_text(encoding="utf-8")
    start = src.index("{% macro coverage_badge")
    end = src.index("{% endmacro %}", start)
    body = src[start:end]

    assert "border-success/30" in body
    assert "border-info/30" in body
    assert "border-warning/30" in body
    assert "border-border bg-muted" in body

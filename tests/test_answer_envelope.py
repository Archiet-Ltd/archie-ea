"""The answer envelope: source, confidence and as-of on every Tier 1 result.

Renders the answer_envelope macro for each of the five provenance states
and asserts the badge text, the words, and that no state renders an empty
line.
"""

from __future__ import annotations

from flask import render_template_string

import pytest


def _render(app, source=None, kind="measured", confidence=None, as_of=None,
            href=None, label=None, class_=""):
    template_str = (
        "{% from 'components/provenance.html' import answer_envelope %}"
        "{{ answer_envelope("
        "source=src, kind=knd, confidence=conf, as_of=aso, "
        "href=href, label=lbl, class=cls)"
        " }}"
    )
    with app.test_request_context("/"):
        return render_template_string(
            template_str,
            src=source,
            knd=kind,
            conf=confidence,
            aso=as_of,
            href=href,
            lbl=label,
            cls=class_,
        )


def _no_empty_lines(html):
    for line in html.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("{%", "{#", "{{")):
            continue
        assert stripped != "", f"empty content line in rendered envelope: {html!r}"


def test_measured_with_everything(app):
    html = _render(
        app,
        source="Application Component Registry",
        kind="measured",
        confidence=0.85,
        as_of="2026-03-14",
        href="/registry",
    )
    assert "Measured" in html, html
    assert "Application Component Registry" in html, html
    assert 'href="/registry"' in html, html
    assert "hover:underline" in html, html
    assert "as of 2026-03-14" in html, html
    assert "confidence 85%" in html, html
    _no_empty_lines(html)


def test_measured_without_confidence(app):
    html = _render(
        app,
        source="Application Component Registry",
        kind="measured",
        as_of="2026-03-14",
    )
    assert "Measured" in html, html
    assert "Application Component Registry" in html, html
    assert "as of 2026-03-14" in html, html
    assert "confidence" not in html, html
    _no_empty_lines(html)


def test_missing_source(app):
    html = _render(app)
    assert "Not recorded" in html, html
    assert "Source not recorded" in html, html
    assert "as of" not in html, html
    assert "confidence" not in html, html
    _no_empty_lines(html)


def test_ai(app):
    html = _render(app, source="AI Model", kind="ai")
    assert "AI-generated" in html, html
    assert "AI Model" in html, html
    _no_empty_lines(html)


def test_unavailable(app):
    html = _render(app, source="Query", kind="unavailable")
    assert "Could not measure" in html, html
    assert "Query" in html, html
    _no_empty_lines(html)


def test_derived(app):
    """Worked out from the model is its own kind: neither a measurement nor a model's prose."""
    html = _render(app, source="the relationships recorded in the model", kind="derived")
    assert "Worked out" in html, html
    assert "the relationships recorded in the model" in html, html
    assert "AI-generated" not in html and "Measured" not in html, html
    _no_empty_lines(html)


def test_bound_envelope_never_invents_a_source(app):
    """A client-rendered envelope carries both outcomes and lets Alpine show one: the
    source when the expression has a value, "Source not recorded" when it has none. No
    static fallback label may stand in for a source."""
    template_str = (
        "{% from 'components/provenance.html' import answer_envelope %}"
        "{{ answer_envelope(x_source=\"node.source_name || ''\", x_kind=\"node.derived ? 'derived' : 'measured'\","
        " x_as_of=\"node.source_timestamp || ''\", x_confidence=\"node.source_confidence\") }}"
    )
    with app.test_request_context("/"):
        html = render_template_string(template_str)
    # Jinja escapes the quotes inside attribute values (&#39;); the browser reads them back.
    html = html.replace("&#39;", "'").replace("&quot;", '"')
    assert 'x-show="(node.source_name || \'\')"' in html, html
    assert 'x-show="!(node.source_name || \'\')"' in html, html
    assert 'x-text="node.source_name || \'\'"' in html, html
    assert "Source not recorded" in html, html
    assert "Worked out" in html and "Measured" in html, html
    assert "Recorded in the model" not in html, html
    _no_empty_lines(html)


def _render_with_trust(app, trust):
    with app.test_request_context("/"):
        return render_template_string(
            "{% from 'components/provenance.html' import answer_envelope %}"
            "{{ answer_envelope(source='applications.owner', trust=trust) }}",
            trust=trust,
        )


def test_trust_line_renders_the_resolver_sentence_under_the_provenance_line(app):
    trust = {"area": "application", "state": "recorded",
             "line": "From the application layer: 31 of 40 owned, last change 3 days ago"}
    html = _render_with_trust(app, trust)
    assert 'data-slot="trust-meter"' in html
    assert 'data-trust-area="application"' in html
    assert "31 of 40 owned" in html
    assert html.index("applications.owner") < html.index("31 of 40 owned")


def test_trust_line_absent_without_a_trust_record(app):
    assert "trust-meter" not in _render_with_trust(app, None)


def test_empty_area_shows_nothing_recorded_and_no_gauge(app):
    trust = {"area": "capability", "state": "nothing_recorded",
             "line": "From the capability layer: nothing recorded"}
    html = _render_with_trust(app, trust)
    assert 'data-trust-state="nothing_recorded"' in html
    assert "nothing recorded" in html
    for banned in ("progress", "role=\"meter\"", "%</", "gauge"):
        assert banned not in html

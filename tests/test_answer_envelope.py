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

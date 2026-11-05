"""Markup contracts behind brief a11y01 (the browser proof is tests/smoke/test_a11y01_wave1_pages.py).

Each assertion fails on the markup before the change: a progress bar without an accessible name,
evidence links told apart by colour alone, and a connector card that is a button holding buttons.
"""

import re

import pytest


@pytest.fixture(scope="module")
def jinja(app):
    return app.jinja_env


def _render(app, source, **ctx):
    with app.test_request_context("/"):
        return app.jinja_env.from_string(source).render(**ctx)


@pytest.mark.parametrize("call", [
    "progress(value=40, max=100)",
    "progress_dynamic(id='p1')",
    "progress_bound('40')",
])
def test_every_progress_macro_names_its_bar(app, call):
    name = call.split("(")[0]
    html = _render(app, "{% from 'components/progress.html' import " + name + " %}{{ " + call + " }}")
    bar = re.search(r'<div[^>]*role="progressbar"[^>]*>', html, re.S)
    assert bar, html
    assert re.search(r'aria-label="[^"]+"', bar.group(0)), bar.group(0)


def test_a_given_progress_label_is_used(app):
    html = _render(
        app, "{% from 'components/progress.html' import progress %}{{ progress(value=5, label='Work package progress') }}")
    assert 'aria-label="Work package progress"' in html


def test_result_footer_evidence_links_are_underlined(app):
    footer = {
        "confidence": "high", "confidence_reason": "r", "rule": "r", "sources": [], "computed_text": "now",
        "newest_input_text": None,
        "evidence": [{"label": "Ledger", "href": "/applications/1", "missing": False}],
    }
    html = _render(
        app, "{% from 'components/provenance.html' import result_footer %}{{ result_footer(footer) }}", footer=footer)
    link = re.search(r'<a [^>]*href="/applications/1"[^>]*>', html)
    assert link, html
    assert re.search(r'class="[^"]*(?<![:\w-])underline(?![\w-])', link.group(0)), link.group(0)


def test_connector_card_has_one_interactive_target(app):
    source = app.jinja_env.loader.get_source(app.jinja_env, "integrations/partials/_connector_card.html")[0]
    assert 'role="button"' not in source
    # The card's own opening tag carries no role; Test and Sync are not inside a button element.
    assert not re.search(r"<button[^>]*>(?:(?!</button>).)*(?:ui\.button|<a )", source, re.S)
    assert source.count("<button") == 1, "the connector name is the one native button in the card"

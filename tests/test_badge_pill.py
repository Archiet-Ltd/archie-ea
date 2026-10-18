"""The badge macro's pill size, and the homepage hero label that uses it.

The default call must render exactly what it rendered before the pill size was
added; the expected strings below were captured from the previous template.
"""

from __future__ import annotations

import re

import pytest

IMPORT = "{% from 'components/badge.html' import badge %}"

DEFAULT_SUCCESS = (
    '\n    \n\n    <div class="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs '
    "font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring "
    'focus:ring-offset-2 bg-success/10 text-success border-success/30 ">\n        Active\n    </div>\n'
)
DEFAULT_PLAIN = (
    '\n    \n\n    <div class="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs '
    "font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring "
    "focus:ring-offset-2 border-transparent bg-primary text-primary-foreground "
    'hover:bg-primary/80 ">\n        X\n    </div>\n'
)
DEFAULT_WITH_CLASS = (
    '\n    \n\n    <div class="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs '
    "font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring "
    'focus:ring-offset-2 bg-primary/10 text-primary border-primary/30 ml-2">\n        Y\n    </div>\n'
)


@pytest.fixture()
def render(app):
    def _render(body, **ctx):
        return app.jinja_env.from_string(IMPORT + body).render(**ctx)

    return _render


def test_default_badge_is_byte_identical(render):
    assert render("{% call badge('success') %}Active{% endcall %}") == DEFAULT_SUCCESS
    assert render("{% call badge() %}X{% endcall %}") == DEFAULT_PLAIN
    assert render("{% call badge('info', 'ml-2') %}Y{% endcall %}") == DEFAULT_WITH_CLASS


def test_pill_with_href_is_a_link(render):
    html = render("{% call badge(size='pill', href='/pricing') %}Label{% endcall %}")
    assert html.lstrip().startswith('<a href="/pricing" ')
    for token in ("rounded-full", "px-4", "py-1.5", "hover:bg-accent"):
        assert token in html
    assert "<div" not in html
    assert ">Label</a>" in html


def test_pill_without_href_is_a_span(render):
    html = render("{% call badge(size='pill') %}Label{% endcall %}")
    assert "<span " in html
    assert "href" not in html
    assert "hover:bg-accent" not in html
    assert "<div" not in html
    assert "rounded-full" in html


def test_pill_ignores_variant(render):
    html = render("{% call badge('success', size='pill') %}Label{% endcall %}")
    assert "bg-success/10" not in html


def test_pill_href_is_escaped(render):
    html = render("{% call badge(size='pill', href=url) %}Label{% endcall %}", url='/x"><script>')
    assert '"><script>' not in html
    assert "&#34;&gt;&lt;script&gt;" in html


def test_home_hero_uses_the_pill(client):
    html = client.get("/").get_data(as_text=True)
    assert "home-hero__chip" not in html
    assert "Open source &middot; Self-host free" in html
    assert "Open source under AGPL-3.0 &middot; Self-host free, forever" in html
    pill = re.search(r'<span class="[^"]*rounded-full[^"]*">\s*<span class="sm:hidden">Open source', html)
    assert pill, "hero label is not rendered as the pill span"
    assert "px-4" in pill.group(0) and "py-1.5" in pill.group(0)

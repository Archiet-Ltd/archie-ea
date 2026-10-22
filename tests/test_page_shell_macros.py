"""Render tests for the screen-system macros (macros/page_shell.html).

These four macros — page_shell, empty_state, stat_card, section_card — become
the only sanctioned page header / empty state / stat card / section container
for every screen rebuilt in docs/superpowers/plans/2026-08-12-shell-wave-1.md.
Later tasks in that plan consume these exact signatures, so these tests pin
both the signature and the rendered shape (single <h1>, breadcrumb nav, dash
for missing values, variant sizing) that those callers rely on.
"""

from __future__ import annotations

import re

from flask import render_template_string


def _render(app, template, **ctx):
    with app.test_request_context("/"):
        return render_template_string(template, **ctx)


def test_page_shell_renders_single_h1_and_breadcrumb_nav(app):
    html = _render(
        app,
        """
        {% from 'macros/page_shell.html' import page_shell %}
        {{ page_shell('Applications', subtitle='Portfolio', breadcrumb=[('Home', '/'), ('Applications', None)]) }}
        """,
    )
    assert html.count("<h1") == 1
    assert "Applications" in html
    assert re.search(r'<h1[^>]*>\s*Applications', html)
    assert html.count('<nav aria-label="Breadcrumb"') == 1


def test_page_shell_actions_and_h1_share_flex_row(app):
    html = _render(
        app,
        """
        {% from 'macros/page_shell.html' import page_shell %}
        {% macro actions() %}<a href="/new">New</a>{% endmacro %}
        {{ page_shell('Applications', actions_caller=actions) }}
        """,
    )
    # The h1 and the actions block must both live inside the same header row
    # container. Below xl (1280px) it stacks (flex-col) so actions get their
    # own full-width row and can wrap; at xl+ it goes side-by-side, matching
    # the pre-responsive-fix layout exactly.
    row_match = re.search(
        r'<div class="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">(.*?)</div>\s*'
        r'(?:<ul|\{%|$)',
        html,
        re.DOTALL,
    )
    assert row_match, html
    row_html = row_match.group(1)
    assert "<h1" in row_html
    assert 'href="/new"' in row_html


def test_page_shell_tabs_render(app):
    html = _render(
        app,
        """
        {% from 'macros/page_shell.html' import page_shell %}
        {{ page_shell('Applications', tabs=[('Overview', '/a', True), ('Detail', '/b', False)]) }}
        """,
    )
    assert 'href="/a"' in html
    assert 'href="/b"' in html
    assert 'aria-current="page"' in html


def test_empty_state_with_cta_renders_one_link(app):
    html = _render(
        app,
        """
        {% from 'macros/page_shell.html' import empty_state %}
        {{ empty_state('inbox', 'No applications yet', 'Get started by adding one.', cta_label='Add application', cta_href='/applications/new') }}
        """,
    )
    assert html.count("<a ") == 1
    assert 'href="/applications/new"' in html
    assert "Add application" in html
    assert "No applications yet" in html


def test_empty_state_without_cta_renders_no_link(app):
    html = _render(
        app,
        """
        {% from 'macros/page_shell.html' import empty_state %}
        {{ empty_state('inbox', 'No applications yet', 'Get started by adding one.') }}
        """,
    )
    assert html.count("<a ") == 0


def test_stat_card_none_value_renders_dash_not_zero(app):
    html = _render(
        app,
        """
        {% from 'macros/page_shell.html' import stat_card %}
        {{ stat_card('Health Score', None) }}
        """,
    )
    assert "—" in html
    assert ">0<" not in html


def test_stat_card_hero_variant_uses_larger_text_class_than_standard(app):
    hero_html = _render(
        app,
        """
        {% from 'macros/page_shell.html' import stat_card %}
        {{ stat_card('Health Score', 92, variant='hero') }}
        """,
    )
    standard_html = _render(
        app,
        """
        {% from 'macros/page_shell.html' import stat_card %}
        {{ stat_card('Health Score', 92, variant='standard') }}
        """,
    )
    assert "text-3xl" in hero_html
    assert "text-3xl" not in standard_html
    assert "text-2xl" in standard_html


def test_stat_card_warning_variant_emits_warning_tokens_standard_does_not(app):
    warning_html = _render(
        app,
        """
        {% from 'macros/page_shell.html' import stat_card %}
        {{ stat_card('Waiting Approval', 3, hint='Review the queue', variant='warning') }}
        """,
    )
    standard_html = _render(
        app,
        """
        {% from 'macros/page_shell.html' import stat_card %}
        {{ stat_card('Waiting Approval', 3, hint='Review the queue', variant='standard') }}
        """,
    )
    assert "bg-warning/10" in warning_html
    assert "border-warning/40" in warning_html
    assert "text-warning-emphasis" in warning_html
    assert "bg-warning/10" not in standard_html
    assert "border-warning/40" not in standard_html
    assert "text-warning-emphasis" not in standard_html
    # warning reuses 'standard' text sizing, not its own scale
    assert "text-2xl" in warning_html


def test_section_card_body_via_caller(app):
    html = _render(
        app,
        """
        {% from 'macros/page_shell.html' import section_card %}
        {% call section_card(title='Recent activity') %}
        <p>Nothing yet.</p>
        {% endcall %}
        """,
    )
    assert "Recent activity" in html
    assert "Nothing yet." in html


def test_page_shell_without_new_slots_is_unchanged(app):
    """icon / subtitle_caller / meta_caller are additive: omitting them must
    render exactly the pre-existing shape (bare <h1>, no extra wrappers)."""
    html = _render(
        app,
        """
        {% from 'macros/page_shell.html' import page_shell %}
        {{ page_shell('Applications', subtitle='Portfolio') }}
        """,
    )
    assert '<h1 class="text-3xl font-bold tracking-[-0.02em] text-foreground">Applications</h1>' in html
    assert '<p class="mt-1 text-sm text-muted-foreground">Portfolio</p>' in html
    assert "data-lucide" not in html


def test_page_shell_icon_sits_beside_the_title(app):
    html = _render(
        app,
        """
        {% from 'macros/page_shell.html' import page_shell %}
        {{ page_shell('Capability Hierarchy', icon='git-branch') }}
        """,
    )
    assert html.count("<h1") == 1
    assert re.search(
        r'data-lucide="git-branch".*?<h1[^>]*>\s*Capability Hierarchy',
        html,
        re.S,
    )


def test_page_shell_subtitle_caller_wins_over_subtitle_and_keeps_markup(app):
    html = _render(
        app,
        """
        {% from 'macros/page_shell.html' import page_shell %}
        {% macro sub() %}<span x-text="count">—</span> shown{% endmacro %}
        {{ page_shell('Trees', subtitle='plain', subtitle_caller=sub) }}
        """,
    )
    assert '<span x-text="count">—</span> shown' in html
    assert "plain" not in html


def test_page_shell_meta_caller_renders_below_the_title(app):
    html = _render(
        app,
        """
        {% from 'macros/page_shell.html' import page_shell %}
        {% macro meta() %}<span class="badge">Active</span>{% endmacro %}
        {{ page_shell('Element', meta_caller=meta) }}
        """,
    )
    assert re.search(r'<h1[^>]*>\s*Element\s*</h1>.*?<span class="badge">Active</span>', html, re.S)

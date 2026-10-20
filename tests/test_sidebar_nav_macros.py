"""The sidebar navigation macros: group, item, collapsible, sub-item.

Renders components/sidebar_nav.html for each shape the pattern allows and
asserts the structure the design names: two levels, a heading per group,
aria-current on the active link, a right-aligned count or pending badge,
a chevron toggle with aria-expanded and aria-controls, persisted open state,
and nothing the north star bans (shadows, raw colours).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from flask import render_template_string

IMPORT = (
    "{% from 'components/sidebar_nav.html' import sidebar_group, sidebar_item, "
    "sidebar_collapsible, sidebar_subitem %}"
)

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"


def _render(app, body, **ctx):
    with app.test_request_context("/"):
        return render_template_string(IMPORT + body, **ctx)


def _attr(html, name):
    m = re.search(r'%s="([^"]*)"' % re.escape(name), html)
    return m.group(1) if m else None


def test_group_renders_heading_and_call_block(app):
    html = _render(
        app,
        "{% call sidebar_group('Model') %}"
        "{{ sidebar_item('/applications', 'Applications', icon='layers') }}"
        "{% endcall %}",
    )
    assert 'data-slot="sidebar-group"' in html
    assert 'role="heading" aria-level="3"' in html
    assert ">Model<" in html
    assert 'data-slot="sidebar-item"' in html
    assert 'href="/applications"' in html
    assert 'data-lucide="layers"' in html


def test_item_active_carries_aria_current_and_the_muted_surface(app):
    active = _render(app, "{{ sidebar_item('/a', 'A', active=True) }}")
    idle = _render(app, "{{ sidebar_item('/a', 'A', active=False) }}")
    assert 'aria-current="page"' in active
    assert "bg-muted" in active and "text-info" in active
    assert 'aria-current' not in idle
    assert "text-muted-foreground" in idle


def test_item_count_is_right_aligned_and_tabular(app):
    html = _render(app, "{{ sidebar_item('/a', 'A', count=42) }}")
    assert 'data-slot="sidebar-count"' in html
    assert re.search(r'class="ml-auto[^"]*tabular-nums[^"]*"[^>]*data-slot="sidebar-count"[^>]*>42<', html)


def test_pending_badge_wins_over_count_and_is_labelled(app):
    html = _render(app, "{{ sidebar_item('/a', 'A', count=42, pending=3) }}")
    assert 'data-slot="sidebar-pending"' in html
    assert 'aria-label="3 pending"' in html
    assert 'data-slot="sidebar-count"' not in html
    assert "bg-primary" in html and "text-primary-foreground" in html


def test_no_badge_when_nothing_to_show(app):
    html = _render(app, "{{ sidebar_item('/a', 'A') }}")
    assert "sidebar-count" not in html and "sidebar-pending" not in html


def test_collapsible_toggle_controls_its_sublist(app):
    html = _render(
        app,
        "{% call sidebar_collapsible('catalogues', 'Catalogues', icon='library', count=6) %}"
        "{{ sidebar_subitem('/catalogues', 'All catalogues') }}"
        "{{ sidebar_subitem('/catalogues/application', 'Application', pending=2) }}"
        "{% endcall %}",
    )
    assert 'data-slot="sidebar-collapsible"' in html
    assert 'data-sidebar-group-id="catalogues"' in html
    assert "sidebarCollapsible('catalogues', false, false)" in html
    assert '<button type="button"' in html
    assert 'aria-controls="sidebar-sub-catalogues"' in html
    assert 'id="sidebar-sub-catalogues"' in html
    assert 'aria-expanded="false"' in html
    assert ':aria-expanded="open' in html
    assert 'data-lucide="chevron-right"' in html
    assert "rotate-90" in html
    # closed by default: the sub-list is cloaked until Alpine applies x-show
    sub = html[html.index('id="sidebar-sub-catalogues"') :]
    assert "x-cloak" in sub.split(">", 1)[0]
    assert html.count('data-slot="sidebar-subitem"') == 2
    assert 'aria-label="2 pending"' in html


def test_collapsible_open_or_active_renders_expanded(app):
    opened = _render(
        app,
        "{% call sidebar_collapsible('g', 'G', open=True) %}"
        "{{ sidebar_subitem('/x', 'X') }}{% endcall %}",
    )
    active = _render(
        app,
        "{% call sidebar_collapsible('g', 'G', active=True) %}"
        "{{ sidebar_subitem('/x', 'X', active=True) }}{% endcall %}",
    )
    for html in (opened, active):
        assert 'aria-expanded="true"' in html
        head = html[html.index('id="sidebar-sub-g"') :].split(">", 1)[0]
        assert "x-cloak" not in head
    assert "sidebarCollapsible('g', true, false)" in opened
    assert "sidebarCollapsible('g', false, true)" in active
    # an active parent is marked, and its active child carries aria-current
    assert "text-foreground font-medium" in active
    assert 'aria-current="page"' in active


def test_subitem_is_indented_and_shorter_than_a_level_one_item(app):
    item = _render(app, "{{ sidebar_item('/a', 'A') }}")
    sub = _render(app, "{{ sidebar_subitem('/a/b', 'B') }}")
    assert "h-8" in item and "px-2" in item
    assert "h-7" in sub and "pl-8" in sub
    assert "data-lucide" not in sub


@pytest.mark.parametrize(
    "body",
    [
        "{% call sidebar_group('Model') %}{{ sidebar_item('/a', 'A', icon='box', count=1) }}{% endcall %}",
        "{% call sidebar_collapsible('g', 'G', icon='box', pending=4) %}{{ sidebar_subitem('/a', 'A', count=2) }}{% endcall %}",
    ],
)
def test_nothing_banned_in_the_markup(app, body):
    html = _render(app, body)
    assert "shadow" not in html
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", html)
    assert "style=" not in html
    assert "bg-slate-" not in html and "text-gray-" not in html


def test_admin_sidebar_renders_zones_through_the_macros():
    src = (TEMPLATES / "components" / "admin_sidebar.html").read_text(encoding="utf-8")
    assert "from 'components/sidebar_nav.html' import" in src
    assert "sidebar_group(" in src and "sidebar_item(" in src
    # the zone heading and the level-1 link exist once, in the macro file
    assert 'role="heading" aria-level="3"' not in src
    assert "js/sidebar/collapsible.js" in src


def test_persistence_component_reads_and_writes_one_key():
    js = (TEMPLATES.parent / "static" / "js" / "sidebar" / "collapsible.js").read_text(encoding="utf-8")
    assert "archie_sidebar_group_" in js
    assert "Alpine.data('sidebarCollapsible'" in js
    assert "localStorage.getItem" in js and "localStorage.setItem" in js
    # opening one never closes another: no cross-item state
    assert "closeOthers" not in js and "accordion" not in js.lower()

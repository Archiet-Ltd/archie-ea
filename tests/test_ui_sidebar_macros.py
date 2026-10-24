"""The sidebar parts: wrapper, sidebar, menu, menu button, sub-menu, trigger.

Renders components/ui_sidebar.html (the library's sidebar-07 parts) for each shape the
shell uses and asserts what a person and the scripts rely on: the state the sidebar
carries for its children, a link or a button with the current page marked, the
icon-rail tooltip text, the drawer-friendly slots, and nothing the design bans
(shadows, raw colours).
"""

from __future__ import annotations

import re
from pathlib import Path

from flask import render_template_string

IMPORT = (
    "{% from 'components/ui_sidebar.html' import sidebar_wrapper, sidebar, sidebar_menu, "
    "sidebar_menu_item, sidebar_menu_button, sidebar_menu_sub, sidebar_menu_sub_item, "
    "sidebar_menu_sub_button, sidebar_group, sidebar_group_label, sidebar_trigger, "
    "sidebar_rail, sidebar_inset %}"
)

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"


def _render(app, body, **ctx):
    with app.test_request_context("/"):
        return render_template_string(IMPORT + body, **ctx)


def _attr(html, name):
    m = re.search(r'%s="([^"]*)"' % re.escape(name), html)
    return m.group(1) if m else None


def test_sidebar_carries_the_state_its_children_read(app):
    expanded = _render(app, "{{ sidebar(collapsible='icon') }}")
    collapsed = _render(app, "{{ sidebar(collapsible='icon', state='collapsed') }}")
    assert _attr(expanded, "data-state") == "expanded"
    assert _attr(expanded, "data-collapsible") == ""
    assert _attr(expanded, "data-collapsible-mode") == "icon"
    assert _attr(collapsed, "data-state") == "collapsed"
    assert _attr(collapsed, "data-collapsible") == "icon"
    assert 'data-slot="sidebar"' in expanded and "group peer" in expanded


def test_sidebar_can_render_as_the_aside_landmark(app):
    html = _render(app, "{{ sidebar(tag='aside', attrs={'aria-label': 'Sidebar'}) }}")
    assert html.lstrip().startswith("<aside")
    assert 'aria-label="Sidebar"' in html


def test_wrapper_renders_its_call_block_and_the_widths_live_in_the_stylesheet(app):
    html = _render(app, "{% call sidebar_wrapper() %}x{% endcall %}")
    assert 'data-slot="sidebar-wrapper"' in html and ">x<" in html
    assert "style=" not in html
    css = (TEMPLATES.parent / "static" / "styles" / "sidebar-shell.css").read_text(encoding="utf-8")
    assert "--sidebar-width: 16rem" in css and "--sidebar-width-icon: 3rem" in css


def test_menu_button_link_marks_the_current_page_and_names_itself_for_the_rail(app):
    current = _render(app, "{% call sidebar_menu_button(href='/applications', active=True, tooltip='Systems') %}Systems{% endcall %}")
    other = _render(app, "{% call sidebar_menu_button(href='/applications', tooltip='Systems') %}Systems{% endcall %}")
    assert current.lstrip().startswith("<a ")
    assert 'href="/applications"' in current
    assert 'aria-current="page"' in current and 'data-active="true"' in current
    assert 'data-tooltip="Systems"' in current
    assert "aria-current" not in other and 'data-active="false"' in other


def test_menu_button_without_an_address_is_a_button(app):
    html = _render(app, "{% call sidebar_menu_button() %}Open{% endcall %}")
    assert html.lstrip().startswith("<button") and 'type="button"' in html


def test_sub_menu_is_hidden_in_the_icon_rail_and_marks_its_current_page(app):
    html = _render(
        app,
        "{% call sidebar_menu_sub() %}{% call sidebar_menu_sub_item() %}"
        "{% call sidebar_menu_sub_button(href='/p', active=True) %}Procurement{% endcall %}"
        "{% endcall %}{% endcall %}",
    )
    assert "group-data-[collapsible=icon]:hidden" in html
    assert 'data-slot="sidebar-menu-sub-button"' in html
    assert 'aria-current="page"' in html and 'href="/p"' in html


def test_group_label_is_a_level_three_heading(app):
    html = _render(app, "{{ sidebar_group_label(text='Architecture') }}")
    assert 'role="heading"' in html and 'aria-level="3"' in html and ">Architecture<" in html


def test_trigger_is_a_named_button_with_the_shortcut_in_its_title(app):
    html = _render(app, "{{ sidebar_trigger(testid='sidebar-trigger') }}")
    assert 'data-slot="sidebar-trigger"' in html
    assert 'aria-label="Toggle sidebar"' in html and "Ctrl+B" in html
    assert 'data-testid="sidebar-trigger"' in html


def test_rail_is_a_button_named_toggle_sidebar(app):
    html = _render(app, "{{ sidebar_rail() }}")
    assert html.lstrip().startswith("<button") and 'aria-label="Toggle sidebar"' in html


def test_the_parts_carry_no_shadow_or_raw_colour(app):
    source = (TEMPLATES / "components" / "ui_sidebar.html").read_text(encoding="utf-8")
    assert not re.search(r"\bshadow(-|\b)", source)
    assert not re.search(r"\b(?:bg|text|border)-(?:gray|slate|zinc|neutral|stone|blue|red|white|black)\b", source)
    assert "style=" not in source and "'style'" not in source

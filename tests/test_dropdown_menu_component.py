"""Accessibility tests for the dropdown_menu component.

The data_action branch must not override the visible text with a hardcoded
aria-label, so screen readers announce the actual action text.
"""

from __future__ import annotations

import re

from flask import render_template_string


def _render(app, template, **ctx):
    with app.test_request_context("/"):
        return render_template_string(template, **ctx)


def test_data_action_item_accessible_name_equals_label(app):
    """A data_action menu item must use its visible text as the accessible name."""
    html = _render(
        app,
        """
        {% from 'components/dropdown_menu.html' import dropdown_menu %}
        {{ dropdown_menu(
            trigger_text='More',
            items=[
                {'text': 'New from Template', 'icon': 'layout-template', 'data_action': 'open-template-modal'},
            ]
        ) }}
        """,
    )
    # Find the data_action button and verify it has no aria-label
    button_match = re.search(
        r'<button[^>]*data-action="open-template-modal"[^>]*>',
        html,
    )
    assert button_match is not None, "data_action button must be present"
    button_tag = button_match.group(0)
    assert 'aria-label' not in button_tag, (
        "data_action button must not have aria-label; "
        "visible text 'New from Template' provides the accessible name"
    )
    # The button must contain the item's visible text
    assert "New from Template" in html
    # The button must carry the data-action attribute
    assert 'data-action="open-template-modal"' in html
    # The button must be a menuitem
    assert 'role="menuitem"' in html


def test_data_action_item_has_no_aria_label_at_all(app):
    """A data_action menu item with visible text needs no aria-label."""
    html = _render(
        app,
        """
        {% from 'components/dropdown_menu.html' import dropdown_menu %}
        {{ dropdown_menu(
            trigger_text='Actions',
            items=[
                {'text': 'Export CSV', 'data_action': 'export-csv'},
            ]
        ) }}
        """,
    )
    # Find the data_action button and verify it has no aria-label
    button_match = re.search(
        r'<button[^>]*data-action="export-csv"[^>]*>',
        html,
    )
    assert button_match is not None, "data_action button must be present"
    button_tag = button_match.group(0)
    assert 'aria-label' not in button_tag, (
        "data_action button must not have aria-label; "
        "visible text 'Export CSV' provides the accessible name"
    )
    assert "Export CSV" in html

_MENU = """
{% from 'components/dropdown_menu.html' import dropdown_menu %}
{{ dropdown_menu(id='m', trigger_text='More', items=[{'text': 'Do it', 'onclick': 'go()'}]__EXTRA__) }}
"""


def test_menu_scales_and_fades_unless_asked_to_only_fade(app):
    """Every existing caller keeps the scale-and-fade; a toolbar asks for opacity only."""
    default = _render(app, _MENU.replace("__EXTRA__", ""))
    assert "scale-95" in default and "x-transition:enter=" in default

    fade_only = _render(app, _MENU.replace("__EXTRA__", ", transition='opacity'"))
    assert "x-transition.opacity" in fade_only
    assert "scale-95" not in fade_only and "x-transition:enter=" not in fade_only

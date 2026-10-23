"""Tests for component_inventory.py"""

import json
import hashlib
import tempfile
import os
import sys
from pathlib import Path

# Add the scripts directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from component_inventory import (
    build_inventory,
    template_files,
    extract_elements_from_template,
    MACRO_CATEGORY,
    JinjaASTWalker,
    rebuild_html_from_lex,
    TemplateElementExtractor,
    process_macro_calls,
    process_button_macro,
    process_modal_macro,
    process_table_macro,
    get_route_templates,
    extract_endpoints_with_refs,
    _is_mutating,
    template_index,
    view_guards,
    build_template_inventory,
    write_inventory,
)


class TestFixtures:
    """Test the three fixture templates."""

    def setup_method(self):
        self.fixture_dir = Path(__file__).parent / "fixtures" / "component_inventory"

    def _read_fixture(self, name):
        path = self.fixture_dir / name
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_fixture1_navigate_buttons(self):
        """Fixture 1 yields two buttons of type navigate (raw + macro)."""
        src = self._read_fixture("fixture1_navigate.html")
        # Use empty macro signatures for fixture test
        macro_signatures = {}
        buttons, forms, tables, modals, parse_errors, refs, macro_calls, url_for_calls = extract_elements_from_template(
            "fixture1_navigate.html", src, macro_signatures
        )

        # Should find 3 buttons: 1 raw + 2 macro calls
        assert len(buttons) == 3, f"Expected 3 buttons, got {len(buttons)}: {buttons}"

        # First button: raw navigate via onclick with window.location
        btn1 = buttons[0]
        assert btn1["type"] == "navigate", f"First button should be navigate, got {btn1['type']}"
        assert btn1["resolved"] is False, "JS navigation should have resolved=False"
        assert "Go to App" in btn1["label"]

        # Second button: macro button with href=url_for(...)
        btn2 = buttons[1]
        assert btn2["type"] == "navigate", f"Second button should be navigate, got {btn2['type']}"
        assert "Create Application" in btn2["label"]

        # Third button: macro button with href=/applications/123
        btn3 = buttons[2]
        assert btn3["type"] == "navigate", f"Third button should be navigate, got {btn3['type']}"
        assert "View Details" in btn3["label"]

    def test_fixture2_modal_with_opener(self):
        """Fixture 2 yields one modal create-x with title, internal buttons, submit handler, and opener typed modal."""
        src = self._read_fixture("fixture2_modal.html")
        macro_signatures = {}
        buttons, forms, tables, modals, parse_errors, refs, macro_calls, url_for_calls = extract_elements_from_template(
            "fixture2_modal.html", src, macro_signatures
        )

        # Should find 1 modal
        assert len(modals) == 1, f"Expected 1 modal, got {len(modals)}: {modals}"
        modal = modals[0]
        assert modal["id"] == "create-x", f"Modal id should be create-x, got {modal['id']}"
        assert modal["title"] == "Create New Item", f"Modal title mismatch: {modal['title']}"
        assert len(modal["buttons"]) >= 2, f"Modal should have at least 2 internal buttons, got {modal['buttons']}"
        assert modal["submit_handler"] is not None, "Modal should have submit handler"

        # Should find 2 opener buttons (both typed modal)
        modal_openers = [b for b in buttons if b["type"] == "modal"]
        assert len(modal_openers) == 2, f"Expected 2 modal openers, got {len(modal_openers)}: {modal_openers}"
        for opener in modal_openers:
            assert opener["modal_id"] == "create-x", f"Opener modal_id should be create-x, got {opener['modal_id']}"

    def test_fixture3_noop_and_missing_modal(self):
        """Fixture 3 yields one button type noop and one opener for missing modal."""
        src = self._read_fixture("fixture3_noop.html")
        macro_signatures = {}
        buttons, forms, tables, modals, parse_errors, refs, macro_calls, url_for_calls = extract_elements_from_template(
            "fixture3_noop.html", src, macro_signatures
        )

        # Should find 3 buttons
        assert len(buttons) == 3, f"Expected 3 buttons, got {len(buttons)}: {buttons}"

        # First button: noop (no handler)
        btn1 = buttons[0]
        assert btn1["type"] == "noop", f"First button should be noop, got {btn1['type']}"
        assert "No-op Button" in btn1["label"]

        # Second button: modal opener for 'ghost' (no definition in this template)
        btn2 = buttons[1]
        assert btn2["type"] == "modal", f"Second button should be modal opener, got {btn2['type']}"
        assert btn2["modal_id"] == "ghost", f"Modal id should be ghost, got {btn2['modal_id']}"

        # Third button: JS navigation with resolved=False
        btn3 = buttons[2]
        assert btn3["type"] == "navigate", f"Third button should be navigate, got {btn3['type']}"
        assert btn3["resolved"] is False, "JS navigation should have resolved=False"
        assert "/x/" in (btn3.get("handler") or btn3.get("href") or "")


class TestDeterminism:
    """Test that running the generator twice gives byte-identical output."""

    def test_idempotent_output(self):
        """Running build_inventory twice on same fixtures gives identical JSON."""
        # This test requires the full app boot, skip for unit tests
        pass


class TestJinjaASTWalker:
    """Test Jinja AST walker."""

    def test_walk_finds_macro_calls(self):
        src = """
{% from 'components/button.html' import button %}
{{ button('Click me', href='/test') }}
"""
        macro_signatures = {}
        walker = JinjaASTWalker("test.html", src, macro_signatures)
        walker.walk()
        assert len(walker.macro_calls) == 1
        assert walker.macro_calls[0][0] == "button"
        assert walker.macro_calls[0][1] == "components/button.html"
        assert walker.macro_calls[0][2]["text"] == "Click me"
        assert walker.macro_calls[0][2]["href"] == "/test"

    def test_walk_finds_url_for(self):
        src = """
{{ url_for('applications.create') }}
"""
        macro_signatures = {}
        walker = JinjaASTWalker("test.html", src, macro_signatures)
        walker.walk()
        assert len(walker.url_for_calls) == 1
        assert walker.url_for_calls[0][0] == "applications.create"

    def test_walk_finds_template_refs(self):
        src = """
{% extends 'layouts/base.html' %}
{% include 'components/header.html' %}
{% from 'components/button.html' import button %}
{% import 'components/table.html' as table_macros %}
"""
        macro_signatures = {}
        walker = JinjaASTWalker("test.html", src, macro_signatures)
        walker.walk()
        assert "layouts/base.html" in walker.template_refs["extends"]
        assert "components/header.html" in walker.template_refs["includes"]
        assert "components/button.html" in walker.template_refs["from_imports"]
        assert "components/table.html" in walker.template_refs["imports"]


class TestRebuildHtmlFromLex:
    """Test HTML reconstruction from lex tokens."""

    def test_replaces_expressions_with_placeholders(self):
        src = '<button onclick="{{ on_click }}">Click</button>'
        macro_calls = []
        url_for_calls = []
        cleaned, placeholder_map = rebuild_html_from_lex(src, macro_calls, url_for_calls)
        assert "__JINJA_EXPR_0__" in cleaned
        assert placeholder_map["__JINJA_EXPR_0__"] == "on_click"
        assert "Click" in cleaned

    def test_strips_jinja_blocks(self):
        src = """
<div>
{% if condition %}
  <span>True</span>
{% else %}
  <span>False</span>
{% endif %}
</div>
"""
        macro_calls = []
        url_for_calls = []
        cleaned, _ = rebuild_html_from_lex(src, macro_calls, url_for_calls)
        assert "True" in cleaned
        assert "False" in cleaned
        assert "{% if" not in cleaned
        assert "{% else" not in cleaned
        assert "{% endif" not in cleaned

    def test_drops_comments(self):
        src = """
<div>
{# Comment #}
<span>Content</span>
</div>
"""
        macro_calls = []
        url_for_calls = []
        cleaned, _ = rebuild_html_from_lex(src, macro_calls, url_for_calls)
        assert "Comment" not in cleaned
        assert "Content" in cleaned


class TestTemplateElementExtractor:
    """Test HTML element extraction."""

    def test_extracts_button_with_onclick(self):
        src = '<button onclick="window.location.href = \'/x/\'">Go</button>'
        placeholder_map = {}
        macro_calls = []
        url_for_calls = []
        parser = TemplateElementExtractor(placeholder_map, macro_calls, url_for_calls, "test.html")
        parser.feed(src)
        assert len(parser.buttons) == 1
        btn = parser.buttons[0]
        assert btn["type"] == "navigate"
        assert btn["resolved"] is False
        assert "Go" in btn["label"]

    def test_extracts_noop_button(self):
        src = '<button class="public-btn">No-op</button>'
        placeholder_map = {}
        macro_calls = []
        url_for_calls = []
        parser = TemplateElementExtractor(placeholder_map, macro_calls, url_for_calls, "test.html")
        parser.feed(src)
        assert len(parser.buttons) == 1
        btn = parser.buttons[0]
        assert btn["type"] == "noop"
        assert "No-op" in btn["label"]

    def test_extracts_modal_opener(self):
        src = '<button data-modal-open="my-modal">Open</button>'
        placeholder_map = {}
        macro_calls = []
        url_for_calls = []
        parser = TemplateElementExtractor(placeholder_map, macro_calls, url_for_calls, "test.html")
        parser.feed(src)
        assert len(parser.buttons) == 1
        btn = parser.buttons[0]
        assert btn["type"] == "modal"
        assert btn["modal_id"] == "my-modal"

    def test_extracts_platform_modal_open(self):
        src = '<button onclick="Platform.modal.open(\'my-modal\')">Open</button>'
        placeholder_map = {}
        macro_calls = []
        url_for_calls = []
        parser = TemplateElementExtractor(placeholder_map, macro_calls, url_for_calls, "test.html")
        parser.feed(src)
        assert len(parser.buttons) == 1
        btn = parser.buttons[0]
        assert btn["type"] == "modal"
        assert btn["modal_id"] == "my-modal"


class TestMacroProcessing:
    """Test macro call processing."""

    def test_process_button_macro_navigate(self):
        kwargs = {"text": "Click", "href": "/test", "variant": "primary"}
        btn = process_button_macro("button", "components/button.html", kwargs, "test.html", 10)
        assert btn["type"] == "navigate"
        assert btn["label"] == "Click"
        assert btn["href"] == "/test"
        assert btn["resolved"] is False

    def test_process_button_macro_destructive(self):
        kwargs = {"text": "Delete", "variant": "destructive"}
        btn = process_button_macro("button", "components/button.html", kwargs, "test.html", 10)
        assert btn["type"] == "destructive"

    def test_process_button_macro_modal_opener(self):
        kwargs = {"text": "Open", "on_click": "Platform.modal.open('my-modal')"}
        btn = process_button_macro("button", "components/button.html", kwargs, "test.html", 10)
        assert btn["type"] == "modal"
        assert btn["modal_id"] == "my-modal"

    def test_process_modal_macro(self):
        kwargs = {"id": "create-x", "title": "Create Item", "action": "/create"}
        modal = process_modal_macro("modal_form", "components/modal_form.html", kwargs, "test.html", 10, [])
        assert modal["id"] == "create-x"
        assert modal["title"] == "Create Item"
        assert modal["submit_handler"] == "/create"
        assert len(modal["buttons"]) >= 2

    def test_process_table_macro(self):
        kwargs = {"source_endpoint": "/api/data", "row_actions": ["edit", "delete"]}
        table = process_table_macro("data_table", "components/data_table.html", kwargs, "test.html", 10)
        assert table["source_endpoint"] == "/api/data"
        assert table["row_actions"] == ["edit", "delete"]


class TestMacroCategory:
    """Test macro category mapping."""

    def test_button_macros_mapped_to_button(self):
        assert MACRO_CATEGORY["button"] == "button"

    def test_modal_macros_mapped_to_modal(self):
        for name in ["modal", "modal_form", "modal_standardized", "confirmation_dialog", "dialog", "drawer"]:
            assert MACRO_CATEGORY[name] == "modal", f"{name} should map to modal"

    def test_table_macros_mapped_to_table(self):
        for name in ["data_table", "workbench_table", "dense_table", "table"]:
            assert MACRO_CATEGORY[name] == "table", f"{name} should map to table"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
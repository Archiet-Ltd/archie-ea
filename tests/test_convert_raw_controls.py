"""Unit tests for scripts/qa/convert_raw_controls.py.

Tests the deterministic converter that rewrites hand-written HTML controls
into component macros.  Each test case proves a specific control type,
attribute pass-through, or skip condition.

Run:
    python -m pytest tests/test_convert_raw_controls.py -q --timeout=30
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Import the converter module
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.qa import convert_raw_controls as crc

# Use a temp dir inside the project root so relative_to works
TEST_TMP = Path(__file__).resolve().parent / "_convert_tmp"


def setup_module():
    TEST_TMP.mkdir(exist_ok=True)


def teardown_module():
    import shutil
    shutil.rmtree(TEST_TMP, ignore_errors=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _convert_text(text: str) -> tuple[str, list, list]:
    """Feed a string through the converter as if it were a file.

    Returns (output_text, converted, skipped).
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, dir=str(TEST_TMP)
    ) as f:
        f.write(text)
        tmp = Path(f.name)

    try:
        result = crc.convert_file(tmp, dry_run=False)
        output = tmp.read_text(encoding="utf-8")
    finally:
        tmp.unlink()

    return output, result["converted"], result["skipped"]


def _dry_run(text: str) -> tuple[str, list, list]:
    """Dry-run the converter without modifying anything."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, dir=str(TEST_TMP)
    ) as f:
        f.write(text)
        tmp = Path(f.name)

    try:
        result = crc.convert_file(tmp, dry_run=True)
        output = tmp.read_text(encoding="utf-8")
    finally:
        tmp.unlink()

    return output, result["converted"], result["skipped"]


# ---------------------------------------------------------------------------
# Button tests
# ---------------------------------------------------------------------------

class TestButtonConversion:
    def test_simple_button(self):
        """A plain submit button becomes a button() call."""
        html = '<button type="submit">Save</button>'
        output, converted, skipped = _convert_text(html)
        assert "button(" in output
        assert "'Save'" in output
        assert "type='submit'" in output
        assert len(converted) == 1
        assert len(skipped) == 0

    def test_button_with_click(self):
        """@click becomes on_click."""
        html = '<button type="button" @click="doSomething()">Click</button>'
        output, converted, skipped = _convert_text(html)
        assert "on_click=" in output
        assert "doSomething()" in output
        assert len(converted) == 1

    def test_button_with_click_stop(self):
        """@click.stop passes through attrs_dict (on_click is only for @click)."""
        html = '<button type="button" @click.stop="handleClick($event)">Stop</button>'
        output, converted, skipped = _convert_text(html)
        assert "on_click=" not in output
        assert "'@click.stop': 'handleClick($event)'" in output
        assert len(converted) == 1

    def test_button_with_aria_label(self):
        """aria-label becomes aria_label."""
        html = '<button type="button" aria-label="Close dialog">X</button>'
        output, converted, skipped = _convert_text(html)
        assert "aria_label=" in output
        assert "Close dialog" in output
        assert len(converted) == 1

    def test_button_with_data_action(self):
        """data-action becomes data_action."""
        html = '<button type="button" data-action="openModal">Open</button>'
        output, converted, skipped = _convert_text(html)
        assert "data_action=" in output
        assert "openModal" in output
        assert len(converted) == 1

    def test_button_with_alpine_disabled(self):
        """:disabled is passed through attrs_dict."""
        html = '<button type="button" :disabled="isSaving">Save</button>'
        output, converted, skipped = _convert_text(html)
        assert "attrs_dict=" in output
        assert ":disabled" in output
        assert len(converted) == 1

    def test_button_with_icon_and_text(self):
        """Button with icon and text becomes a simple button() call."""
        html = '<button type="button"><i data-lucide="plus"></i> Add</button>'
        output, converted, skipped = _convert_text(html)
        assert "button(" in output
        assert len(converted) == 1

    def test_button_with_complex_inner_html(self):
        """Button with nested divs uses call block form."""
        html = '<button type="button" @click="toggle()"><div class="flex"><span>Label</span></div></button>'
        output, converted, skipped = _convert_text(html)
        assert "{% call button(" in output
        assert "{% endcall %}" in output
        assert len(converted) == 1

    def test_button_with_x_text(self):
        """Button with x-text has no text arg (Alpine provides it)."""
        html = '<button type="button" @click="openEdit(row)" x-text="row.name"></button>'
        output, converted, skipped = _convert_text(html)
        assert "button(" in output
        assert "on_click=" in output
        assert "x-text" in output or "attrs_dict" in output
        assert len(converted) == 1


# ---------------------------------------------------------------------------
# Input tests
# ---------------------------------------------------------------------------

class TestInputConversion:
    def test_text_input(self):
        """A plain text input becomes an input() call."""
        html = '<input type="text" id="name" name="name" placeholder="Enter name">'
        output, converted, skipped = _convert_text(html)
        assert "input(" in output
        assert "id='name'" in output
        assert "name='name'" in output
        assert "placeholder='Enter name'" in output
        assert len(converted) == 1

    def test_input_with_required(self):
        """required attribute is preserved."""
        html = '<input type="text" id="email" name="email" required>'
        output, converted, skipped = _convert_text(html)
        assert "required=true" in output
        assert len(converted) == 1

    def test_input_with_alpine_model(self):
        """x-model is passed through attrs_dict."""
        html = '<input type="text" x-model="form.name" id="name" name="name">'
        output, converted, skipped = _convert_text(html)
        assert "input(" in output
        assert "x-model" in output or "attrs_dict" in output
        assert len(converted) == 1

    def test_hidden_input_skipped(self):
        """Hidden inputs are left alone."""
        html = '<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">'
        output, converted, skipped = _convert_text(html)
        assert '<input type="hidden"' in output
        assert len(converted) == 0
        assert len(skipped) == 1

    def test_date_input(self):
        """Date type inputs are converted."""
        html = '<input type="date" id="start" name="start">'
        output, converted, skipped = _convert_text(html)
        assert "input(" in output
        assert "type='date'" in output
        assert len(converted) == 1

    def test_number_input(self):
        """Number type inputs are converted."""
        html = '<input type="number" id="cost" name="cost" step="0.01" min="0">'
        output, converted, skipped = _convert_text(html)
        assert "input(" in output
        assert "type='number'" in output
        assert len(converted) == 1

    def test_input_with_maxlength(self):
        """maxlength is preserved."""
        html = '<input type="text" id="title" name="title" maxlength="200">'
        output, converted, skipped = _convert_text(html)
        assert "maxlength='200'" in output
        assert len(converted) == 1


# ---------------------------------------------------------------------------
# Select tests
# ---------------------------------------------------------------------------

class TestSelectConversion:
    def test_simple_select(self):
        """A plain select becomes a native_select() call block."""
        html = '<select id="type" name="type"><option value="a">A</option></select>'
        output, converted, skipped = _convert_text(html)
        assert "{% call native_select(" in output
        assert "id='type'" in output
        assert "name='type'" in output
        assert "<option value=" in output
        assert "{% endcall %}" in output
        assert len(converted) == 1

    def test_select_with_alpine_model(self):
        """x-model is passed through attrs_dict."""
        html = '<select x-model="form.type" id="type" name="type"><option value="a">A</option></select>'
        output, converted, skipped = _convert_text(html)
        assert "native_select(" in output
        assert "x-model" in output or "attrs_dict" in output
        assert len(converted) == 1

    def test_select_with_aria_label(self):
        """aria-label is preserved."""
        html = '<select id="status" aria-label="Status"><option value="a">Active</option></select>'
        output, converted, skipped = _convert_text(html)
        assert "aria_label='Status'" in output
        assert len(converted) == 1


# ---------------------------------------------------------------------------
# Textarea tests
# ---------------------------------------------------------------------------

class TestTextareaConversion:
    def test_simple_textarea(self):
        """A plain textarea becomes a textarea() call."""
        html = '<textarea id="desc" name="desc" rows="3" placeholder="Description"></textarea>'
        output, converted, skipped = _convert_text(html)
        assert "textarea(" in output
        assert "id='desc'" in output
        assert "name='desc'" in output
        assert "rows=3" in output
        assert len(converted) == 1

    def test_textarea_with_content(self):
        """A textarea with inner content uses call block form."""
        html = '<textarea id="notes" name="notes" rows="4">Existing content</textarea>'
        output, converted, skipped = _convert_text(html)
        assert "{% call textarea(" in output or "textarea(" in output
        assert len(converted) == 1

    def test_textarea_with_alpine_model(self):
        """x-model is passed through attrs_dict."""
        html = '<textarea x-model="form.desc" id="desc" name="desc" rows="3"></textarea>'
        output, converted, skipped = _convert_text(html)
        assert "textarea(" in output
        assert "x-model" in output or "attrs_dict" in output
        assert len(converted) == 1


# ---------------------------------------------------------------------------
# Attribute pass-through tests
# ---------------------------------------------------------------------------

class TestAttributePassthrough:
    def test_data_attributes_preserved(self):
        """data-* attributes are passed through attrs_dict."""
        html = '<button type="button" data-vendor-id="123" data-action="test">Go</button>'
        output, converted, skipped = _convert_text(html)
        assert "data-vendor-id" in output
        assert len(converted) == 1

    def test_alpine_attributes_preserved(self):
        """Alpine attributes are passed through attrs_dict."""
        html = '<input type="text" x-model="form.name" x-ref="input" :value="form.name">'
        output, converted, skipped = _convert_text(html)
        assert "x-model" in output or "attrs_dict" in output
        assert len(converted) == 1

    def test_id_and_name_preserved(self):
        """id and name are first-class kwargs."""
        html = '<input type="text" id="username" name="username">'
        output, converted, skipped = _convert_text(html)
        assert "id='username'" in output
        assert "name='username'" in output
        assert len(converted) == 1


# ---------------------------------------------------------------------------
# Skip / leave-alone tests
# ---------------------------------------------------------------------------

class TestSkipConditions:
    def test_data_slot_button_skipped(self):
        """A button with data-slot is from a macro and is left alone."""
        html = '<button data-slot="button">Already a macro</button>'
        output, converted, skipped = _convert_text(html)
        assert "data-slot" in output
        assert len(converted) == 0

    def test_data_slot_input_skipped(self):
        """An input with data-slot is from a macro and is left alone."""
        html = '<input data-slot="input" type="text">'
        output, converted, skipped = _convert_text(html)
        assert "data-slot" in output
        assert len(converted) == 0

    def test_dry_run_does_not_modify(self):
        """Dry run reports but does not change the file."""
        html = '<button type="submit">Save</button>'
        output, converted, skipped = _dry_run(html)
        # Output should be unchanged
        assert '<button type="submit">' in output
        assert len(converted) == 1
        assert len(skipped) == 0


# ---------------------------------------------------------------------------
# Determinism test
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_input_same_output(self):
        """Running the converter twice gives the same result."""
        html = '<button type="button" @click="test()">Click</button>'
        output1, _, _ = _convert_text(html)
        output2, _, _ = _convert_text(html)
        assert output1 == output2

    def test_idempotent(self):
        """Running the converter on already-converted output is a no-op."""
        html = '<button type="submit">Save</button>'
        output1, c1, _ = _convert_text(html)
        assert len(c1) == 1
        # Run again on the output
        output2, c2, _ = _convert_text(output1)
        assert len(c2) == 0  # Nothing left to convert


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_no_controls(self):
        """A template with no raw controls is unchanged."""
        html = '{% extends "base.html" %}{% block content %}<p>Hello</p>{% endblock %}'
        output, converted, skipped = _convert_text(html)
        assert converted == []
        assert skipped == []

    def test_multiple_controls(self):
        """Multiple controls in one template are all converted."""
        html = """
        <button type="submit">Save</button>
        <input type="text" id="name" name="name">
        <select id="type" name="type"><option value="a">A</option></select>
        <textarea id="desc" name="desc" rows="3"></textarea>
        """
        output, converted, skipped = _convert_text(html)
        assert len(converted) == 4
        assert "button(" in output
        assert "input(" in output
        assert "native_select(" in output
        assert "textarea(" in output

    def test_button_with_no_text(self):
        """A button with only Alpine content has no text arg."""
        html = '<button type="button" @click="open(row)" x-text="row.name"></button>'
        output, converted, skipped = _convert_text(html)
        assert "button(" in output
        assert "on_click=" in output
        assert len(converted) == 1
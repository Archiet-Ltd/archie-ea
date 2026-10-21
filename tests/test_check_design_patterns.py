"""The design-pattern conformance checker, one fixture per rule.

Each fixture is a small Jinja snippet with one intentional violation and one clean example. The
test asserts the rule catches the violation, leaves the clean example alone, reports the right
line, and carries a suggested fix. The rules are pure functions over text, so no app, database or
template engine is involved.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_design_patterns.py"
_spec = importlib.util.spec_from_file_location("check_design_patterns", SCRIPT)
cdp = importlib.util.module_from_spec(_spec)
sys.modules["check_design_patterns"] = cdp
_spec.loader.exec_module(cdp)

CONFORMANCE = {
    "canonical": {
        "button": {"file": "app/templates/components/button.html", "accepted_tokens": ["bg-primary", "bg-destructive", "bg-secondary", "hover:bg-accent", "border-input", "underline-offset-4"]},
        "input": {"height_class": "h-9"},
        "table": {"required_class": "dense-table"},
        "dialog": {"files": ["app/templates/components/modal.html"], "required_tokens": ['role="dialog"', 'aria-modal="true"']},
    },
    "banned_patterns": ["mermaid", "d3-sankey", "word-cloud"],
}


def run(rule, src: str, rel: str = "app/templates/x.html"):
    ctx = cdp.Ctx(CONFORMANCE, rel)
    return rule(cdp.blank_comments(src), ctx)


def one(rule, src: str, rel: str = "app/templates/x.html"):
    found = run(rule, src, rel)
    assert len(found) == 1, found
    v = found[0]
    assert v.suggested_fix
    assert v.rule == rule.__name__[5:].upper()
    return v


# --- SHADOW -------------------------------------------------------------------------------------
def test_shadow_catches_a_shadow_class_and_reports_its_line():
    src = '<div class="rounded-lg border">\n<div class="rounded-lg shadow-md">x</div>\n'
    v = one(cdp.rule_shadow, src)
    assert v.line == 2 and "shadow-md" in v.matched_text


def test_shadow_ignores_shadow_none_and_clean_markup():
    assert run(cdp.rule_shadow, '<div class="rounded-lg border shadow-none">x</div>') == []


# --- ROUNDED_XL_CARD ----------------------------------------------------------------------------
def test_rounded_xl_card_catches_a_card_with_a_large_radius():
    src = '<div class="rounded-xl border bg-card">card</div>\n<div class="rounded-xl p-2">not a card</div>'
    v = one(cdp.rule_rounded_xl_card, src)
    assert v.line == 1


def test_rounded_xl_card_leaves_rounded_lg_cards_alone():
    assert run(cdp.rule_rounded_xl_card, '<div data-slot="card" class="rounded-lg border bg-card">x</div>') == []


# --- RAW_BUTTON ---------------------------------------------------------------------------------
def test_raw_button_catches_a_button_without_a_variant_token():
    src = '<button type="button" class="bg-primary text-sm">ok</button>\n\n<button type="button" class="px-2 text-sm">raw</button>'
    v = one(cdp.rule_raw_button, src)
    assert v.line == 3


def test_raw_button_skips_the_macro_file_itself():
    src = '<button type="button" class="{{ classes }}">x</button>'
    assert run(cdp.rule_raw_button, src, rel="app/templates/components/button.html") == []
    assert run(cdp.rule_raw_button, '<button class="hover:bg-accent h-8">x</button>') == []


# --- RAW_TABLE ----------------------------------------------------------------------------------
def test_raw_table_catches_a_table_without_the_dense_class():
    src = '<table class="dense-table w-full">\n</table>\n<table class="w-full text-sm">\n</table>'
    v = one(cdp.rule_raw_table, src)
    assert v.line == 3


def test_raw_table_accepts_dense_table():
    assert run(cdp.rule_raw_table, '<table class="dense-table w-full border-collapse">') == []


# --- RAW_MODAL ----------------------------------------------------------------------------------
def test_raw_modal_catches_a_dialog_div_without_the_required_tokens():
    src = '<div role="dialog" aria-modal="true" class="modal-root">ok</div>\n<div x-show="isOpen" class="fixed inset-0">raw</div>'
    v = one(cdp.rule_raw_modal, src)
    assert v.line == 2


def test_raw_modal_skips_the_canonical_macro_file():
    src = '<div x-show="open" class="fixed">x</div>'
    assert run(cdp.rule_raw_modal, src, rel="app/templates/components/modal.html") == []


# --- BANNED_PATTERN -----------------------------------------------------------------------------
def test_banned_pattern_catches_a_banned_name_in_a_class_or_data_attribute():
    src = '<div class="chart">ok</div>\n<div class="mermaid" data-view="x">bad</div>'
    v = one(cdp.rule_banned_pattern, src)
    assert v.line == 2 and "mermaid" in v.matched_text


def test_banned_pattern_matches_whole_words_only():
    assert run(cdp.rule_banned_pattern, '<div class="mermaid-free data-wordy">x</div>') == []


# --- HARDCODED_HEX ------------------------------------------------------------------------------
def test_hardcoded_hex_catches_a_hex_colour_in_a_style_attribute():
    src = '<div style="--color-brand: #112233">var def is fine</div>\n<div style="color: #ff0000">bad</div>'
    v = one(cdp.rule_hardcoded_hex, src)
    assert v.line == 2 and v.severity == "warn"


def test_hardcoded_hex_ignores_tokens():
    assert run(cdp.rule_hardcoded_hex, '<div class="text-primary bg-[hsl(var(--card))]">x</div>') == []


# --- INPUT_HEIGHT -------------------------------------------------------------------------------
def test_input_height_catches_a_non_canonical_height():
    src = '<input type="text" class="h-9 rounded-md">\n<input type="text" class="h-10 rounded-md">\n<input type="checkbox" class="h-4 w-4">'
    v = one(cdp.rule_input_height, src)
    assert v.line == 2


def test_input_height_accepts_h9_and_unsized_controls():
    assert run(cdp.rule_input_height, '<select class="h-9 rounded-md"></select><input class="rounded-md">') == []


# --- MULTI_COLUMN_FORM --------------------------------------------------------------------------
def test_multi_column_form_catches_a_grid_inside_a_form():
    src = '<div class="grid grid-cols-2">outside a form</div>\n<form>\n<div class="grid md:grid-cols-2">\n</div>\n</form>'
    v = one(cdp.rule_multi_column_form, src)
    assert v.line == 3


def test_multi_column_form_ignores_single_column_forms():
    assert run(cdp.rule_multi_column_form, '<form><div class="grid grid-cols-1 gap-4"></div></form>') == []


# --- NAMED_BUTTON -------------------------------------------------------------------------------
def test_named_button_catches_an_icon_only_button_without_a_name():
    src = '<button type="button" aria-label="Close"><i data-lucide="x"></i></button>\n<button type="button"><i data-lucide="x"></i></button>'
    v = one(cdp.rule_named_button, src)
    assert v.line == 2


def test_named_button_accepts_text_and_jinja_text():
    assert run(cdp.rule_named_button, '<button type="button">Save</button><button type="button">{{ label }}</button>') == []


# --- NAMED_INPUT --------------------------------------------------------------------------------
def test_named_input_catches_an_input_with_no_label_of_any_kind():
    src = '<label for="name">Name</label><input id="name" type="text">\n<input id="orphan" type="text">'
    v = one(cdp.rule_named_input, src)
    assert v.line == 2 and v.severity == "warn"


def test_named_input_accepts_aria_label_placeholder_and_hidden_inputs():
    src = '<input type="text" aria-label="Search"><input type="search" placeholder="Find"><input type="hidden" name="csrf">'
    assert run(cdp.rule_named_input, src) == []


def test_named_input_accepts_a_control_wrapped_in_its_label():
    src = '<label class="flex items-center gap-2"><input type="checkbox" name="agree"> I agree</label>'
    assert run(cdp.rule_named_input, src) == []


# --- MIXED_CURRENCY -----------------------------------------------------------------------------
def test_mixed_currency_catches_pounds_and_dollars_in_one_template():
    src = '<p>Cost £1,200</p>\n<p>Total $4,000</p>'
    v = one(cdp.rule_mixed_currency, src)
    assert v.line == 1 and v.severity == "warn"


def test_mixed_currency_ignores_javascript_template_strings():
    src = '<p>Cost £1,200</p><script>const s = `${total}`; $(document).ready();</script>'
    assert run(cdp.rule_mixed_currency, src) == []


# --- MATRIX_STICKY ------------------------------------------------------------------------------
def test_matrix_sticky_catches_a_matrix_table_without_both_sticky_classes():
    src = '<table class="dense-table matrix sticky-header sticky-col"></table>\n<div class="matrix-legend"></div>\n<table class="dense-table matrix sticky-header"></table>\n<div class="matrix-legend"></div>'
    v = one(cdp.rule_matrix_sticky, src)
    assert v.line == 3


def test_matrix_sticky_ignores_non_matrix_tables():
    assert run(cdp.rule_matrix_sticky, '<table class="dense-table w-full"></table>') == []


# --- MATRIX_LEGEND ------------------------------------------------------------------------------
def test_matrix_legend_catches_a_matrix_table_without_a_legend():
    src = '<table class="dense-table matrix sticky-header sticky-col"></table>\n' + "\n" * 60 + '<p>nothing</p>'
    v = one(cdp.rule_matrix_legend, src)
    assert v.line == 1


def test_matrix_legend_accepts_a_legend_within_fifty_lines():
    src = '<table class="dense-table matrix sticky-header sticky-col"></table>\n<div class="matrix-legend">legend</div>'
    assert run(cdp.rule_matrix_legend, src) == []


# --- the driver ----------------------------------------------------------------------------------
def test_every_rule_has_a_severity_and_a_fix():
    for name in cdp.RULE_NAMES:
        assert name in cdp.SEVERITY and name in cdp.FIX


def test_the_rulebook_on_this_branch_has_the_conformance_section():
    patterns = json.loads((ROOT / "design" / "patterns.json").read_text(encoding="utf-8"))
    assert "canonical" in patterns["conformance"]
    assert "banned_patterns" in patterns["conformance"]
    assert (ROOT / "design" / "tier1_routes.txt").exists()


def test_the_checker_runs_on_the_tier1_templates_and_writes_a_report(tmp_path):
    report = tmp_path / "report.json"
    code = cdp.main(["--tier", "1", "--report", str(report), "--format", "json"])
    assert code in (0, 1)
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["files_tier1"] == data["files_scanned"] > 0
    assert set(data["summary"]["by_rule"]) == set(cdp.RULE_NAMES)
    for v in data["violations"]:
        assert v["file"] and v["line"] >= 1 and v["suggested_fix"] and v["tier"] == 1


def test_a_missing_conformance_section_exits_2(monkeypatch, tmp_path):
    bad = tmp_path / "patterns.json"
    bad.write_text(json.dumps({"pilot_patterns": {}}), encoding="utf-8")
    monkeypatch.setattr(cdp, "PATTERNS_PATH", bad)
    assert cdp.main(["--report", str(tmp_path / "r.json")]) == 2


def test_raw_modal_still_catches_a_screen_overlay_shown_on_an_open_flag():
    src = '<div x-show="showModal" class="fixed inset-0 z-50 flex items-center">body</div>'
    assert one(cdp.rule_raw_modal, src).rule == "RAW_MODAL"


def test_raw_modal_still_catches_role_dialog_and_alertdialog():
    for role in ("dialog", "alertdialog"):
        src = f'<div role="{role}" class="p-4">body</div>'
        assert one(cdp.rule_raw_modal, src).rule == "RAW_MODAL"


def test_raw_modal_leaves_a_popover_and_a_menu_beside_their_trigger():
    popover = '<div x-show="filtersOpen" class="absolute right-0 mt-2 w-64 rounded-md border bg-popover">f</div>'
    menu = '<div x-show="open" @click.away="open = false" class="absolute z-10 mt-1 rounded-md border">m</div>'
    assert run(cdp.rule_raw_modal, popover) == []
    assert run(cdp.rule_raw_modal, menu) == []


def test_raw_modal_leaves_a_backdrop_hidden_from_assistive_technology():
    src = '<div x-show="sidebarOpen" aria-hidden="true" class="fixed inset-0 bg-overlay/50"></div>'
    assert run(cdp.rule_raw_modal, src) == []


def _banned_rules(src):
    return [v for v in run(cdp.rule_banned_pattern, src) if v.rule == "BANNED_PATTERN"]


def test_banned_engine_is_caught_by_its_use_after_a_class_rename():
    src = ('<div class="archimate-diagram">graph TD; A-->B</div>\n'
           '<script>mermaid.run({ querySelector: ".archimate-diagram" });</script>')
    hits = _banned_rules(src)
    assert hits and "mermaid used" in hits[0].matched_text


def test_banned_engine_is_caught_by_its_script_tag():
    src = '<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>'
    assert _banned_rules(src)


def test_banned_engine_mentioned_only_in_a_jinja_comment_is_not_use():
    src = "{# We replaced mermaid.run(...) with the Composer drawing. #}<div class=\"p-4\">x</div>"
    assert _banned_rules(src) == []


def test_d3_sankey_and_chord_calls_are_caught():
    assert _banned_rules("<script>const s = d3.sankey().nodeWidth(10);</script>")
    conf = dict(CONFORMANCE, banned_patterns=CONFORMANCE["banned_patterns"] + ["d3-chord"])
    ctx = cdp.Ctx(conf, "app/templates/x.html")
    assert cdp.rule_banned_pattern("<script>const c = d3.chord()(matrix);</script>", ctx)


def test_an_engine_that_is_not_banned_is_left_alone():
    assert _banned_rules("<script>const c = d3.chord()(matrix);</script>") == []

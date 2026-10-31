"""The three recurring template faults are ratcheted: one more of any fails the gate."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


contract = _load("ui_contract_under_test", "scripts/check_ui_contract.py")
verify = _load("verify_under_test", "scripts/verify.py")


@pytest.fixture
def scan(tmp_path, monkeypatch):
    monkeypatch.setattr(contract, "ROOT", tmp_path)
    monkeypatch.setattr(contract, "TPL", tmp_path / "t")
    (tmp_path / "t").mkdir()

    def run(rule, body):
        (tmp_path / "t" / "page.html").write_text(body, encoding="utf-8")
        return contract.collect_component_rule(rule)

    return run


@pytest.mark.parametrize("body", [
    "{{ empty_state('x', 'None', 'Nothing') }}",
    "{% call modal(id='m', title='T') %}x{% endcall %}",
    "{{ page_header(title='T') }}",
    "{% call dense_table(columns=[]) %}x{% endcall %}",
    "{{ shadcn_select('a', 'A') }}",
])
def test_old_macro_call_is_counted(scan, body):
    assert len(scan("legacy-macro", body)) == 1


def test_component_use_and_definitions_are_not_counted(scan):
    assert scan("legacy-macro", "{% macro modal(id) %}{{ modal(id) }}{% endmacro %}") == []
    assert scan("legacy-macro", "{{ button('Save') }}{{ explained_value(basis='x') }}") == []


_LEGACY_HOME_AND_NAME = [
    ("components/empty_state.html", "empty_state"),
    ("components/page_header.html", "page_header"),
    ("components/modal.html", "modal"),
    ("components/dense_table.html", "dense_table"),
    ("macros/shadcn_compat.html", "shadcn_select"),
]


def _expected_legacy_message(name):
    if name == "empty_state":
        return "use the ui.empty block (basecoat/empty.html)"
    return "use the component"


@pytest.mark.parametrize("home,name", _LEGACY_HOME_AND_NAME)
def test_aliased_import_call_is_counted(scan, home, name):
    body = f"{{% from '{home}' import {name} as x %}}\n{{{{ x('a') }}}}"
    found = scan("legacy-macro", body)
    assert len(found) == 1
    assert _expected_legacy_message(name) in found[0][2]


@pytest.mark.parametrize("home,name", _LEGACY_HOME_AND_NAME)
def test_namespace_import_call_is_counted(scan, home, name):
    body = f"{{% import '{home}' as ns %}}\n{{{{ ns.{name}('a') }}}}"
    found = scan("legacy-macro", body)
    assert len(found) == 1
    assert _expected_legacy_message(name) in found[0][2]


def test_aliased_import_with_no_call_is_not_counted(scan):
    body = "{% from 'components/empty_state.html' import empty_state as x %}"
    assert scan("legacy-macro", body) == []


def test_unaliased_direct_import_call_still_counted_once(scan):
    body = "{% from 'components/empty_state.html' import empty_state %}\n{{ empty_state('a') }}"
    assert len(scan("legacy-macro", body)) == 1


def test_aliased_import_from_a_non_legacy_home_is_not_counted(scan):
    body = "{% from 'components/button.html' import button as x %}\n{{ x('a') }}"
    assert scan("legacy-macro", body) == []


def test_empty_state_message_names_the_library_block(scan):
    body = (
        '<div>\n'
        '<svg data-lucide="inbox"></svg>\n'
        '\n\n\n\n\n'
        '<p>No items found yet</p>\n'
        '</div>'
    )
    found = scan("empty-state", body)
    assert len(found) == 1
    assert "ui.empty block (basecoat/empty.html)" in found[0][2]
    assert "empty_state macro" not in found[0][2]


def test_no_text_in_the_checker_tells_a_builder_to_call_empty_state_macro():
    contract_src = (ROOT / "scripts" / "check_ui_contract.py").read_text(encoding="utf-8")
    assert "empty_state macro" not in contract_src
    assert "ui.empty block (basecoat/empty.html)" in contract_src


def test_legacy_macro_calls_remediation_names_the_library_block():
    verify_src = (ROOT / "scripts" / "verify.py").read_text(encoding="utf-8")
    assert 'remediation="for empty_state use the ui.empty block (basecoat/empty.html)' in verify_src


def test_raw_button_is_counted_and_component_is_not(scan):
    assert len(scan("raw-button", '<button type="button">Go</button>')) == 1
    assert scan("raw-button", "{{ button('Go') }}") == []


@pytest.mark.parametrize("body", [
    "<td>{{ app.owner or '—' }}</td>",
    "<td>{{ app.owner|default('N/A') }}</td>",
    "<td>{{ 'x' if app.owner else '-' }}</td>",
    "<td>{{ app.cost or 0 }}</td>",
    "<td>{% if app.owner %}{{ app.owner }}{% else %}—{% endif %}</td>",
    "<td>{% if app.owner %}{{ app.owner }}{% else %}<span>N/A</span>{% endif %}</td>",
])
def test_placeholder_for_missing_value_is_counted(scan, body):
    assert len(scan("missing-value", body)) == 1


@pytest.mark.parametrize("body", [
    "{{ explained_value(basis='Not set', action='Set the owner') }}",
    "<td>{{ (a.count or 0) + 1 }}</td>",
    "{# {{ x or '—' }} #}",
])
def test_explained_value_and_non_display_zero_are_not_counted(scan, body):
    assert scan("missing-value", body) == []


@pytest.mark.parametrize("gate,rule,key", [
    ("legacy-macro-calls", "legacy-macro", "legacy_macro_calls"),
    ("second-component-library", "second-library", "second_component_library"),
    ("raw-button", "raw-button", "raw_button"),
    ("missing-value-placeholder", "missing-value", "missing_value_placeholder"),
])
def test_gate_fails_when_one_more_is_added(gate, rule, key):
    baseline = json.loads((ROOT / "verification_baseline.json").read_text())["ratchets"][key]
    at = verify.gate_counted_rule(gate, "scripts/check_ui_contract.py", rule, baseline)
    assert at.status == verify.PASS and at.measured <= baseline
    one_more = verify.gate_counted_rule(gate, "scripts/check_ui_contract.py", rule, at.measured - 1)
    assert one_more.status == verify.FAIL


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A temporary templates tree holding a one-macro library."""
    monkeypatch.setattr(contract, "ROOT", tmp_path)
    monkeypatch.setattr(contract, "TPL", tmp_path / "t")
    lib = tmp_path / "t" / "components"
    lib.mkdir(parents=True)
    (lib / "button.html").write_text("{% macro button(text) %}<b>{{ text }}</b>{% endmacro %}", encoding="utf-8")
    (tmp_path / "t" / "macros").mkdir()
    return tmp_path / "t"


def test_second_library_clean_tree_passes(tree):
    (tree / "page.html").write_text("{% from 'components/button.html' import button %}{{ button('Go') }}", encoding="utf-8")
    assert contract.collect_component_rule("second-library") == []


def test_second_library_planted_macro_file_fails(tree):
    (tree / "macros" / "ui.html").write_text("{% macro button(text='') %}<button>{{ text }}</button>{% endmacro %}", encoding="utf-8")
    found = contract.collect_component_rule("second-library")
    assert [(rel, msg.split("(")[0]) for rel, _, msg in found] == [("t/macros/ui.html", "macro button")]


def test_second_library_import_from_elsewhere_fails(tree):
    (tree / "page.html").write_text("{% from 'macros/ui.html' import button %}", encoding="utf-8")
    assert len(contract.collect_component_rule("second-library")) == 1


def test_second_library_unrelated_macro_and_library_files_pass(tree):
    (tree / "macros" / "x.html").write_text("{% macro unique_thing() %}{% endmacro %}", encoding="utf-8")
    assert contract.collect_component_rule("second-library") == []

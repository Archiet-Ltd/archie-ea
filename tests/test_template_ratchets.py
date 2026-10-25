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
    ("raw-button", "raw-button", "raw_button"),
    ("missing-value-placeholder", "missing-value", "missing_value_placeholder"),
])
def test_gate_fails_when_one_more_is_added(gate, rule, key):
    baseline = json.loads((ROOT / "verification_baseline.json").read_text())["ratchets"][key]
    at = verify.gate_counted_rule(gate, "scripts/check_ui_contract.py", rule, baseline)
    assert at.status == verify.PASS and at.measured <= baseline
    one_more = verify.gate_counted_rule(gate, "scripts/check_ui_contract.py", rule, at.measured - 1)
    assert one_more.status == verify.FAIL

"""The macro import check flags a context-less import of a macro that runs a script.

A script element that is a data block (a JSON type) is never executed by the browser,
so the empty nonce a context-less import gives it does no harm. An executable script
in the same position is refused by the page's script policy and must still be reported.
"""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_macro_import_context", ROOT / "scripts" / "check_macro_import_context.py"
)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def _scan(tmp_path, monkeypatch, macro_body, import_line):
    templates = tmp_path / "app" / "templates"
    templates.mkdir(parents=True)
    (templates / "parts.html").write_text("{% macro part() %}" + macro_body + "{% endmacro %}")
    (templates / "page.html").write_text(import_line + "{{ part() }}")
    monkeypatch.setattr(check, "ROOT", tmp_path)
    monkeypatch.setattr(check, "_template_roots", lambda: [templates])
    return check.scan()


IMPORT_PLAIN = "{% from 'parts.html' import part %}"
IMPORT_WITH_CONTEXT = "{% from 'parts.html' import part with context %}"


def test_json_data_block_imported_without_context_passes(tmp_path, monkeypatch):
    body = '<script type="application/json" data-chart-spec>{{ spec | tojson }}</script>'
    assert _scan(tmp_path, monkeypatch, body, IMPORT_PLAIN) == []


def test_executable_script_imported_without_context_fails(tmp_path, monkeypatch):
    failures = _scan(tmp_path, monkeypatch, "<script>window.ran = true;</script>", IMPORT_PLAIN)
    assert len(failures) == 1
    assert "page.html:1" in failures[0] and "parts.html" in failures[0]


def test_executable_script_imported_with_context_passes(tmp_path, monkeypatch):
    assert _scan(tmp_path, monkeypatch, "<script>window.ran = true;</script>", IMPORT_WITH_CONTEXT) == []


@pytest.mark.parametrize(
    "tag",
    [
        '<script type="text/javascript">x()</script>',
        '<script type="module">x()</script>',
        "<script type='application/javascript'>x()</script>",
        '<script type="">x()</script>',
        '<script src="/static/x.js"></script>',
        '<script type="{{ kind }}">x()</script>',
        '<script type="application/json">{}</script><script>x()</script>',
    ],
)
def test_scripts_a_browser_runs_still_fail(tmp_path, monkeypatch, tag):
    assert len(_scan(tmp_path, monkeypatch, tag, IMPORT_PLAIN)) == 1


@pytest.mark.parametrize("kind", ["application/json", "application/ld+json", "text/template"])
def test_other_data_block_types_pass(tmp_path, monkeypatch, kind):
    body = '<script type="%s">{}</script>' % kind
    assert _scan(tmp_path, monkeypatch, body, IMPORT_PLAIN) == []

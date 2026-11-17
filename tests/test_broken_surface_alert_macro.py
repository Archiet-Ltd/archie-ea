"""The broken-surfaces checker tells the page library's alert macro from the browser's alert()."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def scan_tree(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "check_broken_surfaces_under_test", ROOT / "scripts" / "check_broken_surfaces.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    templates = tmp_path / "app" / "templates"
    js = tmp_path / "app" / "static" / "js"
    templates.mkdir(parents=True)
    js.mkdir(parents=True)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "TEMPLATES", templates)
    monkeypatch.setattr(mod, "STATIC_JS", js)
    monkeypatch.setattr(mod, "_route_matcher", lambda: (lambda path: True, 0))

    def run(name, body):
        target = (templates if name.endswith(".html") else js) / name
        target.write_text(body, encoding="utf-8")
        try:
            return mod.scan()["forbidden-ui"]
        finally:
            target.unlink()

    return run


@pytest.mark.parametrize("body", [
    "{{ alert('warning', 'Check this') }}",
    "{% call alert('warning') %}Check this{% endcall %}",
    "{% macro alert(text='', variant='info') %}<div>{{ text }}</div>{% endmacro %}",
])
def test_alert_macro_forms_are_not_flagged(scan_tree, body):
    assert scan_tree("page.html", body) == []


@pytest.mark.parametrize("name,body", [
    ("page.html", "<script>alert('x')</script>"),
    ("page.html", "<script>if (confirm('x')) { go(); }</script>"),
    ("page.html", "<script>window.alert('x')</script>"),
    ("page.html", "{{ alert('ok') }}<script>alert('x')</script>"),
    ("page.html", "<script>var t = '{{ alert('ok') }}'; alert('x')</script>"),
    ("page.html", "<script>var s = \"{{ alert(\"; </script><script>{{ alert('x') }}</script>"),
    ("page.js", "alert('x');"),
    ("page.js", "if (confirm('x')) { go(); }"),
    ("page.js", "window.alert('x');"),
])
def test_native_dialogs_are_still_flagged(scan_tree, name, body):
    assert scan_tree(name, body)

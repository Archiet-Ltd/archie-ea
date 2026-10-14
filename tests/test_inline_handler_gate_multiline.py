"""The inline-handler gate must see tags laid out one attribute per line, and the
capability map's page-load renderers must carry no inline handler.

Why: /capability-map/ logged "Executing inline event handler violates the
following Content Security Policy directive" on every load once capability rows
rendered, which failed the console-hygiene ratchet on nearly every open PR.
The row checkbox in app/static/js/capability_map/index.js was

    <input type="checkbox"
        class="..."
        onchange="toggleRowSelection(...)">

and scripts/check_inline_handlers.py matched `<tag ... onX=` on a single line
only, so it read 0 while the defect shipped.
"""

import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _gate():
    spec = importlib.util.spec_from_file_location(
        "check_inline_handlers", os.path.join(ROOT, "scripts", "check_inline_handlers.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def test_gate_flags_handler_on_a_later_line_of_the_tag(tmp_path):
    _write(
        str(tmp_path),
        "app/static/js/probe.js",
        "const row = `\n"
        "  <input type=\"checkbox\"\n"
        "      class=\"rounded\"\n"
        "      onchange=\"toggle(1, this.checked)\">\n"
        "`;\n",
    )
    findings = _gate().scan(str(tmp_path))
    assert findings == [("app/static/js/probe.js", 4, "onchange")]


def test_gate_accepts_the_delegated_data_attribute_form(tmp_path):
    _write(
        str(tmp_path),
        "app/static/js/probe.js",
        "const row = `\n"
        "  <input type=\"checkbox\"\n"
        "      class=\"rounded\"\n"
        "      data-cm-change=\"toggle-row-checkbox\">\n"
        "`;\n",
    )
    assert _gate().scan(str(tmp_path)) == []


def test_gate_still_ignores_alpine_directives_and_macro_keywords(tmp_path):
    _write(
        str(tmp_path),
        "app/templates/probe.html",
        "<button\n    @click=\"go()\"\n    class=\"x\">a</button>\n"
        "{{ stat_card('a', onclick=\"setFilter('')\") }}\n",
    )
    assert _gate().scan(str(tmp_path)) == []


def test_gate_honours_allow_marker_on_the_attribute_line(tmp_path):
    _write(
        str(tmp_path),
        "app/templates/probe.html",
        "<input\n  onchange=\"x()\"  {# inline-handler-ok: emailed body #}\n>\n",
    )
    assert _gate().scan(str(tmp_path)) == []


def test_capability_map_page_load_script_has_no_inline_handlers():
    findings = [
        f for f in _gate().scan(ROOT) if f[0] == "app/static/js/capability_map/index.js"
    ]
    assert findings == []

"""A score, percentage or money figure never falls back to an invented 0.

The checker is proven against planted defects in a temporary tree, the figure helper is run in a real
JavaScript engine, the template filter is rendered, and the repository count is pinned to the baseline.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_zero_fallbacks", REPO / "scripts" / "check_zero_fallbacks.py")
checker = importlib.util.module_from_spec(_spec)
sys.modules["check_zero_fallbacks"] = checker
_spec.loader.exec_module(checker)


def _tree(tmp_path, monkeypatch, files):
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    return [(str(rel), line) for rel, line, _, _ in checker.occurrences()]


def test_every_fallback_shape_on_a_figure_is_found(tmp_path, monkeypatch):
    found = _tree(tmp_path, monkeypatch, {
        "app/static/js/a.js": "el.textContent = (stats.coverage_pct || 0) + '%';\nconst c = row.total_cost ?? 0;\n",
        "app/templates/b.html": "{{ x.cost or 0 }}\n{{ x.quality_score|default(0) }}\n",
    })
    assert len(found) == 4


def test_counters_markers_and_unrelated_names_are_left_alone(tmp_path, monkeypatch):
    found = _tree(tmp_path, monkeypatch, {
        "app/static/js/a.js": (
            "el.textContent = stats.total_scored || 0;\n"
            "const n = data.rows.length || 0;\n"
            "const w = (bar.width || 0) + 1;\n"
            "const s = row.score || 0; // zero-fallback-ok: the field is a form default\n"
        ),
        "app/templates/b.html": "{{ item.completed_steps or 0 }}\n",
    })
    assert found == []


def test_the_bundles_and_vendored_code_are_not_counted(tmp_path, monkeypatch):
    found = _tree(tmp_path, monkeypatch, {
        "app/static/js/bundles/core.js": "x(a.score || 0)\n",
        "app/static/js/vendor/lib.js": "x(a.score || 0)\n",
    })
    assert found == []


def test_the_repository_holds_no_fallback_beyond_the_baseline():
    baseline = json.loads((REPO / "verification_baseline.json").read_text())["ratchets"]["zero_fallbacks"]
    proc = subprocess.run([sys.executable, str(REPO / "scripts" / "check_zero_fallbacks.py"), "--count"],
                          capture_output=True, text=True, cwd=REPO)
    assert int(proc.stdout.strip().splitlines()[-1]) <= baseline == 0, proc.stdout


def test_the_gate_is_registered_in_the_static_set():
    from scripts.verify import build_gates

    gates = {g.name: g for g in build_gates({"zero_fallbacks": 0})}
    assert gates["zero-fallbacks"].kind == "ratchet"
    assert "static" in gates["zero-fallbacks"].tags


@pytest.mark.skipif(shutil.which("node") is None, reason="needs a JavaScript engine")
def test_the_figure_helper_says_what_is_missing_and_keeps_a_measured_zero():
    script = (REPO / "app" / "static" / "js" / "core" / "00-namespace.js").read_text()
    probe = """
const window = {}; global.window = window; const document = {dispatchEvent(){}, addEventListener(){}, removeEventListener(){}};
window.document = document; window.CustomEvent = function(){}; global.CustomEvent = window.CustomEvent;
eval(process.argv[1]);
const f = window.Platform.figure;
console.log(JSON.stringify([
  f.percent(null), f.percent(0), f.percent(62.4), f.percent(0.62, 100), f.percent(undefined, 1, 'None yet'),
  f.score(undefined, '/100'), f.score(0, '/100'), f.score(72, '/100'),
  f.money(null), f.money(''), f.money(1200), f.number(NaN), f.number(0),
  f.chartValue(null), f.chartValue(0), f.barWidth(null), f.barWidth(250), String(f.num(null)),
]));
"""
    out = subprocess.run(["node", "-e", probe, script], capture_output=True, text=True, check=True).stdout
    got = json.loads(out)
    assert got == ["Not scored", "0%", "62%", "62%", "None yet", "Not scored", "0/100", "72/100",
                   "Not recorded", "Not recorded", "1,200", "Not scored", "0", None, 0, 0, 100, "NaN"]


def test_the_dash_filter_can_name_what_is_missing(app):
    template = app.jinja_env.from_string(
        "{{ a|dash('%', 'Not scored') }}|{{ b|dash('%', 'Not scored') }}|{{ c|dash('%') }}|{{ d|dash }}")
    assert template.render(a=None, b=0, c=None, d=None) == "Not scored|0%|—|—"

"""The design-register gate: a second definition of a registered selector fails, wherever it lands.

Each test builds a tiny tree in a temp directory, so the gate is proven against the defect it exists
for (two pull requests defining the same button size with different values), not only against the
repository as it is today. The last tests pin the repository facts the gate depends on.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "check_design_register.py"
_spec = importlib.util.spec_from_file_location("check_design_register", SCRIPT)
gate = importlib.util.module_from_spec(_spec)
sys.modules["check_design_register"] = gate
_spec.loader.exec_module(gate)

REGISTER = """
version: 1
scope:
  css: [app/static/css/site.css]
  templates: [app/templates/p.html]
tokens: {}
allowed_literals:
  px: ["1px"]
  colors: []
  shadows: ["0 1px 2px 0 rgb(0 0 0 / 0.05)"]
  font_stacks: []
components:
  - id: btn
    concept: The button
    path: app/static/css/site.css
    selector: .public-btn
    modifiers: [.public-btn--sm]
    reuse: Use public-btn.
raw_utility:
  - id: hand-built
    component: btn
    concept: hand built
    regex: 'class="[^"]*\\bpublic-btn\\b[^"]*\\bbg-primary\\b'
    reuse: Use the modifier.
undecided: []
"""

BASE_CSS = """
.public-site .public-btn { height: 2.5rem; }
.public-site .public-btn--sm { height: 2.25rem; padding: 0.5rem 1rem; }
"""
TEMPLATE = '<a class="public-btn public-btn--sm">x</a>'


def _tree(tmp_path: Path, css: str = BASE_CSS, template: str = TEMPLATE, baseline=None) -> Path:
    (tmp_path / "design").mkdir()
    (tmp_path / "design" / "register.yml").write_text(textwrap.dedent(REGISTER), encoding="utf-8")
    (tmp_path / "app/static/css").mkdir(parents=True)
    (tmp_path / "app/static/css/site.css").write_text(css, encoding="utf-8")
    (tmp_path / "app/templates").mkdir(parents=True)
    (tmp_path / "app/templates/p.html").write_text(template, encoding="utf-8")
    if baseline is not None:
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts/design_register_baseline.json").write_text(
            json.dumps({"patterns": baseline}), encoding="utf-8")
    return tmp_path


def _fails(root: Path) -> list[str]:
    return gate.run(root)[0]


def test_a_clean_tree_passes(tmp_path):
    assert _fails(_tree(tmp_path)) == []


def test_pr_642_645_collision_a_second_btn_sm_with_another_value_fails(tmp_path):
    css = BASE_CSS + ".public-site .public-btn--sm { padding-inline: 1rem; }\n"
    fails = _fails(_tree(tmp_path, css))
    assert len(fails) == 1 and "duplicate:btn/.public-btn--sm" in fails[0]
    assert "padding: 0.5rem 1rem" in fails[0] and "padding-inline: 1rem" in fails[0]


def test_the_prefix_does_not_hide_a_duplicate(tmp_path):
    css = BASE_CSS + ".public-btn--sm { padding-inline: 1rem; }\n"
    assert any("duplicate:btn/.public-btn--sm" in f for f in _fails(_tree(tmp_path, css)))


def test_a_selector_list_member_is_a_duplicate(tmp_path):
    css = BASE_CSS + ".a, .public-site .public-btn--sm { color: red; }\n"
    assert any("duplicate:btn/.public-btn--sm" in f for f in _fails(_tree(tmp_path, css)))


def test_the_same_selector_in_another_media_context_is_not_a_duplicate(tmp_path):
    css = BASE_CSS + "@media (min-width: 1px) { .public-site .public-btn--sm { height: 3rem; } }\n"
    assert _fails(_tree(tmp_path, css)) == []


def test_a_pseudo_class_variant_is_a_different_selector(tmp_path):
    css = BASE_CSS + ".public-site .public-btn--sm:hover { opacity: .9; }\n"
    assert _fails(_tree(tmp_path, css)) == []


def test_a_baselined_duplicate_passes_and_a_further_one_fails(tmp_path):
    css = BASE_CSS + ".public-btn--sm { color: red; }\n"
    key = "duplicate:btn/.public-btn--sm"
    root = _tree(tmp_path, css, baseline={key: {"app/static/css/site.css": 1}})
    assert _fails(root) == []
    (root / "app/static/css/site.css").write_text(css + ".public-btn--sm { color: blue; }\n", encoding="utf-8")
    assert any(key in f for f in _fails(root))


def test_design_ok_with_a_reason_exempts_and_a_bare_marker_does_not(tmp_path):
    ok = BASE_CSS + "/* design-ok: print override */\n.public-btn--sm { color: red; }\n"
    assert _fails(_tree(tmp_path, ok)) == []
    bare = BASE_CSS + "/* design-ok: */\n.public-btn--sm { color: red; }\n"
    (tmp_path / "b").mkdir()
    assert _fails(_tree(tmp_path / "b", bare)) != []


def test_drift_a_hex_colour_and_an_unregistered_px_fail_but_a_registered_one_passes(tmp_path):
    css = BASE_CSS + ".other { color: #123456; margin: 7px; border: 1px solid; }\n"
    fails = _fails(_tree(tmp_path, css))
    assert any("drift:color/#123456" in f for f in fails)
    assert any("drift:px/7px" in f for f in fails)
    assert not any("px/1px" in f for f in fails)


def test_a_registered_shadow_is_not_drift(tmp_path):
    css = BASE_CSS + ".other { box-shadow: 0 1px 2px 0 rgb(0 0 0 / 0.05); }\n"
    assert _fails(_tree(tmp_path, css)) == []


def test_a_hand_built_button_from_utilities_fails(tmp_path):
    fails = _fails(_tree(tmp_path, template='<a class="public-btn public-btn--sm bg-primary">x</a>'))
    assert any("drift:raw-utility/hand-built" in f for f in fails)


def test_staleness_a_component_whose_selector_is_gone_fails(tmp_path):
    fails = _fails(_tree(tmp_path, ".public-site .public-btn { height: 2.5rem; }\n"))
    assert any("[component btn] .public-btn--sm is not defined" in f for f in fails)


def test_staleness_a_component_no_template_uses_fails(tmp_path):
    assert any("used by no template" in f for f in _fails(_tree(tmp_path, template="<p>none</p>")))


def test_a_public_class_no_component_covers_is_reported(tmp_path):
    fails = _fails(_tree(tmp_path, template=TEMPLATE + '<div class="public-new-thing"></div>'))
    assert any("coverage:uncovered/public-new-thing" in f for f in fails)


def test_the_baseline_refuses_to_rise(tmp_path):
    root = _tree(tmp_path, baseline={})
    (root / "app/static/css/site.css").write_text(BASE_CSS + ".public-btn--sm { color: red; }\n", encoding="utf-8")
    data, _ = gate.load_register(root)
    ok, messages = gate.update_baseline(root, data)
    assert not ok and "refused" in messages[0]


def test_a_missing_register_is_a_failure_not_a_pass(tmp_path):
    assert gate.run(tmp_path)[0]


def test_the_repository_register_passes_on_the_current_tree():
    fails, _ = gate.run(REPO)
    assert fails == [], "\n".join(fails)


def test_the_gate_is_registered_in_the_static_tag():
    text = (REPO / "scripts" / "verify.py").read_text(encoding="utf-8")
    start = text.index('Gate("design-register"')
    assert '"static"' in text[start:start + 600]


def test_the_repository_register_names_every_undecided_item_with_an_owner():
    data, problems = gate.load_register(REPO)
    assert problems == []
    assert data["undecided"] and all(u.get("decides") for u in data["undecided"])

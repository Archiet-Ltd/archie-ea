"""The reuse-register gate: a second copy of a registered concept fails, a moved canonical fails.

Each test builds a tiny tree in a temp directory (a register, a canonical module, a baseline), so
the gate is proven against the shapes of the defects it exists for, not only against the repository
as it is today. The last tests pin the repository facts the gate depends on: the register passes
on the current tree, the gate is wired into the static tag, and the untenanted-reads ratchet has no
slack.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "check_reuse_register.py"
_spec = importlib.util.spec_from_file_location("check_reuse_register", SCRIPT)
gate = importlib.util.module_from_spec(_spec)
sys.modules["check_reuse_register"] = gate
_spec.loader.exec_module(gate)

# The measured count of the untenanted-reads gate on 13 Oct 2026, when its limit stopped carrying
# 272 reads of slack. The limit may fall below this, never rise above it.
UNTENANTED_READS_CEILING = 2361

REGISTER = """
version: 1
entries:
  - id: name-matching
    concept: Name matching
    status: active
    canonical:
      path: app/utils/duplicate_guard.py
      symbols: [normalize_name, Guard.check]
    reuse: Use duplicate_guard.normalize_name.
    must_not_create:
      - id: normaliser
        regex: 'def\\s+_?normali[sz]e_?name\\s*\\('
    known_exceptions:
      - path: app/legacy/
        reason: predates the guard
  - id: later
    concept: A concept whose canonical has not merged
    status: pending
    pending_branch: some pull request
    canonical:
      path: app/services/later.py
      symbols: [do_later]
    reuse: Use later.do_later once it lands.
    must_not_create:
      - id: later-copy
        regex: 'def\\s+do_later\\s*\\('
  - id: health
    concept: Health score
    status: undetermined
    candidates: [app/a.py, app/b.py]
"""

CANONICAL = """
def normalize_name(value):
    return " ".join(str(value).split()).casefold()


class Guard:
    def check(self):
        return True
"""


def _tree(tmp_path, register=REGISTER, canonical=CANONICAL, extra=None, baseline=None):
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "app" / "utils").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "reuse-register.yml").write_text(register, encoding="utf-8")
    (tmp_path / "app" / "utils" / "duplicate_guard.py").write_text(canonical, encoding="utf-8")
    for rel, code in (extra or {}).items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(code), encoding="utf-8")
    if baseline is not None:
        (tmp_path / "scripts" / "reuse_register_baseline.json").write_text(
            json.dumps({"patterns": baseline}), encoding="utf-8")
    return tmp_path


def _run(root):
    return gate.run(root)


COPY = "def _normalize_name(v):\n    return v.lower()\n"


def test_clean_tree_passes(tmp_path):
    fails, notes = _run(_tree(tmp_path))
    assert fails == []


def test_a_new_copy_outside_the_canonical_file_fails_and_names_the_entry(tmp_path):
    root = _tree(tmp_path, extra={"app/routes/new_route.py": COPY})
    fails, _ = _run(root)

    assert len(fails) == 1
    message = fails[0]
    assert "name-matching/normaliser" in message
    assert "app/routes/new_route.py:1" in message
    assert "Use duplicate_guard.normalize_name." in message  # what to reuse
    assert "app/utils/duplicate_guard.py" in message  # where it lives


def test_the_canonical_file_may_define_the_pattern(tmp_path):
    canonical = CANONICAL + "\ndef normalize_name_again(v):\n    return v\n"
    assert _run(_tree(tmp_path, canonical=canonical))[0] == []


def test_a_known_exception_directory_is_not_a_hit(tmp_path):
    root = _tree(tmp_path, extra={"app/legacy/old.py": COPY})
    assert _run(root)[0] == []


def test_a_line_marker_with_a_reason_exempts_and_a_bare_marker_does_not(tmp_path):
    ok = "def _normalize_name(v):  # reuse-ok: unrelated, normalises a filename\n    return v\n"
    bare = "def _normalize_name(v):  # reuse-ok:\n    return v\n"
    assert _run(_tree(tmp_path, extra={"app/a.py": ok}))[0] == []
    assert len(_run(_tree(tmp_path / "b", extra={"app/a.py": bare}))[0]) == 1


def test_a_commented_out_definition_is_not_a_hit(tmp_path):
    assert _run(_tree(tmp_path, extra={"app/a.py": "# def normalize_name(v): pass\n"}))[0] == []


def test_a_hit_inside_the_baseline_passes_and_one_more_in_the_same_file_fails(tmp_path):
    two = COPY + "\n" + COPY.replace("_normalize_name", "normalize_name")
    baseline = {"name-matching/normaliser": {"app/a.py": 1}}

    one_copy = _tree(tmp_path, extra={"app/a.py": COPY}, baseline=baseline)
    assert _run(one_copy)[0] == []

    second = _tree(tmp_path / "x", extra={"app/a.py": two}, baseline=baseline)
    fails, _ = _run(second)
    assert len(fails) == 1 and "baseline allows 1" in fails[0]


def test_a_hit_in_a_file_with_no_baseline_entry_fails(tmp_path):
    baseline = {"name-matching/normaliser": {"app/a.py": 1}}
    root = _tree(tmp_path, extra={"app/a.py": COPY, "app/b.py": COPY}, baseline=baseline)
    fails, _ = _run(root)
    assert len(fails) == 1 and "app/b.py" in fails[0]


def test_a_baseline_above_the_tree_is_slack_not_a_failure(tmp_path):
    baseline = {"name-matching/normaliser": {"app/a.py": 3}}
    root = _tree(tmp_path, extra={"app/a.py": COPY}, baseline=baseline)
    fails, notes = _run(root)
    assert fails == []
    assert any("3 -> 1" in n for n in notes)


def test_a_missing_canonical_symbol_fails(tmp_path):
    renamed = CANONICAL.replace("def normalize_name", "def normalise_name_v2")
    fails, _ = _run(_tree(tmp_path, canonical=renamed))
    assert len(fails) == 1
    assert "no longer defines normalize_name" in fails[0]
    assert "name-matching" in fails[0]


def test_a_missing_method_symbol_fails(tmp_path):
    fails, _ = _run(_tree(tmp_path, canonical=CANONICAL.replace("def check", "def verify")))
    assert len(fails) == 1 and "Guard.check" in fails[0]


def test_a_missing_canonical_file_fails(tmp_path):
    root = _tree(tmp_path)
    (root / "app" / "utils" / "duplicate_guard.py").unlink()
    fails, _ = _run(root)
    assert len(fails) == 1 and "no longer exists" in fails[0]


def test_a_pending_canonical_is_not_required_to_exist_but_its_pattern_is_enforced(tmp_path):
    assert _run(_tree(tmp_path))[0] == []  # app/services/later.py absent: fine

    copy = "def do_later():\n    pass\n"
    fails, _ = _run(_tree(tmp_path / "b", extra={"app/elsewhere.py": copy}))
    assert len(fails) == 1 and "later/later-copy" in fails[0]

    landed = _tree(tmp_path / "c", extra={"app/services/later.py": copy})
    fails, notes = _run(landed)
    assert fails == []
    assert any("has landed" in n for n in notes)


def test_a_missing_or_broken_register_fails(tmp_path):
    assert gate.run(tmp_path)[0], "no register must not pass"
    bad = _tree(tmp_path / "b", register="entries: [not, a, mapping")
    assert gate.run(bad)[0]


@pytest.mark.parametrize("mutation, expected", [
    ("status: active", "status: sort-of"),
    ("regex: 'def\\s+_?normali[sz]e_?name\\s*\\('", "regex: '(unclosed'"),
])
def test_a_malformed_entry_is_a_failure(tmp_path, mutation, expected):
    fails, _ = _run(_tree(tmp_path, register=REGISTER.replace(mutation, expected, 1)))
    assert fails


def test_an_active_entry_without_symbols_or_a_reuse_line_is_rejected(tmp_path):
    register = REGISTER.replace("      symbols: [normalize_name, Guard.check]\n", "").replace(
        "    reuse: Use duplicate_guard.normalize_name.\n", "")
    problems = gate.run(_tree(tmp_path, register=register))[0]
    assert any("canonical.symbols" in p for p in problems)
    assert any("`reuse` line" in p for p in problems)


def test_a_macro_symbol_in_a_template_is_found(tmp_path):
    register = REGISTER.replace("app/utils/duplicate_guard.py", "app/templates/e.html").replace(
        "symbols: [normalize_name, Guard.check]", "symbols: [empty_state]")
    root = _tree(tmp_path, register=register)
    (root / "app" / "utils" / "duplicate_guard.py").unlink()
    (root / "app" / "templates").mkdir(parents=True)
    (root / "app" / "templates" / "e.html").write_text("{% macro empty_state(a) %}x{% endmacro %}")
    assert _run(root)[0] == []
    (root / "app" / "templates" / "e.html").write_text("{% macro other(a) %}x{% endmacro %}")
    assert any("empty_state" in f for f in _run(root)[0])


def test_update_baseline_refuses_to_raise_a_count_or_add_a_file(tmp_path):
    baseline = {"name-matching/normaliser": {"app/a.py": 1}}
    root = _tree(tmp_path, extra={"app/a.py": COPY, "app/b.py": COPY}, baseline=baseline)
    entries, _ = gate.load_register(root)
    before = (root / "scripts" / "reuse_register_baseline.json").read_text()

    ok, messages = gate.update_baseline(root, entries)

    assert ok is False
    assert any("app/b.py" in m and "0 -> 1" in m for m in messages)
    assert (root / "scripts" / "reuse_register_baseline.json").read_text() == before


def test_update_baseline_lowers_a_count(tmp_path):
    baseline = {"name-matching/normaliser": {"app/a.py": 2, "app/gone.py": 1}}
    root = _tree(tmp_path, extra={"app/a.py": COPY}, baseline=baseline)
    entries, _ = gate.load_register(root)

    ok, messages = gate.update_baseline(root, entries)

    assert ok is True
    written = json.loads((root / "scripts" / "reuse_register_baseline.json").read_text())["patterns"]
    assert written == {"name-matching/normaliser": {"app/a.py": 1}}
    assert any("2 -> 1" in m for m in messages)


def test_the_cli_exit_code_and_count(tmp_path, capsys):
    root = _tree(tmp_path, extra={"app/a.py": COPY})
    assert gate.main(["--root", str(root), "--count"]) == 1
    assert capsys.readouterr().out.strip().splitlines()[-1] == "1"
    clean = _tree(tmp_path / "ok")
    assert gate.main(["--root", str(clean), "--count"]) == 0


# ------------------------------------------------------------------ the repository itself


def test_the_register_passes_on_the_current_tree_and_is_fast():
    started = time.time()
    fails, _ = gate.run(REPO)
    elapsed = time.time() - started

    assert fails == [], "\n".join(fails)
    assert elapsed < 30, f"the gate must stay a fast static check, took {elapsed:.1f}s"


def test_every_active_canonical_in_the_real_register_exists():
    entries, problems = gate.load_register(REPO)
    assert problems == []
    fails, _ = gate.check_canonicals(REPO, entries)
    assert fails == []
    assert {e["status"] for e in entries} == {"active", "pending", "undetermined"}


def test_the_real_baseline_only_names_register_patterns():
    entries, _ = gate.load_register(REPO)
    known = {f"{e['id']}/{p['id']}" for e in entries for p in e.get("must_not_create") or []}
    baseline = gate.load_baseline(REPO)
    assert set(baseline) <= known


def test_the_gate_is_wired_into_the_static_tag():
    text = (REPO / "scripts" / "verify.py").read_text(encoding="utf-8")
    start = text.index('Gate("reuse-register"')
    block = text[start:text.index("Gate(", start + 10)]
    assert 'tags=["static"' in block


def test_the_untenanted_reads_limit_carries_no_slack():
    ratchets = json.loads((REPO / "verification_baseline.json").read_text(encoding="utf-8"))["ratchets"]
    limit = ratchets["untenanted_reads"]
    assert limit <= UNTENANTED_READS_CEILING, (
        f"untenanted_reads limit {limit} is above the measured {UNTENANTED_READS_CEILING}: "
        "fix the new unscoped reads instead of raising the limit"
    )

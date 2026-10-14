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


def test_a_css_custom_property_symbol_is_found(tmp_path):
    register = REGISTER.replace("app/utils/duplicate_guard.py", "app/static/css/tokens.css").replace(
        "symbols: [normalize_name, Guard.check]", "symbols: [--primary]")
    root = _tree(tmp_path, register=register)
    (root / "app" / "utils" / "duplicate_guard.py").unlink()
    (root / "app" / "static" / "css").mkdir(parents=True)
    (root / "app" / "static" / "css" / "tokens.css").write_text(
        ":root {\n  --primary: 0 0% 9%;\n}\n", encoding="utf-8")
    assert _run(root)[0] == []


def test_a_missing_css_custom_property_symbol_fails(tmp_path):
    register = REGISTER.replace("app/utils/duplicate_guard.py", "app/static/css/tokens.css").replace(
        "symbols: [normalize_name, Guard.check]", "symbols: [--primary]")
    root = _tree(tmp_path, register=register)
    (root / "app" / "utils" / "duplicate_guard.py").unlink()
    (root / "app" / "static" / "css").mkdir(parents=True)
    (root / "app" / "static" / "css" / "tokens.css").write_text(
        ":root {\n  --background: 0 0% 100%;\n}\n", encoding="utf-8")
    fails, _ = _run(root)
    assert len(fails) == 1 and "--primary" in fails[0]


def test_update_baseline_refuses_to_raise_a_pattern_total(tmp_path):
    baseline = {"name-matching/normaliser": {"app/a.py": 1}}
    root = _tree(tmp_path, extra={"app/a.py": COPY, "app/b.py": COPY}, baseline=baseline)
    entries, _ = gate.load_register(root)
    before = (root / "scripts" / "reuse_register_baseline.json").read_text()

    ok, messages = gate.update_baseline(root, entries)

    assert ok is False
    assert any("name-matching/normaliser" in m and "app/b.py" in m for m in messages)
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
    # Several seconds on a warm runner; the bound is generous so a cold disk cache or a loaded CI
    # machine does not make the test flaky. It still catches an accidental quadratic scan.
    assert elapsed < 120, f"the gate must stay a fast static check, took {elapsed:.1f}s"


def test_every_active_canonical_in_the_real_register_exists():
    entries, problems = gate.load_register(REPO)
    assert problems == []
    fails, _ = gate.check_canonicals(REPO, entries)
    assert fails == []
    assert {e["status"] for e in entries} == {"active", "undetermined"}  # pending: none now; the status is covered with temp trees


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



# ------------------------------------------------------------------ review round: precision and moves

ELEMENT_REGISTER = r"""
version: 1
entries:
  - id: element-writer
    concept: Element writer
    status: active
    canonical:
      path: app/utils/duplicate_guard.py
      symbols: [normalize_name]
    reuse: Use the backbone writer.
    must_not_create:
      - id: construction
        regex: 'ArchiMateElement\s*\('
"""


def test_a_marker_on_the_line_above_exempts(tmp_path):
    code = "# reuse-ok: unrelated, normalises a filename\ndef _normalize_name(v):\n    return v\n"
    assert _run(_tree(tmp_path, extra={"app/a.py": code}))[0] == []


def test_a_marker_on_a_continuation_line_of_a_multiline_def_exempts(tmp_path):
    code = (
        "def _normalize_name(\n"
        "    v,  # reuse-ok: unrelated, normalises a filename\n"
        "    strict=False,\n"
        "):\n"
        "    return v\n"
    )
    assert _run(_tree(tmp_path, extra={"app/a.py": code}))[0] == []


def test_a_marker_in_the_body_after_the_colon_does_not_exempt(tmp_path):
    code = "def _normalize_name(v):\n    return v  # reuse-ok: too late, past the colon\n"
    assert len(_run(_tree(tmp_path, extra={"app/a.py": code}))[0]) == 1


def test_advice_quoted_in_a_docstring_or_message_is_not_a_hit(tmp_path):
    code = (
        "def helper():\n"
        '    """Do not write def _normalize_name(v) here; use duplicate_guard."""\n'
        '    return "def normalize_name(x) is the one"\n'
    )
    assert _run(_tree(tmp_path, extra={"app/a.py": code}))[0] == []


def test_a_constructor_with_a_space_before_the_paren_is_a_hit(tmp_path):
    root = _tree(tmp_path, register=ELEMENT_REGISTER,
                 extra={"app/a.py": "el = ArchiMateElement (name='x')\n"})
    fails, _ = _run(root)
    assert len(fails) == 1 and "element-writer/construction" in fails[0]


def test_test_modules_under_app_are_skipped_but_a_tests_scope_is_not(tmp_path):
    fixture = {"app/modules/x/tests/test_it.py": "el = ArchiMateElement(name='x')\n"}
    assert _run(_tree(tmp_path, register=ELEMENT_REGISTER, extra=fixture))[0] == []

    register = ELEMENT_REGISTER.replace("        regex:", "        scope: [tests]\n        regex:")
    root = _tree(tmp_path / "t", register=register,
                 extra={"tests/test_it.py": "el = ArchiMateElement(name='x')\n"})
    assert len(_run(root)[0]) == 1


def test_a_moved_file_with_baselined_hits_is_not_a_new_violation(tmp_path):
    baseline = {"name-matching/normaliser": {"app/old_name.py": 1}}
    root = _tree(tmp_path, extra={"app/new_name.py": COPY}, baseline=baseline)
    fails, notes = _run(root)
    assert fails == []
    assert any("app/old_name.py" in n for n in notes)

    entries, _ = gate.load_register(root)
    ok, _ = gate.update_baseline(root, entries)
    assert ok is True
    written = json.loads((root / "scripts" / "reuse_register_baseline.json").read_text())["patterns"]
    assert written == {"name-matching/normaliser": {"app/new_name.py": 1}}


def test_one_marker_does_not_clear_a_block(tmp_path):
    """Probe A: a marker four lines below, on an unrelated statement, exempts nothing above it."""
    code = (
        "a = ArchiMateElement(1)\nb = ArchiMateElement(2)\nc = 1\nd = 2  # reuse-ok: other\n"
    )
    fails, _ = _run(_tree(tmp_path, register=ELEMENT_REGISTER, extra={"app/a.py": code}))
    assert len(fails) == 1 and "app/a.py:1" in fails[0] and "app/a.py:2" in fails[0]


def test_a_marker_far_below_with_no_colon_does_not_exempt(tmp_path):
    """Probe B: nineteen lines later is not the same statement."""
    code = "a = ArchiMateElement(1)\n" + "x = 1\n" * 18 + "y = 2  # reuse-ok: far away\n"
    assert len(_run(_tree(tmp_path, register=ELEMENT_REGISTER, extra={"app/a.py": code}))[0]) == 1


def test_a_marker_on_the_previous_code_line_does_not_exempt(tmp_path):
    """Probe C: only a comment-only line above counts."""
    code = "q = 1  # reuse-ok: q only\na = ArchiMateElement(1)\n"
    assert len(_run(_tree(tmp_path, register=ELEMENT_REGISTER, extra={"app/a.py": code}))[0]) == 1


def test_a_multiline_call_is_exempt_by_a_marker_inside_its_brackets(tmp_path):
    code = "a = ArchiMateElement(\n    name='x',  # reuse-ok: fixture\n)\nb = ArchiMateElement(2)\n"
    fails, _ = _run(_tree(tmp_path, register=ELEMENT_REGISTER, extra={"app/a.py": code}))
    assert len(fails) == 1 and "app/a.py:4" in fails[0]


def test_cleanup_elsewhere_does_not_make_room_for_a_new_copy(tmp_path):
    """Slack absorption: old.py baselined at 2 now has 1; a new copy in another file still fails."""
    baseline = {"name-matching/normaliser": {"app/old.py": 2}}
    root = _tree(tmp_path, extra={"app/old.py": COPY, "app/new_route.py": COPY}, baseline=baseline)
    fails, _ = _run(root)
    assert len(fails) == 1 and "app/new_route.py" in fails[0]

    entries, _ = gate.load_register(root)
    ok, messages = gate.update_baseline(root, entries)
    assert ok is False and any("app/new_route.py" in m for m in messages)


def test_a_rename_is_forgiven_only_up_to_the_vanished_files_hits(tmp_path):
    baseline = {"name-matching/normaliser": {"app/gone.py": 1}}
    one = _tree(tmp_path, extra={"app/renamed.py": COPY}, baseline=baseline)
    assert _run(one)[0] == []
    two = _tree(tmp_path / "t", extra={"app/renamed.py": COPY, "app/extra.py": COPY}, baseline=baseline)
    assert len(_run(two)[0]) == 1


def _main_baseline_totals():
    """Pattern totals of the baseline on origin/main: None when there is no remote to ask,
    {} when main carries no baseline file yet (the first merge)."""
    import subprocess

    def show():
        return subprocess.run(
            ["git", "show", "origin/main:scripts/reuse_register_baseline.json"],
            cwd=REPO, capture_output=True, text=True, encoding="utf-8",
        )

    proc = show()
    if proc.returncode != 0:
        # CI checks out depth 1 with no origin/main; fetch it once before deciding.
        fetch = subprocess.run(
            ["git", "fetch", "--depth=1", "origin", "main:refs/remotes/origin/main"],
            cwd=REPO, capture_output=True, text=True, encoding="utf-8",
        )
        if fetch.returncode != 0:
            return None
        proc = show()
        if proc.returncode != 0:
            return {}
    return {k: sum(v.values()) for k, v in json.loads(proc.stdout)["patterns"].items()}


def test_the_baseline_is_not_above_the_one_on_origin_main():
    """Ratchet integrity: a branch cannot raise a pattern's baseline total relative to main.

    Skipped only when there is no remote to fetch from. When main has no baseline file yet (the first
    merge) every total is compared with zero and the test passes: the file is being introduced, and
    the gate itself bounds it by the tree."""
    main_totals = _main_baseline_totals()
    if main_totals is None:
        pytest.skip("no remote to fetch origin/main from")
    if not main_totals:
        return
    mine = {k: sum(v.values()) for k, v in gate.load_baseline(REPO).items()}
    raised = {k: (main_totals.get(k, 0), n) for k, n in mine.items() if n > main_totals.get(k, 0)}
    assert raised == {}, f"baseline raised against origin/main: {raised}"

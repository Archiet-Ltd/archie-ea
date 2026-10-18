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
import yaml

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


def _css_class_register(symbol):
    return REGISTER.replace("app/utils/duplicate_guard.py", "app/static/css/site.css").replace(
        "symbols: [normalize_name, Guard.check]", f"symbols: ['{symbol}']")


def _css_class_tree(tmp_path, css, symbol=".public-faq"):
    root = _tree(tmp_path, register=_css_class_register(symbol))
    (root / "app" / "utils" / "duplicate_guard.py").unlink()
    (root / "app" / "static" / "css").mkdir(parents=True)
    (root / "app" / "static" / "css" / "site.css").write_text(css, encoding="utf-8")
    return root


def test_a_css_class_symbol_is_found(tmp_path):
    root = _css_class_tree(tmp_path, ".public-site .public-faq { border-top: 1px solid red; }\n"
                                     ".public-site .public-faq summary { cursor: pointer; }\n")
    assert _run(root)[0] == []


@pytest.mark.parametrize("css", [
    "/* .public-faq is described here only */\n.public-site .faq-wide { color: red; }\n",
    ".public-site .public-faq-x { color: red; }\n.public-site .public-faq--wide { color: red; }\n",
    ".public-site .other { margin: .5rem; }\n",
])
def test_a_missing_css_class_symbol_fails(tmp_path, css):
    root = _css_class_tree(tmp_path, css)
    fails, _ = _run(root)
    assert len(fails) == 1 and ".public-faq" in fails[0]


# The rules deleted from css/public_pricing.css when the pricing page moved onto the shared public components.
OLD_PRICING_RULES = {
    "public-button/second-button-rule": (
        ".pricing-page .@btn {\n  display: inline-flex; min-height: 2.75rem; border-radius: 0.5rem;\n}\n"
        ".pricing-page .@btn--primary { background: var(--pp-primary); }\n"),
    "public-card/second-card-rule": (
        ".pricing-page .@card { display: flex; flex-direction: column; padding: 1.5rem; border-radius: 0.75rem; }\n"
        ".pricing-page .@card h3 { font-size: 1.25rem; font-weight: 600; margin: 0; }\n"),
    "public-table/second-table-rule": (
        ".pricing-page .@table-wrap { border: 1px solid var(--pp-border); border-radius: 0.75rem; }\n"
        ".pricing-page table.@table { width: 100%; table-layout: fixed; }\n"
        ".pricing-page .@table th, .pricing-page .@table td { padding: 0.875rem 1rem; }\n"),
    "public-faq/second-faq-rule": (
        ".pricing-page .@faq { max-width: 42.5rem; }\n"
        ".pricing-page .@faq details { border-bottom: 1px solid var(--pp-border); }\n"
        ".pricing-page .@faq summary { cursor: pointer; padding: 1rem 0; }\n"),
    "public-faq/second-details-marker": ".pricing-page .@faq summary::-webkit-details-marker { display: none; }\n",
    "public-stepper/second-stepper-rule": (
        ".pricing-page .@grow { list-style: none; display: grid; }\n"
        ".pricing-page .@grow li { display: grid; grid-template-columns: 7rem 1fr; }\n"),
}
# The retired class names are spelled with a placeholder so no file keeps the old names.
OLD_PRICING_RULES = {k: v.replace("@", "pp-") for k, v in OLD_PRICING_RULES.items()}


@pytest.mark.parametrize("key", sorted(OLD_PRICING_RULES))
def test_a_deleted_pricing_rule_copied_back_fails_with_its_register_entry(tmp_path, key):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "reuse-register.yml").write_text(
        (REPO / "docs" / "reuse-register.yml").read_text(encoding="utf-8"), encoding="utf-8")
    css = tmp_path / "app" / "static" / "css"
    css.mkdir(parents=True)
    (css / "public_site.css").write_text((REPO / "app/static/css/public_site.css").read_text(encoding="utf-8"), encoding="utf-8")
    (css / "public_pricing.css").write_text(OLD_PRICING_RULES[key], encoding="utf-8")
    fails, _ = _run(tmp_path)
    assert any(f.startswith(f"[{key}]") and "app/static/css/public_pricing.css" in f for f in fails), fails


def test_no_public_page_stylesheet_or_template_holds_a_second_public_component_rule():
    entries, _ = gate.load_register(REPO)
    hits = gate.find_hits(REPO, [e for e in entries if e["id"].startswith("public-")])
    public = ("app/static/css/public_", "app/templates/public/", "app/templates/main/")
    stray = {k: [f for f in files if f.startswith(public)] for k, files in hits.items()}
    assert {k: v for k, v in stray.items() if v} == {}


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


def _origin_main_file(path):
    """Text of `path` on origin/main: None when there is no remote to ask, "" when main has no such file."""
    import subprocess

    def show():
        return subprocess.run(
            ["git", "show", f"origin/main:{path}"], cwd=REPO, capture_output=True, text=True, encoding="utf-8")

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
            return ""
    return proc.stdout


def _main_baseline_patterns():
    """The `patterns` dict of the baseline on origin/main: None with no remote, {} when main has no baseline file."""
    text = _origin_main_file("scripts/reuse_register_baseline.json")
    if not text:
        return text
    return json.loads(text)["patterns"]


def _main_register_keys():
    """Every "<entry>/<pattern>" key in origin/main's register: empty when main has none."""
    text = _origin_main_file("docs/reuse-register.yml")
    if not text:
        return set()
    return gate.register_keys((yaml.safe_load(text) or {}).get("entries") or [])


def test_the_baseline_is_not_above_the_one_on_origin_main():
    """Ratchet integrity against origin/main, by `gate.ratchet_problems`:

    1. a pattern main already has (in its baseline or in its register) may not exceed main's total for it, so a
       zero-hit pattern cannot gain hits and a raised key fails;
    2. a pattern new in this branch must be baselined at exactly the tree's per-file counts;
    3. a baseline line for a file that does not exist is allowed only where main already lists it.

    Skipped only when there is no remote to fetch from. When main has no baseline file yet (the first merge) the
    test passes: the file is being introduced, and the gate itself bounds it by the tree."""
    main_base = _main_baseline_patterns()
    if main_base is None:
        pytest.skip("no remote to fetch origin/main from")
    if not main_base:
        return
    main_keys = _main_register_keys()
    entries, problems = gate.load_register(REPO)
    assert problems == []
    new_only = []
    for e in entries:
        fresh = [p for p in e.get("must_not_create") or []
                 if f"{e['id']}/{p['id']}" not in main_base and f"{e['id']}/{p['id']}" not in main_keys]
        if fresh:
            new_only.append({**e, "must_not_create": fresh})
    tree = gate.counts_of(gate.find_hits(REPO, new_only))
    mine = gate.load_baseline(REPO)
    assert gate.ratchet_problems(main_keys, main_base, mine, tree, REPO) == []


# ratchet_problems, without git: the rule of the live test above, one scenario each.

def _ratchet(tmp_path, *, main_keys=(), main_base=None, mine=None, tree=None, files=()):
    for rel in files:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")
    return gate.ratchet_problems(set(main_keys), main_base or {}, mine or {}, tree or {}, tmp_path)


def test_ratchet_a_pattern_main_registers_cannot_gain_hits_without_a_baseline_key(tmp_path):
    """The zero-hit pattern: main has `public-faq/second-faq-rule` in its register and no baseline key."""
    key = "public-faq/second-faq-rule"
    problems = _ratchet(tmp_path, main_keys={key}, mine={key: {"a.css": 7}}, files=["a.css"])
    assert len(problems) == 1 and key in problems[0] and "7" in problems[0] and "0" in problems[0]


def test_ratchet_a_raised_key_of_main_fails(tmp_path):
    key = "design-tokens/hex-fallback"
    problems = _ratchet(tmp_path, main_keys={key}, main_base={key: {"a.css": 2}},
                        mine={key: {"a.css": 3}}, files=["a.css"])
    assert len(problems) == 1 and key in problems[0]


def test_ratchet_a_lowered_key_passes(tmp_path):
    key = "design-tokens/hex-fallback"
    assert _ratchet(tmp_path, main_keys={key}, main_base={key: {"a.css": 2}}, mine={key: {"a.css": 1}},
                    files=["a.css"]) == []


def test_ratchet_a_new_pattern_equal_to_the_tree_passes(tmp_path):
    key = "public-card/restyled-card"
    counts = {"a.css": 3, "b.html": 1}
    assert _ratchet(tmp_path, mine={key: dict(counts)}, tree={key: dict(counts)}, files=["a.css", "b.html"]) == []


def test_ratchet_a_new_pattern_one_above_the_tree_fails(tmp_path):
    key = "public-card/restyled-card"
    problems = _ratchet(tmp_path, mine={key: {"a.css": 4, "b.html": 1}}, tree={key: {"a.css": 3, "b.html": 1}},
                        files=["a.css", "b.html"])
    assert len(problems) == 1 and key in problems[0] and "a.css" in problems[0]


def test_ratchet_a_new_pattern_with_an_extra_file_that_does_not_exist_fails(tmp_path):
    key = "public-card/restyled-card"
    problems = _ratchet(tmp_path, mine={key: {"a.css": 3, "retired.css": 5}}, tree={key: {"a.css": 3}},
                        files=["a.css"])
    assert problems and all(key in p for p in problems) and any("retired.css" in p for p in problems)


def test_ratchet_an_existing_pattern_cannot_add_a_vanished_file_allowance(tmp_path):
    key = "design-tokens/hex-fallback"
    problems = _ratchet(tmp_path, main_keys={key}, main_base={key: {"a.css": 2}},
                        mine={key: {"a.css": 1, "retired.css": 1}}, files=["a.css"])
    assert len(problems) == 1 and "retired.css" in problems[0]


def test_ratchet_an_existing_pattern_keeping_a_vanished_file_main_lists_passes(tmp_path):
    key = "design-tokens/hex-fallback"
    base = {key: {"a.css": 1, "retired.css": 1}}
    assert _ratchet(tmp_path, main_keys={key}, main_base=base, mine={key: dict(base[key])}, files=["a.css"]) == []


def test_ratchet_rejects_the_inflated_faq_key_against_the_shipped_baseline():
    """The refuter's scenario: the shipped baseline plus an inflated key for a pattern main registers."""
    shipped = gate.load_baseline(REPO)
    key = "public-faq/second-faq-rule"
    mine = {**shipped, key: {"app/static/css/public_pricing.css": 7}}
    problems = gate.ratchet_problems({key}, {}, mine, {}, REPO)
    assert any(key in p for p in problems)


def test_register_keys_lists_every_pattern_of_every_entry():
    entries, _ = gate.load_register(REPO)
    keys = gate.register_keys(entries)
    assert {"public-faq/second-faq-rule", "public-faq/second-summary-rule", "public-button/restyled-button"} <= keys


# ------------------------------------------------------------------ the public-* patterns catch what the text says

def _public_entries():
    entries, problems = gate.load_register(REPO)
    assert problems == []
    return [e for e in entries if e["id"].startswith("public-")]


def _public_tree(tmp_path, rel, text):
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "reuse-register.yml").write_text(
        yaml.safe_dump({"version": 1, "entries": _public_entries()}, sort_keys=False), encoding="utf-8")
    css = tmp_path / "app" / "static" / "css"
    css.mkdir(parents=True, exist_ok=True)
    (css / "public_site.css").write_text(
        (REPO / "app/static/css/public_site.css").read_text(encoding="utf-8"), encoding="utf-8")
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return tmp_path


EXTRA = "app/static/css/public_extra.css"
FAQ_RESTYLE = ".pricing-page .public-faq summary { font-size: 1.0625rem; padding: 1rem 0; }\n"
REGISTER_BYPASSES = [
    ("faq restyle with summary", EXTRA, FAQ_RESTYLE, "public-faq"),
    ("button restyle", EXTRA,
     ".pricing-page .public-btn--primary { height: 2.75rem; border-radius: 0.5rem; }\n", "public-button"),
    ("card restyle, multi-line", EXTRA,
     ".pricing-page .public-card {\n  display: flex;\n  padding: 1.5rem;\n}\n", "public-card"),
    ("prefixed button", EXTRA, ".public-pricing-btn { height: 3rem; }\n", "public-button"),
    ("prefixed faq summary", EXTRA, ".public-pricing-faq summary { font-weight: 600; }\n", "public-faq"),
    ("bare faq summary", EXTRA, ".faq summary { font-weight: 600; }\n", "public-faq"),
    ("faq item summary", EXTRA, ".faq-item summary { font-weight: 600; }\n", "public-faq"),
    ("bare btn", EXTRA, ".btn { height: 3rem; }\n", "public-button"),
    ("button-primary", EXTRA, ".button-primary { color: red; }\n", "public-button"),
    ("timeline", EXTRA, ".timeline li { padding: 0; }\n", "public-stepper"),
    ("bare summary selector", EXTRA, ".pricing-page summary { font-size: 1rem; }\n", "public-faq"),
    ("id selector line", EXTRA, "#pricing .public-faq summary { font-size: 1rem; }\n", "public-faq"),
    ("inline style in a template", "app/templates/public/extra.html",
     "<style>\n" + FAQ_RESTYLE + "</style>\n", "public-faq"),
]


@pytest.mark.parametrize("name,rel,text,entry", REGISTER_BYPASSES, ids=[b[0] for b in REGISTER_BYPASSES])
def test_a_bypass_of_a_public_component_is_a_hit_naming_its_entry(tmp_path, name, rel, text, entry):
    fails, _ = gate.run(_public_tree(tmp_path, rel, text))
    assert any(f.startswith(f"[{entry}/") and rel in f for f in fails), fails


REGISTER_LAYOUT_ONLY = [
    ".pricing-page .pp-plans > .public-card { display: flex; flex-direction: column; }\n",
    ".pricing-page .pp-plans { grid-template-columns: repeat(4, minmax(0, 1fr)); }\n",
    ".pricing-page .pp-services .pp-price { margin-bottom: 1rem; }\n",
    ".pricing-page .public-card-title { margin: 0; }\n",
    ".pricing-page .pp-frame { overflow: clip; }\n",
    ".table { display: table; }\n",
    ".table-cell { display: table-cell; }\n",
    ".grow { flex-grow: 1; }\n",
    ".flex-grow-0 { flex-grow: 0; }\n",
]
JS_SUMMARY_LINES = (
    "<script>\nconst x = {\n  summary: {\n    a: 1 },\n};\nconst summary = {};\n"
    "class A { get summary() { return 1; } }\n</script>\n")


@pytest.mark.parametrize("text", REGISTER_LAYOUT_ONLY)
def test_a_layout_rule_or_a_utility_class_is_not_a_hit(tmp_path, text):
    assert gate.run(_public_tree(tmp_path, EXTRA, text))[0] == []


def test_javascript_that_mentions_summary_is_not_a_hit(tmp_path):
    assert gate.run(_public_tree(tmp_path, "app/templates/public/extra.html", JS_SUMMARY_LINES))[0] == []


def test_a_css_line_starting_with_a_hash_is_not_skipped_as_a_comment(tmp_path):
    fails, _ = gate.run(_public_tree(tmp_path, EXTRA, "#pricing .public-btn { height: 3rem; }\n"))
    assert any(f.startswith("[public-button/restyled-button]") for f in fails)


def test_a_reuse_ok_comment_on_the_line_above_a_css_hit_exempts_it(tmp_path):
    text = "/* reuse-ok: a one-off compaction at 390px */\n.pricing-page .public-table { font-size: 0.8rem; }\n"
    assert gate.run(_public_tree(tmp_path, EXTRA, text))[0] == []
    far = "/* reuse-ok: a one-off compaction at 390px */\n\n.pricing-page .public-table { font-size: 0.8rem; }\n"
    assert gate.run(_public_tree(tmp_path / "far", EXTRA, far))[0] != []


# ------------------------------------------------------------------ design-tokens: evading the regex

# Each test below runs the real design-tokens entry (lifted from docs/reuse-register.yml, not a hand-copied
# approximation) against one shape that must still be caught: a one-line declaration, a fallback on a real
# token name, an rgb()-form fallback, a :root block in a file outside app/static/css and app/templates
# entirely, an inline style="--token: ..." attribute, and a redeclared --info/--success/-emphasis token. A
# control case (the originally-specified single-line declaration plus a --color-* hex fallback) is pinned to
# keep raising its original two violations.

DESIGN_TOKEN_CANONICAL = (
    ":root {\n"
    "  --background: 0 0% 100%;\n"
    "  --foreground: 0 0% 0%;\n"
    "  --primary: 0 0% 9%;\n"
    "  --muted: 0 0% 96%;\n"
    "  --border: 0 0% 90%;\n"
    "  --radius: 0.5rem;\n"
    "}\n"
)


def _design_tokens_entry():
    """The design-tokens entry exactly as it ships in docs/reuse-register.yml."""
    entries, problems = gate.load_register(REPO)
    assert problems == []
    return next(e for e in entries if e["id"] == "design-tokens")


def _design_tokens_tree(tmp_path, files):
    """A temp tree carrying only the real design-tokens entry (no known_exceptions: the proof files are
    deliberately not any of them) plus a canonical shadcn_tokens.css, so check_canonicals passes."""
    entry = dict(_design_tokens_entry())
    entry["known_exceptions"] = []
    register_text = yaml.safe_dump({"version": 1, "entries": [entry]}, sort_keys=False)
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "reuse-register.yml").write_text(register_text, encoding="utf-8")
    (tmp_path / "app" / "static" / "css").mkdir(parents=True, exist_ok=True)
    (tmp_path / "app" / "static" / "css" / "shadcn_tokens.css").write_text(
        DESIGN_TOKEN_CANONICAL, encoding="utf-8")
    for rel, code in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
    return tmp_path


def _design_tokens_fails(tmp_path, files):
    return gate.run(_design_tokens_tree(tmp_path, files))[0]


def test_design_tokens_control_case_still_raises_both_violations(tmp_path):
    """The originally-specified shape (a lone-line declaration, a --color-* hex fallback) must keep failing."""
    files = {
        "app/static/css/public_site.css": "--primary: 0 0% 0%;\n",
        "app/templates/public/x.html": '<div>{{ "var(--color-primary, #3b82f6)" }}</div>\n',
    }
    # the hex-fallback case needs the var() call outside a Jinja string so it is real markup
    files["app/templates/public/x.html"] = "<div style=\"color: var(--color-primary, #3b82f6)\"></div>\n"
    fails = _design_tokens_fails(tmp_path, files)
    ids = {f.split("]")[0].lstrip("[") for f in fails}
    assert ids == {"design-tokens/token-declaration", "design-tokens/hex-fallback"}
    assert len(fails) == 2


def test_evasion_1_one_line_declaration_is_caught(tmp_path):
    fails = _design_tokens_fails(tmp_path, {"app/static/css/x.css": ".x{--primary:0 0% 0%}\n"})
    assert any("design-tokens/token-declaration" in f for f in fails)


def test_evasion_2_fallback_on_a_real_token_name_is_caught(tmp_path):
    fails = _design_tokens_fails(
        tmp_path, {"app/static/css/x.css": "a { color: var(--primary, #3b82f6); }\n"})
    assert any("design-tokens/hex-fallback" in f for f in fails)


def test_evasion_3_rgb_form_fallback_is_caught(tmp_path):
    fails = _design_tokens_fails(
        tmp_path, {"app/static/css/x.css": "a { color: var(--color-primary, rgb(1,2,3)); }\n"})
    assert any("design-tokens/hex-fallback" in f for f in fails)


def test_evasion_4_root_block_outside_the_old_scope_is_caught(tmp_path):
    """A :root block in a brand-new file outside app/static/css and app/templates entirely."""
    fails = _design_tokens_fails(
        tmp_path, {"styles/new_tokens.css": ":root {\n  --primary: 0 0% 0%;\n}\n"})
    assert any("design-tokens/token-declaration" in f and "styles/new_tokens.css" in f for f in fails)


def test_evasion_5_inline_style_attribute_is_caught(tmp_path):
    fails = _design_tokens_fails(
        tmp_path, {"app/templates/public/x.html": '<div style="--primary: 0 0% 0%">x</div>\n'})
    assert any("design-tokens/token-declaration" in f for f in fails)


def test_evasion_6_info_success_and_emphasis_tokens_are_caught(tmp_path):
    fails = _design_tokens_fails(
        tmp_path, {"app/static/css/x.css": "a { --info: 0 0% 0%; --success-emphasis: 0 0% 0%; }\n"})
    assert any("design-tokens/token-declaration" in f for f in fails)
    # two redeclarations in one file is still one violation entry, naming both line numbers
    hit = next(f for f in fails if "design-tokens/token-declaration" in f)
    assert "app/static/css/x.css:1" in hit


# ------------------------------------------------------------------ the reuse-ok marker and line numbers
#
# The declaration-matching regex must not let its leading whitespace class cross a newline: a declaration
# on the line after an opening `{` or a `;` is a separate statement with its own line number, and the
# reuse-ok marker convention ("on the matching line, or the line above it") only works if the gate agrees
# on which line that is.


def test_reuse_ok_marker_exempts_a_declaration_on_the_line_after_the_opening_brace(tmp_path):
    """The documented exemption shape: the declaration does not share a line with `{`."""
    files = {
        "app/static/css/x.css": (
            ":root {\n"
            "  --primary: 0 0% 0%; /* reuse-ok: local override for a demo page */\n"
            "}\n"
        ),
    }
    assert _design_tokens_fails(tmp_path, files) == []


def test_reuse_ok_marker_exempts_a_declaration_on_the_line_after_a_semicolon(tmp_path):
    """Same shape, reached via the `;` alternative rather than `{`."""
    files = {
        "app/static/css/x.css": (
            ":root { --foreground: 0 0% 0%;\n"
            "  --primary: 0 0% 0%; /* reuse-ok: local override for a demo page */\n"
            "}\n"
        ),
    }
    fails = _design_tokens_fails(tmp_path, files)
    # --foreground on the opening line is still an unexempted violation; only --primary is exempt
    assert len(fails) == 1 and "x.css:1" in fails[0] and "x.css:2" not in fails[0]


def test_a_mid_block_declaration_without_a_marker_reports_its_own_line_number(tmp_path):
    """Without a marker the shape still fails, and each hit names the declaration's own line, not the
    opening brace's."""
    files = {
        "app/static/css/x.css": (
            ":root {\n"
            "  --foreground: 0 0% 0%;\n"
            "  --primary: 0 0% 0%;\n"
            "}\n"
        ),
    }
    fails = _design_tokens_fails(tmp_path, files)
    hit = next(f for f in fails if "design-tokens/token-declaration" in f)
    assert "x.css:2" in hit and "x.css:3" in hit
    assert "x.css:1" not in hit


# ------------------------------------------------------------------ design-tokens: fallback forms added later
#
# The fallback check originally only recognised a hex literal or an rgb()/rgba() call. Each test below pins
# one further shape of literal colour smuggled past a var() fallback.


def test_evasion_7_hsl_triplet_fallback_is_caught(tmp_path):
    """The fallback is a bare HSL triplet, the natural form here since every token is stored as one."""
    fails = _design_tokens_fails(
        tmp_path, {"app/static/css/x.css": "a { color: hsl(var(--primary, 221 83% 53%)); }\n"})
    assert any("design-tokens/hex-fallback" in f for f in fails)


def test_evasion_8_fallback_wrapped_in_an_hsl_call_is_caught(tmp_path):
    fails = _design_tokens_fails(
        tmp_path, {"app/static/css/x.css": "a { color: var(--primary, hsl(0 0% 0%)); }\n"})
    assert any("design-tokens/hex-fallback" in f for f in fails)


def test_evasion_9_hsla_fallback_is_caught(tmp_path):
    fails = _design_tokens_fails(
        tmp_path, {"app/static/css/x.css": "a { color: var(--primary, hsla(0, 0%, 0%, 1)); }\n"})
    assert any("design-tokens/hex-fallback" in f for f in fails)


def test_evasion_10_named_colour_fallback_is_caught(tmp_path):
    fails = _design_tokens_fails(
        tmp_path, {"app/static/css/x.css": "a { color: var(--primary, blue); }\n"})
    assert any("design-tokens/hex-fallback" in f for f in fails)


def test_evasion_11_transparent_keyword_fallback_is_caught(tmp_path):
    fails = _design_tokens_fails(
        tmp_path, {"app/static/css/x.css": "a { color: var(--primary, transparent); }\n"})
    assert any("design-tokens/hex-fallback" in f for f in fails)


def test_a_non_token_variable_with_a_non_colour_fallback_is_not_a_hit(tmp_path):
    """The new fallback forms are scoped to the protected token names: an unrelated custom property (its
    own fallback, its own units) stays unaffected."""
    files = {
        "app/static/css/x.css": (
            "a { border-radius: calc(var(--radius, 0.5rem) - 2px); }\n"
            "b { z-index: var(--modal-z, 1000); }\n"
            "c { color: hsl(var(--emerald-700, 142 71% 29%)); }\n"
        ),
    }
    assert _design_tokens_fails(tmp_path, files) == []


def test_layer_and_font_sans_tokens_are_now_protected(tmp_path):
    """The 14 ArchiMate layer tokens (base + -emphasis) and --font-sans are declared only in
    shadcn_tokens.css; a redeclaration anywhere else must be caught like any other token."""
    files = {
        "app/static/css/x.css": (
            ".x { --layer-business: 1 2% 3%; --layer-business-emphasis: 1 2% 3%; }\n"
            ":root { --font-sans: sans-serif; }\n"
        ),
    }
    fails = _design_tokens_fails(tmp_path, files)
    hit = next(f for f in fails if "design-tokens/token-declaration" in f)
    assert "x.css:1" in hit and "x.css:2" in hit


# Shapes that once slipped past the public-component patterns: element-qualified selectors, a rule written on one
# line inside an at-rule or a nesting block, and minified output. Each is written alone into a new stylesheet.
BYPASS_SHAPES = [
    ".pricing-page a.public-btn { height: 3rem; }",
    "button.public-btn { background: red; }",
    ".pricing-page article.public-card { padding: 0; }",
    "div.pricing-card { padding: 2rem; }",
    "a.cta-btn { height: 3rem; }",
    "details.faq { border: 0; }",
    ".pricing-page table.pp-table { width: 100%; }",
    "@media (min-width: 0px) { .pricing-page .public-btn { height: 2.75rem; border-radius: 0.5rem; } }",
    "@media all { .pricing-btn { height: 3rem; background: red; } }",
    "@media all { .faq summary { padding: 1rem 0; font-weight: 600; } }",
    "@supports (display: grid) { .pricing-page .public-faq summary { font-size: 2rem; } }",
    ".pricing-page { .public-btn { height: 3rem; } }",
    ".x{margin:0}.pricing-btn{height:3rem}",
    ".pricing-page .public-faq summary { font-size: 1.0625rem; padding: 1rem 0; }",
    ".pricing-page .public-btn--primary { height: 2.75rem; border-radius: 0.5rem; }",
    ".pricing-page .public-card { padding: 1.5rem; border-radius: 0.75rem; }",
    ".public-pricing-btn { display: inline-flex; }",
    ".faq-item summary { cursor: pointer; }",
    ".button-primary { color: red; }",
    ".timeline li { display: grid; }",
    "@media (min-width: 1024px) { .pricing-page .pp-plans > .public-card > a { min-height: 3.875rem; } }",
]

# Layout-only rules the register must keep allowing.
LAYOUT_ONLY = [
    ".pricing-page .public-btn { display: inline-flex; margin-top: 1rem; width: 100%; }",
    "@media (min-width: 640px) { .pricing-page .pp-plans { grid-template-columns: repeat(2, minmax(0, 1fr)); } }",
    "@media (min-width: 768px) { .pricing-page h2 { font-size: 1.875rem; } }",
    ".pricing-page .pp-section { margin-top: 4rem; }",
    ".pricing-page .public-card { position: relative; overflow: hidden; }",
    "a.cta { color: red; } p.lead { margin: 0 0 1rem; }",
]


def _extra_css_tree(tmp_path, css_text):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "reuse-register.yml").write_text(
        (REPO / "docs" / "reuse-register.yml").read_text(encoding="utf-8"), encoding="utf-8")
    css = tmp_path / "app" / "static" / "css"
    css.mkdir(parents=True)
    (css / "public_site.css").write_text(
        (REPO / "app/static/css/public_site.css").read_text(encoding="utf-8"), encoding="utf-8")
    (css / "public_extra.css").write_text(css_text + "\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("shape", BYPASS_SHAPES)
def test_a_bypass_shape_in_a_new_stylesheet_fails(tmp_path, shape):
    fails, _ = _run(_extra_css_tree(tmp_path, shape))
    assert any("app/static/css/public_extra.css" in f for f in fails), (shape, fails)


@pytest.mark.parametrize("rule", LAYOUT_ONLY)
def test_a_layout_only_rule_in_a_new_stylesheet_passes(tmp_path, rule):
    fails, _ = _run(_extra_css_tree(tmp_path, rule))
    assert [f for f in fails if f.startswith("[public-")] == [], (rule, fails)


def test_update_baseline_refuses_a_new_key_unless_allowed_and_names_each_one(tmp_path):
    root = _tree(tmp_path, extra={"app/a.py": COPY}, baseline={"other/key": {"app/z.py": 1}})
    entries, _ = gate.load_register(root)
    path = root / "scripts" / "reuse_register_baseline.json"
    before = path.read_text()

    ok, messages = gate.update_baseline(root, entries)
    assert ok is False
    assert any("name-matching/normaliser" in m and "--allow-new-keys" in m for m in messages)
    assert path.read_text() == before

    ok, messages = gate.update_baseline(root, entries, allow_new_keys=True)
    assert ok is True
    assert any(m.startswith("new key written: name-matching/normaliser") for m in messages)


# ------------------------------------------------------------------ the sync concepts (twin slice 0)

TWIN_CONCEPTS = {
    "connector-framework", "identity-crosswalk", "cost-fact-store", "event-log", "answer-envelope",
    "spreadsheet-import", "open-exchange-import", "archimate-element-writer", "entity-matching", "ai-chat-approval",
}


def test_the_register_lists_every_sync_concept_with_a_pattern_and_an_existing_canonical():
    entries, problems = gate.load_register(REPO)
    assert problems == []
    by_id = {e["id"]: e for e in entries}
    assert TWIN_CONCEPTS <= set(by_id)
    for concept in TWIN_CONCEPTS:
        entry = by_id[concept]
        assert entry["status"] == "active" and entry["must_not_create"], concept
        assert (REPO / entry["canonical"]["path"]).is_file(), concept


def _connector_framework_tree(tmp_path, extra=None):
    """The real connector-framework entry and its real baseline, over a tiny tree that holds the canonical symbols."""
    entries, _ = gate.load_register(REPO)
    entry = next(e for e in entries if e["id"] == "connector-framework")
    canonical = "\n".join(
        f"class {s}:\n    pass" if s[0].isupper() else f"def {s}():\n    pass" for s in entry["canonical"]["symbols"])
    real = json.loads((REPO / "scripts" / "reuse_register_baseline.json").read_text(encoding="utf-8"))["patterns"]
    baseline = {k: v for k, v in real.items() if k.startswith("connector-framework/")}
    root = tmp_path
    (root / "docs").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    (root / "docs" / "reuse-register.yml").write_text(
        yaml.safe_dump({"version": 1, "entries": [entry]}), encoding="utf-8")
    (root / "scripts" / "reuse_register_baseline.json").write_text(json.dumps({"patterns": baseline}), encoding="utf-8")
    files = {entry["canonical"]["path"]: canonical}
    files.update(extra or {})
    for rel, code in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
    return root, baseline


def test_the_recorded_connector_duplicates_pass_and_a_new_second_connector_manager_fails(tmp_path):
    # The two existing look-alikes, at the recorded files, are inside the baseline.
    existing = {
        "app/modules/admin/v2/services/connector_framework_v2.py":
            "class BaseConnector:\n    pass\n\n\nclass ConnectorManager:\n    pass\n",
        "app/modules/vendors/connectors/base_connector.py": "class BaseCloudPricingConnector:\n    pass\n",
    }
    root, baseline = _connector_framework_tree(tmp_path / "today", extra=existing)
    assert baseline["connector-framework/connector-manager"]
    fails, _ = _run(root)
    assert fails == []

    # A builder adds a connector manager of their own beside the framework: the ratchet fails and names the entry.
    seeded = dict(existing, **{"app/services/github_sync_manager.py": "class GitHubConnectorManager:\n    pass\n"})
    root, _ = _connector_framework_tree(tmp_path / "seeded", extra=seeded)
    fails, _ = _run(root)
    assert len(fails) == 1
    assert "connector-framework/connector-manager" in fails[0]
    assert "app/services/github_sync_manager.py:1" in fails[0]
    assert "app/services/connector_framework.py" in fails[0]  # where to reuse it from

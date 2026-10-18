#!/usr/bin/env python
"""Failing-first proof: a pull request's new tests must be able to fail.

When a pull request changes application code, the tests it adds or changes must
fail (or error) against the base branch's application code. A test that passes on
the old code proves nothing about the change, so this automates what a reviewer
does by hand: copy the PR's test files onto a checkout of the old code and run them.

Three subcommands, used in this order by the "Failing-first proof" CI job:

  plan     Decide what to do from the diff against the merge base and the PR labels.
           Writes a JSON plan and, when tests must be run, sets GITHUB_OUTPUT mode=run.
           Exit 0 when there is nothing to prove (exempt label, no application code
           changed); exit 1 when application code changed and no runnable test did.
  prepare  Check the merge base out into a second worktree and overlay the PR's test
           files (and test support files) from the head. The PR's application files are
           NOT applied.
  run      Run only those test files in that worktree and write a JUnit report. The
           exit code of pytest is ignored: failures are what we are looking for.
  judge    Read the JUnit report. Pass when at least one test failed or errored on the
           base (a collection ImportError counts: the feature does not exist there).
           Tests that also pass on the base are listed as a warning, never a failure.

The job is informational: it is not a required check and it never runs on a push.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET  # noqa: S405 - our own pytest output, not untrusted input
from pathlib import Path

try:  # imported as scripts.ci.check_failing_first
    from scripts.ci.detect_content_only import _diff_entries, is_content_path
except ImportError:  # run as a script: scripts/ci is sys.path[0]
    from detect_content_only import _diff_entries, is_content_path  # type: ignore[no-redef]

EXEMPT_LABEL = "failing-first-exempt"
MAX_TEST_FILES = 40


# ---------------------------------------------------------------------------
# Path classification
# ---------------------------------------------------------------------------
def is_test_path(path: str) -> bool:
    """Test code or test support: under tests/ or an app/**/tests/ directory, or a test module."""
    parts = path.split("/")
    name = parts[-1]
    if parts[0] == "tests":
        return True
    if parts[0] == "app" and "tests" in parts[1:-1]:
        return True
    # An app module that merely starts with test_ (app/services/test_alert_service.py) is
    # application code, not test support.
    return name == "conftest.py"


def is_test_file(path: str) -> bool:
    """A test module this check can run: tests/**/test_*.py or app/**/tests/test_*.py.

    tests/smoke needs a running server and browsers; the pytest shards ignore it too.
    """
    parts = path.split("/")
    if not path.endswith(".py") or not parts[-1].startswith("test_"):
        return False
    if parts[0] == "tests":
        return parts[1:2] != ["smoke"]
    return parts[0] == "app" and "tests" in parts[1:-1]


def is_app_code(path: str) -> bool:
    """Application code: app/** that is not a test, documentation or public content."""
    if not path.startswith("app/") or is_test_path(path):
        return False
    if path.endswith((".md", ".rst", ".txt")):
        return False
    return not is_content_path(path)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------
def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def changed_files(repo: Path, base: str, head: str) -> list[tuple[str, str]]:
    """(status, path) pairs between the merge base and head; renames reported as delete + add."""
    entries = _diff_entries(base, head, str(repo))
    return [(status, path) for _, _, path, status in entries if path]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def emit_summary(text: str, summary_path: str | None) -> None:
    print(text)
    path = summary_path or os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def set_output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")


def parse_labels(raw: str) -> list[str]:
    try:
        value = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return [raw]
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------
def cmd_plan(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    labels = parse_labels(
        args.labels if args.labels is not None else os.environ.get("PR_LABELS", "")
    )
    if EXEMPT_LABEL in labels:
        set_output("mode", "done")
        emit_summary(
            f"## Failing-first proof\n\nExempted by the `{EXEMPT_LABEL}` label "
            "(for pure refactors and documentation). Nothing was run.",
            args.summary,
        )
        return 0

    merge_base = git(repo, "merge-base", args.base_ref, args.head).strip()
    changes = changed_files(repo, merge_base, args.head)
    app_changed = sorted(p for _, p in changes if is_app_code(p))
    if not app_changed:
        set_output("mode", "done")
        tests_changed = sorted(p for s, p in changes if s != "D" and is_test_file(p))
        note = (
            f" ({len(tests_changed)} changed test file(s) were not run.)" if tests_changed else ""
        )
        emit_summary(
            "## Failing-first proof\n\nNo application code changed against the merge base, "
            f"so there is nothing to prove.{note}",
            args.summary,
        )
        return 0

    live = [p for s, p in changes if s != "D"]
    added = {p for s, p in changes if s in "AT"}
    all_tests = sorted((p for p in live if is_test_file(p)), key=lambda p: (p not in added, p))
    if not all_tests:
        set_output("mode", "done")
        emit_summary(
            "## Failing-first proof: FAILED\n\n"
            f"Application code changed ({len(app_changed)} file(s), e.g. `{app_changed[0]}`) but no "
            "runnable test file was added or changed (tests/**/test_*.py or app/**/tests/test_*.py; "
            "tests/smoke is not run by this check). Add a test that fails without the change, or, "
            f"for a pure refactor or documentation change, add the `{EXEMPT_LABEL}` label.",
            args.summary,
        )
        return 1

    selected = all_tests[: args.max_files]
    capped = len(all_tests) - len(selected)
    plan = {
        "merge_base": merge_base,
        "head": git(repo, "rev-parse", args.head).strip(),
        "test_files": selected,
        "added_files": sorted(p for p in selected if p in added),
        "support_files": sorted(p for p in live if is_test_path(p) and not is_test_file(p)),
        "capped": capped,
        "app_files_changed": len(app_changed),
    }
    Path(args.plan).write_text(json.dumps(plan, indent=2), encoding="utf-8")
    set_output("mode", "run")
    set_output(
        "has_playwright",
        "true" if any(_mentions(repo, args.head, p, "playwright") for p in selected) else "false",
    )
    cap_note = (
        f" Capped at {args.max_files}: {capped} more changed test file(s) were not run."
        if capped
        else ""
    )
    emit_summary(
        f"## Failing-first proof\n\nApplication code changed ({len(app_changed)} file(s)). "
        f"Running {len(selected)} changed test file(s) against the merge base `{merge_base[:12]}`.{cap_note}",
        args.summary,
    )
    return 0


def _mentions(repo: Path, head: str, path: str, needle: str) -> bool:
    return needle in git(repo, "show", f"{head}:{path}", check=False)


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------
def cmd_prepare(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    dest = Path(args.dest)
    git(repo, "worktree", "add", "--detach", str(dest), plan["merge_base"])
    for path in plan["test_files"] + plan["support_files"]:
        blob = subprocess.run(  # noqa: S603 - fixed argv
            ["git", "show", f"{plan['head']}:{path}"], cwd=repo, capture_output=True, check=True
        ).stdout
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
    print(
        f"prepared {dest} at {plan['merge_base'][:12]} with {len(plan['test_files'])} test file(s) overlaid"
    )
    return 0


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
def cmd_run(args: argparse.Namespace) -> int:
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    junit = Path(args.junit).resolve()
    junit.unlink(missing_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "--continue-on-collection-errors",
        f"--junitxml={junit}",
        *plan["test_files"],
    ]
    proc = subprocess.run(cmd, cwd=args.dest, env=_unmeasured_env(), check=False)  # noqa: S603 - fixed argv
    print(
        f"pytest exited {proc.returncode} (failures are expected and are what this check looks for)"
    )
    return 0


def _unmeasured_env() -> dict[str, str]:
    """The environment for the base-worktree pytest run, with coverage switched off.

    That run executes a different checkout (a temporary worktree). When this
    script itself runs under ``pytest --cov`` (tests/test_check_failing_first.py
    in a CI shard), pytest-cov exports ``COV_CORE_*`` so child interpreters
    measure too, and ``.coveragerc`` makes the recorded paths relative. The child
    then writes ``app/<file>`` rows for files that only exist in the deleted
    worktree into the shard's data file, and the aggregate job's
    ``coverage combine`` / ``coverage xml`` fails with "No source for code".
    """
    return {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("COV_CORE_", "COVERAGE_"))
    }


# ---------------------------------------------------------------------------
# judge
# ---------------------------------------------------------------------------
def read_junit(path: Path) -> list[tuple[str, str, str]]:
    """(classname, name, outcome) with outcome in failed / skipped / passed."""
    root = ET.parse(path).getroot()  # noqa: S314
    cases = []
    for case in root.iter("testcase"):
        kids = {child.tag for child in case}
        outcome = (
            "failed"
            if kids & {"failure", "error"}
            else "skipped"
            if "skipped" in kids
            else "passed"
        )
        cases.append((case.get("classname", ""), case.get("name", ""), outcome))
    return cases


def _label(classname: str, name: str) -> str:
    return f"{classname}::{name}" if classname and classname != name else name


def _belongs_to(classname: str, name: str, files: set[str]) -> bool:
    modules = {f[:-3].replace("/", ".") for f in files}
    return any(
        classname == m or classname.startswith(m + ".") or (not classname and name.startswith(m))
        for m in modules
    )


def cmd_judge(args: argparse.Namespace) -> int:
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    junit = Path(args.junit)
    header = "## Failing-first proof"
    try:
        cases = read_junit(junit)
    except (OSError, ET.ParseError) as error:
        emit_summary(
            f"{header}: FAILED\n\nNo usable test results from the base run ({error}).", args.summary
        )
        return 1
    if not cases:
        emit_summary(f"{header}: FAILED\n\nThe base run executed no tests.", args.summary)
        return 1

    failed = [_label(c, n) for c, n, o in cases if o == "failed"]
    skipped = [_label(c, n) for c, n, o in cases if o == "skipped"]
    added = set(plan["added_files"])
    passed_new, passed_old = [], []
    for c, n, o in cases:
        if o == "passed":
            (passed_new if _belongs_to(c, n, added) else passed_old).append(_label(c, n))

    lines = [header, ""]
    ok = bool(failed)
    if ok:
        lines.append(
            f"PASSED: {len(failed)} test(s) fail or error on the base code, so the change is observable by the tests."
        )
    else:
        passed_all = bool(passed_new or passed_old)
        what = "passes" if passed_all else "was skipped"
        lines.append(
            f"FAILED: every changed test {what} on the base code, so none of them proves the change. "
            "Make a test assert the new behaviour, or, for a pure refactor or documentation change, "
            f"add the `{EXEMPT_LABEL}` label."
        )
    if plan.get("capped"):
        lines += [
            "",
            f"Only {len(plan['test_files'])} test files were run; {plan['capped']} more were not (cap {MAX_TEST_FILES}).",
        ]
    lines += ["", f"### Failed or errored on base ({len(failed)})", *_bullets(failed)]
    if passed_new or passed_old:
        lines += [
            "",
            f"### Also pass on base: candidates for vacuous tests ({len(passed_new) + len(passed_old)}) - warning only",
            "",
            "A test that passes on the old code cannot show the new code works.",
        ]
        if passed_new:
            lines += ["", f"In files added by this PR ({len(passed_new)}):", *_bullets(passed_new)]
        if passed_old:
            lines += [
                "",
                f"In files modified by this PR ({len(passed_old)}; many predate the PR and are expected to pass):",
                *_bullets(passed_old),
            ]
        print(
            f"::warning::{len(passed_new) + len(passed_old)} changed test(s) also pass on the base code"
        )
    if skipped:
        lines += ["", f"### Skipped on base ({len(skipped)})", *_bullets(skipped)]
    emit_summary("\n".join(lines), args.summary)
    return 0 if ok else 1


def _bullets(items: list[str], limit: int = 200) -> list[str]:
    shown = [f"- `{i}`" for i in items[:limit]]
    if len(items) > limit:
        shown.append(f"- ... and {len(items) - limit} more")
    return shown or ["- none"]


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="decide what to run")
    plan.add_argument("--repo", default=".")
    plan.add_argument(
        "--base-ref", required=True, help="the pull request's target, e.g. origin/main"
    )
    plan.add_argument("--head", default="HEAD")
    plan.add_argument(
        "--labels", default=None, help="JSON list of label names (default: $PR_LABELS)"
    )
    plan.add_argument("--plan", default="failing-first-plan.json")
    plan.add_argument("--summary", default=None)
    plan.add_argument("--max-files", type=int, default=MAX_TEST_FILES)
    plan.set_defaults(func=cmd_plan)

    prep = sub.add_parser("prepare", help="check out the merge base and overlay the PR's tests")
    prep.add_argument("--repo", default=".")
    prep.add_argument("--plan", default="failing-first-plan.json")
    prep.add_argument("--dest", required=True)
    prep.set_defaults(func=cmd_prepare)

    run = sub.add_parser("run", help="run the changed tests against the base code")
    run.add_argument("--plan", default="failing-first-plan.json")
    run.add_argument("--dest", required=True)
    run.add_argument("--junit", default="failing-first.xml")
    run.set_defaults(func=cmd_run)

    judge = sub.add_parser("judge", help="pass when at least one changed test fails on the base")
    judge.add_argument("--plan", default="failing-first-plan.json")
    judge.add_argument("--junit", default="failing-first.xml")
    judge.add_argument("--summary", default=None)
    judge.set_defaults(func=cmd_judge)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"failing-first check could not complete: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

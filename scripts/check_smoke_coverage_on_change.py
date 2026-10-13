#!/usr/bin/env python
"""A template/JS change with no smoke-test touch in the same diff.

WHY THIS EXISTS (13 Sep 2026): the ARB status-chart bug and the AI-chat
persona-switch clipping bug both shipped as pure template/JS edits with no
accompanying browser test, and both were invisible to every other gate --
the DOM and accessibility tree were intact in each case, only the *rendered
pixels* were wrong. CLAUDE.md already states the rule ("Done means
DEMONSTRATED": a write feature/template change is not shipped until a
browser test exercises it) but stated as discipline is not enforced as
mechanism -- an agent (or a person) can simply forget. This makes forgetting
a failed gate instead of a missed step.

Extended the same day to watch `app/static/styles/**/*.css` too: a fix to the
Vendor Catalogue's sticky-column background (a CSS-only change, no template
or JS touched) would itself have shipped unwatched by the original version of
this gate -- the exact blind spot the gate exists to close, found while
closing a different one.

Deliberately git-diff-based rather than route-execution-based (contrast with
the `nav-verified` gate, which instruments a real behavioural run to prove a
sidebar route was actually clicked): that mechanism answers "has this route
EVER been exercised," accumulated across the whole test history, and is the
right tool for sidebar-nav coverage specifically. This gate answers a
narrower, cheaper question -- "did the diff that changed this file also
touch a browser test" -- and needs no live server, no database, no browser,
so it can run in `verify.py --tag static` on every commit, including in a
dependency-free CI job.

DETECTION: diff the target ref (default: the merge-base with origin/main,
falling back to origin/main itself, falling back to HEAD~1 if origin is
unreachable -- e.g. a fresh clone with no push history yet) against the
working tree (committed AND uncommitted changes both count -- the question
is "if you shipped this right now", not "what you already committed").
Every changed `app/templates/**/*.html` or `app/static/js/**/*.js` file
(excluding generated bundles and vendored code) must be matched by at least
one changed file under `tests/smoke/` in the same diff, OR carry the
`smoke-coverage-ok: <reason>` escape hatch on its own first line (a
non-visual file -- an email template, a CLI-only script's template, a pure
data/config module with no rendered surface).

NOT flagged: a diff that touches no template/JS files at all (most backend-
only or docs-only changes), and `app/static/js/bundles/**` (generated,
JS-syntax already gates its own build-freshness via `js-build`/`css-build`).

Proven-against: a synthetic diff of {'app/templates/dashboard/overview.html'}
with no tests/smoke/ file and no escape marker -- red, reporting the one file.
Adding a tests/smoke/test_dashboard.py touch to the same diff, or adding
'smoke-coverage-ok: <reason>' as the template's first line, both turn it
green. Confirmed by monkeypatching `_changed_files` with each of these three
diff shapes and checking `find_unverified()`'s return value directly, since
the real function reads live git state that a unit test cannot control.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

WATCHED_PREFIXES = ("app/templates/", "app/static/js/", "app/static/styles/")
EXCLUDED_PREFIXES = ("app/static/js/bundles/", "app/static/js/vendor/", "app/static/vendor/")
SMOKE_PREFIX = "tests/smoke/"
ESCAPE_MARKER = "smoke-coverage-ok"


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    return proc.stdout.strip()


def resolve_base_ref(strict: bool = False) -> tuple[str | None, str]:
    """The ref to diff against, and a reason when none could be resolved.
    Shared by this script's own diff (below, non-strict) and by another
    gate elsewhere in this suite that needs the same resolution but a
    stricter guarantee (imported directly, not run as a subprocess).

    Non-strict (default -- this script's own use): prefers the merge-base
    with origin/main, so a long-lived branch isn't blamed for files main
    already changed elsewhere; falls back to origin/main directly; then to
    HEAD~1 for a repo with no fetched origin (a fresh clone, or an offline
    sandbox). Always resolves to something -- a diff-coverage question
    needs *some* comparison point even in a degraded clone, and the
    question "did this file change" degrades gracefully with a worse base,
    it does not become meaningless.

    Strict: the range actually under review, not a best-effort comparison
    point -- prefers ``origin/$GITHUB_BASE_REF`` (set inside a pull
    request), else ``origin/main``, and never falls back to HEAD~1:
    resolving anyway in a shallow or origin-less clone would silently
    narrow "the commits under review" to "the one commit before HEAD",
    which is not the same claim and cannot be allowed to pass by accident.
    Returns ``(None, reason)`` instead, so a caller that needs the range to
    mean what it says can skip with the reason rather than pass on a guess.
    """
    if strict:
        base_ref = os.environ.get("GITHUB_BASE_REF")
        base = f"origin/{base_ref}" if base_ref else "origin/main"
        if _run(["git", "rev-parse", "--verify", "--quiet", base]):
            return base, ""
        return None, (
            f"{base} does not resolve in this clone (shallow clone, or no "
            f"matching remote-tracking branch) -- cannot determine which "
            f"commits are under review"
        )
    merge_base = _run(["git", "merge-base", "HEAD", "origin/main"])
    if merge_base:
        return merge_base, ""
    if _run(["git", "rev-parse", "--verify", "origin/main"]):
        return "origin/main", ""
    return "HEAD~1", ""


def _base_ref() -> str:
    """The ref this script's own CLI diffs against -- see resolve_base_ref's
    non-strict mode, which always resolves to something."""
    ref, _reason = resolve_base_ref(strict=False)
    return ref


def _changed_files(base: str) -> set[str]:
    committed = _run(["git", "diff", "--name-only", f"{base}...HEAD"])
    uncommitted = _run(["git", "diff", "--name-only", "HEAD"])
    untracked = _run(["git", "ls-files", "--others", "--exclude-standard"])
    files = set()
    for blob in (committed, uncommitted, untracked):
        files.update(line.strip() for line in blob.splitlines() if line.strip())
    return files


def _is_watched(path: str) -> bool:
    if not path.endswith((".html", ".js", ".css")):
        return False
    if any(path.startswith(p) for p in EXCLUDED_PREFIXES):
        return False
    return any(path.startswith(p) for p in WATCHED_PREFIXES)


def _has_escape_marker(path: str) -> bool:
    full = REPO_ROOT / path
    try:
        with open(full, encoding="utf-8", errors="ignore") as fh:
            # First 5 lines only -- a marker buried mid-file is not a visible,
            # reviewable exception, it's a marker nobody will find on review.
            for _ in range(5):
                line = fh.readline()
                if not line:
                    break
                if ESCAPE_MARKER in line:
                    return True
    except OSError:
        pass
    return False


def find_unverified(base: str | None = None) -> list[str]:
    base = base or _base_ref()
    changed = _changed_files(base)
    if not changed:
        return []
    smoke_touched = any(f.startswith(SMOKE_PREFIX) for f in changed)
    if smoke_touched:
        return []
    unverified = []
    for f in sorted(changed):
        if not _is_watched(f):
            continue
        if _has_escape_marker(f):
            continue
        unverified.append(f)
    return unverified


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=None,
                         help="ref to diff against (default: merge-base with origin/main)")
    parser.add_argument("--count", action="store_true")
    args = parser.parse_args(argv)

    unverified = find_unverified(args.base)
    if args.count:
        print(len(unverified))
        return 0
    for f in unverified:
        print(f"{f}: changed with no tests/smoke/ touch in the same diff")
    if unverified:
        print(f"\n{len(unverified)} template/JS file(s) changed with no accompanying "
              "browser test. Extend an existing tests/smoke/ journey, add a new one, "
              "or mark a deliberate exception with a first-line comment: "
              "'smoke-coverage-ok: <reason>' (a non-visual file: an email template, "
              "a CLI-only script's template, a pure data/config module).")
    return 1 if unverified else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""R2-1 (PR 428 round 3): ``User.is_admin`` is a bound method
(app/models/user.py), not a property. ``not current_user.is_admin`` (no
call) evaluates the method object itself, which is truthy for every signed
-in user including a plain Viewer -- the guard reads like a working
authorisation check and enforces nothing. 37 such sites were found across
this codebase by the PR 428 round 2 cloud review, all on a `*user.is_admin`
attribute reference with no trailing ``()``.

This is the static check the review asked for: a `*user.is_admin` access
that is not immediately called is flagged, so this class of bug cannot
reappear silently. It does not replace the active-org judgement each fixed
site now also carries (see ``_is_active_org_admin`` / ``is_active_org_admin``
in the routes and decorators this PR touched) -- it only proves the method
is actually *invoked*, which is the precondition for that judgement to run
at all.

Deliberately scoped to identifiers ending in "user" (current_user, user,
target_user, ...) rather than a bare `.is_admin` anywhere, so it does not
flag an unrelated same-named field on a different kind of object (for
example ``RouteInfo.is_admin``, a plain boolean column with no method
semantics at all, in app/services/route_discovery_service.py). Also skips
backtick-quoted docstring prose (this repository's own convention for
inline code in a docstring is double backticks), since those are
*describing* the bug, not committing it again.

Escape hatch: `is-admin-called-ok: <reason>` on the same line, for the rare
case a line genuinely needs the bound method object itself (for example
assigning it to a variable before deciding whether to call it, as
_check_solution_access / codegen._check_access's
``is_admin_attr = getattr(user, "is_admin", False)`` do -- that line reads
the literal string "is_admin" through getattr, not a `.is_admin` attribute
access, so it never matches this check's pattern in the first place; the
hatch exists for a future case that does).

    python scripts/check_is_admin_called.py                  # list violations
    python scripts/check_is_admin_called.py --count          # print count only
    python scripts/check_is_admin_called.py FILE [FILE ...]   # scan specific files

Proven-against: app/decorators/adm_permissions.py's `_check_role` temporarily
reverted to its pre-fix `if user.is_admin or "admin" in required_roles:` --
red, naming that exact line; restored, green again (0 violations repo-wide).
"""

from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# e.g. current_user.is_admin, user.is_admin, target_user.is_admin -- but not
# current_user.is_admin() (already called) and not route.is_admin (a
# differently-shaped object with its own, unrelated is_admin field).
UNCALLED = re.compile(r"\b(\w*[Uu]ser)\.is_admin\b(?!\s*\()")

ALLOW = re.compile(r"is-admin-called-ok:[ \t]*\S")

SCAN_DIRS = ("app",)
SKIP_DIR_PARTS = {".git", "node_modules", "__pycache__", "migrations"}


def _iter_py_files(root: str, explicit: list[str]):
    if explicit:
        for path in explicit:
            yield path
        return
    for scan_dir in SCAN_DIRS:
        base = os.path.join(root, scan_dir)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_PARTS]
            for filename in filenames:
                if filename.endswith(".py"):
                    yield os.path.join(dirpath, filename)


def _is_prose(line: str) -> bool:
    """This repo's docstrings quote code with double backticks; a line
    carrying one is describing the pattern, not committing it."""
    return "``" in line or "`" in line


def find_violations(root: str, explicit: list[str] | None = None) -> list[tuple[str, int, str]]:
    violations = []
    for path in _iter_py_files(root, explicit or []):
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("def is_admin") or stripped.startswith("async def is_admin"):
                continue
            if ALLOW.search(line):
                continue
            if _is_prose(line):
                continue
            if stripped.startswith("#"):
                continue
            if UNCALLED.search(line):
                violations.append((os.path.relpath(path, root), lineno, line.rstrip()))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*", help="Specific files to scan")
    parser.add_argument("--count", action="store_true", help="Print violation count only")
    parser.add_argument("--root", default=ROOT)
    args = parser.parse_args()

    violations = find_violations(args.root, args.files)

    if args.count:
        print(len(violations))
        return 0

    if not violations:
        print("check_is_admin_called: no uncalled *.is_admin references found")
        return 0

    print(f"check_is_admin_called: {len(violations)} uncalled *.is_admin reference(s):")
    for rel_path, lineno, line in violations:
        print(f"  {rel_path}:{lineno}: {line}")
    print(
        "\nCall it: `current_user.is_admin()` resolves the active-org judgement; "
        "a bare reference is always truthy. Mark a deliberate exception with "
        "'is-admin-called-ok: <reason>' on the same line."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

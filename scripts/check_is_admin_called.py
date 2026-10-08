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
site now also carries (see ``is_active_org_admin`` in
app/middleware/tenant_decorators.py) -- it only proves the method is
actually *invoked*, which is the precondition for that judgement to run at
all.

Deliberately scoped to identifiers ending in "user" (current_user, user,
target_user, ...) rather than a bare `.is_admin` anywhere, so it does not
flag an unrelated same-named field on a different kind of object (for
example ``RouteInfo.is_admin``, a plain boolean column with no method
semantics at all, in app/services/route_discovery_service.py).

R3-1 (PR 428 round 4): the dot-access form above is not the only shape the
same bug takes. ``getattr(current_user, "is_admin", False)`` with no call
returns the same always-truthy bound method, written a different way, and
the round-3 gate never matched it -- two live write routes (ARB solution
withdraw, ARB decision reopen) were exploitable by any signed-in member
because of it. Also matched now, scoped the same way (the first argument
must look like a user identifier).

The round-3 gate also skipped any line containing a backtick, meant to
exempt this repository's double-backtick docstring-prose convention, but in
practice a single backtick anywhere on a line -- including in a trailing
comment appended to otherwise-live code -- made the whole line invisible to
this check. That skip is removed. Prose is now recognised properly instead:
a line inside a triple-quoted string literal is prose (docstrings describe
the bug; they do not execute it) and a `#`-prefixed line is a comment, in
both cases regardless of any backtick. A real code line is never exempted
by punctuation alone -- only by being a comment/docstring or by carrying
the explicit `is-admin-called-ok: <reason>` marker below.

Escape hatch: `is-admin-called-ok: <reason>` on the same line, for the rare
case a line genuinely needs the bound method object itself -- for example
assigning it to a variable before deciding whether to call it, as
_check_solution_access / codegen._check_access's
``is_admin_attr = getattr(user, "is_admin", False)`` do (the very next line
calls it through ``callable()``).

    python scripts/check_is_admin_called.py                  # list violations
    python scripts/check_is_admin_called.py --count          # print count only
    python scripts/check_is_admin_called.py FILE [FILE ...]   # scan specific files

Proven-against: app/decorators/adm_permissions.py's `_check_role` temporarily
reverted to its pre-fix `if user.is_admin or "admin" in required_roles:` --
red, naming that exact line; restored, green again. Also proven against the
R3-1 getattr form: arb_workflow_routes.py's withdraw-route fix temporarily
reverted to `getattr(current_user, "is_admin", False)` -- red, naming that
line; restored, green. And against the dropped backtick skip: a scratch line
`if not getattr(current_user, "is_admin", False):  # see \`is_admin_attr\``
(a real, live check with a trailing backtick-quoted comment) -- flagged red
under the old backtick-skip gate restored temporarily for the comparison,
confirmed red under this gate; removed afterward.
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

# R3-1: the same bug via getattr(<user-like>, "is_admin", <default>) with no
# immediate call. The default-value fragment is kept paren-free in every
# real site in this codebase (False, None, lambda: False), so stopping at
# the first ")" after the literal is enough to find the call site's own
# closing paren without a full parser.
GETATTR_UNCALLED = re.compile(
    r'getattr\(\s*\w*[Uu]ser\w*\s*,\s*["\']is_admin["\']\s*,[^()]*\)(?!\s*\()'
)

ALLOW = re.compile(r"is-admin-called-ok:[ \t]*\S")

SCAN_DIRS = ("app",)
SKIP_DIR_PARTS = {".git", "node_modules", "__pycache__", "migrations"}

_DOCSTRING_DELIM = re.compile(r'"""|\'\'\'')


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


def _docstring_line_flags(lines: list[str]) -> list[bool]:
    """Return, per line, whether that line sits inside a triple-quoted
    string literal (opened on this line or an earlier one, not yet closed).

    A line-oriented approximation (no full tokenizer), matching the level of
    rigor the rest of this gate already uses: it counts triple-quote
    delimiters per line and toggles an in-string flag each time one appears,
    which is exact for this repository's convention of one docstring
    delimiter pair per statement and no inline triple-quotes inside code.
    """
    flags = []
    in_string = False
    for line in lines:
        starts_in_string = in_string
        count = len(_DOCSTRING_DELIM.findall(line))
        if count % 2 == 1:
            in_string = not in_string
        # A line that opens OR closes a docstring still carries only the
        # delimiter itself as real "code" (a bare `"""`) -- treat it as
        # prose too so the opening/closing line of a docstring is never
        # itself scanned as a statement.
        flags.append(starts_in_string or in_string or count > 0)
    return flags


def find_violations(root: str, explicit: list[str] | None = None) -> list[tuple[str, int, str]]:
    violations = []
    for path in _iter_py_files(root, explicit or []):
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
        except (OSError, UnicodeDecodeError):
            continue
        in_docstring = _docstring_line_flags(lines)
        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("def is_admin") or stripped.startswith("async def is_admin"):
                continue
            if ALLOW.search(line):
                continue
            if in_docstring[lineno - 1]:
                continue
            if stripped.startswith("#"):
                continue
            if UNCALLED.search(line) or GETATTR_UNCALLED.search(line):
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

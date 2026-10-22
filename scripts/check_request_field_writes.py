#!/usr/bin/env python
"""Count loops under app/ that write attributes whose names come from the loop.

    python scripts/check_request_field_writes.py            # list
    python scripts/check_request_field_writes.py --count    # count only
    python scripts/check_request_field_writes.py --json

The shape::

    for field, value in data.items():
        if hasattr(obj, field):
            setattr(obj, field, value)

and its variants (``for k, v in payload.items(): setattr(obj, k, v)``, ``for key in fields:
setattr(obj, key, data[key])``). When the loop's names come from a request body, a caller
chooses which columns are written, including ``organization_id``, ``is_admin`` and every other
column the model has. The tenant fence reads the organisation from the row it is asked to write,
so a forged ``organization_id`` in the body is the shape of a cross-tenant write.

The count includes loops that walk a fixed allowlist, since this script cannot tell a
constant list from a request-supplied one; the fix for a loop is the same either way, a
named schema (explicit assignments or a schema ``load``) that cannot name a column the caller
may not set. The count is a ratchet: it may fall, never rise. A new loop of this shape fails
the build; a deliberate one carries ``request-field-writes-ok: <reason>`` on the ``for`` line
or the line before it.

Proven-against: a new ``for k, v in data.items(): setattr(obj, k, v)`` added under app/ --
the count rises by one and the gate fails (tests/test_check_request_field_writes.py).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "app"
MARKER = re.compile(r"request-field-writes-ok:\s*\S")


def _loop_key(target):
    """The name that carries the field name: the sole name, or the first of a tuple."""
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, (ast.Tuple, ast.List)) and target.elts and isinstance(target.elts[0], ast.Name):
        return target.elts[0].id
    return None


def find_loops(tree, lines):
    """Line numbers of ``for`` loops that call ``setattr(x, <loop key>, ...)`` in their body."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.AsyncFor)):
            continue
        key = _loop_key(node.target)
        if key is None:
            continue
        written = False
        for stmt in node.body:
            for sub in ast.walk(stmt):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                        and sub.func.id == "setattr" and len(sub.args) >= 2
                        and isinstance(sub.args[1], ast.Name) and sub.args[1].id == key):
                    written = True
        if not written:
            continue
        here = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
        before = lines[node.lineno - 2] if node.lineno >= 2 else ""
        if MARKER.search(here) or MARKER.search(before):
            continue
        out.append(node.lineno)
    return sorted(out)


def scan(app=None, repo=None):
    app, repo = app or APP, repo or REPO
    hits = []
    for path in sorted(app.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for line in find_loops(tree, text.splitlines()):
            hits.append({"file": path.relative_to(repo).as_posix(), "line": line})
    return hits


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    hits = scan()
    if args.json:
        print(json.dumps({"count": len(hits), "hits": hits}, indent=2))
    elif args.count:
        print(len(hits))
    else:
        for h in hits:
            print(f"  {h['file']}:{h['line']}: loop writes attributes named by the loop")
        print(f"\n{len(hits)} loop(s). Use named assignments or a schema; or "
              "`# request-field-writes-ok: <reason>`.")
        print(len(hits))
    return 0


if __name__ == "__main__":
    sys.exit(main())

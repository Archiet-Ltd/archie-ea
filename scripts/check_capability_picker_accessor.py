#!/usr/bin/env python
"""Count direct capability queries that bypass UnifiedCapability.active().

A capability retired into another (retired_into_id set) must never be offered by
a dropdown, picker or list. ``UnifiedCapability.active()`` is the one accessor
that leaves them out. A query written directly against the model
(``UnifiedCapability.query``, ``db.session.query(UnifiedCapability...)``,
``db.select(UnifiedCapability...)``) skips that rule, so a new one is a
regression unless it is a record read (``.get``, ``.get_or_404``, ``.count``)
or says why it must see retired rows with ``retired-ok: <reason>`` on the line.

The per-file counts of existing direct queries are frozen in
scripts/capability_picker_baseline.json and may only fall.

Usage:
    python scripts/check_capability_picker_accessor.py            # list excess
    python scripts/check_capability_picker_accessor.py --count    # excess count
    python scripts/check_capability_picker_accessor.py --update-baseline

Proven-against: adding ``UnifiedCapability.query.order_by(...).all()`` to a file
under app/ raises that file above its baseline and the count to 1; switching it
to ``UnifiedCapability.active().order_by(...).all()`` returns it to 0.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join("scripts", "capability_picker_baseline.json")
MARKER = "retired-ok:"
DIRECT_RE = re.compile(
    r"UnifiedCapability\.query(?!\.(?:get|get_or_404|count)\()"
    r"|(?:session\.)?query\(\s*UnifiedCapability\b"
    r"|select\(\s*UnifiedCapability\b"
)
MODEL_FILE = "app/models/unified_capability.py"


def counts(root: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for dirpath, dirnames, filenames in os.walk(os.path.join(root, "app")):
        dirnames[:] = [d for d in dirnames if d not in ("tests", "__pycache__")]
        for name in filenames:
            if not name.endswith(".py") or name.startswith("test_"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if rel == MODEL_FILE:
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    n = sum(
                        1
                        for line in fh
                        if DIRECT_RE.search(line)
                        and MARKER not in line
                        and not line.lstrip().startswith(("#", "from ", "import "))
                    )
            except OSError:
                continue
            if n:
                found[rel] = n
    return found


def excess(root: str) -> list[str]:
    with open(os.path.join(root, BASELINE), encoding="utf-8") as fh:
        base = json.load(fh)
    return [
        f"{rel}: {n} direct capability queries (baseline {base.get(rel, 0)}) — read through UnifiedCapability.active()"
        for rel, n in sorted(counts(root).items())
        if n > base.get(rel, 0)
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", action="store_true")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--root", default=ROOT)
    args = parser.parse_args()
    root = os.path.abspath(args.root)
    if args.update_baseline:
        new = counts(root)
        path = os.path.join(root, BASELINE)
        old = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None
        if old is not None:
            raised = [r for r, n in new.items() if n > old.get(r, 0)]
            if raised:
                print("refusing to raise a baseline: " + ", ".join(raised))
                return 1
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(dict(sorted(new.items())), fh, indent=2)
            fh.write("\n")
        print(sum(new.values()))
        return 0
    problems = excess(root)
    if not args.count:
        for line in problems:
            print("  " + line)
    print(len(problems))
    return 0


if __name__ == "__main__":
    sys.exit(main())

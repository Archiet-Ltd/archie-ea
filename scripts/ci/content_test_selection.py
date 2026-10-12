#!/usr/bin/env python
"""Compute the test files the content-only job runs.

Every tests/test_*.py outside tests/smoke whose text refers to public pages,
public templates or the template tree, or that fetches a public route, plus the
template contract test explicitly. Printed one path per line, sorted.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SELECTION_PATTERN = re.compile(
    r'public_pages|templates/public|rglob\("\*\.html"\)|TEMPLATE_ROOT|TEMPLATES|'
    r'client\.get\("/(pricing|vs|use-cases|modules|vision|legal|llms|sitemap)?|'
    # A public route listed as a string (for example in a route table) counts too.
    r'"/(pricing|vs|use-cases|modules|vision|legal|llms|sitemap)[/"]'
)

ALWAYS_INCLUDED: tuple[str, ...] = ("tests/test_template_contracts.py",)


def select_content_tests(root: Path | str = ".") -> list[str]:
    """Return the sorted, posix-style relative paths of the tests to run."""
    base = Path(root)
    tests_dir = base / "tests"
    smoke_dir = tests_dir / "smoke"
    selected: set[str] = set()
    for path in tests_dir.glob("test_*.py"):
        if smoke_dir in path.parents:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if SELECTION_PATTERN.search(text):
            selected.add(path.relative_to(base).as_posix())
    for extra in ALWAYS_INCLUDED:
        if (base / extra).is_file():
            selected.add(extra)
    return sorted(selected)


def main() -> int:
    selection = select_content_tests(".")
    if not selection:
        print("no content tests selected", file=sys.stderr)
        return 1
    print("\n".join(selection))
    return 0


if __name__ == "__main__":
    sys.exit(main())

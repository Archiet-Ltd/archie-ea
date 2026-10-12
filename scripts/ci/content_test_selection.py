#!/usr/bin/env python
"""Compute the test files the content-only job runs.

Every test_*.py under tests/ (recursively) outside tests/smoke whose text refers to public pages,
public templates or the template tree, or that fetches a public route, plus the
template contract test explicitly. Printed one path per line, sorted.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Public route roots: the section prefixes, the site page slugs under
# content/pages/site and the machine-readable .txt / .xml routes.
PUBLIC_ROUTES = (
    r"pricing|vs|use-cases|modules|vision|legal|connectors|"
    r"about|features|contact|docs|privacy|security|terms|"
    r"architecture-health-check|team-annual-onboarding|"
    r"llms-full\.txt|llms\.txt|robots\.txt|sitemap\.xml|llms|sitemap"
)
_Q = "[\"']"

SELECTION_PATTERN = re.compile(
    r"public_pages|templates/public|"
    # rglob with either quote style
    + r"rglob\(" + _Q + r"\*\.html" + _Q + r"\)|TEMPLATE_ROOT|TEMPLATES|"
    # a fetch of a public route: either quote, optionally an f-string
    # the home page
    + r"\.get\(\s*" + _Q + r"/" + _Q + r"|"
    + r"\.get\(\s*f?" + _Q + r"/(" + PUBLIC_ROUTES + r")|"
    # a public route listed as a string (for example in a route table)
    + _Q + r"/(" + PUBLIC_ROUTES + r")[/\"']"
)

ALWAYS_INCLUDED: tuple[str, ...] = ("tests/test_template_contracts.py",)


def select_content_tests(root: Path | str = ".") -> list[str]:
    """Return the sorted, posix-style relative paths of the tests to run."""
    base = Path(root)
    tests_dir = base / "tests"
    smoke_dir = tests_dir / "smoke"
    selected: set[str] = set()
    for path in tests_dir.rglob("test_*.py"):
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

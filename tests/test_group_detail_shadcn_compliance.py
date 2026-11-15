"""The duplicate detection group detail template uses only shadcn macros.

Every control on the page is a component macro (button, card, badge, table,
tabs, etc.); no hand-written <button>, <input>, <select>, <textarea>, <table>
or <dialog> without data-slot. Uses the same logic as
scripts/qa/entelim_shadcn_compliance.py.
"""

from __future__ import annotations

import os
import re

TEMPLATE = "app/templates/duplicate_detection/group_detail.html"


def check_file(path: str) -> list[tuple[int, str, str]]:
    """Returns list of (line_num, tag_type, line_content) for violations.

    Uses the same logic as scripts/qa/entelim_shadcn_compliance.py.
    """
    FORBIDDEN_RAW_TAGS = {
        "button": re.compile(r"<button(?!\s+[^>]*data-slot=)", re.I),
        "input": re.compile(r"<input(?!\s+[^>]*data-slot=)", re.I),
        "select": re.compile(r"<select(?!\s+[^>]*data-slot=)", re.I),
        "textarea": re.compile(r"<textarea(?!\s+[^>]*data-slot=)", re.I),
        "table": re.compile(r"<table(?!\s+[^>]*data-slot=)", re.I),
        "dialog": re.compile(r"<dialog", re.I),
    }

    violations = []
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()

    for i, line in enumerate(lines, 1):
        stripped = line.lstrip()
        if stripped.startswith("{#") or "ui-contract-ok:" in line:
            continue

        for tag_name, pattern in FORBIDDEN_RAW_TAGS.items():
            if pattern.search(line):
                violations.append((i, tag_name, line.strip()[:100]))

    return violations


def test_group_detail_template_uses_shadcn_macros():
    """Every control is a macro; no raw control without data-slot."""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(repo, TEMPLATE)
    violations = check_file(path)
    assert len(violations) == 0, (
        f"Template {TEMPLATE} has {len(violations)} raw-control violation(s):\n" +
        "\n".join(f"  Line {ln}: raw <{tag}> - {content}" for ln, tag, content in violations)
    )
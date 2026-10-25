#!/usr/bin/env python3
"""shadcn compliance checker for the 100% shadcn rebuild.

Checks that a template uses only component macros, not hand-written HTML controls.

Usage:
    python scripts/qa/entelim_shadcn_compliance.py
    python scripts/qa/entelim_shadcn_compliance.py --file app/templates/solutions/blueprint.html
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TPL_ROOT = ROOT / "app" / "templates"

FORBIDDEN_RAW_TAGS = {
    "button": r"<button(?!\s+[^>]*data-slot=)",  # Allow if it has data-slot (means it's from a macro)
    "input": r"<input(?!\s+[^>]*data-slot=)",
    "select": r"<select(?!\s+[^>]*data-slot=)",
    "textarea": r"<textarea(?!\s+[^>]*data-slot=)",
    "table": r"<table(?!\s+[^>]*data-slot=)",
    "dialog": r"<dialog",  # Native dialog always forbidden
}

# Exception patterns that are allowed
ALLOWED_PATTERNS = [
    r"ui-contract-ok:",  # Explicitly marked as OK
    r"{#.*#}",  # Inside comments
]


def check_file(path: Path) -> list[tuple[int, str, str]]:
    """Returns list of (line_num, tag_type, line_content) for violations."""
    violations = []
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    for i, line in enumerate(lines, 1):
        # Skip if line is in a comment
        stripped = line.lstrip()
        if stripped.startswith("{#") or "ui-contract-ok:" in line:
            continue

        for tag_name, pattern in FORBIDDEN_RAW_TAGS.items():
            if re.search(pattern, line, re.IGNORECASE):
                violations.append((i, tag_name, line.strip()[:100]))

    return violations


def main():
    parser = argparse.ArgumentParser(description="Check shadcn compliance")
    parser.add_argument(
        "--file", type=Path, help="Check a specific file"
    )
    args = parser.parse_args()

    if args.file:
        files = [args.file]
    else:
        files = list(TPL_ROOT.rglob("*.html"))

    total_violations = 0
    for file_path in sorted(files):
        violations = check_file(file_path)
        if violations:
            try:
                rel_path = file_path.relative_to(ROOT)
            except ValueError:
                rel_path = file_path
            print(f"\n{rel_path}: {len(violations)} violations")
            for line_num, tag_type, content in violations:
                print(f"  Line {line_num}: raw <{tag_type}> - {content}")
            total_violations += len(violations)

    if args.file and total_violations == 0:
        try:
            rel_path = args.file.relative_to(ROOT)
        except ValueError:
            # file_path might already be relative or be an absolute path outside ROOT
            rel_path = args.file
        print(f"{rel_path}: ✓ zero defects")

    return 1 if total_violations > 0 else 0


if __name__ == "__main__":
    sys.exit(main())

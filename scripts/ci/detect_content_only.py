#!/usr/bin/env python
"""Detect whether a pull request changes only content paths.

Outputs is_content_only=true or is_content_only=false to GITHUB_OUTPUT
(or stdout if the env var is not set).

Content paths are:
  - content/          (markdown pages)
  - docs/             (documentation)
  - public-page templates under app/templates/
  - page-level tests (unit tests for public pages)

A PR touching anything outside this list runs the full CI suite.
Push to main always runs the full suite (the check is skipped).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Path prefixes that are treated as "content only".
# Directories end with / so any file under them matches.
# Files are exact paths.
CONTENT_PREFIXES: tuple[str, ...] = (
    "content/",
    "docs/",
    "app/templates/public/",
    "app/templates/artefact_share/public.html",
    "app/templates/components/public_footer.html",
    "app/templates/components/public_navbar.html",
    "app/templates/layouts/public_base.html",
    "tests/test_public_content_pages.py",
    "tests/test_public_site_pages.py",
    "tests/test_public_jsonld.py",
    "tests/test_public_visitor_analytics.py",
    "tests/test_legal_pages.py",
    "tests/test_page_shell_macros.py",
    "tests/test_page_guide_history_database.py",
    "tests/test_page_guide_history_policy.py",
    "tests/test_m2_compliance_pages_crosslinked.py",
    "tests/test_procurement_pages.py",
    "tests/test_transformation_room_public_journey.py",
    "tests/csp/probe_pages.py",
    "tests/csp/verify_real_pages.py",
)


def is_content_path(path: str) -> bool:
    """Return True if *path* is a recognised content-only path."""
    for prefix in CONTENT_PREFIXES:
        if prefix.endswith("/"):
            # Directory prefix: match any file under it
            if path.startswith(prefix) or path == prefix.rstrip("/"):
                return True
        else:
            # Exact file match
            if path == prefix:
                return True
    return False


def get_changed_files(base_ref: str = "origin/main") -> list[str]:
    """Return the list of files changed between *base_ref* and HEAD."""
    result = subprocess.run(
        ["git", "diff", "--name-only", base_ref, "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    base_ref = os.environ.get("GITHUB_BASE_REF", "origin/main")
    # For push events or when there is no PR base, check against origin/main
    # but never skip on push to main (the caller handles that via job-level if).
    changed = get_changed_files(base_ref)

    if not changed:
        # No changed files means nothing to skip.
        is_content = "false"
    else:
        is_content = str(all(is_content_path(f) for f in changed)).lower()

    output = os.environ.get("GITHUB_OUTPUT", "")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"is_content_only={is_content}\n")
    else:
        print(f"is_content_only={is_content}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
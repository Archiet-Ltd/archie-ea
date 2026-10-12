#!/usr/bin/env python
"""Detect whether a pull request changes only content paths.

Outputs is_content_only=true or is_content_only=false to GITHUB_OUTPUT
(or stdout if the env var is not set).

Content paths are:
  - content/              (markdown pages)
  - public-page templates under app/templates/public/, EXCEPT the
    sales-enquiry email bodies listed in NON_CONTENT_EXCEPTIONS, which
    change email behaviour rather than the public site.

A PR touching anything outside this list runs the full CI suite, and so does a
change to a symlink or a submodule even when it sits under a content path.
On a push the detector still runs and outputs is_content_only=false, because
there is no pull request base to compare against; every job runs on a push.
"""

from __future__ import annotations

import os
import subprocess
import sys

# Path prefixes that are treated as "content only".
# Directories end with / so any file under them matches.
# Files are exact paths.
CONTENT_PREFIXES: tuple[str, ...] = (
    "content/",
    "app/templates/public/",
)

# Email templates that live under a content prefix but must NOT count as
# content: they are the sales-enquiry email bodies, so a change to them must
# run the full suite (email behaviour can break independently of the site).
# Checked first, before CONTENT_PREFIXES.
NON_CONTENT_EXCEPTIONS: tuple[str, ...] = (
    "app/templates/public/email/",
)


def is_content_path(path: str) -> bool:
    """Return True if *path* is a recognised content-only path."""
    for exception in NON_CONTENT_EXCEPTIONS:
        if path.startswith(exception):
            return False
    for prefix in CONTENT_PREFIXES:
        if prefix.endswith("/"):
            # Directory prefix: match any file under it
            if path.startswith(prefix):
                return True
        else:
            # Exact file match
            if path == prefix:
                return True
    return False


# File modes that are never content: a symlink or a submodule pointer can
# redirect to code however innocent the path under a content directory looks.
NON_REGULAR_MODES: frozenset[str] = frozenset({"120000", "160000"})


def _diff_entries(base_ref: str) -> list[tuple[str, str, str]]:
    """Return (old_mode, new_mode, path) for each file changed since *base_ref*."""
    result = subprocess.run(
        ["git", "diff", "--raw", "--no-renames", "-z", base_ref, "HEAD"],
        capture_output=True, text=True, check=True,
    )
    fields = result.stdout.split("\0")
    entries: list[tuple[str, str, str]] = []
    i = 0
    while i + 1 < len(fields):
        meta = fields[i]
        if meta.startswith(":"):
            parts = meta[1:].split()
            entries.append((parts[0], parts[1], fields[i + 1]))
            i += 2
        else:
            i += 1
    return entries


def get_changed_files(base_ref: str = "origin/main") -> list[str]:
    """Return the list of files changed between *base_ref* and HEAD."""
    return [path for _, _, path in _diff_entries(base_ref) if path]


def get_non_regular_files(base_ref: str = "origin/main") -> frozenset[str]:
    """Changed paths that are, or were, a symlink or a submodule."""
    return frozenset(
        path for old, new, path in _diff_entries(base_ref)
        if old in NON_REGULAR_MODES or new in NON_REGULAR_MODES
    )


def base_reference() -> str:
    """The remote-tracking ref of the pull request's target branch."""
    base = os.environ.get("GITHUB_BASE_REF", "").strip()
    return f"origin/{base}" if base else "origin/main"


def classify(changed: list[str], non_regular: frozenset[str] | set[str] = frozenset()) -> str:
    """'true' only when there is at least one changed file and all are regular content files."""
    if not changed:
        return "false"
    return str(all(is_content_path(f) and f not in non_regular for f in changed)).lower()


def main() -> int:
    # Any failure to compute the diff means "not content only": the full suite runs.
    try:
        base = base_reference()
        is_content = classify(get_changed_files(base), get_non_regular_files(base))
    except (subprocess.CalledProcessError, OSError):
        is_content = "false"

    output = os.environ.get("GITHUB_OUTPUT", "")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"is_content_only={is_content}\n")
    else:
        print(f"is_content_only={is_content}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

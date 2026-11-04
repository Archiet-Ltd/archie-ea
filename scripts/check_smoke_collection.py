#!/usr/bin/env python
"""Fail when the browser journey suite collects different tests in different processes.

pytest-xdist makes every worker collect the suite and refuses to run when two workers
disagree on a test id. A parametrised journey whose value or id is random, time-based
or taken from set order produces a different id on each worker, so the whole suite
stops with "Different tests were collected" even though every test passes alone.

`pytest --collect-only -n 2` does not compare the workers' lists, so this check does the
comparison itself: it collects the suite in two separate processes (different hash
seeds) and diffs the ids, then confirms that `-n 2` collection succeeds.

Proven-against: the viewer email in test_home_link_is_the_persons_own_home put back as a
parametrised value built from a random uuid at import; the check printed "2 differ" with
two different viewer.<hex> ids, and passes with the email created inside the fixture.

Usage:
    python scripts/check_smoke_collection.py
"""

from __future__ import annotations

import difflib
import os
import subprocess
import sys

TARGET = "tests/smoke"


def collect(extra: list[str], seed: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONHASHSEED=seed)
    return subprocess.run(
        [sys.executable, "-m", "pytest", TARGET, "--collect-only", "-qq", "-p", "no:randomly", "-p", "no:cacheprovider", *extra],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
    )


def node_ids(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith("tests/") and "::" in line]


def main() -> int:
    first, second = collect([], "1"), collect([], "2")
    for proc in (first, second):
        if proc.returncode != 0:
            print("collection failed:\n" + (proc.stdout + proc.stderr)[-800:])
            return 1
    a, b = node_ids(first.stdout), node_ids(second.stdout)
    if not a:
        print("no test ids were collected from %s" % TARGET)
        return 1
    if a != b:
        diff = [d for d in difflib.unified_diff(a, b, "process 1", "process 2", lineterm="", n=0)
                if d[:1] in "+-" and d[:3] not in ("+++", "---")]
        print("%s collects different ids in different processes (%d differ):\n%s"
              % (TARGET, len(diff), "\n".join(diff[:20])))
        return 1
    parallel = collect(["-n", "2"], "3")
    if parallel.returncode != 0 or "Different tests were collected" in parallel.stdout + parallel.stderr:
        print("collection with -n 2 failed:\n" + (parallel.stdout + parallel.stderr)[-800:])
        return 1
    print("%s collects the same %d tests in every process" % (TARGET, len(a)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

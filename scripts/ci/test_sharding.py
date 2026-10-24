"""Deterministic, duration-balanced test-FILE sharding for CI matrix parallelism.

Every shard process collects the FULL test set first (pytest always does this
before `pytest_collection_modifyitems` runs), groups items by file, gives each
file a weight, and does a deterministic greedy longest-processing-time bin-pack:
files sorted by weight descending (ties broken by path), each assigned to
whichever shard is currently lightest. Every shard process sees the same
collected items and the same committed table, so they agree on the partition
without sharing any file or state.

The weight of a file is its measured run time, read from
tests/shard_durations.json (regenerate it with scripts/ci/update_shard_durations.py,
which reads the junit files of recent CI runs). Balancing by test COUNT, which
this module did before, put the 31-minute tenant-isolation sweep (18 tests) and
the 12-minute schema-migration file beside ordinary files and left one shard
running 72 minutes while others ran 27.

A file the table does not know (a new test file) is weighted by its collected
test count times the table's median seconds per test, so it always lands in
exactly one shard. A file the table knows is weighted by its recorded seconds
per test times the CURRENT test count, so adding or removing tests in a file
moves its weight proportionally. A missing or unreadable table falls back to
plain test counts. No test is ever dropped or duplicated.

All tests in one file always land in the same shard, so a test that shares
module- or class-scoped state with another test in its own file is never
split across two database sessions. It does not protect against state
shared ACROSS files; ci.yml records what was checked for that.

The one exception is a file named in CI_SHARD_SPLIT_FILES: it is placed one
test function at a time (every parametrisation of a function stays together),
because a single file heavier than a whole shard's share cannot be balanced
otherwise. Each shard then builds that file's module-scoped fixtures for
itself, so name only a file whose functions do not depend on one another's
side effects.

Controlled by environment variables, read once at collection time:

    CI_SHARD_TOTAL       total number of shards (absent, "0" or "1": no-op --
                         every test runs, so this is a plain pytest invocation
                         outside CI too)
    CI_SHARD_INDEX       0-based shard number this process should run
    CI_SHARD_DURATIONS   optional path of the duration table (default:
                         tests/shard_durations.json next to this repository's
                         scripts/ directory)
    CI_SHARD_SPLIT_FILES optional comma-separated test files placed one test
                         function at a time instead of as a whole file

    CI_SHARD_INDEX=0 CI_SHARD_TOTAL=8 pytest --collect-only -q \\
        -p scripts.ci.test_sharding --ignore=tests/smoke
"""
from __future__ import annotations

import json
import os
import statistics
from collections import defaultdict
from pathlib import Path

DEFAULT_TABLE = Path(__file__).resolve().parent.parent.parent / "tests" / "shard_durations.json"

# Used only when no table can be read at all: every test weighs the same, which
# is the plain count balancing this module used before the table existed.
_FALLBACK_SECONDS_PER_TEST = 1.0

# Head start (seconds) for shard 0 so that the bin-packer assigns it
# less ordinary work.  This compensates for the assistant-connector (MCP)
# step that shard 0 runs but the balancing does not count.  The value is
# the median duration of the connector-tests step across runs
# 39647761444, 39648269461, 39651713459, 39651981883.
SHARD_0_CONNECTOR_HEAD_START_SECONDS = 246

# The head start never exceeds this fraction of one shard's even share of the
# work. A fixed 246 seconds is a small lead against the backend suite but was
# more than a whole shard's share of a smaller suite, and shard 0 then
# received no file at all and failed with "0 selected".
SHARD_0_HEAD_START_MAX_FRACTION = 0.5


def _file_of(item) -> str:
    return item.nodeid.split("::", 1)[0]


def split_files() -> frozenset[str]:
    """Files placed one test function at a time (CI_SHARD_SPLIT_FILES)."""
    raw = os.environ.get("CI_SHARD_SPLIT_FILES", "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def _unit_of(item, split: frozenset[str]) -> str:
    """The unit the planner places: the file, or for a split file the test
    function (its node id without the parametrisation)."""
    path = _file_of(item)
    if path in split:
        return item.nodeid.split("[", 1)[0]
    return path


def load_table(path=None) -> dict:
    """The committed duration table, or {} when it is absent or unreadable."""
    path = Path(path or os.environ.get("CI_SHARD_DURATIONS") or DEFAULT_TABLE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and isinstance(data.get("files"), dict) else {}


def default_seconds_per_test(table: dict) -> float:
    """Seconds per test assumed for a file the table does not know."""
    declared = table.get("default_seconds_per_test")
    if isinstance(declared, (int, float)) and declared > 0:
        return float(declared)
    rates = [
        entry["seconds"] / entry["tests"]
        for entry in table.get("files", {}).values()
        if isinstance(entry, dict) and entry.get("tests") and entry.get("seconds") is not None
    ]
    return statistics.median(rates) if rates else _FALLBACK_SECONDS_PER_TEST


def file_weights(file_counts: dict[str, int], table: dict) -> dict[str, int]:
    """Weight (milliseconds) of each collected file: recorded seconds per test
    times today's test count, or the table's median rate for an unknown file.
    A test function of a split file ("path::name") takes its file's rate."""
    fallback = default_seconds_per_test(table)
    known = table.get("files", {})
    weights: dict[str, int] = {}
    for path, count in file_counts.items():
        entry = known.get(path.split("::", 1)[0])
        if isinstance(entry, dict) and entry.get("tests") and entry.get("seconds") is not None:
            rate = entry["seconds"] / entry["tests"]
        else:
            rate = fallback
        weights[path] = max(1, round(rate * count * 1000))
    return weights


def head_start_ms(weights: dict[str, int], total: int) -> int:
    """Shard 0's head start, capped at a fraction of one shard's even share."""
    even_share = sum(weights.values()) / total
    return min(SHARD_0_CONNECTOR_HEAD_START_SECONDS * 1000, int(even_share * SHARD_0_HEAD_START_MAX_FRACTION))


def _bin_pack(weights: dict[str, int], total: int) -> dict[str, int]:
    """Greedy longest-processing-time-first assignment, file -> shard index.

    Shard 0 starts with a head start (ms) so the bin-packer assigns it less
    ordinary work, compensating for the assistant-connector (MCP) step that
    shard 0 runs but the balancing does not count.

    No shard is ever left empty while there are at least as many files as
    shards: once the files still to place are only just enough to give each
    empty shard one, every remaining file goes to an empty shard. An empty
    shard selects no test and pytest fails it with exit code 5.
    """
    ordered = sorted(weights.items(), key=lambda pair: (-pair[1], pair[0]))
    loads = [0] * total
    loads[0] = head_start_ms(weights, total)
    used = [False] * total
    assignment: dict[str, int] = {}
    for position, (path, weight) in enumerate(ordered):
        remaining = len(ordered) - position
        empty = [i for i in range(total) if not used[i]]
        candidates = empty if remaining <= len(empty) else range(total)
        shard = min(candidates, key=lambda i: (loads[i], i))
        assignment[path] = shard
        loads[shard] += weight
        used[shard] = True
    return assignment


def plan(file_counts: dict[str, int], total: int, table: dict | None = None):
    """(assignment, per-shard predicted milliseconds) for collected file counts."""
    weights = file_weights(file_counts, load_table() if table is None else table)
    assignment = _bin_pack(weights, total)
    loads = [0] * total
    for path, shard in assignment.items():
        loads[shard] += weights[path]
    return assignment, loads


def pytest_collection_modifyitems(config, items):  # noqa: ARG001 - pytest hook
    total = int(os.environ.get("CI_SHARD_TOTAL", "0") or "0")
    if total <= 1:
        return
    index = int(os.environ.get("CI_SHARD_INDEX", "0") or "0")
    if not (0 <= index < total):
        raise ValueError(f"CI_SHARD_INDEX={index} out of range for CI_SHARD_TOTAL={total}")

    split = split_files()
    unit_counts: dict[str, int] = defaultdict(int)
    for item in items:
        unit_counts[_unit_of(item, split)] += 1
    assignment, _loads = plan(dict(unit_counts), total)

    kept = []
    deselected = []
    for item in items:
        if assignment[_unit_of(item, split)] == index:
            kept.append(item)
        else:
            deselected.append(item)

    config.hook.pytest_deselected(items=deselected)
    items[:] = kept

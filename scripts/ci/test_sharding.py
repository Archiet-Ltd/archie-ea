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

Controlled by environment variables, read once at collection time:

    CI_SHARD_TOTAL       total number of shards (absent, "0" or "1": no-op --
                         every test runs, so this is a plain pytest invocation
                         outside CI too)
    CI_SHARD_INDEX       0-based shard number this process should run
    CI_SHARD_DURATIONS   optional path of the duration table (default:
                         tests/shard_durations.json next to this repository's
                         scripts/ directory)

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

# Head start (milliseconds) for shard 0 so that the bin-packer assigns it
# less ordinary work.  This compensates for the assistant-connector (MCP)
# step that shard 0 runs but the balancing does not count.  The value was
# measured from the CI junit data as the typical duration of the connector‑tests
# step; see scripts/ci/update_shard_durations.py for the source data.
SHARD_0_CONNECTOR_HEAD_START_MS = 20_000  # 20 s head start


def _file_of(item) -> str:
    return item.nodeid.split("::", 1)[0]


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
    times today's test count, or the table's median rate for an unknown file."""
    fallback = default_seconds_per_test(table)
    known = table.get("files", {})
    weights: dict[str, int] = {}
    for path, count in file_counts.items():
        entry = known.get(path)
        if isinstance(entry, dict) and entry.get("tests") and entry.get("seconds") is not None:
            rate = entry["seconds"] / entry["tests"]
        else:
            rate = fallback
        weights[path] = max(1, round(rate * count * 1000))
    return weights


def _bin_pack(weights: dict[str, int], total: int) -> dict[str, int]:
    """Greedy longest-processing-time-first assignment, file -> shard index.

    Shard 0 starts with a fixed head start (ms) so the bin-packer assigns it
    less ordinary work, compensating for the assistant-connector (MCP) step
    that shard 0 runs but the balancing does not count.
    """
    ordered = sorted(weights.items(), key=lambda pair: (-pair[1], pair[0]))
    loads = [0] * total
    loads[0] = SHARD_0_CONNECTOR_HEAD_START_MS
    assignment: dict[str, int] = {}
    for path, weight in ordered:
        shard = min(range(total), key=lambda i: (loads[i], i))
        assignment[path] = shard
        loads[shard] += weight
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

    file_counts: dict[str, int] = defaultdict(int)
    for item in items:
        file_counts[_file_of(item)] += 1
    assignment, _loads = plan(dict(file_counts), total)

    kept = []
    deselected = []
    for item in items:
        if assignment[_file_of(item)] == index:
            kept.append(item)
        else:
            deselected.append(item)

    config.hook.pytest_deselected(items=deselected)
    items[:] = kept

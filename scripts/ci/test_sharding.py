"""Deterministic, balanced test-FILE sharding for CI matrix parallelism.

No stored per-test duration data exists for this suite, and producing one
means running the ~6,700-test suite once end to end first -- which is the
same two-hour cost this exists to avoid. A plain stable hash of each file's
path, mod the shard count, was tried first and measured badly unbalanced: on
this suite (570 test files, 6,690 items) an 8-way hash split put 1,194 tests
in the heaviest bucket against an 836 average -- 43% over, enough on its own
to miss a 30-minute target.

So this does something a touch more capable than either named option: each
shard process collects the FULL test set first (pytest always does this
before `pytest_collection_modifyitems` runs), groups items by file, and does
a deterministic greedy longest-processing-time bin-pack -- files sorted by
item count descending (ties broken by path), each assigned to whichever
shard bucket is currently smallest. Every shard process sees the same
collected item set and runs the same deterministic algorithm, so they agree
on the partition without sharing any file or state. Measured on this suite:
8-way split lands at 836-837 tests per shard, i.e. within 0.1% of even --
because it re-derives file sizes from the real collection every run, it
tracks test additions/removals/parametrization changes automatically, with
no generated file to regenerate or go stale.

All tests in one file always land in the same shard, so a test that shares
module- or class-scoped state with another test in its own file is never
split across two database sessions. It does not protect against state
shared ACROSS files; ci.yml records what was checked for that.

Controlled by two environment variables, read once at collection time:

    CI_SHARD_TOTAL   total number of shards (absent, "0" or "1": no-op --
                      every test runs, so this is a plain pytest invocation
                      outside CI too)
    CI_SHARD_INDEX   0-based shard number this process should run

    CI_SHARD_INDEX=0 CI_SHARD_TOTAL=8 pytest --collect-only -q \\
        -p scripts.ci.test_sharding --ignore=tests/smoke
"""
from __future__ import annotations

import os
from collections import defaultdict


def _file_of(item) -> str:
    return item.nodeid.split("::", 1)[0]


def _bin_pack(file_counts: dict[str, int], total: int) -> dict[str, int]:
    """Greedy longest-processing-time-first assignment, file -> shard index."""
    ordered = sorted(file_counts.items(), key=lambda pair: (-pair[1], pair[0]))
    loads = [0] * total
    assignment: dict[str, int] = {}
    for path, count in ordered:
        shard = min(range(total), key=lambda i: (loads[i], i))
        assignment[path] = shard
        loads[shard] += count
    return assignment


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
    assignment = _bin_pack(dict(file_counts), total)

    kept = []
    deselected = []
    for item in items:
        if assignment[_file_of(item)] == index:
            kept.append(item)
        else:
            deselected.append(item)

    config.hook.pytest_deselected(items=deselected)
    items[:] = kept

"""Deterministic, balanced test-FILE sharding for CI matrix parallelism.

Duration-first bin-pack: when a durations JSON file is available (built from
the main-branch shards' junit XMLs by the combine job and uploaded as a
workflow artifact), each file's recorded wall-clock duration is its bin-pack
weight.  Files not present in the durations data fall back to their test-item
count, so new tests are distributed evenly by size the moment they are added
rather than being biased towards or away from slow shards.

Without a durations file the behaviour is unchanged from the previous version:
files are sorted by item count descending (ties broken by path), each assigned
to whichever shard bucket is currently smallest. Measured on this suite: 12-way
split lands at 557-558 tests per shard, within 0.1% of even.

All tests in one file always land in the same shard, so a test that shares
module- or class-scoped state with another test in its own file is never split
across two database sessions.  It does not protect against state shared ACROSS
files; ci.yml records what was checked for that.

Controlled by three environment variables, read once at collection time:

    CI_SHARD_TOTAL       total number of shards (absent, "0" or "1": no-op --
                          every test runs, so this is a plain pytest invocation
                          outside CI too)
    CI_SHARD_INDEX       0-based shard number this process should run
    CI_DURATIONS_FILE    path to a JSON file with per-file durations (optional;
                          when absent or unreadable, falls back to file size)

    CI_SHARD_INDEX=0 CI_SHARD_TOTAL=12 pytest --collect-only -q \
        -p scripts.ci.test_sharding --ignore=tests/smoke
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path


def _file_of(item) -> str:
    return item.nodeid.split("::", 1)[0]


def _bin_pack(file_weights: dict[str, float], total: int) -> dict[str, int]:
    """Greedy longest-processing-time-first assignment, file -> shard index.

    ``file_weights`` maps a test file's path to a non-negative weight
    (duration in seconds or test count).  All tests in one file always land in
    the same shard.
    """
    ordered = sorted(file_weights.items(), key=lambda pair: (-pair[1], pair[0]))
    loads = [0.0] * total
    assignment: dict[str, int] = {}
    for path, weight in ordered:
        shard = min(range(total), key=lambda i: (loads[i], i))
        assignment[path] = shard
        loads[shard] += weight
    return assignment


def _load_durations() -> dict[str, float] | None:
    """Return per-file durations from ``CI_DURATIONS_FILE``, or None.

    The durations JSON is produced by ``build_durations_from_junit()`` and
    uploaded as a CI artifact by the combine job on main-branch runs.
    """
    path = os.environ.get("CI_DURATIONS_FILE", "")
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        return None
    return {k: float(v) for k, v in files.items() if isinstance(v, (int, float))}


def build_durations_from_junit(xml_paths: list[str]) -> dict:
    """Build a durations payload from junit XML files.

    Each junit file is the output of one shard; tests within a file are
    grouped by their source file (the ``file`` attribute on each
    ``<testcase>``) and durations are summed.  The result is suitable as the
    ``CI_DURATIONS_FILE`` input to the sharding plugin.
    """
    from xml.etree import ElementTree

    file_durations: dict[str, float] = defaultdict(float)
    for xml_path in xml_paths:
        try:
            tree = ElementTree.parse(xml_path)
        except (OSError, ElementTree.ParseError):
            continue
        for testcase in tree.iter("testcase"):
            file_attr = testcase.get("file")
            if not file_attr:
                continue
            duration_str = testcase.get("time")
            if duration_str is None:
                continue
            try:
                duration = float(duration_str)
            except ValueError:
                continue
            if duration < 0:
                continue
            file_durations[file_attr] += duration

    return {"files": dict(file_durations)}


def pytest_collection_modifyitems(config, items):  # noqa: ARG001 - pytest hook
    total = int(os.environ.get("CI_SHARD_TOTAL", "0") or "0")
    if total <= 1:
        return
    index = int(os.environ.get("CI_SHARD_INDEX", "0") or "0")
    if not (0 <= index < total):
        raise ValueError(f"CI_SHARD_INDEX={index} out of range for CI_SHARD_TOTAL={total}")

    durations = _load_durations()

    file_counts: dict[str, int] = defaultdict(int)
    for item in items:
        file_counts[_file_of(item)] += 1

    if durations:
        file_weights: dict[str, float] = {}
        for fpath in file_counts:
            file_weights[fpath] = durations.get(fpath, float(file_counts[fpath]))
        assignment = _bin_pack(file_weights, total)
    else:
        assignment = _bin_pack({k: float(v) for k, v in file_counts.items()}, total)

    kept = []
    deselected = []
    for item in items:
        if assignment[_file_of(item)] == index:
            kept.append(item)
        else:
            deselected.append(item)

    config.hook.pytest_deselected(items=deselected)
    items[:] = kept
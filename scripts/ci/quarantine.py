"""Quarantine for tests that are known to flake on timing.

The list lives in scripts/ci/quarantine.txt, one test per line:

    <pytest node id>  # <reason>

A line without a reason is an error, so nothing is quarantined silently. An
entry matches the exact node id, or, when it names a parametrised test without
its parameter suffix, every parameter set of that test.

A quarantined test is never dropped from CI. The mode decides where it runs:

    CI_QUARANTINE_MODE=exclude   the blocking shards: listed tests are deselected
    CI_QUARANTINE_MODE=only      the non-blocking job: only listed tests run
    (absent or anything else)    plain pytest: everything runs, as before

Load it with ``-p scripts.ci.quarantine``. It runs before the shard splitter so
every shard sees the same remaining tests and agrees on the partition.

The list may only shrink without review; adding a line needs a reason, and the
rest of the failures on main are not this file's business.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

DEFAULT_LIST = Path(__file__).resolve().parent / "quarantine.txt"
REASON_SEPARATOR = " # "

# Entries that matched nothing in the last collection, for the summary.
_unmatched: list[str] = []


class QuarantineFormatError(ValueError):
    """A quarantine line has no node id or no reason."""


def parse_entries(text: str) -> dict[str, str]:
    """Node id -> reason for every entry. Blank lines and full-line comments are skipped."""
    entries: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        node_id, separator, reason = line.partition(REASON_SEPARATOR)
        node_id, reason = node_id.strip(), reason.strip()
        if not separator or not node_id or not reason:
            raise QuarantineFormatError(
                f"quarantine line {number} needs '<node id>{REASON_SEPARATOR}<reason>': {raw!r}"
            )
        entries[node_id] = reason
    return entries


def load_entries(path=None) -> dict[str, str]:
    path = Path(path or os.environ.get("CI_QUARANTINE_FILE") or DEFAULT_LIST)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    return parse_entries(text)


def is_quarantined(node_id: str, entries) -> bool:
    """Exact node id, or an entry without a parameter suffix covering its parameter sets."""
    if node_id in entries:
        return True
    return any(
        "[" not in entry and node_id.startswith(entry + "[") for entry in entries
    )


def split_items(items, entries):
    """(quarantined, blocking) in collection order."""
    quarantined, blocking = [], []
    for item in items:
        (quarantined if is_quarantined(item.nodeid, entries) else blocking).append(item)
    return quarantined, blocking


def unmatched_entries(items, entries) -> list[str]:
    """Entries that no collected item matches (a renamed or deleted test)."""
    ids = [item.nodeid for item in items]
    return sorted(
        entry
        for entry in entries
        if not any(i == entry or ("[" not in entry and i.startswith(entry + "[")) for i in ids)
    )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    mode = os.environ.get("CI_QUARANTINE_MODE", "")
    if mode not in ("exclude", "only"):
        return
    entries = load_entries()
    if not entries:
        if mode == "only":
            config.hook.pytest_deselected(items=list(items))
            items[:] = []
        return
    quarantined, blocking = split_items(items, entries)
    # Only meaningful when the whole suite was collected; a narrowed run would
    # report every entry as stale.
    _unmatched[:] = unmatched_entries(items, entries) if len(items) > 1000 else []
    keep, drop = (blocking, quarantined) if mode == "exclude" else (quarantined, blocking)
    config.hook.pytest_deselected(items=drop)
    items[:] = keep


def pytest_terminal_summary(terminalreporter):
    mode = os.environ.get("CI_QUARANTINE_MODE", "")
    if mode in ("exclude", "only") and _unmatched:
        terminalreporter.write_sep("-", "quarantine entries matching no collected test")
        for entry in _unmatched:
            terminalreporter.write_line(f"stale: {entry}")

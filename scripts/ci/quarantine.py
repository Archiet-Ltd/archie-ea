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

The list may only shrink without review. A pull request cannot quarantine its
own failing test: in a pull-request run the list that counts is the base
branch's copy, and the pull request's copy may only REMOVE lines from it
(`resolve` below; additions are ignored and reported). A line added on main is
reviewed like any other change before it merges.

    python -I quarantine.py resolve --base origin/main --out accepted.txt
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

try:  # the resolve command runs without pytest being importable from -I
    import pytest
except ImportError:  # pragma: no cover
    pytest = None

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


def _tryfirst(func):
    return pytest.hookimpl(tryfirst=True)(func) if pytest else func


@_tryfirst
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


def effective_text(base_text: str, pr_text: str) -> tuple[str, list[str]]:
    """(accepted list text, node ids the pull request added and so were ignored).

    Only lines the base branch already has count, and only while the pull
    request's copy still has them: the pull request may remove, never add.
    """
    base = parse_entries(base_text)
    mine = parse_entries(pr_text)
    kept = [f"{node}{REASON_SEPARATOR}{reason}" for node, reason in base.items() if node in mine]
    ignored = sorted(node for node in mine if node not in base)
    return "\n".join(kept) + ("\n" if kept else ""), ignored


def _git_show(ref: str, path: str) -> str:
    result = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else ""


def resolve(base_ref: str, out: Path, listing: Path = DEFAULT_LIST) -> list[str]:
    base_text = _git_show(base_ref, "scripts/ci/quarantine.txt")
    try:
        pr_text = listing.read_text(encoding="utf-8")
    except OSError:
        pr_text = ""
    text, ignored = effective_text(base_text, pr_text)
    out.write_text(text, encoding="utf-8")
    return ignored


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("resolve")
    one.add_argument("--base", required=True)
    one.add_argument("--out", required=True)
    one.add_argument("--list", default=str(DEFAULT_LIST))
    args = parser.parse_args(argv)
    ignored = resolve(args.base, Path(args.out), Path(args.list))
    count = len(parse_entries(Path(args.out).read_text(encoding="utf-8")))
    line = f"Quarantine: {count} accepted entries from the base branch, {len(ignored)} additions ignored"
    print(line)
    for node in ignored:
        print(f"::warning::quarantine addition ignored (add it on the base branch first): {node}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"### {line}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

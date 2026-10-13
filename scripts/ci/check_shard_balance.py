"""Warn when the committed shard duration table no longer balances the pytest shards.

Reads the shards' junit files (backend-tests-N.xml), compares each shard's
measured run time with the time tests/shard_durations.json predicted for the
files it ran, and prints a GitHub warning annotation for every shard that has
drifted more than 25% from the others. Runner load moves every shard together,
so the comparison removes the whole-run factor first: a shard is flagged only
when its measured/predicted ratio is more than 25% away from the run's overall
ratio. Never fails the job: a stale table costs wall time, not correctness.

    python scripts/ci/check_shard_balance.py shard-artifacts/tests-shard-*/backend-tests-*.xml

Refresh the table with scripts/ci/update_shard_durations.py when it warns.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_sharding  # noqa: E402
import update_shard_durations as durations  # noqa: E402

DRIFT = 0.25


def analyse(xml_paths, table: dict, repo_root: Path = durations.REPO_ROOT):
    """[(shard, measured_s, predicted_s, unknown_files)] for each junit file."""
    rows = []
    for path in sorted(xml_paths, key=lambda p: str(p)):
        match = re.search(r"backend-tests-(\d+)\.xml$", str(path))
        shard = int(match.group(1)) if match else len(rows)
        files = durations.read_junit([path], repo_root)
        counts = {f: e["tests"] for f, e in files.items()}
        weights = test_sharding.file_weights(counts, table)
        predicted = sum(weights.values()) / 1000
        measured = sum(e["seconds"] for e in files.values())
        unknown = sorted(f for f in files if f not in table.get("files", {}))
        rows.append((shard, measured, predicted, unknown))
    return rows


def drifted(rows, threshold: float = DRIFT):
    """Shards whose measured/predicted ratio is off the run's overall ratio by > threshold."""
    total_measured = sum(r[1] for r in rows)
    total_predicted = sum(r[2] for r in rows)
    if not rows or total_predicted <= 0 or total_measured <= 0:
        return []
    overall = total_measured / total_predicted
    flagged = []
    for shard, measured, predicted, _unknown in rows:
        if predicted <= 0:
            continue
        deviation = (measured / predicted) / overall - 1
        if abs(deviation) > threshold:
            flagged.append((shard, deviation))
    return flagged


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--table", type=Path, default=None)
    args = parser.parse_args(argv)

    table = test_sharding.load_table(args.table)
    if not table:
        print("::warning::tests/shard_durations.json is missing or unreadable; shards are balanced by test count")
        return 0
    paths = [p for p in args.reports if p.is_file()]
    rows = analyse(paths, table)
    if not rows:
        print("no junit reports to check")
        return 0

    mean = sum(r[1] for r in rows) / len(rows)
    lines = ["| shard | measured min | predicted min | files not in table |", "|---|---|---|---|"]
    for shard, measured, predicted, unknown in rows:
        lines.append(f"| {shard} | {measured / 60:.1f} | {predicted / 60:.1f} | {len(unknown)} |")
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("### Pytest shard balance\n\n" + "\n".join(lines) + "\n")

    for shard, deviation in drifted(rows):
        print(
            f"::warning::pytest shard {shard} ran {deviation * 100:+.0f}% against what tests/shard_durations.json "
            "predicted (relative to this run's other shards); regenerate the table with "
            "scripts/ci/update_shard_durations.py --latest 3"
        )
    slowest = max(rows, key=lambda r: r[1])
    if mean and slowest[1] > mean * (1 + DRIFT):
        print(
            f"::warning::pytest shard {slowest[0]} took {slowest[1] / 60:.1f} min against a {mean / 60:.1f} min "
            "mean across shards; the shard duration table may be stale"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

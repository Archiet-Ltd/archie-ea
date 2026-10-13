"""Regenerate tests/shard_durations.json from the junit files of recent CI runs.

The pytest shards (scripts/ci/test_sharding.py) balance by the per-file run
times in that table. Each CI shard uploads backend-tests-N.xml, which records
the time of every test case; this script sums them per test file and writes the
table. Run it from a repository checkout when the CI warning step reports that
the table has drifted:

    python scripts/ci/update_shard_durations.py --latest 3
    python scripts/ci/update_shard_durations.py --run 38441287981 --run 38439758466
    python scripts/ci/update_shard_durations.py --junit-dir path/with/xml/files

--run and --latest download the tests-shard-N artifacts with the GitHub CLI
(`gh`, authenticated, --repo). With several runs the per-file median is taken,
so one run on a busy runner does not skew the table. Add --predict to print
the predicted per-shard minutes for the resulting table and exit without
writing.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TABLE_PATH = REPO_ROOT / "tests" / "shard_durations.json"
DEFAULT_REPO = "Archiet-Ltd/archie-ea"
SHARDS = 8


def class_to_file(classname: str, repo_root: Path, cache: dict) -> str | None:
    """Map a junit classname (tests.pkg.test_mod.TestClass) to its test file."""
    if classname in cache:
        return cache[classname]
    parts = classname.split(".")
    found = None
    for end in range(len(parts), 0, -1):
        candidate = "/".join(parts[:end]) + ".py"
        if (repo_root / candidate).is_file():
            found = candidate
            break
    cache[classname] = found
    return found


def read_junit(paths, repo_root: Path = REPO_ROOT) -> dict[str, dict]:
    """{file: {"seconds": float, "tests": int}} summed over the given xml files."""
    cache: dict = {}
    files: dict[str, dict] = {}
    for path in paths:
        root = ET.parse(path).getroot()
        for case in root.iter("testcase"):
            file = class_to_file(case.get("classname", ""), repo_root, cache)
            if file is None:
                continue
            entry = files.setdefault(file, {"seconds": 0.0, "tests": 0})
            entry["seconds"] += float(case.get("time") or 0)
            entry["tests"] += 1
    return files


def merge_runs(runs: list[dict[str, dict]]) -> dict[str, dict]:
    """Per-file median seconds across runs; test count from the first run that has the file."""
    merged: dict[str, dict] = {}
    for file in sorted({f for run in runs for f in run}):
        seen = [run[file] for run in runs if file in run]
        merged[file] = {
            "seconds": round(statistics.median(e["seconds"] for e in seen), 2),
            "tests": seen[0]["tests"],
        }
    return merged


def build_table(runs: list[dict[str, dict]], run_ids: list[str]) -> dict:
    files = merge_runs(runs)
    rates = [e["seconds"] / e["tests"] for e in files.values() if e["tests"]]
    return {
        "comment": (
            "Per-file run time of the backend tests, used by scripts/ci/test_sharding.py to "
            "balance the eight pytest shards. Regenerate with scripts/ci/update_shard_durations.py."
        ),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "source_runs": run_ids,
        "default_seconds_per_test": round(statistics.median(rates), 3) if rates else 1.0,
        "files": {f: files[f] for f in sorted(files)},
    }


def predict(table: dict, total: int = SHARDS) -> list[float]:
    """Predicted seconds per shard for the files in the table (as collected today)."""
    sys.path.insert(0, str(REPO_ROOT))
    from scripts.ci import test_sharding  # noqa: PLC0415

    counts = {f: e["tests"] for f, e in table["files"].items()}
    _assignment, loads = test_sharding.plan(counts, total, table)
    return [ms / 1000 for ms in loads]


def format_prediction(loads: list[float]) -> str:
    mean = sum(loads) / len(loads)
    lines = [
        f"  shard {i}: {s / 60:6.1f} min  ({(s - mean) / mean * 100:+5.1f}% vs mean)"
        for i, s in enumerate(loads)
    ]
    lines.append(
        f"  mean {mean / 60:.1f} min, max {max(loads) / 60:.1f} min "
        f"({(max(loads) - mean) / mean * 100:+.1f}%)"
    )
    return "\n".join(lines)


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def download_run(run_id: str, repo: str, dest: Path) -> list[Path]:
    target = dest / str(run_id)
    target.mkdir(parents=True, exist_ok=True)
    _gh("run", "download", str(run_id), "-R", repo, "-p", "tests-shard-*", "-D", str(target))
    return sorted(target.rglob("backend-tests-*.xml"))


def latest_run_ids(count: int, repo: str, branch: str) -> list[str]:
    """Newest completed CI runs on the branch that still hold all eight shard artifacts."""
    listing = json.loads(_gh(
        "run", "list", "-R", repo, "--workflow", "CI", "--branch", branch, "--limit", "40",
        "--json", "databaseId,status"))
    found: list[str] = []
    for row in listing:
        if row["status"] != "completed":
            continue
        names = json.loads(_gh(
            "api", f"repos/{repo}/actions/runs/{row['databaseId']}/artifacts?per_page=100"))
        shard_artifacts = [
            a for a in names.get("artifacts", [])
            if a["name"].startswith("tests-shard-") and not a["expired"]
        ]
        if len(shard_artifacts) >= SHARDS:
            found.append(str(row["databaseId"]))
        if len(found) == count:
            break
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="append", default=[], help="CI run id (repeatable)")
    parser.add_argument("--latest", type=int, default=0, help="use the N newest completed runs on --branch")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--junit-dir", action="append", default=[], type=Path,
                        help="directory holding backend-tests-*.xml (repeatable; one run each)")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--output", type=Path, default=TABLE_PATH)
    parser.add_argument("--predict", action="store_true", help="print predicted shard minutes, do not write")
    args = parser.parse_args(argv)

    run_ids = list(args.run)
    if args.latest:
        run_ids += latest_run_ids(args.latest, args.repo, args.branch)
    runs: list[dict[str, dict]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for run_id in run_ids:
            xmls = download_run(run_id, args.repo, Path(tmp))
            if xmls:
                runs.append(read_junit(xmls))
        for directory in args.junit_dir:
            xmls = sorted(directory.rglob("backend-tests-*.xml"))
            if xmls:
                runs.append(read_junit(xmls))
                run_ids.append(str(directory))
    if not runs:
        print("no junit data found", file=sys.stderr)
        return 2

    table = build_table(runs, run_ids)
    print(f"{len(table['files'])} test files, {len(runs)} run(s)")
    print(format_prediction(predict(table)))
    if args.predict:
        return 0
    args.output.write_text(json.dumps(table, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

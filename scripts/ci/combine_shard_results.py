"""Merge the backend-test matrix shards' route_verification.json files.

Each shard only exercises its own slice of the suite, so its
route_verification.json (written by scripts/route_verification_audit.py) only
lists the routes THAT shard hit. The nav-verified gate needs the union across
every shard -- reading any one shard's file directly would report every route
only the other shards exercise as unverified. This also refuses to produce a
result if a shard's artifact never arrived, rather than silently combining a
subset and passing.

Usage: python scripts/ci/combine_shard_results.py <downloaded-artifact-root> <expected-shard-count>

Writes route_verification.json at the repo root -- the same fixed path
scripts/verify.py's nav-verified gate reads.
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: combine_shard_results.py <artifact-root> <expected-shard-count>")
        return 2
    root, expected = sys.argv[1], int(sys.argv[2])

    route_files = sorted(glob.glob(f"{root}/tests-shard-*/route_verification.json"))
    if len(route_files) != expected:
        print(
            f"FAIL: expected {expected} shard route_verification.json files, "
            f"found {len(route_files)}: {route_files}"
        )
        return 1

    merged: set[str] = set()
    for path in route_files:
        merged |= set(json.loads(Path(path).read_text(encoding="utf-8")))

    (REPO / "route_verification.json").write_text(json.dumps(sorted(merged), indent=0), encoding="utf-8")
    print(f"merged {len(route_files)} shard route files -> {len(merged)} endpoints exercised")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

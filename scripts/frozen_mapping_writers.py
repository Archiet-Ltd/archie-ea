"""Count the places that write the two frozen mapping tables, and fail when the count rises.

``application_capability_mapping`` and ``application_capability_coverage`` are frozen: the one store is
``unified_application_capability_mapping``. A row created through either model is mirrored into the store
in the same transaction (a model-layer listener), so an older writer still reaches the store, but no new
code may create such a row. This scan finds constructor calls of the two models and raw SQL inserts into
the tables, per file, and compares them with ``scripts/frozen_mapping_writers_baseline.json``. The numbers
can only fall: a file not in the baseline, or a file over its count, fails.

    python scripts/frozen_mapping_writers.py            # check app/ against the baseline
    python scripts/frozen_mapping_writers.py --update   # lower the baseline (never raises it)
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "scripts" / "frozen_mapping_writers_baseline.json"
MODELS = {"ApplicationCapabilityMapping", "ApplicationCapabilityCoverage"}
SQL_INSERT = re.compile(r"INSERT\s+INTO\s+application_capability_(mapping|coverage)\b", re.IGNORECASE)
# The listeners that mirror a frozen row into the store, and this scan, are not writers.
SKIP_DIRS = {"__pycache__"}


def scan(root: Path) -> Dict[str, int]:
    """``{path relative to root: writer count}`` for every Python file under ``root``."""
    counts: Dict[str, int] = {}
    for path in sorted(root.rglob("*.py")):
        if SKIP_DIRS & set(path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "application_capability" not in text and "ApplicationCapability" not in text:
            continue
        n = len(SQL_INSERT.findall(text))
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
                if name in MODELS:
                    n += 1
        if n:
            counts[str(path.relative_to(root)).replace("\\", "/")] = n
    return counts


def violations(found: Dict[str, int], baseline: Dict[str, int]):
    return [(f, n, baseline.get(f, 0)) for f, n in sorted(found.items()) if n > baseline.get(f, 0)]


def main(argv) -> int:
    found = scan(ROOT / "app")
    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    if "--update" in argv:
        merged = {f: min(n, baseline[f]) for f, n in found.items() if f in baseline}
        BASELINE.write_text(json.dumps(dict(sorted(merged.items())), indent=2) + "\n")
        print(f"baseline written: {sum(merged.values())} writers in {len(merged)} files")
        return 0
    bad = violations(found, baseline)
    for f, n, allowed in bad:
        print(f"frozen-mapping-writers: {f} creates {n} row(s) in a frozen table (allowed {allowed}); "
              "write through capability_application_store.set_mapping instead")
    print(f"frozen-mapping-writers: {sum(found.values())} writers in {len(found)} files; "
          f"baseline {sum(baseline.values())}; {len(bad)} over")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

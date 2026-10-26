"""Rebuild app/release_waves/wave_map.json from the release plan's page list and the registered routes.

    python scripts/build_wave_map.py            # write the map
    python scripts/build_wave_map.py --check    # fail if the shipped map is stale
    python scripts/build_wave_map.py --counts   # print the number of route rules per wave

To refresh the plan itself, copy WAVE-PLAN-items-pages-v2.json over app/release_waves/wave_plan_pages.json first.
"""

from __future__ import annotations

import collections
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("FLASK_CONFIG", "testing")
# the optional connection (MCP) routes are part of the map
os.environ.setdefault("MCP_ENABLED", "true")
os.environ.setdefault("PUBLIC_BASE_URL", "http://127.0.0.1:5000")


def main(argv: list[str]) -> int:
    from app import create_app
    from app.release_waves.build import MAP_PATH, build, dump

    app = create_app("testing")
    rules = [(r.rule, r.endpoint) for r in app.url_map.iter_rules()]
    doc = build(rules)
    text = dump(doc)
    if "--counts" in argv:
        by_wave = collections.Counter()
        by_basis = collections.Counter()
        for rule, endpoint in rules:
            from app.release_waves import entry_for

            entry = entry_for(rule, endpoint)
            by_wave[entry[0]] += 1
            by_basis[entry[2]] += 1
        print("route rules:", len(rules))
        for wave in sorted(by_wave):
            print(f"  {wave}: {by_wave[wave]}")
        print("basis:", dict(by_basis))
        return 0
    if "--check" in argv:
        if MAP_PATH.read_text(encoding="utf-8") != text:
            print("wave_map.json is stale: run python scripts/build_wave_map.py")
            return 1
        print("wave_map.json is current:", len(doc["rules"]), "entries")
        return 0
    MAP_PATH.write_text(text, encoding="utf-8")
    print("wrote", MAP_PATH, len(doc["rules"]), "entries for", len(rules), "route rules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

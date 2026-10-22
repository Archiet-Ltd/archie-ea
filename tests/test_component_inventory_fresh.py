"""The committed docs/component-inventory.json must equal a fresh generation.

Fails when a template gains, loses or changes a control (or a route appears) and the file was not regenerated:

    python scripts/component_inventory.py

The first test needs no running app and names the controls that differ; the second regenerates everything,
routes included, and compares the whole file.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from component_inventory import build_inventory, build_template_inventory  # noqa: E402

COMMITTED = ROOT / "docs" / "component-inventory.json"
RUNTIME_ONLY = {"mutating", "rendered_on", "rendered_on_count"}
HOW = "regenerate it with: python scripts/component_inventory.py"


def _static(control):
    c = {k: v for k, v in control.items() if k not in RUNTIME_ONLY}
    c["target"] = {k: v for k, v in c["target"].items() if k != "endpoint_registered"}
    return json.dumps(c, sort_keys=True)


def test_every_template_control_is_in_the_committed_inventory():
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    fresh = build_template_inventory()
    have = {c["id"]: _static(c) for c in committed["controls"]}
    want = {c["id"]: _static(c) for c in fresh["controls"]}
    missing = sorted(set(want) - set(have))
    stale = sorted(set(have) - set(want))
    changed = sorted(i for i in set(have) & set(want) if have[i] != want[i])
    assert not (missing or stale or changed), (
        f"component-inventory.json is out of date: {len(missing)} control(s) missing, {len(stale)} gone, "
        f"{len(changed)} changed (e.g. {(missing + stale + changed)[:3]}); {HOW}"
    )
    assert committed["templates_sha256"] == fresh["templates_sha256"], f"a template changed; {HOW}"


def test_committed_inventory_equals_a_fresh_generation():
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    fresh = json.loads(json.dumps(build_inventory(), sort_keys=True))
    if committed != fresh:
        diff = [k for k in fresh if committed.get(k) != fresh[k]]
        raise AssertionError(f"component-inventory.json differs from a fresh generation in {diff}; {HOW}")

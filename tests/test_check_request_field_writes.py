"""The request-field-writes gate counts loops that write attributes named by the loop."""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_request_field_writes.py"
_spec = importlib.util.spec_from_file_location("check_request_field_writes", SCRIPT)
gate = importlib.util.module_from_spec(_spec)
sys.modules["check_request_field_writes"] = gate
_spec.loader.exec_module(gate)

ONE = """
def update(obj, data):
    for field, value in data.items():
        if hasattr(obj, field):
            setattr(obj, field, value)
"""


def _count(tmp_path, code):
    app = tmp_path / "app"
    app.mkdir(exist_ok=True)
    (app / "m.py").write_text(textwrap.dedent(code))
    return gate.scan(app=app, repo=tmp_path)


def test_the_items_loop_is_counted(tmp_path):
    assert len(_count(tmp_path, ONE)) == 1


def test_a_second_loop_raises_the_count_by_one(tmp_path):
    second = ONE + ONE.replace("update", "update_two")
    assert len(_count(tmp_path, second)) == len(_count(tmp_path, ONE)) + 1


def test_the_key_loop_variant_is_counted(tmp_path):
    code = "def u(o, d, names):\n    for key in names:\n        setattr(o, key, d[key])\n"
    assert len(_count(tmp_path, code)) == 1


def test_named_assignments_and_other_setattr_are_not_counted(tmp_path):
    code = (
        "def u(o, d):\n    o.name = d['name']\n"
        "    for item in d['items']:\n        setattr(o, 'fixed', item)\n"
    )
    assert _count(tmp_path, code) == []


def test_a_reasoned_marker_exempts_and_a_bare_one_does_not(tmp_path):
    ok = "def u(o, d):\n    # request-field-writes-ok: fixed internal fields\n    for k, v in d.items():\n        setattr(o, k, v)\n"
    bare = ok.replace("fixed internal fields", "")
    assert _count(tmp_path, ok) == []
    assert len(_count(tmp_path, bare)) == 1


def test_the_gate_passes_at_the_baseline_and_fails_at_plus_one():
    import json
    import subprocess
    root = SCRIPT.parent.parent
    baseline = json.loads((root / "verification_baseline.json").read_text())["ratchets"]["request_field_writes"]
    sys.path.insert(0, str(SCRIPT.parent))
    import verify

    count = len(gate.scan())
    assert count <= baseline
    assert verify.gate_request_field_writes(count).status == verify.PASS
    assert verify.gate_request_field_writes(count - 1).status == verify.FAIL

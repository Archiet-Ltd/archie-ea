"""The ratchet fails when a new direct capability query appears under app/."""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "check_capability_picker_accessor.py")


def _tree(tmp_path, body):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "app").mkdir()
    (tmp_path / "scripts" / "capability_picker_baseline.json").write_text(json.dumps({}))
    (tmp_path / "app" / "picker.py").write_text(body)
    out = subprocess.run(
        [sys.executable, SCRIPT, "--count", "--root", str(tmp_path)],
        capture_output=True, text=True, check=True,
    )
    return int(out.stdout.strip().splitlines()[-1])


def test_direct_picker_query_is_counted(tmp_path):
    assert _tree(tmp_path, "rows = UnifiedCapability.query.order_by(x).all()\n") == 1


def test_active_accessor_and_record_reads_are_not_counted(tmp_path):
    body = (
        "rows = UnifiedCapability.active().order_by(x).all()\n"
        "one = UnifiedCapability.query.get(3)\n"
        "n = UnifiedCapability.query.count()\n"
        "h = UnifiedCapability.query.all()  # retired-ok: history page lists every record\n"
    )
    assert _tree(tmp_path, body) == 0


def test_repository_is_within_its_baseline():
    out = subprocess.run(
        [sys.executable, SCRIPT, "--count"], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert out.stdout.strip().splitlines()[-1] == "0", out.stdout

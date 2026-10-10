"""The background-mechanisms gate: unregistered work fails, registered work passes."""
import json
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "check_background_mechanisms.py"

ENTRY = """mechanisms:
- kind: thread
  file: app/worker.py
  entry: run
  purpose: test
  class: queue
  disposition: keep
  tenant_context: none
"""
POOL_SOURCE = (
    "from concurrent.futures import ThreadPoolExecutor\n"
    "def run():\n    return ThreadPoolExecutor(max_workers=2)\n"
)


def _tree(tmp_path, source=POOL_SOURCE, register=None, name="worker.py"):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / name).write_text(source)
    (tmp_path / "docs").mkdir()
    if register is not None:
        (tmp_path / "docs" / "background-mechanisms.yml").write_text(register)
    return tmp_path


def _run(root, *extra):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *extra],
        capture_output=True, text=True,
    )


def test_unregistered_thread_pool_fails(tmp_path):
    proc = _run(_tree(tmp_path, register="mechanisms: []\n"))
    assert proc.returncode == 1
    assert "app/worker.py" in proc.stdout and "not in docs/background-mechanisms.yml" in proc.stdout


def test_registered_thread_pool_passes(tmp_path):
    proc = _run(_tree(tmp_path, register=ENTRY))
    assert proc.returncode == 0, proc.stdout
    assert _run(tmp_path, "--count").stdout.strip() == "1"


def test_aliased_threading_import_is_found(tmp_path):
    source = "import threading as t\ndef go():\n    t.Thread(target=go).start()\n"
    proc = _run(_tree(tmp_path, source=source, register="mechanisms: []\n"))
    assert proc.returncode == 1 and "uses thread" in proc.stdout


@pytest.mark.parametrize("source,kind", [
    ("import multiprocessing\nmultiprocessing.get_context('spawn')\n", "process-pool"),
    ("from celery import Celery\n", "celery"),
    ("from apscheduler.schedulers.background import BackgroundScheduler\n", "apscheduler"),
    ("from flask_rq2 import RQ\n", "rq"),
])
def test_every_mechanism_family_is_detected(tmp_path, source, kind):
    proc = _run(_tree(tmp_path, source=source, register="mechanisms: []\n"))
    assert proc.returncode == 1 and "uses " + kind in proc.stdout


def test_empty_scan_is_no_evidence_not_a_pass(tmp_path):
    proc = _run(_tree(tmp_path, source="x = 1\n", register="mechanisms: []\n"))
    assert proc.returncode == 2
    assert "no-evidence" in proc.stdout


def test_stale_entry_fails(tmp_path):
    root = _tree(tmp_path, register=ENTRY)
    (root / "app" / "other.py").write_text(POOL_SOURCE)
    (root / "app" / "worker.py").write_text("x = 1\n")
    proc = _run(root)
    assert proc.returncode == 1
    assert "no longer uses it" in proc.stdout and "app/other.py" in proc.stdout


def test_entry_missing_a_field_or_with_bad_value_fails(tmp_path):
    bad = ENTRY.replace("disposition: keep", "disposition: someday")
    assert "allowed:" in _run(_tree(tmp_path, register=bad)).stdout
    missing = ENTRY.replace("  tenant_context: none\n", "")
    other = tmp_path / "second"
    other.mkdir()
    assert "lacks tenant_context" in _run(_tree(other, register=missing)).stdout


def test_generated_customer_code_and_test_code_are_not_scanned(tmp_path):
    root = _tree(tmp_path, register=ENTRY)
    template_dir = root / "app" / "modules" / "solutions_product" / "templates"
    template_dir.mkdir(parents=True)
    (template_dir / "gen.py").write_text(POOL_SOURCE)
    tests_dir = root / "app" / "modules" / "x" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_a.py").write_text(POOL_SOURCE)
    assert _run(root).returncode == 0


def test_the_real_tree_is_fully_registered():
    proc = _run(REPO)
    assert proc.returncode == 0, proc.stdout


def test_gate_is_registered_in_verify_with_a_recorded_baseline():
    verify_source = (REPO / "scripts" / "verify.py").read_text()
    assert 'Gate("background-mechanisms"' in verify_source
    baseline = json.loads((REPO / "verification_baseline.json").read_text())
    current = int(_run(REPO, "--count").stdout.strip())
    assert current <= baseline["ratchets"]["background_mechanisms_thread"]

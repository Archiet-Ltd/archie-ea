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
    instances = int(_run(REPO, "--count-instances").stdout.strip())
    assert instances <= baseline["ratchets"]["background_mechanisms_thread_instances"]


# --- the register is keyed on file, kind and enclosing function -------------

TWO_FUNCTIONS = (
    "from concurrent.futures import ThreadPoolExecutor\n"
    "import threading\n"
    "def run():\n    return ThreadPoolExecutor(max_workers=2)\n"
    "def later():\n    threading.Thread(target=run).start()\n"
)


def test_a_second_pool_in_an_already_registered_file_fails(tmp_path):
    root = _tree(tmp_path, source=TWO_FUNCTIONS, register=ENTRY)
    proc = _run(root)
    assert proc.returncode == 1
    assert "uses thread (in later)" in proc.stdout


def test_registering_the_new_function_makes_it_pass_and_counts_both(tmp_path):
    register = ENTRY + ENTRY.split("mechanisms:\n")[1].replace("entry: run", "entry: later")
    root = _tree(tmp_path, source=TWO_FUNCTIONS, register=register)
    assert _run(root).returncode == 0
    assert _run(root, "--count-instances").stdout.strip() == "2"
    assert _run(root, "--count").stdout.strip() == "1"


def test_two_instances_in_one_function_must_be_recorded(tmp_path):
    source = ("import threading\n"
              "def run():\n    threading.Thread(target=run).start()\n"
              "    threading.Thread(target=run).start()\n")
    root = _tree(tmp_path, source=source, register=ENTRY)
    proc = _run(root)
    assert proc.returncode == 1 and "starts 2 thread instance(s) in run" in proc.stdout
    recorded = ENTRY + "  instances: 2\n"
    (root / "docs" / "background-mechanisms.yml").write_text(recorded)
    assert _run(root).returncode == 0
    assert _run(root, "--count-instances").stdout.strip() == "2"


def test_a_type_annotation_or_isinstance_is_not_a_mechanism(tmp_path):
    source = ("import threading\n"
              "class A:\n    def __init__(self):\n"
              "        self.t: threading.Thread = None\n"
              "        self.ok = isinstance(self.t, threading.Thread)\n")
    proc = _run(_tree(tmp_path, source=source, register="mechanisms: []\n"))
    assert proc.returncode == 2   # nothing started: no evidence, not a finding


# --- entries are validated ---------------------------------------------------

def test_an_other_entry_naming_a_missing_file_fails(tmp_path):
    other = ENTRY + ENTRY.replace("kind: thread", "kind: other").split("mechanisms:\n")[1] \
        .replace("app/worker.py", "app/gone.py")
    proc = _run(_tree(tmp_path, register=other))
    assert proc.returncode == 1
    assert "app/gone.py" in proc.stdout and "does not exist" in proc.stdout


def test_a_duplicate_entry_is_refused(tmp_path):
    duplicate = ENTRY + ENTRY.split("mechanisms:\n")[1]
    proc = _run(_tree(tmp_path, register=duplicate))
    assert proc.returncode == 1 and "duplicate entry" in proc.stdout
    assert _run(tmp_path, "--count-instances").stdout.strip() == "1"


# --- processes that outlive the request, and the worker command --------------

DETACHED = ("import subprocess, sys\n"
            "def start():\n    p = subprocess.Popen([sys.executable, '-m', 'x'])\n"
            "    return p.pid\n")


def test_a_detached_subprocess_is_found(tmp_path):
    proc = _run(_tree(tmp_path, source=DETACHED, register="mechanisms: []\n"))
    assert proc.returncode == 1 and "uses subprocess (in start)" in proc.stdout


@pytest.mark.parametrize("source", [
    "import subprocess\ndef f():\n    p = subprocess.Popen(['ls'])\n    p.wait()\n    return 1\n",
    "import subprocess\ndef f():\n    p = subprocess.Popen(['ls'])\n    return p.communicate()\n",
    "import subprocess\ndef f():\n    return subprocess.run(['ls'])\n",
])
def test_a_subprocess_the_request_waits_for_is_not_a_mechanism(tmp_path, source):
    proc = _run(_tree(tmp_path, source=source, register="mechanisms: []\n"))
    assert proc.returncode == 2


def test_aliased_popen_and_fork_are_found(tmp_path):
    aliased = "from subprocess import Popen as P\ndef f():\n    P(['x'])\n"
    assert "uses subprocess" in _run(_tree(tmp_path, source=aliased, register="mechanisms: []\n")).stdout
    other = tmp_path / "second"
    other.mkdir()
    fork = "import os\ndef f():\n    os.fork()\n"
    assert "uses subprocess" in _run(_tree(other, source=fork, register="mechanisms: []\n")).stdout


def test_event_loop_executor_helpers_are_excluded_and_the_header_says_why(tmp_path):
    source = ("import asyncio\n"
              "async def f(loop, fn):\n"
              "    await asyncio.to_thread(fn)\n"
              "    await loop.run_in_executor(None, fn)\n")
    assert _run(_tree(tmp_path, source=source, register="mechanisms: []\n")).returncode == 2
    header = (REPO / "docs" / "background-mechanisms.yml").read_text().split("mechanisms:\n")[0]
    assert "asyncio.to_thread" in header and "run_in_executor" in header
    assert "Excluded on purpose" in header


def test_manage_py_is_scanned(tmp_path):
    root = _tree(tmp_path, source="x = 1\n", register="mechanisms: []\n")
    (root / "manage.py").write_text("from rq import Worker\n")
    proc = _run(root)
    assert proc.returncode == 1 and "manage.py" in proc.stdout and "uses rq" in proc.stdout


def test_the_rq_worker_command_and_the_detached_server_are_in_the_real_register():
    import yaml
    entries = yaml.safe_load((REPO / "docs" / "background-mechanisms.yml").read_text())["mechanisms"]
    keys = {(e["file"], e["kind"]) for e in entries}
    assert ("manage.py", "rq") in keys
    assert ("app/modules/codegen/services/deployment_orchestrator.py", "subprocess") in keys
    uvicorn = next(e for e in entries if e["kind"] == "subprocess")
    assert uvicorn["class"] == "generated-code" and uvicorn["disposition"] == "out-of-scope"
    assert "Dockerfile.worker" in next(e for e in entries if e["file"] == "manage.py")["purpose"]


# --- --update-baseline lowers this ratchet ----------------------------------

def test_update_baseline_lowers_the_background_mechanisms_keys(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("verify_under_test", REPO / "scripts" / "verify.py")
    verify = importlib.util.module_from_spec(spec)
    sys.modules["verify_under_test"] = verify
    spec.loader.exec_module(verify)
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"ratchets": {
        "background_mechanisms_thread": 40, "background_mechanisms_thread_instances": 60}}))
    monkeypatch.setattr(verify, "BASELINE_PATH", path)

    assert verify.main(["--gate", "background-mechanisms", "--update-baseline"]) == 0

    written = json.loads(path.read_text())["ratchets"]
    files = int(_run(REPO, "--count").stdout.strip())
    instances = int(_run(REPO, "--count-instances").stdout.strip())
    assert written["background_mechanisms_thread"] == files < 40
    assert written["background_mechanisms_thread_instances"] == instances < 60


def test_the_instance_ratchet_fails_when_the_baseline_is_exceeded(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("verify_under_test2", REPO / "scripts" / "verify.py")
    verify = importlib.util.module_from_spec(spec)
    sys.modules["verify_under_test2"] = verify
    spec.loader.exec_module(verify)
    instances = int(_run(REPO, "--count-instances").stdout.strip())
    files = int(_run(REPO, "--count").stdout.strip())
    assert verify.gate_background_mechanisms(files, instances).status == verify.PASS
    assert verify.gate_background_mechanisms(files, instances - 1).status == verify.FAIL
    assert verify.gate_background_mechanisms(files - 1, instances).status == verify.FAIL

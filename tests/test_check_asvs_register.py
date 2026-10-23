"""Tests for scripts/check_asvs_register.py.

These build a tiny synthetic register (docs/security/asvs-l2.yml) plus a few
fake test files under a tmp directory and point the check at it with --root, so
the test is fast and self-contained. The real register is exercised when the
gate runs against the repository root.

The register is a flat list of rows under a top-level ``rows:`` key. Each row
carries ``id``, ``family``, ``applies``, ``evidence`` (a block list) and
``status``. The script must parse this with PyYAML when it is importable and
with a built-in fallback loader when it is not, so the gate runs the same way
with or without PyYAML on the path. One of the tests below runs the script with
PyYAML forced off (PYTHONPATH scrubbed of yaml) to prove the fallback works.
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "check_asvs_register.py")

BRIEF_TOKEN = "T-qa-13"


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _run(root: str, *args: str, drop_yaml: bool = False):
    env = dict(os.environ)
    if drop_yaml:
        # Force the fallback loader: remove any path that could carry PyYAML.
        env["PYTHONPATH"] = ""
    result = subprocess.run(
        [sys.executable, SCRIPT, "--root", root, *args],
        capture_output=True,
        text=True,
        env=env,
    )
    return result


def _build_tree(tmp_path, register_text, test_files=None):
    root = str(tmp_path)
    _write(os.path.join(root, "docs", "security", "asvs-l2.yml"), register_text)
    os.makedirs(os.path.join(root, "tests"), exist_ok=True)
    for rel, text in (test_files or {}).items():
        _write(os.path.join(root, rel), text)
    return root


EXISTING_TEST = (
    "import pytest\n\n\n"
    "class TestAccess:\n"
    "    def test_method_boundary(self):\n"
    "        assert True\n\n\n"
    "def test_org_isolation(two_orgs):\n"
    "    assert True\n"
)


def test_evidence_naming_existing_node_passes(tmp_path):
    register = (
        "rows:\n"
        "  - id: V4.1.1\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - tests/test_access.py::test_org_isolation\n"
        "  - id: V4.1.3\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - tests/test_access.py::TestAccess::test_method_boundary\n"
    )
    root = _build_tree(tmp_path, register, {"tests/test_access.py": EXISTING_TEST})
    result = _run(root, "--count")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_evidence_naming_missing_node_fails(tmp_path):
    register = (
        "rows:\n"
        "  - id: V4.1.1\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - tests/test_access.py::test_does_not_exist\n"
    )
    root = _build_tree(tmp_path, register, {"tests/test_access.py": EXISTING_TEST})
    result = _run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "1"
    assert any("test_does_not_exist" in line for line in lines[:-1])


def test_fail_row_without_brief_id_is_reported_malformed(tmp_path):
    register = (
        "rows:\n"
        "  - id: V13.1.1\n"
        "    family: webhook\n"
        "    applies: true\n"
        "    status: fail\n"
        "    evidence:\n"
        "      - no brief named here\n"
    )
    root = _build_tree(tmp_path, register, {})
    result = _run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "1"
    assert any("V13.1.1" in line for line in lines[:-1])


def test_fail_row_naming_brief_id_passes(tmp_path):
    register = (
        "rows:\n"
        "  - id: V13.1.1\n"
        "    family: webhook\n"
        "    applies: true\n"
        "    status: fail\n"
        "    evidence:\n"
        "      - no evidence yet; tracked by " + BRIEF_TOKEN + "\n"
    )
    root = _build_tree(tmp_path, register, {})
    result = _run(root, "--count")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_applies_false_row_needs_no_evidence(tmp_path):
    register = (
        "rows:\n"
        "  - id: V9.1.1\n"
        "    family: account\n"
        "    applies: false\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - not applicable: TLS is terminated at the load balancer\n"
    )
    root = _build_tree(tmp_path, register, {})
    result = _run(root, "--count")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_control_description_with_existing_file_passes(tmp_path):
    register = (
        "rows:\n"
        "  - id: V14.1.1\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - build pipeline recorded in Dockerfile\n"
    )
    root = _build_tree(tmp_path, register, {})
    _write(os.path.join(root, "Dockerfile"), "FROM python:3.11\n")
    result = _run(root, "--count")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_control_description_with_missing_file_fails(tmp_path):
    register = (
        "rows:\n"
        "  - id: V14.1.1\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - control recorded in docs/security/no_such_file.md\n"
    )
    root = _build_tree(tmp_path, register, {})
    result = _run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "1"
    assert any("no_such_file" in line for line in lines[:-1])


def test_count_prints_trailing_integer(tmp_path):
    register = (
        "rows:\n"
        "  - id: V4.1.1\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - tests/test_access.py::test_missing_one\n"
        "  - id: V4.1.2\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - tests/test_access.py::test_missing_two\n"
    )
    root = _build_tree(tmp_path, register, {"tests/test_access.py": EXISTING_TEST})
    result = _run(root, "--count")
    last = result.stdout.strip().splitlines()[-1]
    assert last.isdigit()
    assert last == "2"


def test_fallback_loader_runs_without_pyyaml(tmp_path):
    # Same as the passing case, but with PyYAML forced off the path. The gate
    # must still parse the register and report 0 problems.
    register = (
        "rows:\n"
        "  - id: V4.1.1\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - tests/test_access.py::test_org_isolation\n"
    )
    root = _build_tree(tmp_path, register, {"tests/test_access.py": EXISTING_TEST})
    result = _run(root, "--count", drop_yaml=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"

"""Tests for scripts/check_two_org_coverage.py.

These build a tiny synthetic tree (a small component-inventory.json plus a few
fake test files) under a tmp directory and point the check at it with --root, so
the test is fast and does not depend on the ~5MB real inventory. The real
inventory is exercised when the gate runs against the repository root.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "check_two_org_coverage.py")


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _inventory(routes):
    return json.dumps({"routes": routes})


def _run(root: str, *args: str):
    result = subprocess.run(
        [sys.executable, SCRIPT, "--root", root, *args],
        capture_output=True,
        text=True,
    )
    return result


def _build_tree(tmp_path, routes, test_files):
    root = str(tmp_path)
    _write(os.path.join(root, "docs", "component-inventory.json"), _inventory(routes))
    # ensure the tests directory exists even when empty
    os.makedirs(os.path.join(root, "tests"), exist_ok=True)
    for rel, text in test_files.items():
        _write(os.path.join(root, rel), text)
    return root


def test_family_with_matching_two_org_test_is_covered(tmp_path):
    routes = [
        {"blueprint": "widgets", "login_required": True, "rule": "/widgets/"},
    ]
    files = {
        "tests/test_widgets_iso.py": (
            "def two_orgs():\n    pass\n\n"
            "def test_widgets_isolation(two_orgs):\n    assert True\n"
        ),
    }
    root = _build_tree(tmp_path, routes, files)
    result = _run(root, "--count")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_family_without_two_org_test_is_uncovered(tmp_path):
    routes = [
        {"blueprint": "lonely", "login_required": True, "rule": "/lonely/"},
    ]
    files = {
        "tests/test_unrelated.py": "def test_nothing():\n    assert True\n",
    }
    root = _build_tree(tmp_path, routes, files)
    result = _run(root)
    # An uncovered non-startup family over baseline makes the gate fail (exit 1).
    assert result.returncode == 1, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "1"
    assert any("lonely" in line for line in lines[:-1])


def test_family_that_does_not_read_org_data_is_ignored(tmp_path):
    # No login_required route -> not a tenant-scoped surface -> not counted.
    routes = [
        {"blueprint": "public_bp", "login_required": False, "rule": "/public/"},
    ]
    root = _build_tree(tmp_path, routes, {})
    result = _run(root, "--count")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_escape_hatch_excuses_a_family(tmp_path):
    routes = [
        {"blueprint": "excused_bp", "login_required": True, "rule": "/excused/"},
    ]
    files = {
        "tests/test_marker.py": (
            "# two-org-coverage-ok: excused_bp is a read-only status surface\n"
            "def test_marker():\n    assert True\n"
        ),
    }
    root = _build_tree(tmp_path, routes, files)
    result = _run(root, "--count")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_count_prints_trailing_integer(tmp_path):
    routes = [
        {"blueprint": "a_bp", "login_required": True, "rule": "/a/"},
        {"blueprint": "b_bp", "login_required": True, "rule": "/b/"},
    ]
    root = _build_tree(tmp_path, routes, {})
    result = _run(root, "--count")
    last = result.stdout.strip().splitlines()[-1]
    assert last.isdigit()
    assert last == "2"


def test_startup_families_all_covered_via_alias_map(tmp_path):
    # One route per startup family blueprint, all reading org data.
    startup_blueprints = [
        "account",
        "m365_connector",
        "intelligence_api",
        "unified_applications",
        "billing",
        "team",
        "dashboard",
    ]
    routes = [
        {"blueprint": bp, "login_required": True, "rule": f"/{bp}/"}
        for bp in startup_blueprints
    ]
    # Fake two-org tests whose sources name each startup family keyword.
    files = {
        "tests/smoke/test_authorisation_matrix.py": (
            "def two_orgs():\n    pass\n\n"
            "def test_matrix(two_orgs):\n"
            "    # account connector intelligence application billing team dashboard\n"
            "    assert True\n"
        ),
    }
    root = _build_tree(tmp_path, routes, files)
    result = _run(root, "--startup-only", "--count")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"

"""PR test selector: fast-lane test selection from changed files."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts.ci.pr_test_selector import (
    _module_name_from_path,
    select_tests,
)

REPO = Path(__file__).resolve().parent.parent


# ── _module_name_from_path ────────────────────────────────────────────────


def test_module_name_from_path_converts_slashes_to_dots():
    assert _module_name_from_path("scripts/ci/test_sharding.py") == "scripts.ci.test_sharding"
    assert _module_name_from_path("tests/test_foo.py") == "tests.test_foo"
    assert _module_name_from_path("app/modules/foo/routes.py") == "app.modules.foo.routes"


# ── select_tests: parent-package expansion (D2 regression guard) ──────────


def _fake_changed_files(files: set[str]):
    """Return a monkeypatch for _changed_files that returns *files*."""
    import scripts.ci.pr_test_selector as sel

    original = sel._changed_files

    def patched(_base):
        return files

    sel._changed_files = patched
    return original


def test_parent_package_not_added_when_init_not_changed(monkeypatch):
    """When a module under scripts/ci/ changes but its __init__.py does not,
    parent packages (scripts, scripts.ci) must NOT be added to changed_modules.
    This prevents over-matching: a test that imports from scripts.verify
    should not be selected just because scripts/ci/test_sharding.py changed."""
    import scripts.ci.pr_test_selector as sel

    monkeypatch.setattr(sel, "_changed_files", lambda _: {
        "scripts/ci/test_sharding.py",
    })

    test_files, changed_modules, changed_test = sel.select_tests("ignored")

    # The changed module itself must be present
    assert "scripts.ci.test_sharding" in changed_modules
    # But parent packages must NOT be added (their __init__.py did not change)
    assert "scripts" not in changed_modules, (
        "scripts was added even though scripts/__init__.py did not change"
    )
    assert "scripts.ci" not in changed_modules, (
        "scripts.ci was added even though scripts/ci/__init__.py did not change"
    )


def test_parent_package_added_when_init_is_changed(monkeypatch):
    """When __init__.py is itself changed, its parent package SHOULD be in
    changed_modules so that tests importing from that package are selected."""
    import scripts.ci.pr_test_selector as sel

    monkeypatch.setattr(sel, "_changed_files", lambda _: {
        "scripts/__init__.py",
        "scripts/ci/__init__.py",
    })

    test_files, changed_modules, changed_test = sel.select_tests("ignored")

    assert "scripts" in changed_modules, (
        "scripts should be in changed_modules when scripts/__init__.py changed"
    )
    assert "scripts.ci" in changed_modules, (
        "scripts.ci should be in changed_modules when scripts/ci/__init__.py changed"
    )


def test_select_tests_returns_changed_test_files(monkeypatch):
    """A test file that changed itself must be in the selected list."""
    import scripts.ci.pr_test_selector as sel

    monkeypatch.setattr(sel, "_changed_files", lambda _: {
        "tests/test_dummy_selector.py",
    })

    test_files, changed_modules, changed_test = sel.select_tests("ignored")

    assert "tests/test_dummy_selector.py" in test_files
    assert "tests/test_dummy_selector.py" in changed_test


def test_select_tests_does_not_pull_unrelated_tests(monkeypatch):
    """When scripts/ci/test_sharding.py changes, tests that import from
    scripts.verify (not scripts.ci.test_sharding) must NOT be selected.
    This is the concrete over-matching scenario from the D2 defect."""
    import scripts.ci.pr_test_selector as sel

    monkeypatch.setattr(sel, "_changed_files", lambda _: {
        "scripts/ci/test_sharding.py",
    })

    test_files, changed_modules, changed_test = sel.select_tests("ignored")

    # The changed module should be in changed_modules
    assert "scripts.ci.test_sharding" in changed_modules
    # scripts and scripts.ci should NOT be in changed_modules
    assert "scripts" not in changed_modules
    assert "scripts.ci" not in changed_modules

    # No test files should be selected as affected (only the changed test file
    # itself, but there are none in this scenario)
    # Tests that import from scripts.verify should NOT be pulled in
    for tf in test_files:
        assert "test_ci_nav_verification_lifecycle" not in tf, (
            "test_ci_nav_verification_lifecycle.py imports from scripts.verify, "
            "not from scripts.ci.test_sharding, so it must not be selected"
        )
        assert "test_production_readiness" not in tf, (
            "production_readiness tests import from scripts, not from "
            "scripts.ci.test_sharding, so they must not be selected"
        )
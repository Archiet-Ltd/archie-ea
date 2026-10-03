"""Select test files affected by a PR's changes for the fast-lane CI job.

Usage: python scripts/ci/pr_test_selector.py [base-ref]

Writes to stdout a space-separated list of pytest file paths for test files
that either (a) changed themselves, or (b) import a Python module that
changed in this branch relative to the base ref.

When no base-ref is given, defaults to origin/main.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
TEST_DIRS = {"tests", "app/modules"}


def _changed_files(base: str) -> set[str]:
    """Return paths, relative to the repo root, changed on this branch."""
    try:
        output = subprocess.check_output(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            cwd=str(REPO), text=True,
        )
    except subprocess.CalledProcessError:
        return set()
    return {line.strip() for line in output.splitlines() if line.strip()}


def _imported_modules(file_path: str) -> set[str]:
    """Return the set of module names a Python file imports."""
    full = REPO / file_path
    if not full.exists():
        return set()
    try:
        tree = ast.parse(full.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return set()

    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module)
    return modules


def _module_name_from_path(py_path: str) -> str:
    """Convert a repo-relative .py path to a dotted module name."""
    without_ext = py_path[:-3] if py_path.endswith(".py") else py_path
    return without_ext.replace("/", ".").replace("\\", ".")


def _is_test_file(rel_path: str) -> bool:
    """Return True if the path is a test file under tests/ or app/modules/*/tests/."""
    parts = Path(rel_path).parts
    if not rel_path.endswith(".py"):
        return False
    if parts[0] == "tests":
        return True
    if len(parts) >= 3 and parts[0] == "app" and parts[1] == "modules" and "tests" in parts[2:]:
        return True
    return False


def select_tests(base_ref: str = "origin/main") -> tuple[list[str], set[str], set[str]]:
    """Return (test_files, changed_modules, changed_test_files)."""
    changed = _changed_files(base_ref)
    changed_py = {f for f in changed if f.endswith(".py")}
    changed_non_test = {f for f in changed_py if not _is_test_file(f)}
    changed_test = {f for f in changed_py if _is_test_file(f)}

    changed_modules = {_module_name_from_path(f) for f in changed_non_test}
    # Only add parent packages when the corresponding __init__.py is itself
    # changed.  Without this guard, changing any module under scripts/ci/ adds
    # "scripts" and "scripts.ci" to changed_modules, which then pulls in every
    # test file that imports anything from scripts/ — even when the changed
    # module is unrelated.
    extra = set()
    for mod in list(changed_modules):
        parts = mod.split(".")
        for i in range(1, len(parts)):
            parent_pkg = ".".join(parts[:i])
            init_path = parent_pkg.replace(".", "/") + "/__init__.py"
            if init_path in changed_py:
                extra.add(parent_pkg)
    changed_modules |= extra

    # Find test files that import any changed module
    affected_tests: set[str] = set(changed_test)
    for root, _, files in os.walk(REPO / "tests"):
        for fname in files:
            if not fname.endswith(".py") or fname.startswith("__"):
                continue
            rel = str(Path(root, fname).relative_to(REPO))
            if rel in changed_test:
                continue
            imports = _imported_modules(rel)
            if imports & changed_modules:
                affected_tests.add(rel)

    # Also scan app/modules/*/tests/
    modules_tests = REPO / "app" / "modules"
    if modules_tests.exists():
        for mod_dir in modules_tests.iterdir():
            if not mod_dir.is_dir():
                continue
            test_dir = mod_dir / "tests"
            if not test_dir.is_dir():
                continue
            for root, _, files in os.walk(test_dir):
                for fname in files:
                    if not fname.endswith(".py") or fname.startswith("__"):
                        continue
                    rel = str(Path(root, fname).relative_to(REPO))
                    if rel in changed_test:
                        continue
                    imports = _imported_modules(rel)
                    if imports & changed_modules:
                        affected_tests.add(rel)

    return sorted(affected_tests), changed_modules, changed_test


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    test_files, modules, changed_tests = select_tests(base)
    if test_files:
        print(" ".join(test_files))
    else:
        print("")
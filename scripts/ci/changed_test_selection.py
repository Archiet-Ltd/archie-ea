#!/usr/bin/env python
"""Choose the pytest files the fast lane runs for a pull request.

The fast lane runs on ordinary pull-request pushes, with the full suite held
back until the pull request carries the `full-ci` label. It runs:

  * every test file the pull request adds or changes, and
  * every test file that imports a module the pull request changes.

Browser journeys under tests/smoke need a live server and are left to the full
run. Printed one path per line, sorted. Nothing is printed when no test file is
affected, which is not an error.

    python scripts/ci/changed_test_selection.py --base origin/main
    python scripts/ci/changed_test_selection.py --files app/foo.py tests/test_foo.py
    python scripts/ci/changed_test_selection.py --browsers tests/test_a.py ...
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

TEST_ROOTS = ("tests", "app/modules")
SMOKE_PREFIX = "tests/smoke/"

# A change here can alter any test, which the fast lane cannot cover.
GLOBAL_IMPACT = (
    "tests/conftest.py",
    "app/modules/conftest.py",
    "pytest.ini",
    "requirements.txt",
    "requirements-test.txt",
)

BROWSERS = ("firefox", "webkit")


def is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    if not (name.startswith("test_") and name.endswith(".py")):
        return False
    if path.startswith(SMOKE_PREFIX):
        return False
    return path.startswith("tests/") or (
        path.startswith("app/modules/") and "/tests/" in path
    )


def module_name(path: str) -> str | None:
    """Dotted module name of a python file in the checkout, or None."""
    if not path.endswith(".py"):
        return None
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else None


def imported_names(source: str, module: str, is_package: bool = False) -> set[str]:
    """Every dotted name a module imports, including `from a import b` as a.b.

    Relative imports are resolved against the importing module's own package.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    package = module.split(".") if is_package else module.split(".")[:-1]
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = len(package) - (node.level - 1)
                if keep < 0:
                    continue
                base = package[:keep]
                if node.module:
                    base = base + node.module.split(".")
                base_name = ".".join(base)
            else:
                base_name = node.module or ""
            if not base_name:
                continue
            names.add(base_name)
            for alias in node.names:
                if alias.name != "*":
                    names.add(f"{base_name}.{alias.name}")
    return names


def list_test_files(root: Path) -> list[str]:
    found: list[str] = []
    for top in TEST_ROOTS:
        for path in (root / top).rglob("test_*.py"):
            rel = path.relative_to(root).as_posix()
            if is_test_file(rel):
                found.append(rel)
    return sorted(found)


def select_tests(changed: list[str], root: Path | str = ".") -> list[str]:
    """Test files affected by the changed paths (posix, relative to root)."""
    base = Path(root)
    selected: set[str] = set()
    changed_modules: set[str] = set()
    for path in changed:
        if is_test_file(path):
            if (base / path).is_file():
                selected.add(path)
        name = module_name(path)
        if name and not path.startswith(SMOKE_PREFIX):
            changed_modules.add(name)
    if changed_modules:
        for rel in list_test_files(base):
            if rel in selected:
                continue
            try:
                source = (base / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            own = module_name(rel) or rel
            if imported_names(source, own) & changed_modules:
                selected.add(rel)
    return sorted(selected)


def global_impact(changed: list[str]) -> list[str]:
    return sorted(path for path in changed if path in GLOBAL_IMPACT)


def browsers_needed(files: list[str], root: Path | str = ".") -> list[str]:
    """Browsers beyond chromium that the files name; chromium is always installed."""
    base = Path(root)
    needed: set[str] = set()
    for rel in files:
        try:
            text = (base / rel).read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        needed.update(browser for browser in BROWSERS if browser in text)
    return [browser for browser in BROWSERS if browser in needed]


def changed_files(base_ref: str, head: str = "HEAD") -> list[str]:
    """Files the pull request adds, copies, modifies or renames against the merge base."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMR", f"{base_ref}...{head}"],
        capture_output=True, text=True, check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", help="git ref of the merge target, for example origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--files", nargs="*", help="changed paths, instead of asking git")
    parser.add_argument("--browsers", nargs="*", help="print the extra browsers these test files need")
    args = parser.parse_args(argv)

    if args.browsers is not None:
        print(" ".join(browsers_needed(args.browsers)))
        return 0
    if args.files is not None:
        changed = args.files
    elif args.base:
        changed = changed_files(args.base, args.head)
    else:
        parser.error("give --base or --files")
    for path in global_impact(changed):
        print(f"notice: {path} changed; the fast lane cannot cover it, the full run will", file=sys.stderr)
    print("\n".join(select_tests(changed)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

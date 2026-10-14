#!/usr/bin/env python
"""Choose the pytest files the fast lane runs for a pull request.

The fast lane runs on ordinary pull-request pushes, with the full suite held
back until the pull request carries the `full-ci` label. It runs:

  * every test file the pull request adds or changes,
  * every test file that imports a module the pull request changes (a
    `from app.models import Foo` import also matches app/models/foo.py), and
  * for a change under app/modules/<m>/, that module's own tests and every test
    that imports anything from the module.

Browser journeys under tests/smoke need a live server and are left to the full
run. Printed one path per line, sorted. Nothing is printed when no test file is
affected, which is not an error.

The fast lane is deliberately narrow, so some changes must not use it at all:
a change to shared test or application wiring (see ESCALATING), and a change
touching many source files that selects only a few tests (see
WIDE_CHANGE_SOURCE_FILES and WIDE_CHANGE_MIN_TESTS). For those `--escalate`
prints the reason and the workflow runs the full suite instead.

    python -I scripts/ci/changed_test_selection.py --base origin/main
    python -I scripts/ci/changed_test_selection.py --base origin/main --escalate
    python -I scripts/ci/changed_test_selection.py --files app/foo.py tests/test_foo.py
    python -I scripts/ci/changed_test_selection.py --browsers tests/test_a.py ...
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

TEST_ROOTS = ("tests", "app/modules")
SMOKE_PREFIX = "tests/smoke/"

# A change to any of these can alter the result of any test, which the fast lane
# cannot cover: the pull request runs the full suite instead.
ESCALATING_FILES = (
    "tests/conftest.py",
    "app/modules/conftest.py",
    "conftest.py",
    "pytest.ini",
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
    "config.py",
    "app/config.py",
    "app/__init__.py",
    "manage.py",
    "tests/schema_baseline.json",
    "tests/shard_durations.json",
    ".coveragerc",
    "ruff.toml",
    "scripts/deploy_workflow.py",
    "scripts/route_verification_audit.py",
    "package.json",
    "package-lock.json",
    "tests/factories.py",
    ".github/dependabot.yml",
    "Makefile",
    "alembic.ini",
    "gunicorn.conf.py",
    "wsgi.py",
    "runtime.txt",
    "compose.yml",
    "compose.yaml",
)
ESCALATING_PREFIXES = (
    "migrations/",
    "app/_bootstrap/",
    "app/config/",
    "app/extensions/",
    "scripts/ci/",
    "tests/fixtures/",
    ".github/workflows/",
    ".github/actions/",
    "tests/helpers/",
    "requirements/",
)
ESCALATING_PATTERNS = (
    re.compile(r"^requirements[^/]*\.txt$"),
    re.compile(r"^tests/_[^/]*\.py$"),
    re.compile(r"(^|/)Dockerfile[^/]*$"),
    re.compile(r"(^|/)conftest\.py$"),
    re.compile(r"^docker-compose[^/]*\.ya?ml$"),
)

# Changes under these roots count as source changes whatever their file type.
SOURCE_ROOTS = ("app/", "migrations/", "scripts/")

# More source files than this changed and fewer test files than the minimum were
# selected: the import map is too thin to trust, so run the full suite.
WIDE_CHANGE_SOURCE_FILES = 10
WIDE_CHANGE_MIN_TESTS = 15

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


def snake_case(name: str) -> str:
    """WorkPackage -> work_package."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()


def module_package(path: str) -> str | None:
    """`app.modules.<m>` for a file under app/modules/<m>/, else None."""
    parts = path.split("/")
    if len(parts) > 3 and parts[0] == "app" and parts[1] == "modules":
        return ".".join(parts[:3])
    return None


def imported_names(source: str, module: str, is_package: bool = False) -> set[str]:
    """Every dotted name a module imports, including `from a import b` as a.b.

    A `from a import FooBar` also yields a.foobar and a.foo_bar, so a model
    class import reaches the lower-case module file that defines it. Relative
    imports are resolved against the importing module's own package.
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
                    names.add(f"{base_name}.{alias.name.lower()}")
                    names.add(f"{base_name}.{snake_case(alias.name)}")
    return names


def list_test_files(root: Path) -> list[str]:
    found: list[str] = []
    for top in TEST_ROOTS:
        for path in (root / top).rglob("test_*.py"):
            rel = path.relative_to(root).as_posix()
            if is_test_file(rel):
                found.append(rel)
    return sorted(found)


def _imports_into(names: set[str], packages: set[str]) -> bool:
    return any(n == p or n.startswith(p + ".") for n in names for p in packages)


def tests_importing(modules: set[str], base: Path) -> list[str]:
    """Test files that import any of the dotted module names."""
    found = []
    for rel in list_test_files(base):
        try:
            source = (base / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if imported_names(source, module_name(rel) or rel) & modules:
            found.append(rel)
    return found


def select_tests(changed: list[str], root: Path | str = ".") -> list[str]:
    """Test files affected by the changed paths (posix, relative to root)."""
    base = Path(root)
    selected: set[str] = set()
    changed_modules: set[str] = set()
    changed_packages: set[str] = set()
    for path in changed:
        if is_test_file(path):
            if (base / path).is_file():
                selected.add(path)
        name = module_name(path)
        if name and not path.startswith(SMOKE_PREFIX):
            changed_modules.add(name)
        package = module_package(path)
        if package and not is_test_file(path):
            changed_packages.add(package)
    tests = list_test_files(base) if (changed_modules or changed_packages) else []
    for rel in tests:
        if rel in selected:
            continue
        # A module's own tests run when anything in the module changed.
        if any(rel.startswith(p.replace(".", "/") + "/") for p in changed_packages):
            selected.add(rel)
            continue
        try:
            source = (base / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        names = imported_names(source, module_name(rel) or rel)
        if names & changed_modules or _imports_into(names, changed_packages):
            selected.add(rel)
    return sorted(selected)


def source_files(changed: list[str]) -> list[str]:
    """Changed non-test files under the application, migration and script roots,
    of any type: templates, static files, SQL and YAML count as well as Python."""
    return [
        p for p in changed
        if p.startswith(SOURCE_ROOTS) and "/tests/" not in p
        and not p.rsplit("/", 1)[-1].startswith("test_")
    ]


def untested_application_files(changed: list[str], root: Path | str = ".") -> list[str]:
    """Non-Python files under app/ that no test can be tied to, file by file.

    A module's non-Python files (templates, static files) select that module's
    tests through the module mapping; anywhere else under app/ there is no
    mapping. Each file is judged on its own mapping, never against the whole
    selection, so an unrelated selected test cannot hide a changed template.
    """
    return sorted(
        p for p in changed
        if p.startswith("app/") and not p.endswith(".py") and "/tests/" not in p
        and not select_tests([p], root)
    )


def clean(path: str) -> str:
    """A file name safe to print: control characters (newlines included) replaced."""
    return "".join("?" if ord(ch) < 32 or ord(ch) == 127 else ch for ch in path)


def escalating(changed: list[str]) -> list[str]:
    """Changed paths that make the fast lane unsafe, sorted."""
    hits = []
    for path in changed:
        if module_package(path) and path.endswith("/conftest.py") and not path.startswith("app/modules/conftest.py"):
            continue  # a module's own conftest is covered by the module mapping
        if (
            path in ESCALATING_FILES
            or path.startswith(ESCALATING_PREFIXES)
            or any(pattern.search(path) for pattern in ESCALATING_PATTERNS)
        ):
            hits.append(path)
    return sorted(hits)


def escalation_reason(changed: list[str], selected: list[str], root: Path | str = ".") -> str:
    """Why the full suite must run instead of the fast lane, or an empty string."""
    hits = escalating(changed)
    if hits:
        return "shared wiring changed: " + ", ".join(map(clean, hits[:5])) + (" ..." if len(hits) > 5 else "")
    helpers = [
        p for p in changed
        if p.startswith("scripts/") and p.endswith(".py") and module_name(p)
        and tests_importing({module_name(p)}, Path(root))
    ]
    if helpers:
        return "scripts that tests import changed: " + ", ".join(map(clean, sorted(helpers)[:5]))
    orphans = untested_application_files(changed, root)
    if orphans:
        return "application files changed that no test is tied to: " + ", ".join(map(clean, orphans[:5]))
    sources = source_files(changed)
    if len(sources) > WIDE_CHANGE_SOURCE_FILES and len(selected) < WIDE_CHANGE_MIN_TESTS:
        return (
            f"{len(sources)} source files changed but only {len(selected)} test files "
            f"matched (fewer than {WIDE_CHANGE_MIN_TESTS})"
        )
    return ""


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
    """Every path the pull request touches against the merge base, deleted files
    and both sides of a rename included (renames are reported as a delete plus an add)."""
    result = subprocess.run(
        ["git", "diff", "-z", "--name-only", "--no-renames", f"{base_ref}...{head}"],
        capture_output=True, text=True, check=True,
    )
    return [path for path in result.stdout.split("\0") if path]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", help="git ref of the merge target, for example origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--files", nargs="*", help="changed paths, instead of asking git")
    parser.add_argument("--browsers", nargs="*", help="print the extra browsers these test files need")
    parser.add_argument("--escalate", action="store_true",
                        help="print why the full suite must run instead, or nothing")
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
    selected = select_tests(changed)
    if args.escalate:
        print(escalation_reason(changed, selected))
        return 0
    if selected:
        print("\n".join(selected))
    return 0


if __name__ == "__main__":
    sys.exit(main())

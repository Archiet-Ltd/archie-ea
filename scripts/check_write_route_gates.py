#!/usr/bin/env python
"""Count write routes guarded only by require_roles or login_required.

The matrix decorator ``require_permission(action, object_type)`` from
``app.core.auth.decorators`` is the one home for permission checks. This
ratchet counts write routes (POST, PUT, PATCH, DELETE) that lack it and are
instead guarded only by ``require_roles`` or ``login_required`` (or nothing).
The baseline can only fall.

Usage:
    python scripts/check_write_route_gates.py            # list occurrences
    python scripts/check_write_route_gates.py --count    # count only
"""

from __future__ import annotations

import argparse
import ast
import glob
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Files/dirs to exclude from scanning
EXCLUDED_DIRS = ("__pycache__", ".git", "venv", ".venv", "migrations", "tests")
EXCLUDED_FILES = ("conftest.py",)


def is_excluded(path: Path) -> bool:
    parts = set(path.parts)
    return any(ex in parts for ex in EXCLUDED_DIRS) or path.name in EXCLUDED_FILES


def find_route_files() -> list[Path]:
    """Find all Python files under app/ that likely contain routes."""
    files = []
    for pattern in ("app/**/*.py",):
        for path_str in glob.glob(str(REPO_ROOT / pattern), recursive=True):
            path = Path(path_str)
            if not is_excluded(path):
                files.append(path)
    return sorted(files)


class RouteVisitor(ast.NodeVisitor):
    """AST visitor to find Flask route decorators and their guards."""

    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.routes: list[dict] = []
        self._current_decorators: list[ast.expr] = []

    def visit_FunctionDef(self, node: ast.FunctionDef):
        # Check if this function has a route decorator
        route_info = self._analyze_decorators(node.decorator_list, node.name)
        if route_info:
            self.routes.append(route_info)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        route_info = self._analyze_decorators(node.decorator_list, node.name)
        if route_info:
            self.routes.append(route_info)
        self.generic_visit(node)

    def _analyze_decorators(self, decorators: list[ast.expr], func_name: str) -> dict | None:
        """Analyze decorators on a function to find route and auth info."""
        route_decorator = None
        auth_decorators = []

        for dec in decorators:
            # Check for route decorator: @bp.route(...) or @app.route(...)
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                if dec.func.attr == "route":
                    route_decorator = dec
            # Check for auth decorators
            elif isinstance(dec, ast.Call):
                if isinstance(dec.func, ast.Name):
                    if dec.func.id in ("login_required", "require_roles", "require_auth", "require_permission"):
                        auth_decorators.append(dec.func.id)
                elif isinstance(dec.func, ast.Attribute):
                    if dec.func.attr in ("login_required", "require_roles", "require_auth", "require_permission"):
                        auth_decorators.append(dec.func.attr)
            elif isinstance(dec, ast.Name):
                if dec.id in ("login_required", "require_roles", "require_auth", "require_permission"):
                    auth_decorators.append(dec.id)
            elif isinstance(dec, ast.Attribute):
                if dec.attr in ("login_required", "require_roles", "require_auth", "require_permission"):
                    auth_decorators.append(dec.attr)

        if not route_decorator:
            return None

        # Extract HTTP methods from route decorator
        methods = self._extract_methods(route_decorator)
        if not methods:
            # Default to GET if no methods specified
            methods = {"GET"}

        # Only care about write methods
        write_methods = methods & WRITE_METHODS
        if not write_methods:
            return None

        # Check if matrix require_permission is present
        has_matrix_permission = any(
            d == "require_permission" for d in auth_decorators
        )

        # Check if only login_required or require_roles (no matrix permission)
        has_login_required = "login_required" in auth_decorators
        has_require_roles = "require_roles" in auth_decorators
        has_require_auth = "require_auth" in auth_decorators

        # A route is "bare" if it has no matrix permission and only has
        # login_required/require_roles/require_auth (or nothing)
        is_bare = not has_matrix_permission and (
            has_login_required or has_require_roles or has_require_auth or not auth_decorators
        )

        # Get the route path for reporting
        path = self._extract_path(route_decorator)

        return {
            "file": str(self.filepath.relative_to(REPO_ROOT)),
            "function": func_name,
            "path": path,
            "methods": sorted(write_methods),
            "auth_decorators": auth_decorators,
            "has_matrix_permission": has_matrix_permission,
            "is_bare": is_bare,
        }

    def _extract_methods(self, route_decorator: ast.Call) -> set[str]:
        """Extract HTTP methods from @bp.route(..., methods=[...])"""
        methods = set()
        for kw in route_decorator.keywords:
            if kw.arg == "methods" and isinstance(kw.value, ast.List):
                for elt in kw.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        methods.add(elt.value.upper())
        return methods

    def _extract_path(self, route_decorator: ast.Call) -> str:
        """Extract route path from @bp.route("/path", ...)"""
        if route_decorator.args and isinstance(route_decorator.args[0], ast.Constant):
            return route_decorator.args[0].value
        return "?"


def scan_file(filepath: Path) -> list[dict]:
    """Scan a single file for bare write routes."""
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    try:
        tree = ast.parse(content, filename=str(filepath))
    except SyntaxError:
        return []

    visitor = RouteVisitor(filepath)
    visitor.visit(tree)
    return visitor.routes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", action="store_true")
    args = parser.parse_args(argv)

    files = find_route_files()
    all_bare_routes = []

    for filepath in files:
        routes = scan_file(filepath)
        for route in routes:
            if route["is_bare"]:
                all_bare_routes.append(route)
                if not args.count:
                    methods = ",".join(route["methods"])
                    auth = ", ".join(route["auth_decorators"]) if route["auth_decorators"] else "none"
                    print(f"{route['file']}:{route['function']} {methods} {route['path']} — guards: {auth}")

    if args.count:
        print(len(all_bare_routes))
        return 0

    print(f"\n{len(all_bare_routes)} bare write route(s) found. Add @require_permission(action, object_type) "
          "or mark a deliberate exception.")
    return 1 if all_bare_routes else 0


if __name__ == "__main__":
    sys.exit(main())
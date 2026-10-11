#!/usr/bin/env python3
"""Interface contract gate — two checks.

Check (i) — Unlisted routes (kind ``zero``):
    Every token-accepting route under /api/v1 must have a matching operation
    in the committed contract subset with a compatible scope.

Check (ii) — Unbuilt operations (kind ``ratchet``):
    Subset operations with no matching token-accepting route are counted;
    the gate passes while the count is at most the baseline.

Scope matching rule:
    A route tagged ``api:read`` is satisfied by a contract operation whose
    required scopes are all read-class (``model:read``, ``graph:read``,
    ``graph:query``, ``intelligence:read`` and any scope ending in ``:read``).
    A route tagged ``api:write`` requires an operation with a non-read scope.

Usage:
    python scripts/check_interface_contract.py --subset <path> [--count]
    python scripts/check_interface_contract.py --subset <path> [--baseline N]
    python scripts/check_interface_contract.py --subset <path> --fixture-app
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Scopes that are considered read-class for the purpose of scope matching.
READ_CLASS_SCOPES = {
    "model:read",
    "graph:read",
    "graph:query",
    "intelligence:read",
}


def _is_read_scope(scope: str) -> bool:
    """Return True if *scope* is a read-class scope."""
    if scope in READ_CLASS_SCOPES:
        return True
    if scope.endswith(":read"):
        return True
    return False


def _operation_scopes(operation: dict) -> list[str]:
    """Extract the list of required OAuth scopes from a contract operation."""
    scopes: list[str] = []
    for sec in operation.get("security", []):
        for _provider, scope_list in sec.items():
            scopes.extend(scope_list)
    return scopes


def _operation_has_read_only_scopes(operation: dict) -> bool:
    """Return True if all required scopes on the operation are read-class."""
    scopes = _operation_scopes(operation)
    if not scopes:
        # No scopes means no restriction — treat as read-class
        return True
    return all(_is_read_scope(s) for s in scopes)


def _load_subset(path: Path) -> dict:
    """Load and return the contract subset from *path*."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _get_token_accepting_routes(app) -> list[tuple[str, str, str]]:
    """Return list of (path, method, scope) for every token-accepting route
    under /api/v1.

    Uses extract_route_info from app.utils.openapi_generator and the
    _api_token_scope attribute set by require_api_scope.
    """
    from app.utils.openapi_generator import extract_route_info

    routes: list[tuple[str, str, str]] = []
    for info in extract_route_info(app):
        path = info["path"]
        if not path.startswith("/api/v1"):
            continue
        view_func = info["view_func"]
        scope = getattr(view_func, "_api_token_scope", None)
        if scope is None:
            continue
        for method in info["methods"]:
            routes.append((path, method.lower(), scope))
    return routes


def _normalize_path(path: str) -> str:
    """Normalize a path for comparison by stripping the /api/v1 prefix and
    replacing path parameters with a generic placeholder.

    /api/v1/elements/{element_id} -> /elements/{}
    /elements/{id} -> /elements/{}
    """
    import re
    # Strip /api/v1 prefix if present
    if path.startswith("/api/v1"):
        path = path[len("/api/v1"):]
    return re.sub(r"\{[^}]+\}", "{}", path)


def _build_route_index(subset: dict) -> dict[tuple[str, str], dict]:
    """Build a dict of (normalized_path, method) -> operation from the subset paths."""
    index: dict[tuple[str, str], dict] = {}
    for path_key, path_item in subset.get("paths", {}).items():
        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if operation is not None:
                index[(_normalize_path(path_key), method)] = operation
    return index


def check_unlisted_routes(
    app, subset: dict
) -> tuple[bool, list[str]]:
    """Check (i): every token-accepting route has a matching subset operation.

    Returns (passes, violations) where violations is a list of human-readable
    strings describing each failure.
    """
    violations: list[str] = []
    route_index = _build_route_index(subset)

    for path, method, route_scope in _get_token_accepting_routes(app):
        norm_path = _normalize_path(path)
        operation = route_index.get((norm_path, method))
        if operation is None:
            violations.append(
                f"Route {method.upper()} {path} (scope={route_scope}) "
                f"has no matching operation in the contract subset"
            )
            continue

        # Check scope compatibility
        route_is_write = route_scope == "api:write"
        op_is_read_only = _operation_has_read_only_scopes(operation)

        if route_is_write and op_is_read_only:
            op_scopes = _operation_scopes(operation)
            violations.append(
                f"Route {method.upper()} {path} (scope={route_scope}) "
                f"requires a non-read operation but the matching contract "
                f"operation has only read-class scopes: {op_scopes}"
            )
        elif not route_is_write and not op_is_read_only:
            op_scopes = _operation_scopes(operation)
            violations.append(
                f"Route {method.upper()} {path} (scope={route_scope}) "
                f"is a read-scoped route but the matching contract "
                f"operation requires non-read scopes: {op_scopes}"
            )

    return len(violations) == 0, violations


def count_unbuilt_operations(
    app, subset: dict
) -> int:
    """Check (ii): count subset operations with no matching token-accepting route.

    Returns the count of unbuilt operations.
    """
    route_index = _build_route_index(subset)
    token_routes = set()

    for path, method, _scope in _get_token_accepting_routes(app):
        token_routes.add((_normalize_path(path), method))

    unbuilt = 0
    for path_key, path_item in subset.get("paths", {}).items():
        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if operation is not None:
                if (_normalize_path(path_key), method) not in token_routes:
                    unbuilt += 1

    return unbuilt


def _build_fixture_app() -> Any:
    """Build a minimal Flask app with a token-accepting route for testing.

    This is used by the --fixture-app flag for testing the gate logic
    without needing the real application.
    """
    from flask import Flask

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"

    # Register a minimal token-accepting route
    from functools import wraps

    def _make_require_api_scope(scope: str):
        def decorator(view):
            @wraps(view)
            def wrapped(*args, **kwargs):
                return view(*args, **kwargs)
            wrapped._api_token_scope = scope
            return wrapped
        return decorator

    @app.route("/api/v1/elements", methods=["GET"])
    @_make_require_api_scope("api:read")
    def list_elements():
        return {"elements": []}

    @app.route("/api/v1/elements/<int:element_id>", methods=["GET"])
    @_make_require_api_scope("api:read")
    def get_element(element_id):
        return {"id": element_id}

    return app


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Interface contract gate — check routes against the contract subset"
    )
    parser.add_argument(
        "--subset", required=True,
        help="Path to the committed contract subset YAML",
    )
    parser.add_argument(
        "--count", action="store_true",
        help="Print the unbuilt-operations count and exit",
    )
    parser.add_argument(
        "--baseline", type=int, default=None,
        help="Baseline for the unbuilt-operations ratchet",
    )
    parser.add_argument(
        "--fixture-app", action="store_true",
        help="Use a fixture app instead of the real application (for testing)",
    )
    parser.add_argument(
        "--fixture-route-scope", default=None,
        help="Override the fixture route scope (for testing scope matching)",
    )
    args = parser.parse_args()

    subset_path = Path(args.subset)
    if not subset_path.exists():
        print(f"Error: subset file not found: {subset_path}", file=sys.stderr)
        return 1

    subset = _load_subset(subset_path)

    if args.fixture_app:
        app = _build_fixture_app()
        if args.fixture_route_scope:
            # Rebuild fixture app with custom scope
            from flask import Flask
            from functools import wraps

            app = Flask(__name__)
            app.config["TESTING"] = True
            app.config["SECRET_KEY"] = "test-secret-key"

            scope = args.fixture_route_scope

            def _make_require_api_scope(s: str):
                def decorator(view):
                    @wraps(view)
                    def wrapped(*args, **kwargs):
                        return view(*args, **kwargs)
                    wrapped._api_token_scope = s
                    return wrapped
                return decorator

            @app.route("/api/v1/elements", methods=["GET"])
            @_make_require_api_scope(scope)
            def list_elements():
                return {"elements": []}

            @app.route("/api/v1/elements/<int:element_id>", methods=["GET"])
            @_make_require_api_scope(scope)
            def get_element(element_id):
                return {"id": element_id}
    else:
        # Import the real application
        os.environ.setdefault("FLASK_CONFIG", "testing")
        from app import create_app
        app = create_app("testing")

    if args.count:
        # Print the unbuilt-operations count
        count = count_unbuilt_operations(app, subset)
        print(count)
        return 0

    # Check (i): unlisted routes
    passes, violations = check_unlisted_routes(app, subset)
    if not passes:
        print("FAIL: interface-contract-unlisted")
        for v in violations:
            print(f"  {v}")
        print(f"\n{len(violations)} violation(s) found")

    # Check (ii): unbuilt operations
    unbuilt = count_unbuilt_operations(app, subset)
    baseline = args.baseline if args.baseline is not None else 0
    unbuilt_ok = unbuilt <= baseline

    if not unbuilt_ok:
        print(f"FAIL: interface-contract-unimplemented")
        print(f"  {unbuilt} unbuilt operation(s) > baseline {baseline}")

    if passes and unbuilt_ok:
        print("PASS: interface-contract")
        if unbuilt > 0:
            print(f"  {unbuilt} unbuilt operation(s) within baseline {baseline}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
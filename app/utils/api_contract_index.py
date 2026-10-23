"""One way to match a route to an operation of the API contract.

The contract subset (``app/config/api_contract_subset.yaml``) lists operations by
path (``/elements/{id}``); the running app knows routes by rule
(``/api/v1/elements/<int:element_id>``) and requests by concrete path. Both the
CI gate (``scripts/check_interface_contract.py``) and the runtime
(``app.modules.oauth_provider.scopes``) normalise to the same shape and use the
same index, so a route the gate accepts is a route the runtime resolves.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

API_PREFIX = "/api/v1"
HTTP_METHODS = ("get", "post", "put", "patch", "delete")
CONTRACT_SUBSET = Path(__file__).resolve().parents[1] / "config" / "api_contract_subset.yaml"


def under_api_v1(path: str) -> bool:
    """True for /api/v1 and anything below it, but not /api/v10."""
    return path == API_PREFIX or path.startswith(API_PREFIX + "/")


def normalize_path(path: str) -> str:
    """Strip the /api/v1 prefix and replace every path parameter with ``{}``.

    Handles the contract form (``{id}``) and the Flask rule form
    (``<int:element_id>``):

    /api/v1/elements/{element_id} -> /elements/{}
    /api/v1/elements/<int:id>     -> /elements/{}
    """
    if under_api_v1(path):
        path = path[len(API_PREFIX):] or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return re.sub(r"\{[^}]+\}|<[^>]+>", "{}", path)


def load_subset(path: Path = CONTRACT_SUBSET) -> dict:
    """Load and return the contract subset from *path*."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_route_index(subset: dict) -> dict[tuple[str, str], dict]:
    """(normalised path, method) -> operation, for every operation in the subset."""
    index: dict[tuple[str, str], dict] = {}
    for path_key, path_item in (subset or {}).get("paths", {}).items():
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if operation is not None:
                index[(normalize_path(path_key), method)] = operation
    return index


def build_operation_paths(subset: dict) -> dict[str, str]:
    """operationId -> its contract path under /api/v1 (parameters left as ``{name}``)."""
    paths: dict[str, str] = {}
    for path_key, path_item in (subset or {}).get("paths", {}).items():
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if operation is not None and operation.get("operationId"):
                paths[operation["operationId"]] = API_PREFIX + path_key
    return paths

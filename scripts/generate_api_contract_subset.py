#!/usr/bin/env python3
"""Generate a subset of the published API contract for a published group.

Usage:
    python scripts/generate_api_contract_subset.py \\
        --source /path/to/api-model-v1.yaml \\
        --output app/config/api_contract_subset.yaml

Reads the source contract, keeps the operations whose first path segment is
in PUBLISHED_GROUPS, copies every component those operations reference
(following $ref transitively), and writes the subset with a header comment
holding the source file name and sha256.

Deterministic output: stable ordering so a regeneration of unchanged input
produces an identical file.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any

import yaml


# The groups whose operations are published in this subset.
# Each entry is the first path segment (without leading /).
PUBLISHED_GROUPS = ["elements"]


def _to_plain(obj: Any) -> Any:
    """Recursively convert OrderedDict to plain dict for clean YAML output."""
    if isinstance(obj, OrderedDict):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_plain(item) for item in obj]
    return obj


def _ordered_dump(data: Any, default_flow_style: bool = False) -> str:
    """Dump *data* to YAML with stable key ordering."""
    return yaml.dump(
        _to_plain(data),
        default_flow_style=default_flow_style,
        allow_unicode=True,
        sort_keys=False,
    )


def _sha256(path: Path) -> str:
    """Return the hex sha256 digest of *path*."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _first_path_segment(path: str) -> str:
    """Return the first segment of an OpenAPI path, e.g. '/elements/{id}' -> 'elements'."""
    return path.strip("/").split("/")[0] if path else ""


def _collect_refs(obj: Any, refs: set) -> None:
    """Recursively collect every $ref string from *obj* into *refs*."""
    if isinstance(obj, dict):
        if "$ref" in obj:
            refs.add(obj["$ref"])
        for v in obj.values():
            _collect_refs(v, refs)
    elif isinstance(obj, list):
        for item in obj:
            _collect_refs(item, refs)


def _resolve_ref(ref: str, components: dict) -> Any | None:
    """Resolve a $ref like '#/components/schemas/Foo' against *components*."""
    if not ref.startswith("#/"):
        return None
    parts = ref[2:].split("/")
    current = components
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _gather_referenced_components(
    operations: list[tuple[str, str, dict]],
    source_components: dict,
    source_paths: dict,
    subset_path_keys: list[str],
) -> dict:
    """Given a list of (path, method, operation) tuples, return a dict of
    components (schemas, parameters, responses, headers, etc.) that are
    referenced by those operations, following $ref transitively.

    Returns a dict with the same top-level keys as source_components
    (schemas, responses, parameters, etc.), each being an OrderedDict of
    name -> resolved component.
    """
    # Collect all $refs from the operations and path-level items
    all_refs: set[str] = set()
    for _path, _method, operation in operations:
        _collect_refs(operation, all_refs)

    # Also collect refs from path-level items (parameters, etc.)
    for path_key in subset_path_keys:
        path_item = source_paths.get(path_key, {})
        _collect_refs(path_item, all_refs)

    # Resolve refs and gather referenced component names
    result: dict[str, OrderedDict] = {}
    # Track which component names we've already included to avoid infinite loops
    included: dict[str, set[str]] = {}

    # Process refs iteratively until no new ones are found
    pending = set(all_refs)
    while pending:
        ref = pending.pop()
        if not ref.startswith("#/components/"):
            continue
        parts = ref[2:].split("/")
        if len(parts) < 3:
            continue
        category = parts[1]  # e.g. "schemas", "responses", "parameters"
        name = "/".join(parts[2:])  # e.g. "ElementDetail"

        if category not in included:
            included[category] = set()
        if name in included[category]:
            continue
        included[category].add(name)

        if category not in result:
            result[category] = OrderedDict()
        if category not in source_components:
            continue

        component = source_components[category].get(name)
        if component is None:
            continue

        result[category][name] = component

        # Collect refs from this component too
        new_refs: set[str] = set()
        _collect_refs(component, new_refs)
        for nr in new_refs:
            if nr not in all_refs:
                all_refs.add(nr)
                pending.add(nr)

    return result


def generate_subset(source_path: Path) -> dict:
    """Read the source contract and return the subset dict."""
    with open(source_path, encoding="utf-8") as f:
        source = yaml.safe_load(f)

    if not isinstance(source, dict):
        raise ValueError("Source contract is not a valid YAML mapping")

    source_components = source.get("components", {}) or {}
    source_paths = source.get("paths", {}) or {}

    # Filter paths by published groups
    subset_paths: dict[str, dict] = OrderedDict()
    operations: list[tuple[str, str, dict]] = []

    for path_key in sorted(source_paths.keys()):
        group = _first_path_segment(path_key)
        if group in PUBLISHED_GROUPS:
            path_item = source_paths[path_key]
            subset_paths[path_key] = OrderedDict()

            # Copy path-level items (parameters, summary, description)
            for key in ("parameters", "summary", "description"):
                if key in path_item:
                    subset_paths[path_key][key] = path_item[key]

            for method in ("get", "post", "put", "patch", "delete", "head", "options"):
                if method in path_item:
                    operation = path_item[method]
                    subset_paths[path_key][method] = operation
                    operations.append((path_key, method, operation))

    # Gather referenced components
    subset_components = _gather_referenced_components(
        operations, source_components, source_paths, list(subset_paths.keys())
    )

    # Build the subset
    subset: dict[str, Any] = OrderedDict()
    subset["openapi"] = source.get("openapi", "3.1.0")
    subset["info"] = source.get("info", {"title": "API Contract Subset", "version": "1.0.0"})

    # Copy servers and security if present
    if "servers" in source:
        subset["servers"] = source["servers"]
    if "security" in source:
        subset["security"] = source["security"]

    # Collect unique tags from the operations
    tags_set: set[str] = set()
    for _path, _method, operation in operations:
        for tag in operation.get("tags", []):
            tags_set.add(tag)
    if tags_set:
        subset["tags"] = [{"name": t, "description": f"{t} operations"} for t in sorted(tags_set)]

    subset["paths"] = subset_paths

    if subset_components:
        subset["components"] = OrderedDict()
        for category in sorted(subset_components.keys()):
            subset["components"][category] = subset_components[category]

    return subset


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a subset of the published API contract"
    )
    parser.add_argument(
        "--source", required=True,
        help="Path to the source contract YAML file",
    )
    parser.add_argument(
        "--output", "-o", required=True,
        help="Output file path for the generated subset",
    )
    args = parser.parse_args()

    source_path = Path(args.source)
    if not source_path.exists():
        print(f"Error: source file not found: {source_path}", file=sys.stderr)
        sys.exit(1)

    sha = _sha256(source_path)
    subset = generate_subset(source_path)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write with header comment
    header = (
        f"# API Contract Subset — auto-generated\n"
        f"# Source: {source_path.name}\n"
        f"# SHA256: {sha}\n"
        f"# Do not edit by hand.\n"
    )

    body = _ordered_dump(subset)
    output_path.write_text(header + body, encoding="utf-8")

    print(f"Wrote subset to {output_path} ({len(subset.get('paths', {}))} paths)", file=sys.stderr)


if __name__ == "__main__":
    main()
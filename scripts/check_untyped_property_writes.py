#!/usr/bin/env python
"""Count direct writes to ArchiMate element property stores outside the writer.

PR 1 introduces one writer for typed ArchiMate element properties:
``PropertyService.set_element_property`` / ``merge_element_properties`` for
``acm_properties`` and its legacy mirror on ``properties``. Direct writes to an
element's ``properties``/``acm_properties`` fields outside import paths bypass
type coercion, unit preservation, source tracking and refusal on invalid input.

This checker freezes the remaining bypass count so it can only fall.

Usage:
    python scripts/check_untyped_property_writes.py
    python scripts/check_untyped_property_writes.py --count
    python scripts/check_untyped_property_writes.py --root /path/to/tree

Proven-against: a scratch file containing ``element.properties = json.dumps({})``
outside the allowlist reports one finding; switching the same write to
``PropertyService().set_element_property(...)`` returns zero.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOW_MARKER = "untyped-property-write-ok:"
ASSIGN_RE = re.compile(r"\.((?:acm_)?properties)\s*=")

ALLOWED_SUFFIXES = {
    os.path.join("app", "modules", "architecture_assistant", "property_service.py"),
    os.path.join("app", "services", "archimate_import_service.py"),
    os.path.join("app", "services", "archimate_oef_service.py"),
    os.path.join("app", "services", "lucid_import_service.py"),
    os.path.join("app", "modules", "architecture", "services", "application_layer_service.py"),
    os.path.join("app", "modules", "architecture", "services", "business_layer_service.py"),
    os.path.join("app", "modules", "architecture", "services", "driver_service.py"),
    os.path.join("app", "modules", "architecture", "services", "document_processor.py"),
    os.path.join("app", "modules", "architecture", "services", "goal_service.py"),
    os.path.join("app", "modules", "architecture", "services", "implementation_migration_service.py"),
    os.path.join("app", "modules", "architecture", "services", "outcome_service.py"),
    os.path.join("app", "modules", "architecture", "services", "principle_service.py"),
    os.path.join("app", "modules", "architecture", "services", "stakeholder_service.py"),
    os.path.join("app", "modules", "architecture", "services", "tabular_data_extractor.py"),
    os.path.join("app", "modules", "architecture", "services", "technology_layer_service.py"),
    os.path.join("app", "modules", "architecture", "services", "unified_archimate_layer_services.py"),
    os.path.join("app", "modules", "architecture", "services", "application_inference_service.py"),
    # Non-ArchiMateElement writes (proposals, work packages, views) — not in scope for this checker
    os.path.join("app", "modules", "architecture_assistant", "acm_domain_service.py"),
    os.path.join("app", "modules", "architecture_assistant", "journey_orchestrator.py"),
    os.path.join("app", "modules", "solutions_strategic", "v2", "services", "solution_composer_service.py"),
    os.path.join("app", "modules", "solutions_strategic", "v2", "routes", "journey_v2_routes.py"),
    os.path.join("app", "implementation_planning", "routes.py"),
}


def _iter_py_files(root: str):
    for dirpath, _, filenames in os.walk(os.path.join(root, "app")):
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _allowed(path: str, root: str) -> bool:
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    return any(rel.endswith(suffix.replace(os.sep, "/")) for suffix in ALLOWED_SUFFIXES)


def scan(root: str) -> list[str]:
    problems = []
    for path in _iter_py_files(root):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        if _allowed(path, root):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, 1):
                    if ALLOW_MARKER in line:
                        continue
                    if ASSIGN_RE.search(line):
                        problems.append(
                            f"{rel}:{lineno}: direct ArchiMate element property write — use PropertyService.set_element_property()/merge_element_properties()"
                        )
        except OSError:
            continue
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", action="store_true")
    parser.add_argument("--root", default=ROOT)
    args = parser.parse_args()

    problems = scan(os.path.abspath(args.root))
    if not args.count:
        for line in problems:
            print("  " + line)
        if problems:
            print()
            print("Route ArchiMate element property writes through PropertyService, or mark a deliberate exception with 'untyped-property-write-ok: <reason>'.")
    print(len(problems))
    return 0


if __name__ == "__main__":
    sys.exit(main())

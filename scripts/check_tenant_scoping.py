#!/usr/bin/env python
"""Find ORM queries over a tenant-owned-but-unmixed model with no org predicate.

    python scripts/check_tenant_scoping.py            # report
    python scripts/check_tenant_scoping.py --count    # count only
    python scripts/check_tenant_scoping.py --json

Why
---
`do_orm_execute` (app/middleware/tenant_isolation.py) injects a
`WHERE organization_id = g.current_org_id` predicate automatically, but only for
models that inherit `TenantMixin`. Several models carry an `organization_id`
column — added for exactly the reason TenantMixin exists, tenant scoping — but
were never given the mixin, so they get NONE of that automatic filtering. A
query against one of them reads every organisation's rows unless the caller
remembers to filter by hand, and the historical record is that callers forget:

  - `User.query.count()` fed a dashboard step with every tenant's user count.
  - `ApplicationCapabilityMapping` counts (capability coverage) were global,
    while the denominator they were divided into was one tenant's application
    total — a percentage that could exceed 100%.
  - a `@cached()` route cached its response under a key with no org component,
    so the second organisation to hit the endpoint got the first org's answer
    back from Redis.

This is the ORM-side twin of `check_raw_sql_tenancy.py`, which covers the raw
`text()` case; TenantMixin already makes true raw SQL AND ordinary ORM access
on *mixed-in* models safe, so this gate exists only for the gap: ORM access on
models that are tenant-owned in shape (have the column) but not in mechanism
(no mixin).

What it does and does not catch
--------------------------------
It flags `<Model>.query`, `db.session.query(<Model>, ...)` and
`func.count(<Model>...)` shapes, for models discovered by parsing app/models/**
for classes with an `organization_id` column and no `TenantMixin` base, in
request-path directories, where no `organization_id` / `current_org_id`
reference appears in a window of source around the call. It also flags
`@cached(...)` on a route with no `key_func=` keyword at all — an org-blind
cache key leaks the first tenant's answer to the next one.

It does not understand control flow, so a filter genuinely applied several
functions away (passed a pre-filtered queryset, scoped by a decorator, etc.)
can read as unscoped. It also cannot tell a real predicate from a matching
identifier that happens to be nearby but unused. As with raw_sql_tenancy, a
clean run does not prove tenancy; it proves the one mechanically detectable
failure — no predicate-shaped text anywhere nearby — is absent.

A model chosen at runtime (`REGISTRY[key].query`, `session.get(cls, id)` with
`cls = _import_model(...)`, `getattr(mod, name).query`) has no name to match, so it is reported
as a `dynamic-dispatch` finding unless the statement carries `tenant-scoping-ok: <reason>` or
the site is listed as `file:line` in scripts/dynamic_dispatch_sites.txt. Detection lives in
scripts/dynamic_dispatch.py, shared with check_untenanted_reads.py.

Exemptions
----------
Append `tenant-scoping-ok: <reason>` on the line to record a deliberate
exception — CLI/scheduler paths with no request context, and aggregates that
are genuinely meant to be global, are the expected users of it.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dynamic_dispatch  # noqa: E402  (shared with check_untenanted_reads.py)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MODELS_DIR = os.path.join(REPO_ROOT, "app", "models")

# Wave-4 Task-2/3 review: these 3 models carry organization_id (added in Phase
# A for schema symmetry with their 11 per-tenant siblings) but are shared
# catalogs/templates, not per-tenant data — see
# app/commands/backfill_arb_ea_tenancy.py's GLOBAL_REFERENCE (table-name form
# of the same intent) and tests/test_tenant_isolation_matrix.py's
# INTENTIONALLY_GLOBAL entries for them. A bare `.query` on one of these has
# no tenant filter to miss by design, so they must never be treated as leaky.
GLOBAL_REFERENCE_MODELS = frozenset({
    "ARBGovernanceStandard",
    "ARBWorkflowStage",
    "EAWorkflowDefinition",
})

SCAN_DIRS = [
    os.path.join(REPO_ROOT, "app", "modules"),
    os.path.join(REPO_ROOT, "app", "api"),
    os.path.join(REPO_ROOT, "app", "main"),
    os.path.join(REPO_ROOT, "app", "routes"),
    os.path.join(REPO_ROOT, "app", "services"),
    os.path.join(REPO_ROOT, "app", "application_mgmt"),
    os.path.join(REPO_ROOT, "app", "import"),
]

# `organization_id` / `current_org_id` as a WHOLE identifier — see
# check_raw_sql_tenancy.py's note on why `vendor_organization_id` must not match.
_ORG_REF = re.compile(
    r"(?<![A-Za-z0-9_])(organization_id|current_org_id)(?![A-Za-z0-9_])"
)

WINDOW_BEFORE = 4
WINDOW_AFTER = 16


def leaky_models() -> set[str]:
    """Model class names with an `organization_id` column but no TenantMixin base.

    Static AST parse of app/models/** — no DB or app-boot required, unlike
    check_raw_sql_tenancy.py's ORM introspection, so this stays usable in a
    plain `--tag static` run.
    """
    found: set[str] = set()
    for dirpath, dirs, files in os.walk(MODELS_DIR):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                source = io.open(path, encoding="utf-8", errors="ignore").read()
                tree = ast.parse(source)
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                base_names = set()
                for b in node.bases:
                    if isinstance(b, ast.Name):
                        base_names.add(b.id)
                    elif isinstance(b, ast.Attribute):
                        base_names.add(b.attr)
                if "TenantMixin" in base_names:
                    continue
                has_org_col = False
                for stmt in node.body:
                    if isinstance(stmt, ast.Assign):
                        if any(
                            isinstance(t, ast.Name) and t.id == "organization_id"
                            for t in stmt.targets
                        ):
                            has_org_col = True
                    elif isinstance(stmt, ast.AnnAssign):
                        if isinstance(stmt.target, ast.Name) and stmt.target.id == "organization_id":
                            has_org_col = True
                if has_org_col:
                    found.add(node.name)
    return found - GLOBAL_REFERENCE_MODELS


def _name_of(node: ast.AST) -> str | None:
    """The bare name for `Model`, `module.Model`, etc. — last component only."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _window_evidence(lines: list[str], lineno: int) -> tuple[bool, bool]:
    """(has_org_predicate, has_hatch) for the window around a 1-based lineno."""
    lo = max(0, lineno - 1 - WINDOW_BEFORE)
    hi = min(len(lines), lineno + WINDOW_AFTER)
    window = lines[lo:hi]
    raw = "\n".join(window)
    stripped = "\n".join(re.sub(r"#.*$", "", ln) for ln in window)
    return bool(_ORG_REF.search(stripped)), ("tenant-scoping-ok" in raw)


def scan_file(path: str, models: set[str]) -> list[dict]:
    try:
        source = io.open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = source.splitlines()
    findings: list[dict] = []
    seen_linenos: set[tuple[int, str]] = set()

    def flag(lineno: int, model: str, reason: str) -> None:
        key = (lineno, model)
        if key in seen_linenos:
            return
        has_org, hatched = _window_evidence(lines, lineno)
        if has_org or hatched:
            return
        seen_linenos.add(key)
        findings.append({
            "file": os.path.relpath(path, REPO_ROOT).replace("\\", "/"),
            "line": lineno,
            "model": model,
            "reason": reason,
        })

    for node in ast.walk(tree):
        # <Model>.query[...]
        if isinstance(node, ast.Attribute) and node.attr == "query":
            model = _name_of(node.value)
            if model in models:
                flag(node.lineno, model, f"{model}.query with no organization_id filter nearby")
            continue

        if not isinstance(node, ast.Call):
            continue
        fn = node.func

        # db.session.query(Model, ...)
        if (
            isinstance(fn, ast.Attribute)
            and fn.attr == "query"
            and isinstance(fn.value, ast.Attribute)
            and fn.value.attr == "session"
        ):
            for arg in node.args:
                model = _name_of(arg)
                if model in models:
                    flag(node.lineno, model,
                         f"db.session.query({model}, ...) with no organization_id filter nearby")
            continue

        # func.count(Model.something) / func.count(Model.id)
        fn_name = getattr(fn, "attr", None) or getattr(fn, "id", None)
        if fn_name == "count" and node.args:
            arg = node.args[0]
            target = arg.value if isinstance(arg, ast.Attribute) else arg
            model = _name_of(target)
            if model in models:
                flag(node.lineno, model,
                     f"func.count({model}...) with no organization_id filter nearby")
            continue

        # @cached(...) with no key_func= keyword — org-blind cache key.
        if fn_name == "cached":
            has_key_func = any(kw.arg == "key_func" for kw in node.keywords)
            if not has_key_func:
                lineno = node.lineno
                _, hatched = _window_evidence(lines, lineno)
                if not hatched:
                    findings.append({
                        "file": os.path.relpath(path, REPO_ROOT).replace("\\", "/"),
                        "line": lineno,
                        "model": "@cached()",
                        "reason": "@cached(...) with no key_func= — cache key is org-blind",
                    })

    rel = os.path.relpath(path, REPO_ROOT).replace("\\", "/")
    for site in dynamic_dispatch.unlisted(rel, dynamic_dispatch.find_dynamic_sites(tree, lines)):
        findings.append({
            "file": rel,
            "line": site["line"],
            "model": "<unresolved>",
            "reason": f"dynamic-dispatch: {site['shape']} on a model chosen at runtime; "
                      "confirm it is fenced, then list it in scripts/dynamic_dispatch_sites.txt",
        })

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--count", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    models = leaky_models()

    results: list[dict] = []
    for scan_dir in SCAN_DIRS:
        if not os.path.isdir(scan_dir):
            continue
        for root, dirs, files in os.walk(scan_dir):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(root, fn)
                results.extend(scan_file(path, models))

    results.sort(key=lambda r: (r["file"], r["line"]))

    if args.count:
        print(len(results))
        return 0
    if args.json:
        print(json.dumps({"leaky_models": sorted(models), "findings": results}, indent=2))
        return 1 if results else 0

    for r in results:
        print(f"  {r['file']}:{r['line']}  [{r['model']}]  {r['reason']}")
    if results:
        print(f"\n{len(results)} unscoped query/cache statement(s) across "
              f"{len(models)} tenant-owned-but-unmixed model(s).")
        print("Scope the query, or append 'tenant-scoping-ok: <reason>' if it is "
              "deliberately global.")
        return 1
    print(f"No unscoped ORM tenant-scoping issues found ({len(models)} leaky models tracked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

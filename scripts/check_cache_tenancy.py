#!/usr/bin/env python
"""A module-level cache over tenant data must be keyed by the tenant.

Two of these were found on 30 Aug 2026, and both were cross-tenant data leaks
rather than caching quirks. In each case the underlying query was correctly
tenant-scoped and the cache in front of it threw that scoping away.

    capability_health_service._health_metrics_cache
        A single module-level tuple shared by the whole process, holding
        health_by_capability -- every capability's name, id, domain and score.
        For 60 seconds after ANY tenant loaded the capability health dashboard,
        EVERY other tenant was served that tenant's capability names and scores.

    multi_domain_chat_service._RAG_CONTEXT_CACHE
        Keyed by business domain alone, holding architecture principles, PRIOR
        ARB DECISION TITLES and reference architectures -- and injected into the
        AI system prompt, so one tenant's governance history reached another
        tenant's assistant to answer from and cite.

Neither required any action by the receiving user. Nothing detected either: the
`tenant-scoping` and `raw-sql-tenancy` gates read QUERIES, and these are
dictionaries. `do_orm_execute` cannot help, because a cache hit emits no SQL for
it to filter -- the same blind spot CLAUDE.md records for `Query.get()` on an
identity-map hit.

The rule: a module-level mutable mapping in a module that also talks about
tenants must be written with a key that includes the organisation. A dict keyed
by anything else -- a domain, a name, nothing at all -- is shared by every
tenant in the process.

Detection: find module-level dict assignments whose name looks like a cache, in
a module that references current_org_id or organization_id, and check every
write to that name (``NAME[...] = ...``) for an organisation term in the
subscript. A cache written only via a variable is checked for that variable
being derived from the tenant in the same function.

Escape hatch: `cache-tenancy-ok: <reason>` on the declaration, for a cache whose
contents are genuinely global (a framework registry, an enum lookup, a parsed
config). Say WHY the contents are tenant-independent -- "it's only small" is not
a reason, and neither leak was large.

    python scripts/check_cache_tenancy.py
    python scripts/check_cache_tenancy.py --count

Proven-against: `_health_metrics_cache[tenant]` changed back to a shared
constant key -- red naming that module, green when the tenant key was restored.
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOW = re.compile(r"cache-tenancy-ok:[ \t]*\S")

CACHE_NAME = re.compile(r"cache|_memo|_store|_registry_by", re.I)
# A term that means "this key includes the tenant".
TENANT_TERM = re.compile(r"org|tenant", re.I)
# The module deals with tenant-owned data at all. Reading through the ORM counts: a
# module such as the architecture assistant routes never names an organisation yet
# queries tenant tables, and its f-string-keyed cache was skipped for that reason.
TENANT_MODULE = re.compile(r"current_org_id|organization_id|TenantMixin|.queryb|db.session")


def _module_level_caches(tree: ast.Module) -> dict:
    """{name: lineno} for module-level dict literals whose name reads as a cache."""
    found = {}
    for node in tree.body:
        targets = []
        value = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if not isinstance(value, ast.Dict):
            continue
        for target in targets:
            if isinstance(target, ast.Name) and CACHE_NAME.search(target.id):
                found[target.id] = node.lineno
    return found


def _writes_to(tree: ast.Module, source: str, name: str) -> list:
    """Every ``name[<key>] = ...`` in the module, as (lineno, key source)."""
    writes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Subscript):
                continue
            base = target.value
            if isinstance(base, ast.Name) and base.id == name:
                key_src = ast.get_source_segment(source, target.slice) or ""
                writes.append((node.lineno, key_src))
    return writes


def scan(root: str) -> list[str]:
    problems = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(root, "app")):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            path = os.path.join(dirpath, filename)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            try:
                with open(path, encoding="utf-8") as fh:
                    source = fh.read()
                tree = ast.parse(source)
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue

            if not TENANT_MODULE.search(source):
                continue

            lines = source.split("\n")
            for name, lineno in _module_level_caches(tree).items():
                declaration = lines[lineno - 1] if lineno <= len(lines) else ""
                if ALLOW.search(declaration):
                    continue
                writes = _writes_to(tree, source, name)
                if not writes:
                    # Never written: a lookup table, not a cache of tenant data.
                    continue
                for write_line, key_src in writes:
                    if TENANT_TERM.search(key_src):
                        continue
                    # The key is usually a variable. Follow ONE assignment: a key
                    # built from a local that is itself the tenant is correctly
                    # scoped. Without this the gate reports its own fix, where
                    # cache_key = None if _tenant is None else (_tenant, domain)
                    # reaches current_org_id one hop away.
                    window = chr(10).join(lines[max(0, write_line - 40):write_line])
                    variable = key_src.strip()
                    assignment = re.search(
                        r"^\s*" + re.escape(variable) + r"\s*=\s*(.+)$", window, re.M
                    )
                    if assignment and TENANT_TERM.search(assignment.group(1)):
                        continue
                    problems.append(
                        "%s:%d [cache-tenancy] %s is written with key %s, which "
                        "carries no organisation -- every tenant in this process "
                        "shares the entry" % (rel, write_line, name, key_src or "<?>")
                    )
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
            print(
                "Include the organisation in the key (g.current_org_id), cache nothing\n"
                "when there is no tenant context, and bound the map. Or append\n"
                "'cache-tenancy-ok: <reason>' to the declaration saying why the\n"
                "contents are tenant-independent."
            )
    print(len(problems))
    return 0


if __name__ == "__main__":
    sys.exit(main())

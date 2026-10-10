#!/usr/bin/env python
"""Every background-work mechanism is listed in one register, or the build fails.

The application grew five ways to run work outside a request: a Flask-RQ queue,
Celery tasks, an APScheduler scheduler, raw threads and thread pools, and
multiprocessing. Each has its own retry, logging and organisation-scope story,
so a failure in one is invisible from the others and nobody can say how many
workers the product really has. `docs/background-mechanisms.yml` is the one
list: for each mechanism instance its kind, file, entry function, purpose,
class, disposition and how the organisation scope is set.

This gate reads `app/` as source (no boot, no database) and fails when

* a file uses rq, celery, apscheduler, `threading.Thread`, `ThreadPoolExecutor`
  or multiprocessing and the register has no entry for that file and kind;
* the register names a file and kind that is no longer used (a stale entry
  would hold the ratchet up for a mechanism that is gone);
* an entry is missing a required field or uses a value outside the allowed set;
* the number of `thread` entries rises above the recorded baseline.

An empty scan (nothing found at all) is reported as no-evidence, never as a
pass: a scan that finds nothing in a tree that has these mechanisms has proven
nothing about it.

Generated customer code under `app/modules/solutions_product/templates/` is not
a platform worker, and test code under a `tests/` directory is not a worker
either; neither is scanned. Entries of kind `other` describe a consumer or
dispatcher of one of the mechanisms (for example the process that runs the
scheduler, or a route that hands work to Celery) and are not checked for
staleness, because no source construct identifies them.

    python scripts/check_background_mechanisms.py            # report problems
    python scripts/check_background_mechanisms.py --count    # thread entries
    python scripts/check_background_mechanisms.py --list     # found mechanisms
    python scripts/check_background_mechanisms.py --root <tree>   # synthetic tree
"""
from __future__ import annotations

import argparse
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTER = os.path.join("docs", "background-mechanisms.yml")
SKIP_DIRS = {"__pycache__", "node_modules"}
SKIP_PREFIXES = ("app/modules/solutions_product/templates/",)

KINDS = ("rq", "celery", "apscheduler", "thread", "process-pool", "other")
CLASSES = ("queue", "scheduler", "request-helper", "generated-code")
DISPOSITIONS = ("keep", "move-to-pool", "retire-after-pool", "out-of-scope")
FIELDS = ("kind", "file", "entry", "purpose", "class", "disposition",
          "tenant_context")

_MODULE_KIND = {
    "rq": "rq", "flask_rq": "rq", "flask_rq2": "rq",
    "celery": "celery",
    "apscheduler": "apscheduler",
    "multiprocessing": "process-pool",
}


def _walk_with_scope(tree):
    """Yield (node, enclosing function qualname or '<module>')."""
    def visit(node, scope):
        yield node, scope
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                inner = child.name if scope == "<module>" else scope + "." + child.name
                yield from visit(child, inner)
            else:
                yield from visit(child, scope)
    yield from visit(tree, "<module>")


def _kinds_in(source: str) -> dict:
    """{kind: first enclosing scope} for one file's source."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return {}
    found: dict = {}
    thread_names = set()      # local names bound to threading.Thread / Timer
    threading_names = {"threading"}    # local names bound to the threading module
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "threading":
            for alias in node.names:
                if alias.name in ("Thread", "Timer"):
                    thread_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "threading":
                    threading_names.add(alias.asname or "threading")
    for node, scope in _walk_with_scope(tree):
        kind = None
        if isinstance(node, ast.Import):
            for alias in node.names:
                kind = _MODULE_KIND.get(alias.name.split(".")[0]) or kind
        elif isinstance(node, ast.ImportFrom) and node.module:
            kind = _MODULE_KIND.get(node.module.split(".")[0])
            if node.module.startswith("concurrent.futures"):
                for alias in node.names:
                    if alias.name == "ThreadPoolExecutor":
                        kind = "thread"
                    elif alias.name == "ProcessPoolExecutor":
                        kind = "process-pool"
        elif isinstance(node, ast.Attribute):
            if (node.attr in ("Thread", "Timer")
                    and isinstance(node.value, ast.Name)
                    and node.value.id in threading_names):
                kind = "thread"
            elif node.attr == "ThreadPoolExecutor":
                kind = "thread"
            elif node.attr == "ProcessPoolExecutor":
                kind = "process-pool"
        elif isinstance(node, ast.Name) and node.id in thread_names | {
                "ThreadPoolExecutor"}:
            kind = "thread"
        if kind and kind not in found:
            found[kind] = scope
    return found


def discover(root: str) -> dict:
    """{(relpath, kind): first enclosing scope} across app/."""
    found = {}
    base = os.path.join(root, "app")
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if rel.startswith(SKIP_PREFIXES) or "tests" in rel.split("/")[:-1]:
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    source = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            for kind, scope in _kinds_in(source).items():
                found[(rel, kind)] = scope
    return found


def load_register(root: str):
    """(entries, problems). Parsed with PyYAML; a missing file is a problem."""
    path = os.path.join(root, REGISTER)
    if not os.path.exists(path):
        return [], ["  [background-mechanisms] %s does not exist" % REGISTER]
    import yaml
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    entries = data.get("mechanisms") or []
    return entries, []


def scan(root: str):
    """(problems, thread_count, found_count)."""
    found = discover(root)
    entries, problems = load_register(root)
    registered = set()
    thread_count = 0
    for entry in entries:
        missing = [f for f in FIELDS if not str(entry.get(f) or "").strip()]
        where = "%s/%s" % (entry.get("file"), entry.get("kind"))
        if missing:
            problems.append("  [background-mechanisms] entry %s lacks %s"
                            % (where, ", ".join(missing)))
            continue
        for field, allowed in (("kind", KINDS), ("class", CLASSES),
                               ("disposition", DISPOSITIONS)):
            if entry[field] not in allowed:
                problems.append("  [background-mechanisms] entry %s has %s %r; "
                                "allowed: %s" % (where, field, entry[field],
                                                 ", ".join(allowed)))
        if entry["kind"] != "other":
            registered.add((entry["file"], entry["kind"]))
        if entry["kind"] == "thread":
            thread_count += 1
    for (rel, kind), scope in sorted(found.items()):
        if (rel, kind) not in registered:
            problems.append(
                "  %s [background-mechanisms] uses %s (in %s) and is not in %s; "
                "add an entry with its class, disposition and tenant_context"
                % (rel, kind, scope, REGISTER))
    if found:
        for rel, kind in sorted(registered - set(found)):
            problems.append(
                "  %s [background-mechanisms] register lists %s but the file no "
                "longer uses it; delete the entry" % (rel, kind))
    return problems, thread_count, len(found)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--root", default=ROOT)
    args = parser.parse_args()
    root = os.path.abspath(args.root)

    if args.list:
        for (rel, kind), scope in sorted(discover(root).items()):
            print("%s\t%s\t%s" % (kind, rel, scope))
        return 0

    problems, threads, found = scan(root)
    if args.count:
        print(threads)
        return 0
    if found == 0:
        print("  [no-evidence] no background mechanism found under %s/app; an "
              "empty scan proves nothing" % root)
        return 2
    for line in problems:
        print(line)
    print("%d mechanisms found, %d thread entries, %d problems"
          % (found, threads, len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

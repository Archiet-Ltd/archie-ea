#!/usr/bin/env python
"""Every background-work mechanism is listed in one register, or the build fails.

The application grew five ways to run work outside a request: a Flask-RQ queue,
Celery tasks, an APScheduler scheduler, raw threads and thread pools, and
multiprocessing. Each has its own retry, logging and organisation-scope story,
so a failure in one is invisible from the others and nobody can say how many
workers the product really has. `docs/background-mechanisms.yml` is the one
list: for each mechanism its kind, file, entry function, purpose, class,
disposition and how the organisation scope is set. An entry is keyed on
(file, kind, entry function); an entry may carry `instances: N` when one function
starts N of them.

This gate reads `app/` as source (no boot, no database) and fails when

* a file uses rq, celery, apscheduler, `threading.Thread`, `ThreadPoolExecutor`,
  multiprocessing, a `subprocess.Popen` that outlives the request or `os.fork`
  and the register has no entry for that file, kind and enclosing function (a
  second pool in an already registered file therefore fails too);
* a function starts a different number of instances than its entry records;
* the register names a file, kind and function that is no longer used (a stale
  entry would hold the ratchet up for a mechanism that is gone);
* an entry names a file that does not exist (also for `other` entries) or
  repeats another entry's key;
* an entry is missing a required field or uses a value outside the allowed set;
* the number of `thread` instances, or of files holding them, rises above
  the recorded baseline (`--count-instances`, `--count`).

An empty scan (nothing found at all) is reported as no-evidence, never as a
pass: a scan that finds nothing in a tree that has these mechanisms has proven
nothing about it.

Scanned: every .py file under `app/` and `manage.py` (it holds the RQ worker
command). Not scanned: generated customer code under
`app/modules/solutions_product/templates/` and test code under a `tests/`
directory. Deliberately excluded: `asyncio.to_thread` and
`loop.run_in_executor`, which run a call on the event loop's default executor
and are awaited by the request that made them, so they neither outlive the
request nor own a pool. A `subprocess.Popen` is a background mechanism only when
its enclosing function never waits on it (`wait`/`communicate`); `subprocess.run`
and the `check_*` helpers are synchronous and not scanned. For rq, celery,
apscheduler and multiprocessing the construct is the import, so the entry
function is the scope of the first import (usually `<module>`).

Entries of kind `other` describe a consumer or dispatcher of one of the
mechanisms (for example the process that runs the scheduler, or a route that
hands work to Celery). No source construct identifies them, so they are not
checked for staleness, but their file must exist.

    python scripts/check_background_mechanisms.py            # report problems
    python scripts/check_background_mechanisms.py --count    # thread entries
    python scripts/check_background_mechanisms.py --list     # found mechanisms
    python scripts/check_background_mechanisms.py --root <tree>   # synthetic tree
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTER = os.path.join("docs", "background-mechanisms.yml")
SKIP_DIRS = {"__pycache__", "node_modules"}
SKIP_PREFIXES = ("app/modules/solutions_product/templates/",)
ROOT_FILES = ("manage.py",)
_WAITS = {"wait", "communicate"}
_OS_FORKS = {"fork", "forkpty", "posix_spawn", "posix_spawnp"}

KINDS = ("rq", "celery", "apscheduler", "thread", "process-pool", "subprocess",
         "other")
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


# Cheap prefilter: a file naming none of these cannot start a mechanism, so it is
# never parsed (parsing every module under app/ is most of the run time).
_HINT = re.compile(r"threading|Thread|Timer|Executor|subprocess|Popen|fork|spawn|"
                   r"multiprocessing|celery|apscheduler|\brq\b|flask_rq")


def _module_aliases(tree, modules):
    """({module: names bound to it}, {module: {local name: original}})."""
    mods = {m: {m} for m in modules}
    members = {m: {} for m in modules}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in mods:
                    mods[alias.name].add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module in members:
            for alias in node.names:
                members[node.module][alias.asname or alias.name] = alias.name
    return mods, members


def _instances(source: str) -> dict:
    """{(kind, scope): instance count} for one file's source."""
    if not _HINT.search(source):
        return {}
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return {}
    found: dict = {}

    def add(kind, scope):
        found[(kind, scope)] = found.get((kind, scope), 0) + 1

    mods, members = _module_aliases(
        tree, ("threading", "concurrent.futures", "subprocess", "os"))
    thr_mods, thr_members = mods["threading"], members["threading"]
    fut_members = members["concurrent.futures"]
    sub_mods, sub_members = mods["subprocess"], members["subprocess"]
    os_mods, os_members = mods["os"], members["os"]
    thread_names = {n for n, o in thr_members.items() if o in ("Thread", "Timer")}
    pool_names = {n for n, o in fut_members.items() if o == "ThreadPoolExecutor"}
    ppool_names = {n for n, o in fut_members.items() if o == "ProcessPoolExecutor"}
    popen_names = {n for n, o in sub_members.items() if o == "Popen"}
    fork_names = {n for n, o in os_members.items() if o in _OS_FORKS}

    # A mechanism is started where its class is called or subclassed; a bare
    # reference (a type annotation, isinstance) starts nothing.
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            used.add(id(node.func))
        elif isinstance(node, ast.ClassDef):
            used.update(id(b) for b in node.bases)

    waits_in = {}      # scope -> True when something in it waits on a process
    popens = []        # (scope, node)
    imported = set()   # import kinds already counted in this file
    for node, scope in _walk_with_scope(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in _WAITS:
            waits_in[scope] = True
        if isinstance(node, ast.Import):
            for alias in node.names:
                kind = _MODULE_KIND.get(alias.name.split(".")[0])
                if kind and kind not in imported:
                    imported.add(kind)
                    add(kind, scope)
        elif isinstance(node, ast.ImportFrom) and node.module:
            kind = _MODULE_KIND.get(node.module.split(".")[0])
            if kind and kind not in imported:
                imported.add(kind)
                add(kind, scope)
        elif isinstance(node, ast.Attribute) and id(node) in used:
            base = node.value.id if isinstance(node.value, ast.Name) else None
            if node.attr in ("Thread", "Timer") and base in thr_mods:
                add("thread", scope)
            elif node.attr == "ThreadPoolExecutor":
                add("thread", scope)
            elif node.attr == "ProcessPoolExecutor":
                add("process-pool", scope)
            elif node.attr == "Popen" and base in sub_mods:
                popens.append(scope)
            elif node.attr in _OS_FORKS and base in os_mods:
                add("subprocess", scope)
        elif isinstance(node, ast.Name) and id(node) in used:
            if node.id in thread_names | pool_names:
                add("thread", scope)
            elif node.id in ppool_names:
                add("process-pool", scope)
            elif node.id in popen_names:
                popens.append(scope)
            elif node.id in fork_names:
                add("subprocess", scope)
    for scope in popens:
        if not waits_in.get(scope):
            add("subprocess", scope)
    return found


def _kinds_in(source: str) -> dict:
    """{kind: first scope} for one file's source (kept for callers listing kinds)."""
    out: dict = {}
    for (kind, scope) in _instances(source):
        out.setdefault(kind, scope)
    return out


def _python_files(root):
    base = os.path.join(root, "app")
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)
    for name in ROOT_FILES:
        path = os.path.join(root, name)
        if os.path.isfile(path):
            yield path


def discover(root: str) -> dict:
    """{(relpath, kind, scope): instance count} across app/ and manage.py."""
    found = {}
    for path in _python_files(root):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        if rel.startswith(SKIP_PREFIXES) or "tests" in rel.split("/")[:-1]:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        for (kind, scope), n in _instances(source).items():
            found[(rel, kind, scope)] = n
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
    """(problems, thread_files, found_count, thread_instances)."""
    found = discover(root)
    entries, problems = load_register(root)
    registered = {}
    thread_instances = 0
    thread_files = set()
    for entry in entries:
        missing = [f for f in FIELDS if not str(entry.get(f) or "").strip()]
        where = "%s/%s/%s" % (entry.get("file"), entry.get("kind"), entry.get("entry"))
        if missing:
            problems.append("  [background-mechanisms] entry %s lacks %s"
                            % (where, ", ".join(missing)))
            continue
        bad = False
        for field, allowed in (("kind", KINDS), ("class", CLASSES),
                               ("disposition", DISPOSITIONS)):
            if entry[field] not in allowed:
                bad = True
                problems.append("  [background-mechanisms] entry %s has %s %r; "
                                "allowed: %s" % (where, field, entry[field],
                                                 ", ".join(allowed)))
        try:
            instances = int(entry.get("instances", 1))
        except (TypeError, ValueError):
            instances = 0
        if instances < 1:
            bad = True
            problems.append("  [background-mechanisms] entry %s has instances %r; "
                            "expected a whole number of at least 1"
                            % (where, entry.get("instances")))
        if bad:
            continue
        if not os.path.exists(os.path.join(root, entry["file"])):
            problems.append("  %s [background-mechanisms] entry %s names a file "
                            "that does not exist; delete or correct the entry"
                            % (entry["file"], where))
        key = (entry["file"], entry["kind"], entry["entry"])
        if key in registered:
            problems.append("  %s [background-mechanisms] duplicate entry for %s "
                            "%s in %s; keep one (use `instances` for several in "
                            "one function)" % (entry["file"], entry["kind"],
                                               entry["entry"], REGISTER))
            continue
        registered[key] = instances
        if entry["kind"] == "thread":
            thread_instances += instances
            thread_files.add(entry["file"])
    for key, n in sorted(found.items()):
        rel, kind, scope = key
        if key not in registered:
            problems.append(
                "  %s [background-mechanisms] uses %s (in %s) and is not in %s; "
                "add an entry with entry: %s, its class, disposition and "
                "tenant_context" % (rel, kind, scope, REGISTER, scope))
        elif registered[key] != n:
            problems.append(
                "  %s [background-mechanisms] %s starts %d %s instance(s) in %s "
                "but the register records %d; update `instances`"
                % (rel, kind, n, kind, scope, registered[key]))
    if found:
        for key in sorted(set(registered) - set(found)):
            rel, kind, scope = key
            if kind == "other":
                continue
            problems.append(
                "  %s [background-mechanisms] register lists %s in %s but the "
                "file no longer uses it there; delete the entry" % (rel, kind, scope))
    return problems, len(thread_files), len(found), thread_instances


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", action="store_true",
                        help="number of files holding thread entries")
    parser.add_argument("--count-instances", action="store_true",
                        help="number of thread instances")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--root", default=ROOT)
    args = parser.parse_args()
    root = os.path.abspath(args.root)

    if args.list:
        for (rel, kind, scope), n in sorted(discover(root).items()):
            print("%s\t%s\t%s\t%d" % (kind, rel, scope, n))
        return 0

    problems, files, found, threads = scan(root)
    if args.count:
        print(files)
        return 0
    if args.count_instances:
        print(threads)
        return 0
    if found == 0:
        print("  [no-evidence] no background mechanism found under %s/app; an "
              "empty scan proves nothing" % root)
        return 2
    for line in problems:
        print(line)
    print("%d mechanisms found, %d thread instances in %d files, %d problems"
          % (found, threads, files, len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

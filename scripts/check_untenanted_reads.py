#!/usr/bin/env python
"""Find reads of a model that carries no tenant fence, with no organisation predicate.

    python scripts/check_untenanted_reads.py            # report
    python scripts/check_untenanted_reads.py --count    # count only
    python scripts/check_untenanted_reads.py --json
    python scripts/check_untenanted_reads.py --models   # list the unfenced models

Why
---
``do_orm_execute`` (app/middleware/tenant_isolation.py) adds ``WHERE organization_id =
<caller's organisation>`` to reads of models that inherit ``TenantMixin``. A model
that does not inherit it gets no such filter, whatever its columns are. Two real
leaks came from exactly this and were invisible to every existing gate:

  - ``OrganizationUnit`` and ``ApplicationOwnership`` had no tenant column at all.
    A read was added weeks after they were classified "dead scaffolding", and a
    forged ``organization_unit_id`` on one ownership row could have exposed another
    organisation's unit.
  - ``programme_for_element`` read work package owners with
    ``db.select(User).where(User.id.in_(ids))``. ``User`` has an ``organization_id``
    column but no mixin, so a foreign ``owner_id`` would have named another
    organisation's user.

``check_tenant_scoping.py`` covers models that have the column but not the mixin, and
only the ``.query`` shapes. It does not see ``db.select(Model)`` or a model with no
column at all. This gate does: it reads every ``db.select(Model)``, ``Model.query``,
``session.query(Model)`` and ``session.get(Model, id)`` in application code.

What it does and does not catch
-------------------------------
A read is flagged when the model is unfenced (no TenantMixin anywhere in its bases)
and the statement that contains the read mentions no organisation (``organization_id``,
``org_id``, ``tenant``) and carries no ``tenant-scoping-ok:`` marker. It looks only at
the one statement, so a filter applied in a later statement reads as absent and needs
the marker with a reason. That is deliberate: a read whose safety depends on code
elsewhere should say so where it is written.

It does not prove tenancy. It stops a NEW read of an unfenced model from arriving
without either a predicate or a written reason. The count is a ratchet: it may fall,
never rise. ``session.get(Model, id)`` is always flagged for an unfenced model, since
a lookup by id has no place for a predicate.

Two checks, one script
----------------------
``--count`` / the default report is the READ gate above. ``--new-tables`` is the TABLE
gate: every database table whose model has no tenant fence must be listed in
``scripts/unfenced_tables.txt``, and that list is a baseline: it is compared with the committed
copy on main (the pull request's base branch in CI, origin/main locally; the committed
``scripts/unfenced_tables_baseline.txt`` only when no main ref exists) and any added name fails, so it
can only shrink. A new
table is protected (TenantMixin) or declared shared reference data in
``scripts/shared_reference_tables.txt`` with a reason after ``#`` (a line with no reason fails). A new table that is neither fenced nor listed fails, so
the decision (fence it, or say it is global or reached only through a fenced parent)
is made when the table is created, not after a later feature reads it. Regenerate the
list with ``--tables`` when a table is removed or gains the mixin.

Proven-against: a new ``db.select(User).where(User.id == user_id)`` added to a route
with no organisation predicate -- the read count rises by one; and a new model with a
``__tablename__`` and no ``TenantMixin`` added without listing it -- the table check
reports it by name. Both are pinned in tests/test_check_untenanted_reads.py.

Dynamic dispatch
----------------
A model chosen at runtime has no name for the checks above to match: ``REGISTRY[key].query``,
``session.get(cls, id)`` where ``cls = _import_model(path)``, ``getattr(mod, name).query``.
Such a site is reported as ``[dynamic-dispatch] <unresolved>`` and counts as a read until it is
exempted with ``tenant-scoping-ok: <reason>`` or listed as ``file:line`` in
``scripts/dynamic_dispatch_sites.txt`` after a person has confirmed every model the site can
reach is fenced or the caller checks tenancy where it dispatches. Detection is in
``scripts/dynamic_dispatch.py``, shared with ``check_tenant_scoping.py``.

Exemptions
----------
``# tenant-scoping-ok: <reason>`` on the line before the statement, or on any line of
it, says why the read is safe (the ids were already tenant-validated, the model is
global reference data, the caller is a cross-tenant CLI command). Reference data that
is genuinely global belongs in ``GLOBAL_MODELS`` below, with the reason.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dynamic_dispatch  # noqa: E402  (shared with check_tenant_scoping.py)

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "app"
TABLE_LIST = REPO / "scripts" / "unfenced_tables.txt"
# The committed snapshot of the list. The list may lose names; it may never gain one.
TABLE_BASELINE = REPO / "scripts" / "unfenced_tables_baseline.txt"
# Tables that are shared reference data on purpose. One per line, `table  # reason`.
SHARED_REFERENCE = REPO / "scripts" / "shared_reference_tables.txt"

FENCE_BASES = ("TenantMixin", "HybridCapabilityTenantMixin", "HybridTenantMixin")
MODEL_BASES = ("db.Model", "Model", "Base")
MARKER = "tenant-scoping-ok"
# The hatch needs a reason: `tenant-scoping-ok: <why>`. A bare marker does not exempt a read.
MARKER_WITH_REASON = re.compile(r"tenant-scoping-ok:\s*\S")
# A predicate is the org column compared or passed as a filter: `organization_id == x`,
# `organization_id=x`, `organization_id.in_(...)`, `x == ...organization_id`. The bare word
# inside another name (`other_org_id`, `tenant_label`) or a comment does not count.
_ORG = r"(?:organization_id|org_id|tenant_id)"
PREDICATE = re.compile(
    r"(?<![\w])" + _ORG + r"\b\s*(?:==|=(?!=)|\.in_\(|\bin\b)"
    r"|==\s*[\w.]*(?<![\w])" + _ORG + r"\b"
)

# Directories whose code runs across tenants on purpose (operator commands, bootstrap).
SKIP_DIRS = ("app/models/", "app/commands/", "app/_bootstrap/")

# Model names defined more than once with different fencing; filled by unfenced_models().
AMBIGUOUS: set[str] = set()

# Global reference data: shared by every organisation by design. Each needs a reason.
GLOBAL_MODELS: dict[str, str] = {
    "VendorOrganization": (
        "shared vendor catalogue, deliberately not tenant-scoped (ADR-0003, see the model docstring): "
        "facts about the vendor in the world, name globally unique"
    ),
    "APQCProcess": (
        "the APQC process classification framework, a shared reference catalogue: rows are created only by "
        "the seed commands (seed_capabilities, seed_apqc_vendor_mapping); the tenant-specific link is "
        "SolutionAPQCProcess"
    ),
    "TechnicalCapability": (
        "the ACM technical capability catalogue, shared reference data: code is globally unique and rows are "
        "created only by the seed commands (seed_capabilities, acm seed-capabilities)"
    ),
}


class ModelInfo:
    def __init__(self, name, path, bases, has_org_col, table):
        self.name, self.path, self.bases, self.has_org_col, self.table = name, path, bases, has_org_col, table


def _py_files(app=None):
    for path in sorted((app or APP).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _parse(path):
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return None


def discover_models(app=None, repo=None):
    """Every class that looks like a model, with its bases and whether it is fenced."""
    repo = repo or REPO
    classes: dict[str, list[ModelInfo]] = {}
    for path in _py_files(app):
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = [ast.unparse(b) for b in node.bases]
            has_col, table = False, None
            for stmt in node.body:
                targets = []
                if isinstance(stmt, ast.Assign):
                    targets = [t for t in stmt.targets if isinstance(t, ast.Name)]
                elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    targets = [stmt.target]
                for t in targets:
                    if t.id == "organization_id":
                        has_col = True
                    if t.id == "__tablename__" and isinstance(getattr(stmt, "value", None), ast.Constant):
                        table = stmt.value.value
            classes.setdefault(node.name, []).append(
                ModelInfo(node.name, path.relative_to(repo).as_posix(), bases, has_col, table)
            )
    return classes


def _resolve(name, classes, seen=None):
    """(is_model, is_fenced, has_org_col) for a class name, following its bases."""
    seen = seen or set()
    if name in seen or name not in classes:
        return (False, False, False)
    seen = seen | {name}
    is_model = is_fenced = has_col = False
    for info in classes[name]:
        has_col = has_col or info.has_org_col
        for base in info.bases:
            short = base.split(".")[-1]
            if base in MODEL_BASES or short in ("Model",):
                is_model = True
            if short in FENCE_BASES:
                is_fenced = True
            m, f, c = _resolve(short, classes, seen)
            is_model, is_fenced, has_col = is_model or m, is_fenced or f, has_col or c
        if info.table is not None:
            is_model = True
    return (is_model, is_fenced, has_col)


def unfenced_models(classes):
    """name -> {'has_col': bool, 'paths': [...]} for every model with no fence."""
    out = {}
    for name in classes:
        is_model, fenced, has_col = _resolve(name, classes)
        if not is_model or name in GLOBAL_MODELS:
            continue
        # A name defined twice with different fencing is ambiguous: skip rather than guess,
        # but say so (see --ambiguous), so a skipped model is never silent.
        statuses = {_resolve(name, {name: [i]})[1] for i in classes[name]}
        if len(classes[name]) > 1 and len(statuses) > 1:
            AMBIGUOUS.add(name)
            continue
        if fenced:
            continue
        out[name] = {"has_col": has_col, "paths": sorted({i.path for i in classes[name]})}
    return out


class _Scan(ast.NodeVisitor):
    def __init__(self, path, lines, models, include_predicated=False):
        self.path, self.lines, self.models = path, lines, models
        self.include_predicated = include_predicated
        self.stmts: list[ast.stmt] = []
        self.hits: list[dict] = []

    def visit(self, node):
        is_stmt = isinstance(node, ast.stmt)
        if is_stmt:
            self.stmts.append(node)
        try:
            self._inspect(node)
            self.generic_visit(node)
        finally:
            if is_stmt:
                self.stmts.pop()

    # -- what counts as a read of model X --------------------------------
    def _model_name(self, expr):
        text = ast.unparse(expr) if expr is not None else ""
        short = text.split(".")[-1]
        return short if short in self.models else None

    def _inspect(self, node):
        name = shape = None
        if isinstance(node, ast.Call):
            func = ast.unparse(node.func)
            if func.endswith("select") and node.args:
                name, shape = self._model_name(node.args[0]), "select"
            elif func.endswith("session.query") and node.args:
                name, shape = self._model_name(node.args[0]), "session.query"
            elif func.endswith("session.get") and node.args:
                name, shape = self._model_name(node.args[0]), "session.get"
        elif isinstance(node, ast.Attribute) and node.attr == "query":
            name, shape = self._model_name(node.value), "Model.query"
        if name:
            self._record(node, name, shape)

    def _segment(self):
        stmt = self.stmts[-1] if self.stmts else None
        if stmt is None:
            return 1, 1
        end = stmt.end_lineno
        body = getattr(stmt, "body", None)
        if body and isinstance(body, list) and body and isinstance(body[0], ast.stmt):
            end = max(stmt.lineno, body[0].lineno - 1)
        return stmt.lineno, end

    def _record(self, node, name, shape):
        first, last = self._segment()
        block = "\n".join(self.lines[first - 1:last])
        before = self.lines[first - 2] if first >= 2 else ""
        exempt = bool(MARKER_WITH_REASON.search(block) or MARKER_WITH_REASON.search(before))
        predicated = bool(PREDICATE.search(block))
        always = shape == "session.get"
        if exempt or (predicated and not always and not self.include_predicated):
            return
        self.hits.append({
            "file": self.path, "line": node.lineno, "model": name, "shape": shape,
            "has_org_col": self.models[name]["has_col"],
        })


def unfenced_tables(classes, models):
    """Sorted names of database tables (own ``__tablename__``) whose model is unfenced."""
    return sorted({
        info.table
        for name in models
        for info in classes[name]
        if info.table
    })


def listed_tables(path=None):
    path = path or TABLE_LIST
    if not path.exists():
        return set()
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.add(line)
    return names


def shared_reference_tables(path=None):
    """(names, lines_without_a_reason) from the shared-reference file."""
    path = path or SHARED_REFERENCE
    names, bare = set(), []
    if not path.exists():
        return names, bare
    for line in path.read_text(encoding="utf-8").splitlines():
        body, _, reason = line.partition("#")
        body = body.strip()
        if not body:
            continue
        names.add(body)
        if not reason.strip():
            bare.append(body)
    return names, bare


LIST_REL = "scripts/unfenced_tables.txt"
BASE_REFS = ("origin/main", "anioko/main")


def _git_show(repo, ref, rel):
    proc = subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=repo, capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def _names(text):
    out = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


def base_table_list(repo=None, env=None):
    """(names, source, error): the unfenced list as main has it, which this branch may not add to.

    On a pull request run (``GITHUB_BASE_REF`` set) the base branch's copy is read, fetching that
    one ref when the checkout is shallow; if it cannot be read, ``error`` says so and the gate
    fails (it never passes without a base to compare). Otherwise the list at the merge base with
    origin/main or anioko/main (whichever is nearer this branch) is used, so a table that main
    gained after this branch left it is not read as added here. With no main ref at all the committed snapshot is the
    fallback, with a note on stderr.
    """
    repo = repo or REPO
    env = os.environ if env is None else env
    base = env.get("GITHUB_BASE_REF", "").strip()
    if base:
        text = _git_show(repo, f"origin/{base}", LIST_REL)
        source = f"origin/{base}"
        if text is None:
            fetched = subprocess.run(["git", "fetch", "--depth=1", "origin", base], cwd=repo,
                                     capture_output=True, text=True)
            text = _git_show(repo, "FETCH_HEAD", LIST_REL) if fetched.returncode == 0 else None
            source = f"FETCH_HEAD ({base})"
        if text is None:
            return None, None, (f"cannot read {LIST_REL} from the pull request's base branch '{base}'; "
                                "the gate fails rather than pass without a list to compare with")
        return _names(text), source, None
    best = None
    for ref in BASE_REFS:
        mb = subprocess.run(["git", "merge-base", "HEAD", ref], cwd=repo, capture_output=True, text=True)
        if mb.returncode != 0 or not mb.stdout.strip():
            continue
        sha = mb.stdout.strip()
        ahead = subprocess.run(["git", "rev-list", "--count", f"{sha}..HEAD"], cwd=repo,
                               capture_output=True, text=True).stdout.strip()
        text = _git_show(repo, sha, LIST_REL)
        if text is not None and ahead.isdigit() and (best is None or int(ahead) < best[0]):
            best = (int(ahead), text, f"{ref} (merge base {sha[:9]})")
    if best:
        return _names(best[1]), best[2], None
    print("note: no main ref found (origin/main, anioko/main); comparing with the committed "
          "snapshot scripts/unfenced_tables_baseline.txt instead", file=sys.stderr)
    return listed_tables(TABLE_BASELINE), "snapshot", None


def table_problems(tables, listed=None, baseline=None, shared=None):
    """Every reason the table gate fails, as printable lines (empty = pass).

    - a name in the unfenced list that the committed baseline does not have (the list can
      only shrink);
    - a table with no fence that is neither listed nor declared shared reference data;
    - a shared-reference line with no reason after ``#``.
    Removing a name from either file never fails.
    """
    listed = listed_tables() if listed is None else listed
    baseline = listed_tables(TABLE_BASELINE) if baseline is None else baseline
    shared_names, bare = shared_reference_tables() if shared is None else shared
    problems = []
    for name in sorted(listed - baseline):
        problems.append(f"  added to the unfenced list: {name}")
    for name in sorted(t for t in tables if t not in listed and t not in shared_names):
        problems.append(f"  new unfenced table: {name}")
    for name in sorted(bare):
        problems.append(f"  shared reference table with no reason: {name}")
    for name in sorted(listed & shared_names):
        problems.append(f"  table is on both the unfenced list and the shared reference list: {name}")
    return problems


# A route-like file: the places a person's request reaches a table.
ROUTE_PATH = re.compile(r"route|api|view|blueprint|endpoint|/mcp")


def gate_base_sha(repo=None, env=None):
    """The commit this branch is compared with for the changed-route check, or None.

    On a pull request run (``GITHUB_BASE_REF`` set) it is the merge base with ``origin/<base>``; a
    local run names the ref in ``TENANT_GATE_BASE``. Only an explicit base counts: the nearest main
    ref can be a whole release behind a long-lived integration branch, and every file since would
    read as changed.
    """
    repo = repo or REPO
    env = os.environ if env is None else env
    base = env.get("GITHUB_BASE_REF", "").strip()
    override = env.get("TENANT_GATE_BASE", "").strip()
    refs = [override] if override else ([f"origin/{base}"] if base else [])
    for ref in refs:
        mb = subprocess.run(["git", "merge-base", "HEAD", ref], cwd=repo, capture_output=True, text=True)
        if mb.returncode == 0 and mb.stdout.strip():
            return mb.stdout.strip()
    return None


def changed_files(repo=None, env=None):
    """Files this branch changed against its base (committed and in the working tree), or None.

    None means there is no base to compare with, and the changed-route check is skipped (said on
    stderr).
    """
    repo = repo or REPO
    sha = gate_base_sha(repo, env)
    if sha is None:
        print("note: no pull request base (GITHUB_BASE_REF or TENANT_GATE_BASE); the changed-route check is skipped",
              file=sys.stderr)
        return None
    names = set()
    for args in (["git", "diff", "--name-only", sha, "HEAD"], ["git", "diff", "--name-only", "HEAD"]):
        out = subprocess.run(args, cwd=repo, capture_output=True, text=True).stdout.split()
        names.update(out)
    return sorted(names)


# The release valve for the changed-route check: one ``file:table:batch`` line per unfenced read in a
# route that a pull request changes, with the batch that will fence the table. Like the unfenced
# list it can only shrink; a line whose table is fenced or declared shared must be removed.
PENDING = REPO / "scripts" / "qa" / "tenant_gate_pending.txt"


def pending_entries(path=None):
    """{(file, table): batch} from the pending list."""
    path = path or PENDING
    entries = {}
    if not path.exists():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(":")
        if len(parts) >= 3:
            entries[(parts[0].strip(), parts[1].strip())] = parts[2].strip()
        else:
            entries[(parts[0].strip(), parts[1].strip() if len(parts) > 1 else "")] = ""
    return entries


def pending_growth_problems(current, base_text):
    """The pending list may not have more lines than on the base. No list on the base (the first
    population) is the one exception."""
    if base_text is None:
        return []
    before = len(_names(base_text))
    after = len(current)
    if after > before:
        return [f"  the pending list grew from {before} to {after} lines; it can only shrink"]
    return []


def changed_route_problems(files, listed=None, shared=None, app=None, repo=None, pending=None):
    """A changed route reads a table that is still on the unfenced list and is not marked shared.

    Builders protect what they touch: touching a route that reads an unfenced table means fencing
    that table (or declaring it shared with a reason) in the same change. Every read counts here,
    predicated or not; ``tenant-scoping-ok: <reason>`` on the statement is the only exemption.
    """
    app, repo = app or APP, repo or REPO
    listed = listed_tables() if listed is None else listed
    shared_names = shared_reference_tables()[0] if shared is None else shared[0]
    classes = discover_models(app, repo)
    models = unfenced_models(classes)
    table_of = {n: {i.table for i in classes[n] if i.table} for n in models}
    pending = pending_entries() if pending is None else pending
    problems = []
    reads = set()
    for rel in files or ():
        if not rel.endswith(".py") or not rel.startswith("app/") or any(rel.startswith(d) for d in SKIP_DIRS):
            continue
        if not ROUTE_PATH.search(rel):
            continue
        path = repo / rel
        if not path.exists():
            continue
        tree = _parse(path)
        if tree is None:
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        scanner = _Scan(rel, lines, models, include_predicated=True)
        scanner.visit(tree)
        for hit in scanner.hits:
            for table in sorted(table_of.get(hit["model"], ())):
                if table in listed and table not in shared_names:
                    reads.add((rel, table))
                    if (rel, table) not in pending:
                        problems.append(
                            f"  changed route reads an unfenced table: {hit['file']}:{hit['line']} "
                            f"{hit['model']} ({table}); fence it, declare it shared with a reason, or list "
                            f"{rel}:{table}:<batch> in scripts/qa/tenant_gate_pending.txt")
    scanned = {f for f in (files or ())}
    for (rel, table), batch in sorted(pending.items()):
        if table not in listed or table in shared_names:
            problems.append(f"  pending entry no longer needed (the table is fenced or shared): {rel}:{table}; remove the line")
        elif rel in scanned and (rel, table) not in reads:
            problems.append(f"  pending entry no longer needed (the route does not read it): {rel}:{table}; remove the line")
    return sorted(set(problems))


def scan(app=None, repo=None):
    app, repo = app or APP, repo or REPO
    classes = discover_models(app, repo)
    models = unfenced_models(classes)
    hits = []
    for path in _py_files(app):
        rel = path.relative_to(repo).as_posix()
        if any(rel.startswith(d) for d in SKIP_DIRS):
            continue
        tree = _parse(path)
        if tree is None:
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        scanner = _Scan(rel, lines, models)
        scanner.visit(tree)
        hits.extend(scanner.hits)
        for site in dynamic_dispatch.unlisted(rel, dynamic_dispatch.find_dynamic_sites(tree, lines)):
            hits.append({
                "file": rel, "line": site["line"], "model": "<unresolved>",
                "shape": "dynamic-dispatch", "has_org_col": None,
            })
    return models, hits


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--models", action="store_true")
    parser.add_argument("--ambiguous", action="store_true", help="model names skipped because they are defined twice with different fencing")
    parser.add_argument("--tables", action="store_true", help="print the unfenced table names")
    parser.add_argument("--new-tables", action="store_true", help="unfenced tables missing from the list")
    args = parser.parse_args()
    if args.tables or args.new_tables:
        classes = discover_models()
        tables = unfenced_tables(classes, unfenced_models(classes))
        if args.tables:
            print("# Database tables whose model has no tenant fence (no TenantMixin).")
            print("# Checked by scripts/check_untenanted_reads.py --new-tables. A new table must either")
            print("# inherit TenantMixin or be added here, which shows up in review.")
            print(chr(10).join(tables))
            return 0
        base, source, error = base_table_list()
        if error:
            print(f"  {error}")
            print(1)
            return 0
        problems = table_problems(tables, baseline=base)
        problems += changed_route_problems(changed_files())
        sha = gate_base_sha()
        if sha is not None:
            problems += pending_growth_problems(
                pending_entries(), _git_show(REPO, sha, "scripts/qa/tenant_gate_pending.txt"))
        for line in problems:
            print(line)
        if problems:
            print("Give the model TenantMixin, or declare it shared reference data in "
                  "scripts/shared_reference_tables.txt with a reason after `#`. "
                  "scripts/unfenced_tables.txt can only shrink; a name added to it fails.")
        print(len(problems))
        return 0
    models, hits = scan()
    if args.ambiguous:
        print(chr(10).join(sorted(AMBIGUOUS)))
        return 0
    if args.models:
        for name, info in sorted(models.items()):
            print(f"{name:45s} column={'yes' if info['has_col'] else 'NO '}  {info['paths'][0]}")
        print(len(models))
        return 0
    if args.json:
        print(json.dumps({"models": len(models), "hits": hits}, indent=2))
        return 0
    if args.count:
        print(len(hits))
        return 0
    for hit in sorted(hits, key=lambda h: (h["file"], h["line"])):
        col = {True: "column", False: "NO COLUMN", None: "model resolved at runtime"}[hit["has_org_col"]]
        print(f"  {hit['file']}:{hit['line']}: [{hit['shape']}] {hit['model']} ({col}) read with no organisation predicate")
    print(f"\n{len(hits)} read(s) of {len(models)} unfenced model(s)")
    print(f"Add a predicate, or `# {MARKER}: <reason>` on the statement. See the module docstring.")
    print(len(hits))
    return 0


if __name__ == "__main__":
    sys.exit(main())

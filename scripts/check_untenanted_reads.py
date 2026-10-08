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
``scripts/unfenced_tables.txt``. A new table that is neither fenced nor listed fails, so
the decision (fence it, or say it is global or reached only through a fenced parent)
is made when the table is created, not after a later feature reads it. Regenerate the
list with ``--tables`` when a table is removed or gains the mixin.

Proven-against: a new ``db.select(User).where(User.id == user_id)`` added to a route
with no organisation predicate -- the read count rises by one; and a new model with a
``__tablename__`` and no ``TenantMixin`` added without listing it -- the table check
reports it by name. Both are pinned in tests/test_check_untenanted_reads.py.

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
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "app"
TABLE_LIST = REPO / "scripts" / "unfenced_tables.txt"

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
    def __init__(self, path, lines, models):
        self.path, self.lines, self.models = path, lines, models
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
        if exempt or (predicated and not always):
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
        new = [t for t in tables if t not in listed_tables()]
        for t in new:
            print(f"  new unfenced table: {t}")
        if new:
            print("Give the model TenantMixin, or list the table in scripts/unfenced_tables.txt "
                  "(global reference data, or reached only through a fenced parent).")
        print(len(new))
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
        col = "column" if hit["has_org_col"] else "NO COLUMN"
        print(f"  {hit['file']}:{hit['line']}: [{hit['shape']}] {hit['model']} ({col}) read with no organisation predicate")
    print(f"\n{len(hits)} read(s) of {len(models)} unfenced model(s)")
    print(f"Add a predicate, or `# {MARKER}: <reason>` on the statement. See the module docstring.")
    print(len(hits))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Shared detection of dynamically dispatched model reads, for the two tenancy gates.

``check_untenanted_reads.py`` and ``check_tenant_scoping.py`` recognise a read by the
NAME of the model in the source text: ``Model.query``, ``db.select(Model)``,
``session.get(Model, id)``. A read whose model is chosen at runtime has no name to
recognise, so both gates passed it silently:

    MODEL_REGISTRY[slug].query.filter_by(id=entity_id)      # subscript base
    cls = _import_model(dotted_path); db.session.get(cls, id)  # resolved helper
    getattr(models_module, name).query                        # non-literal getattr

Three cross-organisation leaks shipped through exactly this shape. This module finds
those sites; each gate reports one that is not accepted as a ``dynamic-dispatch``
finding, and the gate's count rises by one until the site is either fixed, marked
``tenant-scoping-ok: <reason>`` on the statement, or listed in
``scripts/dynamic_dispatch_sites.txt`` (``file:line`` per entry, one decision each).
Both gates share that one list.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SITE_LIST = REPO / "scripts" / "dynamic_dispatch_sites.txt"

# Calls that turn a string into a class at runtime.
RESOLVER_HELPERS = frozenset({"_import_model", "import_module", "__import__"})
# select(X) / session.query(X) / session.get(X, id) take the model as first argument.
_READ_CALL_SUFFIXES = ("select", "session.query", "session.get")
MARKER_WITH_REASON = re.compile(r"tenant-scoping-ok:\s*\S")


def _is_dynamic_expr(expr, tainted):
    """True when ``expr`` evaluates to a model class chosen at runtime."""
    if isinstance(expr, ast.Subscript):
        return True
    if isinstance(expr, ast.Name):
        return expr.id in tainted
    if isinstance(expr, ast.Call):
        func = expr.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in RESOLVER_HELPERS:
            return True
        if name == "getattr":
            second = expr.args[1] if len(expr.args) > 1 else None
            return not (isinstance(second, ast.Constant) and isinstance(second.value, str))
    return False


class _DynamicScan(ast.NodeVisitor):
    def __init__(self, lines):
        self.lines = lines
        self.stmts: list[ast.stmt] = []
        self.scopes: list[set[str]] = [set()]
        self.sites: list[dict] = []

    def visit(self, node):
        is_stmt = isinstance(node, ast.stmt)
        is_func = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        if is_stmt:
            self.stmts.append(node)
        if is_func:
            self.scopes.append(set())
        try:
            self._inspect(node)
            self.generic_visit(node)
        finally:
            if is_func:
                self.scopes.pop()
            if is_stmt:
                self.stmts.pop()

    def _inspect(self, node):
        tainted = self.scopes[-1]
        if isinstance(node, ast.Assign) and _is_dynamic_expr(node.value, tainted):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    tainted.add(target.id)
            return
        shape = None
        if isinstance(node, ast.Call) and node.args:
            func = ast.unparse(node.func)
            if func.endswith(_READ_CALL_SUFFIXES) and _is_dynamic_expr(node.args[0], tainted):
                shape = func.rsplit(".", 1)[-1] if func.endswith("select") else func
        elif isinstance(node, ast.Attribute) and node.attr == "query":
            if _is_dynamic_expr(node.value, tainted):
                shape = "Model.query"
        if shape and not self._exempt():
            self.sites.append({"line": node.lineno, "shape": shape})

    def _exempt(self):
        stmt = self.stmts[-1] if self.stmts else None
        if stmt is None:
            return False
        first = stmt.lineno
        end = stmt.end_lineno
        body = getattr(stmt, "body", None)
        if body and isinstance(body, list) and isinstance(body[0], ast.stmt):
            end = max(first, body[0].lineno - 1)
        block = "\n".join(self.lines[max(first - 2, 0):end])
        return bool(MARKER_WITH_REASON.search(block))


def find_dynamic_sites(tree, lines):
    """[{'line', 'shape'}] for every unexempted dynamically dispatched model read."""
    scan = _DynamicScan(lines)
    scan.visit(tree)
    seen, sites = set(), []
    for site in scan.sites:
        if (site["line"], site["shape"]) not in seen:
            seen.add((site["line"], site["shape"]))
            sites.append(site)
    return sites


def listed_sites(path=None):
    """The accepted ``file:line`` entries (comments after ``#`` ignored)."""
    path = path or SITE_LIST
    if not path.exists():
        return set()
    entries = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            entries.add(line)
    return entries


def unlisted(rel_path, sites, listed=None):
    """The sites in ``rel_path`` whose ``file:line`` is not on the accepted list."""
    listed = listed_sites() if listed is None else listed
    return [s for s in sites if f"{rel_path}:{s['line']}" not in listed]

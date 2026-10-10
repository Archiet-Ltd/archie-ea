#!/usr/bin/env python
"""Find a write route on a platform-wide model that is not platform-admin-gated.

    python scripts/check_platform_admin_coverage.py            # report
    python scripts/check_platform_admin_coverage.py --count    # count only
    python scripts/check_platform_admin_coverage.py --json

Why
---
A handful of models are deliberately platform-wide: no ``organization_id``
column, no ``TenantMixin`` -- one ``ExternalSystem`` row serves every tenant's
Abacus/Jira/ServiceNow connector, one ``Job`` queue runs their syncs, one
``AIPromptTemplate`` override replaces a solution-drafting prompt for every
organisation, one ``ScoringConfiguration``/``FeatureFlag`` changes behaviour
platform-wide. That data model is correct.

What went wrong, seven separate times in one review cycle (PRs 307, 312, 314,
315, each caught only by a human/AI reviewer reading the diff): the write
route for one of these models was gated with ``@admin_required``
(``Permission.ADMINISTER``, which any organisation's own administrator role
holds) instead of ``@platform_admin_required`` (``is_platform_admin``, the
actual cross-tenant check). ``@admin_required`` is correct for the other ~80
admin routes in the same files -- users, roles, webhook settings, API keys --
which really are that organisation's own data. Nothing at the call site flags
"this one is different", so the far more common decorator gets copy-pasted
onto the rare case that needed the other one. ``scripts/check_untenanted_reads.py``
already tracks exactly which models carry no tenant column at all for the READ
side; this is the WRITE-side analogue the review cycle kept finding by hand.

What it does and does not catch
--------------------------------
A route is flagged when: it accepts a write HTTP method (POST/PUT/PATCH/DELETE);
its own function body references a model that ``check_untenanted_reads.py``
classifies as unfenced AND with no ``organization_id`` column at all (a model
that merely carries the column but lacks ``TenantMixin`` is tenant data missing
an explicit predicate -- that is ``check_tenant_scoping.py``'s job, not this
gate's: fencing it with ``platform_admin_required`` would be wrong, since it is
NOT platform-wide); and no ``@platform_admin_required`` decorator (by name, any
position) and no exemption marker.

It looks only inside the decorated function's own body -- a write made through
a helper in another module (``save_relationship_mappings`` calling
``app.config.abacus_field_mapping.save_outconnection_mappings``) is invisible
to this gate unless the route function itself also references the model, as
every real instance in this codebase happened to. That is a known blind spot,
the same shape of limitation ``check_untenanted_reads.py``'s own docstring
accepts for its single-statement scope.

A model is counted as WRITTEN, not merely referenced, when: it is directly
constructed (``Model(...)``); a variable fetched via ``Model.query``,
``db.select(Model)``, ``session.query(Model)`` or ``session.get(Model, id)``
is later attribute-assigned (``row = Model.query.get(id); row.field = x``,
the fetch-then-mutate shape every real instance in this codebase used) or
passed to ``db.session.delete(row)``; or it is the target of a chained
``.query...update(...)``/``.query...delete(...)``. A bare read used only to
validate a foreign key before writing an unrelated, correctly-scoped model
(``ADMPhase.query.get(id)`` before creating a ``KanbanCard``) is not a write
of that model and is not flagged -- an earlier version of this gate did not
make this distinction and drowned its real findings in exactly this noise.

A ratchet: the count may fall, never rise.

Exemptions
----------
``# platform-admin-ok: <reason>`` on the route's decorator block or the line
above it says why the write is safe without the gate (for example: the body
only ever reads the model to look up a value already proven tenant-owned by an
earlier predicate in the same function, and never constructs or mutates a row).

Proven-against: reverting app/modules/ai_chat/routes/chat_admin_routes.py's
admin_prompt_update/admin_prompt_reset from the in-body _require_platform_admin()
call back to the original _require_admin() -- the real AIPromptTemplate instance
this gate's own --named-count sub-gate exists to catch -- the broad scan (with its
``platform-admin-ok`` markers temporarily removed) flags both again. Pinned in
tests/test_check_platform_admin_coverage.py's bare-read and mutation-shape tests.

    python scripts/check_platform_admin_coverage.py --models   # list in-scope platform-wide models
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "app"
MARKER = "platform-admin-ok"
MARKER_WITH_REASON = re.compile(r"platform-admin-ok:\s*\S")
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
GATE_DECORATOR = "platform_admin_required"

# Directories with no HTTP routes, or that run across tenants on purpose.
SKIP_DIRS = ("app/models/", "app/commands/", "app/_bootstrap/", "app/tasks/")

# Confirmed-real singleton-config models: no tenant column AND no FK to a
# fenced parent either -- genuinely nothing to scope a write to but
# "platform admin or not". Most of the broad scan's hits are the OTHER
# shape (a child row scoped via a foreign key to an already-fenced parent,
# e.g. CodegenGeneration.solution_id -> Solution), where demanding
# platform_admin_required would be wrong. This list is the ratchet that
# should actually reach and hold at 0 -- see --named-count and the
# "named-platform-admin-coverage" gate in verify.py (nit 3,
# pr321-review-v1, the independent read review on PR #321).
NAMED_PLATFORM_MODELS = {
    "ExternalSystem", "Job", "AIPromptTemplate", "ScoringConfiguration", "FeatureFlag",
}


def _load_untenanted_reads():
    """Load check_untenanted_reads.py by path (scripts/ is not a package)."""
    path = REPO / "scripts" / "check_untenanted_reads.py"
    spec = importlib.util.spec_from_file_location("check_untenanted_reads", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("check_untenanted_reads", module)
    spec.loader.exec_module(module)
    return module


def platform_wide_models(app=None, repo=None, untenanted=None):
    """Unfenced models with no organization_id column at all -- genuinely platform-wide.

    A model with the column but no TenantMixin is tenant data missing a
    predicate (check_tenant_scoping.py's job); only a model with neither is a
    candidate for "this write needs platform_admin_required".
    """
    untenanted = untenanted or _load_untenanted_reads()
    classes = untenanted.discover_models(app, repo)
    models = untenanted.unfenced_models(classes)
    return {name: info for name, info in models.items() if not info["has_col"]}


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


def _route_methods(decorator):
    """HTTP methods a route decorator declares, or None if it is not a route."""
    if not isinstance(decorator, ast.Call):
        return None
    func = ast.unparse(decorator.func)
    if not func.endswith(".route"):
        return None
    for kw in decorator.keywords:
        if kw.arg == "methods" and isinstance(kw.value, ast.List):
            methods = set()
            for elt in kw.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    methods.add(elt.value.upper())
            return methods
    return {"GET"}  # Flask's own default when methods= is omitted.


def _has_gate_decorator(decorators):
    for dec in decorators:
        name = ast.unparse(dec)
        if name == GATE_DECORATOR or name.startswith(GATE_DECORATOR + "("):
            return True
        # @some_bp.route(...) decorators are Calls too; match the bare name
        # anywhere a decorator resolves to it (e.g. a Call whose func is it).
        func = dec.func if isinstance(dec, ast.Call) else dec
        if ast.unparse(func) == GATE_DECORATOR:
            return True
    return False


class _ModelRefScan(ast.NodeVisitor):
    """Every model this function's own body actually WRITES -- not merely reads.

    A bare read (``ADMPhase.query.get(id)`` used only to validate a foreign key
    before writing an unrelated KanbanCard) is not a write of ADMPhase and must
    not be flagged, or the gate drowns real findings in this kind of noise.
    A model counts as written when: it is directly constructed (``Model(...)``);
    a variable bound to a query/select/get of it is later attribute-assigned
    (``row = Model.query.get(id); row.field = x``, the fetch-then-mutate shape
    every real instance in this codebase used) or passed to
    ``db.session.delete(row)``; or it is the target of a chained
    ``.query...update(...)``/``.query...delete(...)``.
    """

    def __init__(self, models):
        self.models = models
        self.found: set[str] = set()
        self.tracked: dict[str, str] = {}  # local variable name -> model it was fetched/built from

    def _short(self, expr):
        text = ast.unparse(expr) if expr is not None else ""
        return text.split(".")[-1]

    def _chain_model(self, node):
        """Walk a .query.filter_by(...).first() -style chain back to its base name.

        The chain alternates Call and Attribute nodes (.filter_by(...) is a
        Call whose .func is an Attribute whose .value is the next link), so
        peeling off only one layer of each stops partway through -- it must
        alternate until it reaches something that isn't either. A link may
        itself be ``db.session.query(Model)``/``db.select(Model)``/
        ``session.get(Model, id)``, which names the model as a Call ARGUMENT
        rather than in the chain's base name -- checked at every Call layer,
        not only at the point ``_value_model`` is first called.
        """
        cur = node
        while isinstance(cur, (ast.Call, ast.Attribute)):
            if isinstance(cur, ast.Call) and cur.args:
                func_text = ast.unparse(cur.func)
                if func_text.endswith(("select", "session.query", "session.get")):
                    arg_name = self._short(cur.args[0])
                    if arg_name in self.models:
                        return arg_name
            cur = cur.func if isinstance(cur, ast.Call) else cur.value
        name = cur.id if isinstance(cur, ast.Name) else None
        return name if name in self.models else None

    def _value_model(self, value):
        """The model a value was fetched from or constructed as, if any."""
        if isinstance(value, ast.Call):
            callee = self._short(value.func)
            if callee in self.models:
                return callee
            func_text = ast.unparse(value.func)
            if func_text.endswith(("select", "session.query", "session.get")) and value.args:
                arg_name = self._short(value.args[0])
                if arg_name in self.models:
                    return arg_name
            return self._chain_model(value.func)
        if isinstance(value, ast.Attribute):
            return self._chain_model(value)
        return None

    def visit_Assign(self, node):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            model = self._value_model(node.value)
            if model:
                self.tracked[node.targets[0].id] = model
                # A constructor assigned to a variable is a write the moment it
                # is built -- db.session.add(row) always follows in real code,
                # and waiting to see that add() is an unnecessary second hop.
                if isinstance(node.value, ast.Call) and self._short(node.value.func) == model:
                    self.found.add(model)
        else:
            for target in node.targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                    model = self.tracked.get(target.value.id)
                    if model:
                        self.found.add(model)
        self.generic_visit(node)

    def visit_Call(self, node):
        func = ast.unparse(node.func)
        # A bare, unassigned constructor: db.session.add(Model(...)).
        callee = self._short(node.func)
        if callee in self.models and not func.endswith(("select", "session.query", "session.get")):
            self.found.add(callee)
        # Chained bulk update/delete: Model.query.filter_by(...).update({...}).
        if func.endswith((".update", ".delete")):
            model = self._chain_model(node.func)
            if model:
                self.found.add(model)
        # db.session.delete(row) where row was fetched from a tracked model.
        if func.endswith("session.delete") and node.args and isinstance(node.args[0], ast.Name):
            model = self.tracked.get(node.args[0].id)
            if model:
                self.found.add(model)
        self.generic_visit(node)


def scan(app=None, repo=None, models=None):
    app, repo = app or APP, repo or REPO
    models = models if models is not None else platform_wide_models(app, repo)
    hits = []
    for path in _py_files(app):
        rel = path.relative_to(repo).as_posix()
        if any(rel.startswith(d) for d in SKIP_DIRS):
            continue
        tree = _parse(path)
        if tree is None:
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.decorator_list:
                continue
            write_methods = set()
            is_route = False
            for dec in node.decorator_list:
                methods = _route_methods(dec)
                if methods is not None:
                    is_route = True
                    write_methods |= methods & WRITE_METHODS
            if not is_route or not write_methods:
                continue
            if _has_gate_decorator(node.decorator_list):
                continue
            first = min([d.lineno for d in node.decorator_list] + [node.lineno])
            window = "\n".join(lines[max(0, first - 2):node.end_lineno])
            if MARKER_WITH_REASON.search(window):
                continue
            ref_scan = _ModelRefScan(models)
            ref_scan.visit(node)
            if not ref_scan.found:
                continue
            hits.append({
                "file": rel, "line": node.lineno, "function": node.name,
                "methods": sorted(write_methods), "models": sorted(ref_scan.found),
            })
    return hits


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--models", action="store_true", help="list in-scope platform-wide models")
    parser.add_argument(
        "--named-count", action="store_true",
        help="count only hits on NAMED_PLATFORM_MODELS -- the confirmed-real singleton-config "
             "models, not the broad FK-scoped-child noise. See NAMED_PLATFORM_MODELS.",
    )
    args = parser.parse_args()
    models = platform_wide_models()
    if args.models:
        for name in sorted(models):
            print(name)
        print(len(models))
        return 0
    hits = scan(models=models)
    if args.named_count:
        named_hits = [h for h in hits if set(h["models"]) & NAMED_PLATFORM_MODELS]
        if not args.count:
            for hit in sorted(named_hits, key=lambda h: (h["file"], h["line"])):
                print(
                    f"  {hit['file']}:{hit['line']}: {hit['function']}() accepts "
                    f"{'/'.join(hit['methods'])} and writes {'/'.join(hit['models'])} "
                    f"with no @platform_admin_required"
                )
        print(len(named_hits))
        return 0
    if args.json:
        print(json.dumps({"models": len(models), "hits": hits}, indent=2))
        return 0
    if args.count:
        print(len(hits))
        return 0
    for hit in sorted(hits, key=lambda h: (h["file"], h["line"])):
        print(
            f"  {hit['file']}:{hit['line']}: {hit['function']}() accepts "
            f"{'/'.join(hit['methods'])} and writes {'/'.join(hit['models'])} "
            f"with no @platform_admin_required"
        )
    print(f"\n{len(hits)} unguarded write route(s) on {len(models)} platform-wide model(s)")
    print(
        "Confirm the model is really platform-wide, then add @platform_admin_required -- "
        "if it is instead scoped through a foreign key to a tenant-fenced parent, that "
        f"decorator is wrong; add `# {MARKER}: <reason>` and verify the parent-ownership "
        "fence instead. See the module docstring."
    )
    print(len(hits))
    return 0


if __name__ == "__main__":
    sys.exit(main())

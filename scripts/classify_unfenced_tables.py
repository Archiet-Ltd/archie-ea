#!/usr/bin/env python
"""Classify every table in scripts/unfenced_tables.txt (analysis only, changes nothing).

Classes
-------
customer_owned          rows belong to one organisation; owner_path says how to find it
shared_reference        same rows serve every organisation and no request route writes them
tenant_root_or_identity organisations, users, roles, tokens, flags: not batched, each with a note
unused                  nothing in app/ reads, writes or mentions the model or table

Owner path rules (T-iso-02, corrected)
--------------------------------------
1. A table with its own organization_id column is customer_owned with owner_path
   "own organization_id column" (it is unprotected only because the model lacks TenantMixin).
2. Otherwise the shortest foreign-key path to a protected table that carries organization_id.
   Protected = listed in scripts/tenant_tables.txt, or fenced by TenantMixin / Hybrid mixins
   in the model code but missing from that list (reported in the .md).
   Fallbacks, each at lower confidence: a parent that has organization_id but is itself
   unfenced; then users.organization_id (never through a created_by style audit column
   without saying so). A path through business_actors is valid because that table
   carries organization_id (checked in the metadata, not assumed).
3. Metadata comes from importing the models (SQLAlchemy), so columns and foreign keys are exact.
   Tables whose module cannot be imported fall back to reading the source.

Run with the product venv from the repository root:
    venv/bin/python scripts/classify_unfenced_tables.py --write
"""

from __future__ import annotations

import argparse
import ast
import importlib
import json
import math
import os
import re
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
APP = REPO / "app"
UNFENCED_LIST = REPO / "scripts" / "unfenced_tables.txt"
TENANT_LIST = REPO / "scripts" / "tenant_tables.txt"
OUTPUT_JSON = REPO / "docs" / "tenant-isolation" / "unfenced-classification.json"
OUTPUT_MD = REPO / "docs" / "tenant-isolation" / "unfenced-classification.md"

FENCE_BASES = ("TenantMixin", "HybridCapabilityTenantMixin", "HybridTenantMixin")
APP_CAPABILITY_PARENTS = {"application_components", "business_capability", "unified_capabilities", "capabilities"}
BATCH_MIN, BATCH_MAX = 20, 40

# ---------------------------------------------------------------------------------------------
# Human decisions, each with the reason. Everything else is derived.
# ---------------------------------------------------------------------------------------------
IDENTITY_NOTES = {
    "organizations": "the tenant root: each row is an organisation; access is by membership, not by an organization_id filter",
    "users": "users carry organization_id; scoped by membership (the signed-in user's active organisation)",
    "roles": "platform role definitions shared by every organisation; not tenant data, read by role name",
    "permissions": "platform permission catalogue shared by every organisation; read by name",
    "role_permissions": "links platform roles to platform permissions; same rows for every organisation",
    "user_roles": "links users to roles; isolated through users.organization_id (scoped by membership)",
    "org_roles": "an organisation's membership roles: carries organization_id; scoped by membership",
    "pending_invitations": "invitations into an organisation: carries organization_id; scoped by membership",
    "user_sessions": "sign-in sessions: carries organization_id and user_id; scoped by the signed-in user",
    "user_preferences": "per-user settings; scoped by user_id, which belongs to one organisation through users",
    "sso_configs": "an organisation's sign-in provider settings: carries organization_id; scoped by membership",
    "oauth_clients": "OAuth client registrations; looked up by client id before any organisation is known, so isolation is by token, not by filter",
    "oauth_authorization_codes": "OAuth codes: carry organization_id and user_id; resolved by code, which fixes the organisation",
    "oauth_tokens": "OAuth access tokens: carry organization_id and user_id; resolved by token, which fixes the organisation",
    "feature_flags": "platform-level flags, evaluated per organisation but the rows are global; not tenant data",
}

# Table-level overrides: class, confidence, note (and optional extras).
OVERRIDES = {
    "vendor_organizations": dict(
        cls="shared_reference", confidence="medium",
        note="writes limited to platform administrators (PR 867); private vendors planned",
        check="on this base 17 request routes carry platform_admin_required, but import_routes.auto_create_vendors (login_required only) still creates vendor rows, "
              "and the AI chat create_vendor helper and the vendor catalogue service create/update are reached from login_required routes; confirm PR 867 is in the base before relying on the note",
    ),
}

AUDIT_COL = re.compile(r"(_by|_by_id|_by_user_id)$|^(created|updated|approved|reviewed)_by")
NOT_OWNER_COL = re.compile(r"^retired_into|^previous_|^replacement_|^merged_into|^parent_(id|process_id|domain_id)$")
FORBIDDEN_TRAVERSE = {"organizations", "users"}  # never an intermediate hop


@dataclass
class Rec:
    name: str
    model: str | None = None
    cls: str = "unused"
    owner_path: str | None = None
    reads: int = 0
    writes: int = 0
    batch: int | None = None
    confidence: str = "high"
    note: str = ""
    parent: str | None = None  # table the owner is read from (end of the foreign-key path)
    fk_owner_path: str | None = None
    fk_parent: str | None = None
    writers: list = field(default_factory=list)
    low_reason: str | None = None
    writer_check: str | None = None


# ---------------------------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------------------------
def load_lists():
    def load(path):
        out = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                out.add(line)
        return out
    return load(UNFENCED_LIST), load(TENANT_LIST)


def load_metadata():
    """Import every module that declares a table; return (tables, models, fenced_names)."""
    os.environ.setdefault("FLASK_ENV", "testing")
    import app.models  # noqa: F401
    from app import db

    for p in sorted(APP.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        try:
            s = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "__tablename__" in s or "db.Table(" in s:
            mod = ".".join(p.relative_to(REPO).with_suffix("").parts).replace(".__init__", "")
            try:
                importlib.import_module(mod)
            except BaseException:
                pass
    models: dict[str, list[str]] = defaultdict(list)
    fenced: set[str] = set()
    for mp in db.Model.registry.mappers:
        t = mp.local_table.name
        models[t].append(mp.class_.__name__)
        if any(b.__name__ in FENCE_BASES for b in mp.class_.__mro__):
            fenced.add(t)
    return db.metadata.tables, models, fenced


def ast_fallback_tables(wanted: set[str]):
    """For tables the import could not reach: read class name, organization_id and foreign keys from source."""
    info: dict[str, dict] = {}
    for p in sorted(APP.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            table, has_org, fks = None, False, []
            for stmt in node.body:
                if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                for t in targets:
                    if not isinstance(t, ast.Name):
                        continue
                    if t.id == "__tablename__" and isinstance(stmt.value, ast.Constant):
                        table = stmt.value.value
                    if t.id == "organization_id":
                        has_org = True
                    for sub in ast.walk(stmt.value) if stmt.value is not None else []:
                        if isinstance(sub, ast.Call) and "ForeignKey" in ast.unparse(sub.func) and sub.args:
                            m = re.match(r"""["']([^"'.]+)""", ast.unparse(sub.args[0]))
                            if m:
                                fks.append((t.id, m.group(1)))
            if table in wanted:
                info[table] = dict(model=node.name, org=has_org, fks=fks, path=p.relative_to(REPO).as_posix())
    return info


# ---------------------------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------------------------
READ_PATTERNS = [re.compile(p) for p in (r"\.query\b", r"select\s*\(", r"session\.get\s*\(")]
WRITE_PATTERNS = [re.compile(p) for p in (r"\.add\s*\(", r"\.delete\s*\(", r"\.update\s*\(", r"\.insert\s*\(", r"\.merge\s*\(")]
SKIP_DIRS = ("app/models/", "app/commands/", "app/_bootstrap/")


def py_files(root: Path):
    for p in sorted(root.rglob("*.py")):
        if "__pycache__" not in p.parts:
            yield p


def build_usage_index():
    """Reads and writes per capitalised name, from non-model application code (heuristic line counts)."""
    usage = defaultdict(lambda: [0, 0])
    inst = re.compile(r"\b([A-Z][A-Za-z0-9]*)\s*\(")
    word = re.compile(r"\b([A-Z][A-Za-z0-9]*)\b")
    for p in py_files(APP):
        rel = p.relative_to(REPO).as_posix()
        if any(rel.startswith(d) for d in SKIP_DIRS):
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            is_read = any(r.search(line) for r in READ_PATTERNS)
            is_write = any(r.search(line) for r in WRITE_PATTERNS)
            for m in inst.findall(line):
                usage[m][1] += 1
            if is_read:
                for m in word.findall(line):
                    usage[m][0] += 1
            elif is_write:
                for m in word.findall(line):
                    usage[m][1] += 1
    return usage


def build_mention_index(files_for_text):
    """name -> set of files (outside its definition) that mention it as a word."""
    return files_for_text


# ---------------------------------------------------------------------------------------------
# Writers: who writes a table, and are they requests, CLI, or seeders?
# ---------------------------------------------------------------------------------------------
WRITE_CALLS = {"delete", "update", "insert", "bulk_insert_mappings", "bulk_save_objects", "bulk_update_mappings"}
SQL_WRITE = re.compile(r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+[\"`]?([a-z_][a-z0-9_]*)", re.I)
ROUTE_DECOR = re.compile(r"\.(route|get|post|put|patch|delete)\(|^route\(")
CLI_DECOR = re.compile(r"\.command\(|click\.|cli\.")
SEEDER_PATH = re.compile(r"seed|bootstrap|fixture|populate|loader|_init_data|reference_data", re.I)
SEEDER_NAME = re.compile(r"^(seed|populate|load_|init_|bootstrap|install|import_reference|ensure_|create_default|setup_)", re.I)


@dataclass
class Fn:
    fid: str
    file: str
    name: str
    kind: str
    calls: set
    names: set
    inst: set
    root_writes: set
    sql_tables: set
    session_write: bool
    attr_assign: bool
    gates: str = ""


def classify_fn(rel: str, qual: str, decorators: list[str]) -> str:
    if any(ROUTE_DECOR.search(d) for d in decorators):
        return "route"
    if rel.startswith(("app/commands/", "app/cli/")) or any(CLI_DECOR.search(d) for d in decorators):
        return "cli"
    if SEEDER_PATH.search(rel) or SEEDER_NAME.search(qual.split(".")[-1]) or not rel.startswith("app/"):
        return "seeder"
    return "helper"


def index_functions() -> tuple[dict[str, Fn], dict[str, list[str]]]:
    fns: dict[str, Fn] = {}
    by_name: dict[str, list[str]] = defaultdict(list)
    roots = [APP] + sorted(REPO.glob("*.py")) + sorted((REPO / "scripts").glob("*.py"))
    files = []
    for r in roots:
        files.extend(py_files(r) if r.is_dir() else [r])
    for p in files:
        if p.name == "classify_unfenced_tables.py":
            continue
        rel = p.relative_to(REPO).as_posix()
        if "/tests/" in rel or p.name.startswith("test_") or p.name == "conftest.py":
            continue  # tests are not application writers
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue

        def visit(node, stack, deco_stack):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.ClassDef):
                    visit(child, stack + [child.name], deco_stack)
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qual = ".".join(stack + [child.name])
                    decos = [ast.unparse(d) for d in child.decorator_list]
                    fid = f"{rel}:{qual}"
                    fn = Fn(fid, rel, qual, classify_fn(rel, qual, decos), set(), set(), set(), set(), set(), False, False)
                    fn.gates = ",".join(sorted({g for d in decos for g in re.findall(r"(platform_admin_required|admin_required|org_admin_required|roles_required|login_required|csrf_exempt|require_api_key|api_key_required)", d)}))
                    walk_body(child, fn)
                    fns[fid] = fn
                    by_name[child.name].append(fid)
                    visit(child, stack + [child.name], deco_stack)
                else:
                    visit(child, stack, deco_stack)

        def walk_body(fnode, fn):
            stack = list(ast.iter_child_nodes(fnode))
            while stack:
                n = stack.pop()
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue  # nested defs are their own functions
                stack.extend(ast.iter_child_nodes(n))
                if isinstance(n, ast.Name):
                    fn.names.add(n.id)
                if isinstance(n, ast.Assign):
                    if any(isinstance(t, ast.Attribute) for t in n.targets):
                        fn.attr_assign = True
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) < 4000:
                    for m in SQL_WRITE.finditer(n.value):
                        fn.sql_tables.add(m.group(1).lower())
                if isinstance(n, ast.Call):
                    f = n.func
                    if isinstance(f, ast.Name):
                        fn.calls.add(f.id)
                        fn.inst.add(f.id)
                        if f.id in ("insert", "update", "delete") and n.args and isinstance(n.args[0], ast.Name):
                            fn.root_writes.add(n.args[0].id)
                    elif isinstance(f, ast.Attribute):
                        fn.calls.add(f.attr)
                        if f.attr in ("add", "add_all", "delete", "merge", "commit", "flush"):
                            fn.session_write = True
                        if f.attr in WRITE_CALLS:
                            root = f
                            while isinstance(root, (ast.Attribute, ast.Call, ast.Subscript)):
                                root = root.value if isinstance(root, (ast.Attribute, ast.Subscript)) else root.func
                            if isinstance(root, ast.Name):
                                fn.root_writes.add(root.id)
                            if f.attr in ("insert", "update", "delete") and isinstance(f.value, ast.Attribute) and f.value.attr == "__table__":
                                if isinstance(f.value.value, ast.Name):
                                    fn.root_writes.add(f.value.value.id)

        visit(tree, [], [])
        # module level code (e.g. scripts)
        modfn = Fn(f"{rel}:<module>", rel, "<module>", "seeder" if not rel.startswith("app/") else "helper", set(), set(), set(), set(), set(), False, False)
        walk_body(tree, modfn)
        fns[modfn.fid] = modfn
    return fns, by_name


def writers_of(table: str, model_names: list[str], fns: dict[str, Fn]) -> list[dict]:
    out = []
    mn = set(model_names)
    for fn in fns.values():
        how = None
        if mn & fn.inst:
            how = "creates rows"
        elif mn & fn.root_writes:
            how = "bulk update/delete"
        elif table in fn.sql_tables:
            how = "raw SQL write"
        elif (mn & fn.names) and fn.session_write and fn.attr_assign:
            how = "edits rows (heuristic)"
        if how:
            out.append(dict(site=fn.fid, kind=fn.kind, how=how, **({"gates": fn.gates} if fn.kind == "route" else {})))
    return sorted(out, key=lambda d: d["site"])


def find_aliases(model_names: set[str]) -> dict[str, set[str]]:
    """Other names a model is bound to in application code (X = Model, import Model as X)."""
    out: dict[str, set[str]] = defaultdict(set)
    for p in py_files(APP):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Name) and n.value.id in model_names:
                for t in n.targets:
                    if isinstance(t, ast.Name) and t.id != n.value.id:
                        out[n.value.id].add(t.id)
            elif isinstance(n, ast.ImportFrom):
                for a in n.names:
                    if a.asname and a.name in model_names:
                        out[a.name].add(a.asname)
    return out


def route_reach(fid: str, fns: dict[str, Fn], by_name: dict[str, list[str]], callers: dict[str, list[str]],
                name_users: dict[str, list[str]]) -> list[str]:
    """Request routes that reach a helper by name matching, up to 4 levels (an over-approximation).

    A method is reached by callers of its name, and by any function that mentions its class.
    """
    seen, frontier, routes = {fid}, [fid], []
    for _ in range(4):
        nxt = []
        for f in frontier:
            qual = fns[f].name
            simple = qual.split(".")[-1]
            cands = []
            if len(by_name.get(simple, [])) <= 6 and not simple.startswith("__"):
                cands += callers.get(simple, [])
            if "." in qual:
                cands += name_users.get(qual.split(".")[0], [])
            for c in cands:
                if c in seen:
                    continue
                seen.add(c)
                if fns[c].kind == "route":
                    routes.append(c)
                elif fns[c].kind == "helper":
                    nxt.append(c)
        frontier = nxt
    return sorted(set(routes))


# ---------------------------------------------------------------------------------------------
# Foreign-key paths
# ---------------------------------------------------------------------------------------------
def find_path(start: str, edges: dict[str, list[tuple[str, str]]], is_terminal, allow_audit: bool, max_depth: int = 6):
    """Shortest path start -> terminal. Returns list of (col, table) hops or None."""
    q = deque([(start, [])])
    seen = {start}
    while q:
        cur, path = q.popleft()
        if len(path) >= max_depth:
            continue
        cand = sorted(edges.get(cur, []), key=lambda e: (bool(AUDIT_COL.search(e[0])), e[0], e[1]))
        for col, ref in cand:
            if ref in seen or ref == cur:
                continue
            if NOT_OWNER_COL.search(col):
                continue
            if not allow_audit and AUDIT_COL.search(col):
                continue
            hop = path + [(col, ref)]
            if is_terminal(ref):
                return hop
            if ref in FORBIDDEN_TRAVERSE:
                continue
            seen.add(ref)
            q.append((ref, hop))
    return None


def path_string(hops):
    parts = [hops[0][0]]
    for i, (_, tbl) in enumerate(hops[:-1]):
        parts.append(f"{tbl}.{hops[i + 1][0]}")
    parts.append(f"{hops[-1][1]}.organization_id")
    return " -> ".join(parts)


# ---------------------------------------------------------------------------------------------
# Main classification
# ---------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    unfenced, tenant_list = load_lists()
    tables, models, fenced_meta = load_metadata()
    missing = {t for t in unfenced if t not in tables}
    fb = ast_fallback_tables(missing)

    # Protected tables that carry organization_id: the list, plus fenced models not in the list
    has_org = {t for t, tb in tables.items() if "organization_id" in tb.c}
    for t, d in fb.items():
        if d["org"]:
            has_org.add(t)
    protected = {t for t in tenant_list if t in has_org}
    extra_fenced = {t for t in fenced_meta if t in has_org and t not in tenant_list and t not in unfenced}
    protected_all = protected | extra_fenced

    edges: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for t, tb in tables.items():
        seen = set()
        for fk in tb.foreign_keys:
            e = (fk.parent.name, fk.column.table.name)
            if e not in seen:
                seen.add(e)
                edges[t].append(e)
    for t, d in fb.items():
        edges[t] = sorted(set(d["fks"]))

    usage = build_usage_index()
    fns, by_name = index_functions()
    callers: dict[str, list[str]] = defaultdict(list)
    name_users: dict[str, list[str]] = defaultdict(list)
    for f in fns.values():
        for c in f.calls:
            callers[c].append(f.fid)
        for n in f.names:
            name_users[n].append(f.fid)
    aliases = find_aliases(set(n for ms in models.values() for n in ms) | {d['model'] for d in fb.values()})

    # mentions of each model or table outside the file that defines it
    app_text = {p.relative_to(REPO).as_posix(): p.read_text(encoding="utf-8", errors="replace") for p in py_files(APP)}
    defining_files: dict[str, set[str]] = defaultdict(set)
    for rel, text in app_text.items():
        for m in re.finditer(r"__tablename__\s*=\s*[\"']([a-z0-9_]+)[\"']", text):
            defining_files[m.group(1)].add(rel)
        for m in re.finditer(r"db\.Table\(\s*[\"']([a-z0-9_]+)[\"']", text):
            defining_files[m.group(1)].add(rel)

    token_files: dict[str, set[str]] = defaultdict(set)
    tok = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    for rel, text in app_text.items():
        if rel.startswith("app/models/"):
            continue
        for t in set(tok.findall(text)):
            token_files[t].add(rel)

    def mentions(table, names):
        hits = set()
        for n in list(names) + [table]:
            hits |= token_files.get(n, set())
        return sorted(h for h in hits if h not in defining_files.get(table, ()))

    recs: list[Rec] = []
    for name in sorted(unfenced):
        r = Rec(name=name)
        mnames = list(models.get(name, []))
        if name in fb:
            mnames = mnames or [fb[name]["model"]]
        r.model = ", ".join(mnames) if mnames else None
        r.reads = sum(usage.get(m, [0, 0])[0] for m in mnames)
        r.writes = sum(usage.get(m, [0, 0])[1] for m in mnames)
        own_org = name in has_org

        # fk-derived parent (also computed for tables with their own column, for batching)
        def terminal_p1(ref):
            return ref in protected_all and ref != "users"

        def terminal_p3(ref):
            return ref in has_org and ref not in ("users", "organizations") and ref in unfenced

        hops = find_path(name, edges, terminal_p1, allow_audit=False) or find_path(name, edges, terminal_p1, allow_audit=True)
        level = "protected"
        if hops is None:
            hops = find_path(name, edges, terminal_p3, allow_audit=False)
            level = "unfenced_parent"
        if hops is None:
            hops = find_path(name, edges, lambda x: x == "users", allow_audit=False)
            level = "users"
        if hops is None:
            hops = find_path(name, edges, lambda x: x == "users", allow_audit=True)
            level = "users_audit"
        if hops:
            r.fk_owner_path = path_string(hops)
            r.fk_parent = hops[-1][1]
            if level == "protected" and any(AUDIT_COL.search(c) for c, _ in hops):
                level = "protected_audit"

        wr = writers_of(name, mnames + sorted(a for m in mnames for a in aliases.get(m, ())), fns)
        for w in wr:
            if w["kind"] == "helper":
                w["via_routes"] = [x for x in route_reach(w["site"], fns, by_name, callers, name_users)][:4]
        r.writers = wr
        ment = mentions(name, mnames)

        # ---- identity / root
        if name in IDENTITY_NOTES:
            r.cls = "tenant_root_or_identity"
            r.note = IDENTITY_NOTES[name]
            r.owner_path = "own organization_id column" if own_org else None
            recs.append(r)
            continue

        if name in OVERRIDES:
            o = OVERRIDES[name]
            r.cls, r.confidence, r.note = o["cls"], o["confidence"], o["note"]
            r.writer_check = o.get("check")
            recs.append(r)
            continue

        # ---- unused
        if r.reads == 0 and r.writes == 0 and not ment and not wr:
            r.cls = "unused"
            r.confidence = "high" if mnames else "medium"
            r.note = ("model exists" if mnames else "no model class found") + "; nothing in app/ reads, writes or mentions it"
            if own_org:
                r.owner_path = "own organization_id column"
                r.note += "; carries organization_id, so add TenantMixin if it is ever used"
            elif r.fk_owner_path:
                r.owner_path = r.fk_owner_path
                r.note += f"; would derive its owner by {r.fk_owner_path}"
            recs.append(r)
            continue

        reaches = [w for w in wr if w["kind"] == "route" or (w["kind"] == "helper" and w.get("via_routes"))]
        request_writer = [w for w in reaches if "heuristic" not in w["how"]]
        soft_writer = [w for w in reaches if "heuristic" in w["how"]]
        direct_route = [w for w in request_writer if w["kind"] == "route"]

        # ---- own column
        if own_org:
            r.cls = "customer_owned"
            r.owner_path = "own organization_id column"
            r.parent = r.fk_parent if r.fk_parent in APP_CAPABILITY_PARENTS else "own column"
            r.confidence = "high"
            r.note = "has its own organization_id column; unprotected only because the model lacks TenantMixin"
            if r.reads == 0 and r.writes == 0:
                r.confidence = "medium"
                r.note += "; usage found only as references, not direct queries"
            recs.append(r)
            continue

        # ---- fk path
        if hops and level in ("protected", "protected_audit"):
            r.cls = "customer_owned"
            r.owner_path = r.fk_owner_path
            r.parent = r.fk_parent
            n = len(hops)
            r.confidence = "high" if n == 1 else "medium"
            r.note = f"owner derived through {n} foreign key hop{'s' if n > 1 else ''}"
            if r.fk_parent in extra_fenced and r.fk_parent not in tenant_list:
                r.note += f"; {r.fk_parent} is fenced in code but missing from tenant_tables.txt"
            if level == "protected_audit":
                r.confidence = "low"
                r.low_reason = "only reaches an owner through an audit column (created_by and similar)"
            if n > 1:
                r.note += " (multi-hop; confirm the intermediate links are never null)"
            recs.append(r)
            continue
        if hops and level == "unfenced_parent":
            r.cls = "customer_owned"
            r.owner_path = r.fk_owner_path
            r.parent = r.fk_parent
            r.confidence = "medium"
            r.note = f"owner read from {r.fk_parent}, which has organization_id but is itself unfenced; fence it first"
            recs.append(r)
            continue

        # ---- no protected link: shared reference candidate or users only
        user_only = hops and level in ("users", "users_audit")
        if user_only and level == "users" and (direct_route or request_writer or r.writes):
            r.cls = "customer_owned"
            r.owner_path = r.fk_owner_path
            r.parent = "users"
            r.confidence = "medium"
            r.note = "owner is the user's organisation (users.organization_id); users are unfenced, scoped by membership"
            recs.append(r)
            continue

        if request_writer:
            r.cls = "customer_owned"
            r.parent = "users" if user_only else None
            r.owner_path = r.fk_owner_path if user_only else None
            r.confidence = "low"
            sites = "; ".join(
                w["site"] + (" (a request route)" if w["kind"] == "route" else f" (helper, reached by name match from {w['via_routes'][0]})")
                for w in request_writer[:3])
            r.low_reason = f"written on request paths: {sites}; but no organization_id and no foreign key to an owner" + (
                "; only link is an audit user column" if user_only else "")
            r.note = r.low_reason
            recs.append(r)
            continue

        r.cls = "shared_reference"
        r.confidence = "medium" if (r.writes or wr) else "high"
        if soft_writer:
            r.confidence = "low"
            r.low_reason = ("no organization_id and no owner link; request code that may edit rows (it references the model and sets attributes): "
                            + ", ".join(w["site"] for w in soft_writer[:3]) + "; check whether customers can edit these rows")
        if user_only:
            r.note = "no organization_id and no link to an owner except an audit user column; no request route writes it"
        else:
            r.note = "no organization_id and no foreign key to an owner; no request route writes it"
        if wr:
            r.note += "; writers: " + ", ".join(f"{w['site']} ({w['kind']})" for w in wr[:4])
            if r.confidence == "medium":
                r.low_reason = None
        recs.append(r)

    # Manual verdicts after reading the writers (see the .md section "Shared reference: writer check")
    apply_manual_verdicts(recs)

    assign_batches(recs)
    write_outputs(recs, extra_fenced, tenant_list, args)
    return 0


# Verdicts reached by reading the writers and callers by hand (the call-name trace cannot see through
# instances and aliases). Each note says what was read.
MANUAL = {
    "solution_tco_items": dict(batch=1, parent="solutions",
        owner_path="solution_id -> solutions.organization_id",
        note="an overview CFO figure summed this table across organisations; any write makes it live"),
    "audit_events": dict(cls="customer_owned", confidence="low", parent="users",
        owner_path="user_id (no foreign key declared) -> users.organization_id",
        note="written by the audit logger on request paths; user_id is a plain integer, system events have none",
        low_reason="written by the audit logger during requests and holds user email, resource and old/new values, but has no organization_id; user_id is a plain integer with no foreign key and system events carry no user"),
    "batch_job_errors": dict(cls="customer_owned", confidence="low", parent="users",
        owner_path="batch_job_id -> batch_jobs.created_by_id -> users.organization_id",
        note="written by the import batch service, which import routes and import_history_routes call",
        low_reason="written from request paths (import routes); the owner is reachable only through the creator of the job (an audit column)"),
    "codegen_scenario_results": dict(cls="customer_owned", confidence="medium", parent="solutions",
        owner_path="solution_id (no foreign key declared) -> solutions.organization_id",
        note="written by POST .../codegen/test/record-result (login_required) through ScenarioTracker.record; solution_id is a plain integer column"),
    "generation_event": dict(cls="customer_owned", confidence="low", parent=None, owner_path=None,
        note="autogen event log written by autogen_service.log_event, used by the autogen routes and the ArchiMate LLM service",
        low_reason="written from request paths; points at its target by target_type and target_id only, so there is no organization_id and no foreign key to derive an owner from; needs a column"),
    "llm_metrics": dict(cls="customer_owned", confidence="low", parent=None, owner_path=None,
        note="provider latency, throughput and cost samples written by the LLM router on each call",
        low_reason="written during requests with free-form labels (JSON) and no organization_id; the values look like platform telemetry, so a person should decide between adding a column and treating it as platform data"),
    "codegen_system_boundaries": dict(cls="customer_owned", confidence="low", parent="users",
        owner_path="created_by_id -> users.organization_id",
        note="created by boundary_routes (SystemBoundary is an alias of CodegenSystemBoundary); solutions link to it through codegen_system_boundary_solutions",
        low_reason="created from a request route; the only direct link is the creator (an audit column); the owner could instead be read from codegen_system_boundary_solutions.solution_id"),
    "vendor_concentration_analysis": dict(cls="customer_owned", confidence="low", parent=None, owner_path=None,
        note="aggregates the applications that use a vendor's products, then stores one row per vendor",
        low_reason="the row is computed from application components (customer data) but keyed only by vendor_organization_id, so two organisations would share one row; needs an organization_id"),
    "rag_queries": dict(cls="unused", confidence="medium", parent=None, owner_path=None,
        note="written only by RAGEngine.query, which nothing calls; the live chat path uses cross_entity_search, which writes nothing"),
    "rag_results": dict(cls="unused", confidence="medium", parent=None, owner_path=None,
        note="written only by RAGEngine._save_query_results via RAGEngine.query, which nothing calls"),
    "rag_metrics": dict(cls="unused", confidence="medium", parent=None, owner_path=None,
        note="written only by RAGEngine._record_metrics via RAGEngine.query, which nothing calls"),
    "systems": dict(cls="unused", confidence="medium", parent=None, owner_path=None,
        note="a stub model 'required for FK resolution' (card_systems points at it); no import or query of System outside the model file; the read and write counts come from the common word System"),
    "arb_waiver_expiry_checkpoints": dict(cls="shared_reference", confidence="low", parent=None, owner_path=None,
        note="checkpoint rows for the scheduled waiver-expiry job, written by the job (app bootstrap scheduler) and the process_arb_waiver_expiries CLI, not by requests",
        low_reason="not reference data in the strict sense: it is platform job state with no organisation column; check whether the checkpoints record organisation-specific waivers"),
    "exchange_rates": dict(cls="shared_reference", confidence="high",
        note="record_exchange_rate raises PermissionError unless the actor is a platform administrator and nothing in app/ calls it"),
    "service_incidents": dict(cls="shared_reference", confidence="high",
        note="platform status incidents; open_incident and resolve_incident are called only from the service_incident CLI commands"),
    "scoring_configs": dict(cls="shared_reference", confidence="medium",
        note="platform scoring weights; seed_defaults is a seeder and update_config has no caller in app/"),
    "taxonomy_mappings": dict(cls="shared_reference", confidence="medium",
        note="vendor taxonomy mappings; create_taxonomy_mapping and validate_mapping have no caller in app/"),
    "vendor_product_families": dict(cls="shared_reference", confidence="medium",
        note="vendor product catalogue; written by the end-of-life sync (sync_all, no caller in app/) and the standardize_vendor_domains CLI"),
    "regulatory_frameworks": dict(cls="shared_reference", confidence="medium",
        note="framework catalogue (for example SOC 2 and ISO); only seeders create it (seed_manufacturing_frameworks, seed_security_demo)"),
    "technical_capabilities": dict(cls="shared_reference", confidence="high",
        note="the ACM technical capability catalogue; the update and delete routes carry platform_admin_required, creation is by the seed_capabilities CLI and seeders; the foreign key to unified_capabilities is a retirement redirect, not an owner; the helpers the trace lists as editing rows write application-to-capability mappings, not this table"),
}


def apply_manual_verdicts(recs):
    for r in recs:
        v = MANUAL.get(r.name)
        if not v:
            continue
        r.cls = v.get("cls", r.cls)
        r.confidence = v.get("confidence", r.confidence)
        r.note = v["note"]
        if "low_reason" in v:
            r.low_reason = v["low_reason"]
        if "owner_path" in v:
            r.owner_path = v["owner_path"]
        if "parent" in v:
            r.parent = v["parent"]


# ---------------------------------------------------------------------------------------------
# Batches
# ---------------------------------------------------------------------------------------------
def assign_batches(recs):
    owned = [r for r in recs if r.cls == "customer_owned" and r.owner_path]
    for r in recs:
        r.batch = None
    groups: dict[str, list[Rec]] = defaultdict(list)
    for r in owned:
        key = "APPCAP" if r.parent in APP_CAPABILITY_PARENTS else (r.parent or r.name)
        groups[key].append(r)

    def use(r):
        return r.reads + r.writes

    def is_map(r):
        return bool(re.search(r"mapping|coverage", r.name))

    ordered_groups = []
    if "APPCAP" in groups:
        g = sorted(groups.pop("APPCAP"), key=lambda r: (not is_map(r), -use(r), r.name))
        ordered_groups.append(g)
    for key, g in sorted(groups.items(), key=lambda kv: (-sum(use(r) for r in kv[1]), kv[0])):
        ordered_groups.append(sorted(g, key=lambda r: (-use(r), r.name)))

    # split large groups into balanced units, then pack units into batches of 20-40
    units = []
    for g in ordered_groups:
        if len(g) > BATCH_MAX:
            k = math.ceil(len(g) / 30)
            size = math.ceil(len(g) / k)
            units.extend(g[i:i + size] for i in range(0, len(g), size))
        else:
            units.append(g)
    batches: list[list[Rec]] = [[]]
    for u in units:
        cur = batches[-1]
        if len(cur) + len(u) <= BATCH_MAX:
            cur.extend(u)
            if len(cur) >= 30 and u is not units[-1]:
                batches.append([])
        elif len(cur) >= BATCH_MIN:
            batches.append(list(u))
        else:
            room = BATCH_MAX - len(cur)
            cur.extend(u[:room])
            batches.append(list(u[room:]))
    batches = [b for b in batches if b]
    if len(batches) > 1 and len(batches[-1]) < BATCH_MIN:
        last, prev = batches[-1], batches[-2]
        if len(prev) + len(last) <= BATCH_MAX:
            prev.extend(last)
            batches.pop()
        else:
            need = BATCH_MIN - len(last)
            moved = prev[-need:]
            del prev[-need:]
            last[0:0] = moved
    for i, b in enumerate(batches, 1):
        for r in b:
            r.batch = i
    # A MANUAL entry may pin a table to a batch (a table already summed across organisations).
    for r in recs:
        pin = MANUAL.get(r.name, {}).get("batch")
        if pin and r.batch and r.batch != pin:
            r.batch = pin


# ---------------------------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------------------------
def write_outputs(recs, extra_fenced, tenant_list, args):
    counts = defaultdict(int)
    for r in recs:
        counts[r.cls] += 1
    total = sum(counts.values())
    assert total == len(recs) and len({r.name for r in recs}) == len(recs)
    print(f"counts {dict(counts)} total {total}", file=sys.stderr)

    rows = []
    for r in recs:
        row = dict(name=r.name, model=r.model, **{"class": r.cls}, owner_path=r.owner_path, reads=r.reads,
                   writes=r.writes, batch=r.batch, confidence=r.confidence, note=r.note)
        if r.parent:
            row["parent"] = r.parent
        if r.cls in ("shared_reference",) or r.name in ("vendor_organizations", "technical_capabilities"):
            row["writers"] = r.writers
        if r.low_reason:
            row["low_confidence_reason"] = r.low_reason
        if r.writer_check:
            row["writer_check"] = r.writer_check
        rows.append(row)
    if not args.write:
        print(json.dumps(rows, indent=2))
        return
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")

    md = ["# Unprotected tables, classified", "",
          "Produced by `scripts/classify_unfenced_tables.py` from the model metadata. Analysis only: no change to app/, migrations or the table lists.", "",
          "## Counts per class", "", "| Class | Tables |", "|---|---|"]
    for c in ("customer_owned", "shared_reference", "tenant_root_or_identity", "unused"):
        md.append(f"| {c} | {counts.get(c, 0)} |")
    md.append(f"| **total** | **{total}** |")
    md.append("")
    for r in recs:
        if r.confidence == "low" and not r.low_reason:
            r.low_reason = r.note
    low = [r for r in recs if r.confidence == "low"]
    med = [r for r in recs if r.confidence == "medium"]
    md += [f"Low-confidence calls: {len(low)}. Medium-confidence calls: {len(med)}.", ""]
    if extra_fenced:
        md += ["Protected parents used as owners that are fenced in code but missing from `scripts/tenant_tables.txt`: "
               + ", ".join(sorted(extra_fenced)) + ". Hybrid mixins leave `organization_id` nullable, so a null there means a shared row.", ""]

    md += ["## Tenant root and identity (not batched)", ""]
    for r in recs:
        if r.cls == "tenant_root_or_identity":
            md.append(f"- **{r.name}**: {r.note}")
    md.append("")

    md += ["## Shared reference: writer check", ""]
    for r in recs:
        if r.cls == "shared_reference" or r.name in ("vendor_organizations", "technical_capabilities"):
            md.append(f"- **{r.name}** ({r.model or 'no model'}), reads {r.reads}, writes {r.writes}: {r.note}")
            shown = r.writers
            if len(shown) > 8:
                routes = [w for w in shown if w["kind"] == "route"]
                ungated = [w for w in routes if "platform_admin_required" not in w.get("gates", "") and "heuristic" not in w["how"]]
                md.append(f"  - {len(shown)} writer sites: {len(routes)} request routes ({sum(1 for w in routes if 'platform_admin_required' in w.get('gates', ''))} with platform_admin_required, {len(ungated)} creating or deleting rows without it), "
                          f"{sum(1 for w in shown if w['kind'] in ('cli', 'seeder'))} seeders or CLI, {sum(1 for w in shown if w['kind'] == 'helper')} helpers")
                if r.writer_check:
                    md.append(f"  - check: {r.writer_check}")
                md.append("  - 'edits rows' sites are a heuristic (the function names the model and sets attributes) and are counted but not listed")
                for w in ungated:
                    md.append(f"  - route without platform_admin_required: {w['site']} ({w['how']}); decorators: {w.get('gates') or 'none'}")
                shown = []
            for w in shown:
                via = f"; reached from routes {', '.join(w['via_routes'])}" if w.get("via_routes") else ""
                gate = f"; decorators: {w['gates']}" if w.get("gates") else ""
                md.append(f"  - writer {w['site']} ({w['kind']}, {w['how']}){via}{gate}")
            if not r.writers:
                md.append("  - no writer found in app/")
    md.append("")

    md += ["## Batches (customer-owned, most used first)", "",
           "Batch 1 and the batches that follow it hold the application and capability tables (owner read from application_components or the capability tables), mapping and coverage tables first. "
           "The remaining parents follow, the most read and written first. A batch holds 20 to 40 tables; a large parent is split, so usage falls within a parent but a small parent can fill the end of a batch.", ""]
    by_batch = defaultdict(list)
    for r in recs:
        if r.batch:
            by_batch[r.batch].append(r)
    for b in sorted(by_batch):
        items = by_batch[b]
        md.append(f"### Batch {b} ({len(items)} tables, {sum(r.reads + r.writes for r in items)} reads+writes)")
        md.append("")
        for r in items:
            md.append(f"- **{r.name}** ({r.model or 'no model'}) reads {r.reads}, writes {r.writes}; owner path: {r.owner_path}; {r.confidence}")
        md.append("")
    nob = [r for r in recs if r.cls == "customer_owned" and not r.batch]
    md += ["### Customer-owned, not batched (no derivable owner; a person decides)", ""]
    for r in nob:
        md.append(f"- **{r.name}** ({r.model or 'no model'}) reads {r.reads}, writes {r.writes}: {r.note}")
    md.append("")

    md += ["## Low-confidence calls to check", ""]
    for r in low:
        md.append(f"- **{r.name}** ({r.cls}): {r.low_reason or r.note}")
    md.append("")
    md += ["## Unused (product-manager decides)", ""]
    for r in recs:
        if r.cls == "unused":
            md.append(f"- {r.name} ({r.model or 'no model'})" + (f": {r.owner_path}" if r.owner_path else ""))
    OUTPUT_MD.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"written {OUTPUT_JSON} and {OUTPUT_MD}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())

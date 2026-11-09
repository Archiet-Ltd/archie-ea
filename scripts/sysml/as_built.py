#!/usr/bin/env python3
"""Generate the as-built part of the system model from the code: what is there, not what was meant.

    python scripts/sysml/as_built.py --root . --out model/as-built      # write the files
    python scripts/sysml/as_built.py --root <other checkout> --out <folder>

Reads (model/scope.json says where to look; the code decides the facts):
  - every Flask route under app/ (blueprint, URL rule, methods, handler, file, line);
  - the places a stored field gets its allowed values (a form's select, an AI tool's enum, a template's field);
  - the forms on the scoped screens and the route each form's Save event calls.
Writes a SysML v2 package (model/as-built/<slice>.sysml) and an interaction model in JSON that follows the shape of
the Interaction Flow Modeling Language (model/as-built/<slice>.ifml.json, schema in model/schema/ifml.schema.json).
Deterministic: the same tree always gives byte-identical files, so a CI run can fail when the committed copy is stale.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROUTE_VERBS = {"route", "get", "post", "put", "delete", "patch"}


# ---------------------------------------------------------------------------------------------- routes
def _py_files(root: Path):
    for p in sorted((root / "app").rglob("*.py")):
        if "__pycache__" in p.parts or "/tests/" in p.as_posix():
            continue
        yield p


def _const(node):
    return node.value if isinstance(node, ast.Constant) else None


def route_index(root: Path) -> list[dict]:
    """Every route handler under app/: file, line, handler, blueprint, rule, methods, endpoint."""
    blueprint_names: dict[str, str] = {}  # variable name -> blueprint name, first seen (used only for imported names)
    per_file: dict[Path, dict[str, str]] = {}
    parsed = []
    for p in _py_files(root):
        text = p.read_text(encoding="utf-8", errors="replace")
        if ".route(" not in text and "Blueprint(" not in text and not re.search(r"\.(get|post|put|delete|patch)\(", text):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        parsed.append((p, tree))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                fn = node.value.func
                if (getattr(fn, "id", None) or getattr(fn, "attr", None)) == "Blueprint" and node.value.args:
                    name = _const(node.value.args[0])
                    for t in node.targets:
                        if isinstance(t, ast.Name) and name:
                            blueprint_names.setdefault(t.id, name)
                            per_file.setdefault(p, {})[t.id] = name
    routes = []
    for p, tree in parsed:
        rel = p.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for d in node.decorator_list:
                if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in ROUTE_VERBS):
                    continue
                if not d.args or _const(d.args[0]) is None or not isinstance(d.func.value, ast.Name):
                    continue
                methods = ["GET"]
                if d.func.attr != "route":
                    methods = [d.func.attr.upper()]
                for kw in d.keywords:
                    if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                        methods = sorted(str(_const(e)).upper() for e in kw.value.elts if _const(e))
                bp_var = d.func.value.id
                bp = per_file.get(p, {}).get(bp_var) or blueprint_names.get(bp_var, bp_var)
                routes.append({
                    "file": rel, "line": node.lineno, "handler": node.name, "blueprint": bp,
                    "rule": _const(d.args[0]), "methods": methods, "endpoint": f"{bp}.{node.name}",
                })
    routes.sort(key=lambda r: (r["file"], r["line"], r["rule"]))
    return routes


def rule_tail(rule: str) -> str:
    return rule.rstrip("/").rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------------------- vocabularies
def _module_file(root: Path, module: str) -> str | None:
    base = module.replace(".", "/")
    for cand in (base + ".py", base + "/__init__.py"):
        if (root / cand).exists():
            return cand
    return None


def _imports(tree) -> dict[str, str]:
    out = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            for a in n.names:
                out[a.asname or a.name] = n.module
    return out


def _values_of(node) -> list[str] | None:
    if isinstance(node, (ast.List, ast.Tuple)):
        vals = []
        for e in node.elts:
            if isinstance(e, (ast.Tuple, ast.List)) and e.elts and _const(e.elts[0]) is not None:
                vals.append(str(_const(e.elts[0])))
            elif _const(e) is not None:
                vals.append(str(_const(e)))
            else:
                return None
        return vals
    return None


def _name_in(node) -> str | None:
    """The imported name a list is built from: `NAME` or `list(NAME)`."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("list", "tuple", "sorted") and node.args:
        return _name_in(node.args[0])
    return None


def _origin(root: Path, tree, node, here: str) -> tuple[str, list[str] | None]:
    vals = _values_of(node)
    if vals is not None:
        return here + " (written out in place)", vals
    name = _name_in(node)
    mod = _imports(tree).get(name or "")
    if mod:
        f = _module_file(root, mod)
        if f:
            return f, None
    return here + " (not resolved)", None


def _dget(d: ast.Dict, key: str):
    for k, v in zip(d.keys, d.values):
        if _const(k) == key:
            return v
    return None


def source_form_select(root: Path, spec: dict) -> dict:
    f = spec["file"]
    tree = ast.parse((root / f).read_text(encoding="utf-8"))
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef) and cls.name == spec["class"]:
            for st in cls.body:
                if (isinstance(st, ast.Assign) and isinstance(st.targets[0], ast.Name) and st.targets[0].id == spec["field"]
                        and isinstance(st.value, ast.Call)):
                    kind = getattr(st.value.func, "id", "")
                    for kw in st.value.keywords:
                        if kw.arg == "choices":
                            o, v = _origin(root, tree, kw.value, f)
                            return {"file": f, "line": st.lineno, "origin": o, "values": v, "note": kind}
                    return {"file": f, "line": st.lineno, "origin": f + " (free text)", "values": None, "note": kind}
    return {"file": f, "line": 0, "origin": f + " (field not found)", "values": None, "note": ""}


def source_tool_enum(root: Path, spec: dict) -> dict:
    f = spec["file"]
    tree = ast.parse((root / f).read_text(encoding="utf-8"))
    for d in ast.walk(tree):
        if isinstance(d, ast.Dict) and _const(_dget(d, "route") or ast.Constant(None)) == spec["tool_route"]:
            params = _dget(d, "parameters")
            props = _dget(params, "properties") if isinstance(params, ast.Dict) else None
            p = _dget(props, spec["param"]) if isinstance(props, ast.Dict) else None
            en = _dget(p, "enum") if isinstance(p, ast.Dict) else None
            if en is not None:
                o, v = _origin(root, tree, en, f)
                return {"file": f, "line": en.lineno, "origin": o, "values": v, "note": "enum"}
    return {"file": f, "line": 0, "origin": f + " (tool not found)", "values": None, "note": ""}


def source_template_field(root: Path, spec: dict) -> dict:
    f = spec["file"]
    lines = (root / f).read_text(encoding="utf-8").splitlines()
    name = spec["name"]
    for i, ln in enumerate(lines, 1):
        if re.search(r"edit_row\([^)]*['\"]%s['\"]" % re.escape(name), ln):
            m = re.search(r"edit_row\([^)]*['\"]%s['\"][^,]*,[^,]*,\s*(\d+)" % re.escape(name), ln)
            return {"file": f, "line": i, "origin": f + " (free text)", "values": None,
                    "note": "text box" + (f", up to {m.group(1)} characters" if m else "")}
        if "native_select" in ln and ("name='%s'" % name in ln or 'name="%s"' % name in ln):
            for p in _py_files(root):
                t = p.read_text(encoding="utf-8", errors="replace")
                if re.search(r"^def status_options\(", t, re.M):
                    return {"file": f, "line": i, "origin": p.relative_to(root).as_posix(), "values": None, "note": "select"}
            return {"file": f, "line": i, "origin": f + " (options not resolved)", "values": None, "note": "select"}
    return {"file": f, "line": 0, "origin": f + " (field not found)", "values": None, "note": ""}


SOURCE_KINDS = {"form-select": source_form_select, "tool-enum": source_tool_enum, "template-field": source_template_field}


# ----------------------------------------------------------------------------------------- interaction
FORM_RE = re.compile(r"<form\b(?P<attrs>[^>]*)>", re.S)
URL_FOR_RE = re.compile(r"url_for\(\s*['\"]([\w.]+)['\"]")


def screen_forms(root: Path, template: str) -> list[dict]:
    text = (root / template).read_text(encoding="utf-8")
    forms = []
    for m in FORM_RE.finditer(text):
        attrs = m.group("attrs")
        action = re.search(r"action\s*=\s*\"([^\"]*)\"", attrs, re.S) or re.search(r"action\s*=\s*'([^']*)'", attrs, re.S)
        if not action:
            continue
        u = URL_FOR_RE.search(action.group(1))
        fid = re.search(r"\bid\s*=\s*[\"']([^\"']+)", attrs)
        meth = re.search(r"method\s*=\s*[\"'](\w+)", attrs)
        line = text.count("\n", 0, m.start()) + 1
        forms.append({
            "id": fid.group(1) if fid else f"form-line-{line}",
            "line": line,
            "method": (meth.group(1) if meth else "GET").upper(),
            "endpoint": u.group(1) if u else None,
            "rawAction": action.group(1).strip(),
        })
    return forms


# ---------------------------------------------------------------------------------------------- emit
def q(s) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def generate(root: Path, scope: dict) -> tuple[str, str]:
    routes = route_index(root)
    by_endpoint = {r["endpoint"]: r for r in routes}
    tails = set(scope["routeTails"])
    scoped_routes = [r for r in routes if rule_tail(r["rule"]) in tails]

    vocab = []
    for v in scope.get("vocabularies", []):
        for spec in v["sources"]:
            s = SOURCE_KINDS[spec["kind"]](root, spec)
            s.update({"column": v["column"], "kind": spec["kind"]})
            vocab.append(s)

    modules: dict[str, list[dict]] = {}
    for r in scoped_routes:
        modules.setdefault(r["file"], []).append(r)
    screens = set(scope.get("screens", []))
    for s in vocab:
        if s["file"] not in screens:
            modules.setdefault(s["file"], [])

    out = []
    w = out.append
    w("package AsBuilt_" + scope["slice"] + " {")
    w("    doc /* Generated from the code by scripts/sysml/as_built.py. Do not edit: a change here is lost on the next run. */")
    w("    private import EntelimVocabulary::*;")
    w("")
    for f in sorted(modules):
        w(f"    part '{f}' : Module {{")
        w(f"        :>> sourcePath = {q(f)};")
        for r in modules[f]:
            w(f"        port '{r['endpoint']}' : HttpRoutePort {{")
            w(f"            :>> rule = {q(r['rule'])};")
            w(f"            :>> methods = {q(','.join(r['methods']))};")
            w(f"            :>> endpoint = {q(r['endpoint'])};")
            w(f"            :>> handlerFile = {q(r['file'])};")
            w(f"            :>> handlerLine = {q(r['line'])};")
            w("        }")
        w("    }")
    w("")
    for t in scope.get("screens", []):
        w(f"    part '{t}' : Screen {{")
        w(f"        :>> template = {q(t)};")
        w("    }")
    w("")
    for i, s in enumerate(sorted(vocab, key=lambda s: (s["column"], s["file"], s["kind"])), 1):
        w(f"    part vocabulary_{s['column']}_{i} : VocabularySource {{")
        w(f"        :>> columnName = {q(s['column'])};")
        w(f"        :>> sourceKind = {q(s['kind'])};")
        w(f"        :>> sourceFile = {q(s['file'] + ':' + str(s['line']))};")
        w(f"        :>> origin = {q(s['origin'])};")
        w(f"        :>> valueList = {q(','.join(s['values']) if s['values'] else '')};")
        w("    }")
    w("}")
    sysml = "\n".join(out) + "\n"

    containers = []
    for t in scope.get("screens", []):
        comps, events = [], []
        for f in screen_forms(root, t):
            comps.append({"id": f["id"], "type": "Form", "line": f["line"]})
            tgt = by_endpoint.get(f["endpoint"] or "")
            events.append({
                "id": f"{f['id']}.submit", "type": "submit", "component": f["id"], "line": f["line"],
                "action": {
                    "endpoint": f["endpoint"], "method": f["method"], "dynamic": f["endpoint"] is None,
                    "route": ({"rule": tgt["rule"], "file": tgt["file"], "line": tgt["line"]} if tgt else None),
                },
            })
        containers.append({"id": t, "template": t, "viewComponents": comps, "events": events})
    ifml = {"ifml": "1.0", "model": scope["slice"], "viewContainers": containers, "navigationFlows": []}
    return sysml, json.dumps(ifml, indent=2, sort_keys=True) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=str(REPO), help="the checkout to read")
    ap.add_argument("--scope", default=str(REPO / "model" / "scope.json"))
    ap.add_argument("--out", required=True, help="folder to write into")
    a = ap.parse_args(argv)
    scope = json.loads(Path(a.scope).read_text(encoding="utf-8"))
    sysml, ifml = generate(Path(a.root).resolve(), scope)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{scope['slice']}.sysml").write_text(sysml, encoding="utf-8")
    (out / f"{scope['slice']}.ifml.json").write_text(ifml, encoding="utf-8")
    print(f"as-built: wrote {scope['slice']}.sysml and {scope['slice']}.ifml.json to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Generate the as-built part of the system model from the code: what is there, not what was meant.

    python scripts/sysml/as_built.py --out model/as-built                  # write the files
    python scripts/sysml/as_built.py --root <other checkout> --out <folder>

Reads (model/scope.json says which slice to model; the code decides the facts):
  - the routes and the forms of the screens, from scripts/component_inventory.py, the one reader of both
    (docs/component-inventory.json for this checkout; a fresh run of the inventory for another checkout);
  - every place in the repository where a stored field of the slice gets its allowed values (a form's select,
    an AI tool's enum, a template's select or text box) and where each of them takes the list from.
Writes a SysML v2 package (model/as-built/<slice>.sysml) and an interaction model in JSON that follows the shape of
the Interaction Flow Modeling Language (model/as-built/<slice>.ifml.json, schema in model/schema/ifml.schema.json).
Deterministic: the same tree always gives byte-identical files, so a CI run can fail when the committed copy is stale.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INVENTORY = "docs/component-inventory.json"


# ------------------------------------------------------------------------------- routes and forms
def _const(node):
    return node.value if isinstance(node, ast.Constant) else None


def fresh_inventory(root: Path) -> dict:
    """Run the inventory against another checkout. Its folders are linked into a scratch folder that holds this
    checkout's scripts, so the checkout is read and never written."""
    root = Path(root).resolve()
    with tempfile.TemporaryDirectory() as td:
        farm = Path(td) / "tree"
        farm.mkdir()
        for entry in root.iterdir():
            if entry.name not in ("scripts", ".git"):
                (farm / entry.name).symlink_to(entry)
        shutil.copytree(REPO / "scripts", farm / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        out = Path(td) / "inventory.json"
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([sys.executable, str(farm / "scripts" / "component_inventory.py"), "--out", str(out)],
                           cwd=farm, env=env, capture_output=True, text=True)
        if r.returncode or not out.is_file():
            raise SystemExit("The component inventory could not be built for " + str(root) + ":\n" + (r.stderr or r.stdout)[-1500:])
        return json.loads(out.read_text(encoding="utf-8"))


def load_inventory(root: Path, regenerate: bool = False) -> dict:
    """This checkout's committed inventory, or a fresh one for another checkout or on request."""
    root = Path(root).resolve()
    if root == REPO and not regenerate:
        f = root / INVENTORY
        if f.is_file():
            return json.loads(f.read_text(encoding="utf-8"))
    return fresh_inventory(root)


def routes_of(inventory: dict) -> list[dict]:
    """Every route of the inventory: handler file and line (None when the inventory could not trace it), blueprint,
    rule, methods, endpoint."""
    out = []
    for r in inventory["routes"]:
        out.append({
            "file": r.get("handler_file"), "line": r.get("handler_line"), "handler": r.get("handler"),
            "blueprint": r.get("blueprint") or "", "rule": r["rule"], "methods": list(r["methods"]),
            "endpoint": r["endpoint"],
        })
    out.sort(key=lambda r: (r["file"] or "", r["line"] or 0, r["rule"], r["endpoint"]))
    return out


def unresolved_routes(inventory: dict) -> list[dict]:
    """Routes the inventory could not trace to a handler file: listed, counted by the ratchet, never treated as clean."""
    return [r for r in routes_of(inventory) if not r["file"]]


def forms_of(inventory: dict, template: str) -> list[dict]:
    """The forms of one template, each with the events that submit it: the form's own action and every button's formaction."""
    forms = []
    for c in inventory["controls"]:
        if c["kind"] != "form" or c["template"] != template:
            continue
        t = c["target"]
        calls = [("form", t.get("action"), t.get("endpoint"))]
        calls += [("formaction", fa.get("action"), fa.get("endpoint")) for fa in t.get("formactions", [])]
        calls = [x for x in calls if x[1]]
        if not calls:
            continue
        n = len(forms) + 1
        forms.append({
            "id": t.get("dom_id") or (f"form-line-{c['line']}" if c.get("line") else f"form-{n}"),
            "line": c.get("line") or 1,
            "method": (t.get("method") or "GET").upper(),
            "events": [{"via": via, "rawAction": str(raw).strip(), "endpoint": ep} for via, raw, ep in calls],
        })
    return forms


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


def _corpus(root: Path) -> list[tuple[Path, str]]:
    """Every Python file and template under app/, read once."""
    out = []
    for p in sorted((root / "app").rglob("*")):
        if p.suffix in (".py", ".html") and "__pycache__" not in p.parts and "/tests/" not in p.as_posix():
            try:
                out.append((p, p.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
    return out


def _files_naming(corpus, column: str, suffixes: tuple[str, ...]):
    """Files whose text holds the column name, so only they are read in full."""
    for p, text in corpus:
        if p.suffix in suffixes and column in text:
            yield p, text


def form_selects(root: Path, corpus, column: str) -> list[dict]:
    """Every form class under app/ with a field named `column`, and where its choices come from."""
    out = []
    for p, text in _files_naming(corpus, column, (".py",)):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        rel = p.relative_to(root).as_posix()
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            for st in cls.body:
                if not (isinstance(st, ast.Assign) and isinstance(st.targets[0], ast.Name) and st.targets[0].id == column
                        and isinstance(st.value, ast.Call)):
                    continue
                kind = getattr(st.value.func, "id", None) or getattr(st.value.func, "attr", "")
                if not kind.endswith("Field"):
                    continue  # a model column or a serialiser field is not a form a person fills in
                choices = next((kw for kw in st.value.keywords if kw.arg == "choices"), None)
                if choices is not None:
                    o, v = _origin(root, tree, choices.value, rel)
                    out.append({"file": rel, "line": st.lineno, "origin": o, "values": v, "note": kind, "kind": "form-select"})
                else:
                    out.append({"file": rel, "line": st.lineno, "origin": rel + " (free text)", "values": None,
                                "note": kind, "kind": "form-select"})
    return out


def tool_enums(root: Path, corpus, column: str) -> list[dict]:
    """Every AI tool that sets `column`, and where the list of values it accepts comes from."""
    out = []
    for p, text in _files_naming(corpus, column, (".py",)):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        rel = p.relative_to(root).as_posix()
        for d in ast.walk(tree):
            if not isinstance(d, ast.Dict):
                continue
            route = _const(_dget(d, "route") or ast.Constant(None))
            if not (isinstance(route, str) and route.rsplit(".", 1)[-1] == column):
                continue
            params = _dget(d, "parameters")
            props = _dget(params, "properties") if isinstance(params, ast.Dict) else None
            if not isinstance(props, ast.Dict):
                continue
            for pv in props.values:
                en = _dget(pv, "enum") if isinstance(pv, ast.Dict) else None
                if en is not None:
                    o, v = _origin(root, tree, en, rel)
                    out.append({"file": rel, "line": en.lineno, "origin": o, "values": v, "note": "enum", "kind": "tool-enum"})
    return out


def _definitions(root: Path, corpus) -> dict[str, list[str]]:
    """Top-level function and constant names under app/ and the file each is defined in."""
    out: dict[str, list[str]] = {}
    for p, text in corpus:
        if p.suffix != ".py":
            continue
        for m in re.finditer(r"^(?:def\s+(\w+)\(|([A-Za-z_]\w*)\s*(?::[^=\n]+)?=)", text, re.M):
            out.setdefault(m.group(1) or m.group(2), []).append(p.relative_to(root).as_posix())
    return out


def _balanced(text: str, start: int) -> int:
    """Index just past the bracket that closes the one at `start`; quotes are skipped."""
    depth, quote, i = 0, None, start
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def _top_level_args(args: str) -> dict[str, str]:
    """`name=value` arguments of a call, split at the commas that sit outside every bracket and quote."""
    parts, depth, quote, cur = [], 0, None, ""
    for ch in args:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    out = {}
    for part in parts:
        m = re.match(r"\s*(\w+)\s*=(.*)$", part, re.S)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


_NOT_A_READER = {"list", "tuple", "sorted", "set", "dict", "len", "range", "enumerate", "zip", "reversed"}


def _expression_origin(defs: dict[str, list[str]], expr: str, here: str) -> tuple[str, list[str] | None]:
    """Where the options of a template's select come from: the file that defines a function or constant the expression
    calls, else the template itself with the values written out in place."""
    names = [m.group(1) for m in re.finditer(r"(?<![\w.'\"])([A-Za-z_]\w*)\s*(?:\(|(?=\s*[+)\],]|$))", expr)]
    names = [n for n in names if n not in _NOT_A_READER and n in defs]
    if names:
        files = sorted({f for n in names for f in defs[n]})
        return (files[0] if len(files) == 1 else here + " (options read from " + ", ".join(files) + ")"), None
    vals = re.findall(r"\(\s*['\"]([^'\"]+)['\"]\s*,", expr) or re.findall(r"['\"]([^'\"]+)['\"]", expr)
    return here + " (written out in place)", vals or None


def template_fields(root: Path, corpus, column: str, defs: dict[str, list[str]]) -> list[dict]:
    """Every select or text box named `column` in a template under app/, and where a select's options come from."""
    out = []
    for p, text in _files_naming(corpus, column, (".html",)):
        rel = p.relative_to(root).as_posix()
        for m in re.finditer(r"edit_row\(", text):
            call = text[m.start():_balanced(text, m.end() - 1)]
            if not re.search(r"""['"]%s['"]""" % re.escape(column), call):
                continue
            n = re.search(r"""['"]%s['"][^,]*,[^,]*,\s*(\d+)""" % re.escape(column), call)
            out.append({"file": rel, "line": text.count("\n", 0, m.start()) + 1, "origin": rel + " (free text)", "values": None,
                        "note": "text box" + (f", up to {n.group(1)} characters" if n else ""), "kind": "template-field"})
        for m in re.finditer(r"native_select\(", text):
            end = _balanced(text, m.end() - 1)
            call_args = text[m.end():end - 1]
            args = _top_level_args(call_args)
            if re.sub(r"[()'\"\s]", "", args.get("name", "")) != column:
                continue
            line = text.count("\n", 0, m.start()) + 1
            body = ""
            tail = text[end:end + 400]
            if re.match(r"\s*%\}", tail):
                stop = text.find("endcall", end)
                body = text[end:stop if stop > 0 else end]
            if "options" in args:
                origin, vals = _expression_origin(defs, args["options"], rel)
            elif "<option" in body or "{% for" in body:
                lists = re.findall(r"\bin\s*\[([^\]]*)\]", body)
                vals = [v for lst in lists[:1] for v in re.findall(r"['\"]([^'\"]+)['\"]", lst)] or \
                    re.findall(r"""<option\s+value=["']([^"'{]+)["']""", body)
                origin = rel + " (written out in place)"
            else:
                origin, vals = rel + " (options not resolved)", None
            out.append({"file": rel, "line": line, "origin": origin, "values": vals or None, "note": "select",
                        "kind": "template-field"})
    return out


def vocabulary_sources(root: Path, scope: dict) -> list[dict]:
    """Every reader of each stored field the slice names, found by reading the whole of app/."""
    out = []
    corpus = _corpus(root)
    defs = _definitions(root, corpus)
    for v in scope.get("vocabularies", []):
        found = (form_selects(root, corpus, v["column"]) + tool_enums(root, corpus, v["column"])
                 + template_fields(root, corpus, v["column"], defs))
        for s in found:
            s["column"] = v["column"]
            out.append(s)
    return out


# ---------------------------------------------------------------------------------------------- emit
def q(s) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def generate(root: Path, scope: dict, inventory: dict | None = None) -> tuple[str, str]:
    inventory = inventory if inventory is not None else load_inventory(root)
    routes = routes_of(inventory)
    by_endpoint = {}
    for r in routes:
        by_endpoint.setdefault(r["endpoint"], r)
    tails = set(scope["routeTails"])
    scoped_routes = [r for r in routes if rule_tail(r["rule"]) in tails]

    vocab = vocabulary_sources(root, scope)

    modules: dict[str, list[dict]] = {}
    for r in scoped_routes:
        modules.setdefault(r["file"] or "", []).append(r)
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
        w(f"    part '{f or 'unresolved handlers'}' : Module {{")
        if f:
            w(f"        :>> sourcePath = {q(f)};")
        used: dict[str, int] = {}
        for r in modules[f]:
            used[r["endpoint"]] = used.get(r["endpoint"], 0) + 1
            suffix = "" if used[r["endpoint"]] == 1 else f" {used[r['endpoint']]}"
            w(f"        port '{r['endpoint']}{suffix}' : HttpRoutePort {{")
            w(f"            :>> rule = {q(r['rule'])};")
            w(f"            :>> methods = {q(','.join(r['methods']))};")
            w(f"            :>> endpoint = {q(r['endpoint'])};")
            w(f"            :>> handlerFile = {q(r['file'] or '')};")
            w(f"            :>> handlerLine = {q(r['line'] or '')};")
            w("        }")
        w("    }")
    w("")
    for t in scope.get("screens", []):
        w(f"    part '{t}' : Screen {{")
        w(f"        :>> template = {q(t)};")
        w("    }")
    w("")
    for i, s in enumerate(sorted(vocab, key=lambda s: (s["column"], s["file"], s["line"], s["kind"])), 1):
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
        for f in forms_of(inventory, t):
            comps.append({"id": f["id"], "type": "Form", "line": f["line"]})
            for k, ev in enumerate(f["events"]):
                tgt = by_endpoint.get(ev["endpoint"] or "")
                events.append({
                    "id": f"{f['id']}.submit" if ev["via"] == "form" else f"{f['id']}.formaction-{k}",
                    "type": "submit", "component": f["id"], "line": f["line"],
                    "action": {
                        "endpoint": ev["endpoint"], "method": f["method"], "dynamic": ev["endpoint"] is None,
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
    ap.add_argument("--out", help="folder to write into (default model/as-built with --check)")
    ap.add_argument("--check", action="store_true", help="write nothing; exit 1 when the files in --out differ from the code")
    a = ap.parse_args(argv)
    scope = json.loads(Path(a.scope).read_text(encoding="utf-8"))
    sysml, ifml = generate(Path(a.root).resolve(), scope)
    if a.check:
        out = Path(a.out or REPO / "model" / "as-built")
        stale = [name for name, text in ((f"{scope['slice']}.sysml", sysml), (f"{scope['slice']}.ifml.json", ifml))
                 if not (out / name).is_file() or (out / name).read_text(encoding="utf-8") != text]
        if stale:
            print(f"as-built: {', '.join(stale)} in {out} differ from the code; regenerate with: "
                  "python scripts/sysml/as_built.py --out model/as-built")
            return 1
        print(f"as-built: {scope['slice']}.sysml and {scope['slice']}.ifml.json match the code")
        return 0
    if not a.out:
        ap.error("--out is required unless --check is given")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{scope['slice']}.sysml").write_text(sysml, encoding="utf-8")
    (out / f"{scope['slice']}.ifml.json").write_text(ifml, encoding="utf-8")
    print(f"as-built: wrote {scope['slice']}.sysml and {scope['slice']}.ifml.json to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

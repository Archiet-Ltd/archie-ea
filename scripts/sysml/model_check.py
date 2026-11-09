#!/usr/bin/env python3
"""The system model's consistency checks.

    python scripts/sysml/model_check.py check                  # parse the model, regenerate the as-built part, run checks 1 to 4 and the screen check
    python scripts/sysml/model_check.py check --root <tree>    # the same against another checkout of the code
    python scripts/sysml/model_check.py matrix                 # requirement, method, case, last result
    python scripts/sysml/model_check.py impact --open A.md --open B.md --new C.md     # check 5, at dispatch

The five checks (rules in docs/system-model.md of the design record):
  1  one definition per concept     one handler per save route the model names
  2  allocation completeness        every function is allocated to one part, every part is in the code
  3  one source per figure or list  every reader of a field's allowed values reads the one the model names
  4  satisfied and verified         every requirement has a part that satisfies it and a test that proves it
  5  no collision                   no two open briefs change the same model element
and the screen check: every event on a screen reaches the route the model names, never a second one.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import as_built  # noqa: E402
import sysml_model  # noqa: E402

REPO = sysml_model.REPO
MODEL = sysml_model.MODEL_DIR
CHECK_NAMES = {
    "1": "one definition per concept",
    "2": "allocation completeness",
    "3": "one source per list or figure",
    "4": "every requirement satisfied and verified",
    "S": "screen events reach the model's route",
}


# ------------------------------------------------------------------------------------ pure checks
def check_one_definition(authored_routes: list[dict], built_routes: list[dict]) -> list[str]:
    """Check 1. For each route the model names, every other handler with the same address tail is a second definition."""
    out = []
    for want in authored_routes:
        tail = as_built.rule_tail(want["rule"])
        same = [r for r in built_routes if as_built.rule_tail(r["rule"]) == tail]
        canonical = [r for r in same if r["endpoint"] == want["endpoint"]]
        if not canonical:
            out.append(f"the model names {want['endpoint']} for '{tail}' but the code has no such handler")
        for r in same:
            if r["endpoint"] != want["endpoint"]:
                out.append(
                    f"{r['file']}:{r['line']} {r['endpoint']} also handles '{tail}'; the model names {want['endpoint']} "
                    "as the one handler. Remove this one, or make it redirect, and move its callers."
                )
    return out


def check_allocation(functions: list[str], allocations: list[tuple], parts: dict, root: Path) -> list[str]:
    """Check 2. Each function goes to exactly one part; each part named in the model exists in the code."""
    out = []
    for f in functions:
        to = [b for a, b in allocations if a == f]
        if len(to) != 1:
            out.append(f"function {f.split('::')[-1]} is allocated to {len(to)} parts; it must be exactly one")
    for p in parts.values():
        path = p["sourcePath"]
        if path and not (root / path).exists():
            out.append(f"part {p['name']} is meant to live in {path}, which does not exist in the code")
    return out


def check_one_source(constraints: dict, sources: list[dict], root: Path) -> list[str]:
    """Check 3. Every reader of a field's allowed values reads the one source the model names."""
    out = []
    for c in constraints.values():
        for s in sources:
            if s["column"] != c["column"]:
                continue
            if s["origin"] != c["canonicalSource"]:
                vals = f" ({s['values']})" if s["values"] else ""
                out.append(
                    f"{s['sourceFile']} ({s['sourceKind']}) takes {c['column']} from {s['origin']}{vals}, "
                    f"not from {c['canonicalSource']}"
                )
    return out


def check_satisfied_and_verified(reqs: dict, satisfies: list[tuple], cases: dict, root: Path) -> tuple[list[str], dict]:
    """Check 4. Returns findings and the rule-line coverage figures."""
    out = []
    by_id = {r["id"]: r for r in reqs.values()}
    qn_to_id = {qn: r["id"] for qn, r in reqs.items()}
    children = {}
    for r in reqs.values():
        children.setdefault(r["parent"], []).append(r["id"])
    for r in sorted(reqs.values(), key=lambda r: r["id"]):
        rid = r["id"]
        if r["parent"] and r["parent"] not in by_id:
            out.append(f"{rid}: its parent {r['parent']} is not in the model")
        # climb to a need
        seen, cur = set(), r
        while cur["parent"] and cur["parent"] in by_id and cur["id"] not in seen:
            seen.add(cur["id"])
            cur = by_id[cur["parent"]]
        if not cur["id"].startswith("NEED-") and r["parent"] in by_id:
            out.append(f"{rid}: the trace upward ends at {cur['id']}, not at a stated need")
        if not rid.startswith("REQ-"):
            continue
        for fld in ("text", "rationale", "ownerRole", "method", "ruleLine", "job"):
            if not r[fld]:
                out.append(f"{rid}: has no {fld}")
        if not [s for q, s in satisfies if q == next((k for k, v in qn_to_id.items() if v == rid), None)]:
            out.append(f"{rid}: no part satisfies it")
        mine = [c for c in cases.values() if any(qn_to_id.get(v) == rid for v in c["verifies"])]
        if not mine:
            out.append(f"{rid}: no test verifies it")
        for c in mine:
            f = root / (c["testFile"] or "")
            if not c["testFile"] or not f.is_file():
                out.append(f"{rid}: its test file {c['testFile']} does not exist in the code")
            elif not re.search(r"^\s*(async\s+)?def\s+%s\b" % re.escape(c["testName"] or ""), f.read_text(encoding="utf-8"), re.M):
                out.append(f"{rid}: {c['testFile']} has no test named {c['testName']}")
    rules = [r for r in reqs.values() if r["id"].startswith("RR-")]
    undecomposed = [r["id"] for r in rules if not any(c.startswith("REQ-") for c in children.get(r["id"], []))]
    return out, {"rule_lines": len(rules), "without_requirement": sorted(undecomposed, key=lambda x: int(x.split("-")[1]))}


def check_screen_events(ifml: dict, authored_routes: list[dict]) -> list[str]:
    """Screen check. An event that calls a route with the same address tail as the model's route must call the model's route."""
    out = []
    want = {as_built.rule_tail(r["rule"]): r["endpoint"] for r in authored_routes}
    for vc in ifml["viewContainers"]:
        for ev in vc["events"]:
            act = ev["action"]
            if act["dynamic"]:
                continue
            where = f"{vc['template']}:{ev['line']}"
            if act["route"] is None:
                out.append(f"{where} {ev['component']} calls {act['endpoint']}, which is not a route in the code")
                continue
            tail = as_built.rule_tail(act["route"]["rule"])
            if tail in want and act["endpoint"] != want[tail]:
                out.append(
                    f"{where} the Save of {ev['component']} calls {act['endpoint']} ({act['route']['file']}:{act['route']['line']}); "
                    f"the model names {want[tail]}"
                )
    return out


# --------------------------------------------------------------------------------------- check 5
def _field(text: str, label: str) -> list[str]:
    m = re.search(r"^\s*[-*]?\s*\**%s\**\s*:\s*(.+)$" % label, text, re.M | re.I)
    return [x.strip().strip("`") for x in re.split(r"[,;]", m.group(1)) if x.strip()] if m else []


def brief_elements(path: Path, base: str, git_dir: Path) -> dict:
    """The model elements one brief changes: those it declares, plus the source files its branch changes."""
    text = path.read_text(encoding="utf-8")
    name = path.stem
    declared = _field(text, "Model changes")
    builds_on = [b.removesuffix(".md") for b in _field(text, "Builds on")]
    branch_m = re.search(r"[Bb]ranch\s+`?(ui/[\w-]+)`?", text)
    branch = branch_m.group(1) if branch_m else None
    parent_m = re.search(r"(?:based on|off|on)\s+`?(ui/[\w.-]+)`?", text[branch_m.end():branch_m.end() + 200]) if branch_m else None
    if not parent_m and branch_m:
        parent_m = re.search(r"based on\s+`?(ui/[\w.-]+)`?", text)
    from_branch = []
    if branch:
        ref_base = parent_m.group(1) if parent_m and _ref_exists(git_dir, parent_m.group(1)) else base
        if _ref_exists(git_dir, branch):
            r = subprocess.run(["git", "diff", "--name-only", f"{ref_base}...{branch}", "--", "app/"],
                               cwd=git_dir, capture_output=True, text=True)
            from_branch = [p for p in r.stdout.split() if p]
    return {"brief": name, "declared": declared, "from_branch": from_branch, "builds_on": builds_on, "branch": branch}


def _ref_exists(git_dir: Path, ref: str) -> bool:
    return subprocess.run(["git", "rev-parse", "--verify", "-q", ref], cwd=git_dir, capture_output=True).returncode == 0


def check_collisions(new: dict, open_briefs: list[dict]) -> list[str]:
    """Check 5. The new brief may share an element with an open brief only when it says it builds on that brief."""
    out = []
    mine = set(new["declared"]) | set(new["from_branch"])
    for o in open_briefs:
        theirs = set(o["declared"]) | set(o["from_branch"])
        shared = sorted(mine & theirs)
        if shared and o["brief"] not in new["builds_on"]:
            shown = ", ".join(shared[:6]) + (f" and {len(shared) - 6} more" if len(shared) > 6 else "")
            out.append(f"{new['brief']} and {o['brief']} both change {shown}")
    return out


# ------------------------------------------------------------------------------------------ driver
def collect(root: Path):
    """Regenerate the as-built part for `root`, parse everything, and return the model with the interaction data."""
    scope = json.loads((MODEL / "scope.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as td:
        ab = Path(td)
        sysml, ifml = as_built.generate(root, scope)
        (ab / f"{scope['slice']}.sysml").write_text(sysml, encoding="utf-8")
        model, line = sysml_model.parse_model(MODEL, ab)
    ifml_data = json.loads(ifml)
    schema = json.loads((MODEL / "schema" / "ifml.schema.json").read_text(encoding="utf-8"))
    import jsonschema
    jsonschema.validate(ifml_data, schema)
    return model, ifml_data, line


def split_routes(model: sysml_model.Model):
    authored, built = [], []
    for p in model.parts().values():
        for name, port in p["ports"].items():
            if not port.get("endpoint"):
                continue
            if p["qn"].startswith("AsBuilt_"):
                built_port = next(x for x in model.children[p["qn"]] if x["name"] == name)
                built.append({
                    "endpoint": port["endpoint"], "rule": port["rule"], "methods": port["methods"],
                    "file": model.attr(built_port["qn"], "handlerFile"), "line": model.attr(built_port["qn"], "handlerLine"),
                })
            else:
                authored.append({"endpoint": port["endpoint"], "rule": port["rule"]})
    return authored, built


def vocabulary_sources(model: sysml_model.Model):
    out = []
    for e in model.of_type(sysml_model.VOCAB + "VocabularySource", "PartUsage"):
        q = e["qn"]
        vals = model.attr(q, "valueList")
        out.append({
            "column": model.attr(q, "columnName"), "sourceKind": model.attr(q, "sourceKind"),
            "sourceFile": model.attr(q, "sourceFile"), "origin": model.attr(q, "origin"),
            "values": vals or "",
        })
    return sorted(out, key=lambda s: (s["column"], s["sourceFile"]))


def run_checks(root: Path):
    model, ifml, parser_line = collect(root)
    authored, built = split_routes(model)
    parts = {q: p for q, p in model.parts().items() if not q.startswith("AsBuilt_") and p["type"] in ("Module", "Screen")}
    findings = {
        "1": check_one_definition(authored, built),
        "2": check_allocation(list(model.functions()), model.allocations(), parts, root),
        "3": check_one_source(model.constraints(), vocabulary_sources(model), root),
        "S": check_screen_events(ifml, authored),
    }
    f4, cover = check_satisfied_and_verified(model.requirements(), model.satisfies(), model.cases(), root)
    findings["4"] = f4
    return findings, cover, parser_line, model


def cmd_check(a) -> int:
    root = Path(a.root).resolve()
    findings, cover, parser_line, model = run_checks(root)
    ratchet = json.loads((MODEL / "ratchet.json").read_text(encoding="utf-8"))["slice"]
    print(parser_line)
    bad = 0
    for k in ("1", "2", "3", "4", "S"):
        print(f"\ncheck {k}: {CHECK_NAMES[k]}")
        for f in findings[k]:
            print(f"  FINDING {f}")
        n, cap = len(findings[k]), ratchet[k]
        status = "ok" if n <= cap else "OVER THE RATCHET"
        if n < cap:
            status = "ok, below the ratchet: lower it to " + str(n)
        bad += n > cap
        print(f"  check {k}: findings={n} ratchet={cap} {status}")
    reqs = model.requirements()
    print(f"\nmodel: {len(reqs)} requirements, {len(model.parts())} parts; "
          f"release-rule lines without a requirement under them: {len(cover['without_requirement'])} of {cover['rule_lines']}")
    print(f"model-check result: findings={sum(len(v) for v in findings.values())} over_ratchet={bad}")
    if a.report_only:
        return 0
    return 1 if bad else 0


def cmd_matrix(a) -> int:
    root = Path(a.root).resolve()
    model, _, parser_line = collect(root)
    results = json.loads(Path(a.results).read_text(encoding="utf-8")) if a.results else {}
    reqs = model.requirements()
    cases = model.cases()
    print("requirement | method | case | case in code | last result | evidence")
    for qn, r in sorted(reqs.items(), key=lambda kv: kv[1]["id"]):
        if not r["id"].startswith("REQ-"):
            continue
        mine = [c for c in cases.values() if qn in c["verifies"]]
        for c in mine or [None]:
            if c is None:
                print(f"{r['id']} | {r['method']} | none | no | not run | none")
                continue
            key = f"{c['testFile']}::{c['testName']}"
            present = (root / c["testFile"]).is_file() and re.search(
                r"def\s+%s\b" % re.escape(c["testName"]), (root / c["testFile"]).read_text(encoding="utf-8"))
            res = results.get(key, {})
            print(f"{r['id']} | {r['method']} | {key} | {'yes' if present else 'no'} | "
                  f"{res.get('result', 'not recorded')} | {res.get('evidence', 'none')}")
    return 0


def cmd_impact(a) -> int:
    git_dir = Path(a.repo).resolve()
    base = a.base
    new = brief_elements(Path(a.new), base, git_dir)
    opens = [brief_elements(Path(p), base, git_dir) for p in a.open]
    print(f"{new['brief']}: declares {len(new['declared'])} elements, its branch changes {len(new['from_branch'])} files")
    clashes = check_collisions(new, opens)
    for c in clashes:
        print(f"  REFUSED {c}")
    if not clashes:
        print(f"  clean: {new['brief']} shares no element with {len(opens)} open brief(s)")
    return 1 if clashes else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--root", default=str(REPO))
    c.add_argument("--report-only", action="store_true", help="print findings but exit 0 (to read counts)")
    c.set_defaults(fn=cmd_check)
    m = sub.add_parser("matrix")
    m.add_argument("--root", default=str(REPO))
    m.add_argument("--results", help="JSON: {'tests/x.py::name': {'result': 'passed', 'evidence': '...'}}")
    m.set_defaults(fn=cmd_matrix)
    i = sub.add_parser("impact")
    i.add_argument("--new", required=True)
    i.add_argument("--open", action="append", default=[])
    i.add_argument("--repo", default=str(REPO))
    i.add_argument("--base", default="anioko/ui/shadcn-rebuild")
    i.set_defaults(fn=cmd_impact)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

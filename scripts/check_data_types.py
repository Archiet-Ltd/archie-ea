#!/usr/bin/env python
"""Every page shows each kind of data with the one component the data-type register names.

The pattern library says how a list or a record looks; it never said which component a page must
use because of the data it holds, so a solution record could ship tabs that described their
content instead of listing it and still pass every gate (26 Oct 2026). docs/data-type-register.yml
is that decision; this script enforces it.

Rules (each a count; scripts/verify.py ratchets them, `declared` at zero):
  declared             a page's design-map entry lists a data type it holds (`data_types`), and the
                       page does not import the type's component, lacks a required call, does not
                       render the declared test id, or its journey is missing or never names the
                       test id
  undecided            a page template shows a type's signals but neither declares the type nor
                       imports one of its components
  retired              an import of a component the register retires for a type
  hand-pagination      page links built by hand from a pagination object, with no pagination control
  placeholder-content  a signed-in page saying content will come later instead of showing it

    python scripts/check_data_types.py                     # summary of every rule
    python scripts/check_data_types.py --rule declared --list
    python scripts/check_data_types.py --rule undecided --count

Proven-against: the risks table test id renamed on the solution record (risk-row to risk-line in
solutions/record.html, which the design map declares for its risks) -- declared went from 0 to 1 and
the check exited 1, 27 Oct 2026.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTER = "docs/data-type-register.yml"
DESIGN_MAP = "docs/design-map.json"
TEMPLATES = "app/templates"
RULES = ("declared", "undecided", "retired", "hand-pagination", "placeholder-content")

_COMMENT = re.compile(r"{#.*?#}", re.S)
_IMPORT = re.compile(r"{%-?\s*(?:from|import|include|extends)\s+['\"]([^'\"]+)['\"]")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _view(text: str) -> str:
    """The template without its comments, so documentation never counts as use."""
    return _COMMENT.sub("", text)


def _imports(view: str) -> set[str]:
    return set(_IMPORT.findall(view))


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def load(root: Path = None):
    root = root or ROOT
    register = yaml.safe_load(_read(root / REGISTER))
    design_map = json.loads(_read(root / DESIGN_MAP))
    return register, design_map


def _page_templates(root: Path):
    """Page templates: every template outside the shared component and macro folders."""
    tpl = root / TEMPLATES
    for path in sorted(tpl.rglob("*.html")):
        rel = path.relative_to(tpl).as_posix()
        if rel.startswith(("components/", "macros/", "basecoat/")):
            continue
        yield path, rel


def rule_declared(root: Path, register: dict, design_map: dict):
    types = {t["id"]: t for t in register["types"]}
    out = []
    for rel, entry in sorted(design_map.get("pages", {}).items()):
        declared = entry.get("data_types") or []
        if not declared:
            continue
        path = root / rel
        if not path.exists():
            out.append((rel, 1, "the design map declares data types for a template that does not exist"))
            continue
        text = _read(path)
        view = _view(text)
        imports = _imports(view)
        for d in declared:
            tid = d.get("type")
            t = types.get(tid)
            if not t:
                out.append((rel, 1, f"data type '{tid}' is not in the data-type register"))
                continue
            wanted = t.get("components") or []
            if wanted and not any(c in imports for c in wanted):
                out.append((rel, 1, f"holds {tid} but imports none of {', '.join(wanted)}"))
            for req in t.get("requires") or []:
                if req not in view:
                    out.append((rel, 1, f"holds {tid} but never uses {req}"))
            testid = d.get("testid")
            if not testid:
                out.append((rel, 1, f"{tid}: no test id declared for the journey to assert"))
            elif testid not in view:
                out.append((rel, 1, f"{tid}: the page never renders the declared test id '{testid}'"))
            journey = d.get("journey")
            if not journey:
                out.append((rel, 1, f"{tid}: no browser journey declared"))
            elif not (root / journey).exists():
                out.append((rel, 1, f"{tid}: the journey {journey} does not exist"))
            elif testid and testid not in _read(root / journey):
                out.append((rel, 1, f"{tid}: the journey {journey} never names '{testid}'"))
    return out


def rule_undecided(root: Path, register: dict, design_map: dict):
    pages = design_map.get("pages", {})
    out = []
    for path, rel in _page_templates(root):
        text = _read(path)
        view = _view(text)
        imports = _imports(view)
        declared = {d.get("type") for d in (pages.get(f"{TEMPLATES}/{rel}", {}).get("data_types") or [])}
        for t in register["types"]:
            if t["id"] in declared or any(c in imports for c in t.get("components") or []):
                continue
            for sig in t.get("signals") or []:
                m = re.search(sig, view)
                if m:
                    out.append((f"{TEMPLATES}/{rel}", _line(view, m.start()),
                                f"shows {t['id']} without its component ({', '.join(t.get('components') or [])})"))
                    break
    return out


def rule_retired(root: Path, register: dict, design_map: dict):
    out = []
    retired = {}
    for t in register["types"]:
        for r in t.get("retired") or []:
            retired.setdefault(r, t["id"])
    tpl = root / TEMPLATES
    for path in sorted(tpl.rglob("*.html")):
        rel = path.relative_to(tpl).as_posix()
        view = _view(_read(path))
        for m in _IMPORT.finditer(view):
            target = m.group(1)
            if target in retired and target != rel:
                out.append((f"{TEMPLATES}/{rel}", _line(view, m.start()),
                            f"imports {target}, retired for {retired[target]}"))
    return out


def rule_hand_pagination(root: Path, register: dict, design_map: dict):
    spec = register.get("hand_pagination") or {}
    sig = re.compile(spec.get("signal", r"$^"))
    allowed = set(spec.get("allowed_with") or [])
    out = []
    for path, rel in _page_templates(root):
        view = _view(_read(path))
        if allowed & _imports(view):
            continue
        m = sig.search(view)
        if m:
            out.append((f"{TEMPLATES}/{rel}", _line(view, m.start()), "page links built by hand; use the list table"))
    return out


def rule_placeholder_content(root: Path, register: dict, design_map: dict):
    spec = register.get("placeholder_content") or {}
    signed_in = re.compile(spec.get("signed_in", r"$^"))
    sig = re.compile(spec.get("signal", r"$^"))
    out = []
    for path, rel in _page_templates(root):
        view = _view(_read(path))
        if not signed_in.search(view):
            continue
        # attribute values (placeholder="...", title="...") are not content a person reads as a promise
        body = re.sub(r"\b[\w:-]+\s*=\s*\"[^\"]*\"|\b[\w:-]+\s*=\s*'[^']*'", "", view)
        m = sig.search(body)
        if m:
            out.append((f"{TEMPLATES}/{rel}", _line(body, m.start()),
                        f"says '{m.group(0)}' instead of showing the content"))
    return out


RULE_FUNCS = {
    "declared": rule_declared,
    "undecided": rule_undecided,
    "retired": rule_retired,
    "hand-pagination": rule_hand_pagination,
    "placeholder-content": rule_placeholder_content,
}


def collect(rule: str, root: Path = None):
    root = root or ROOT
    register, design_map = load(root)
    return RULE_FUNCS[rule](root, register, design_map)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if "--rule" in argv:
        rule = argv[argv.index("--rule") + 1]
        if rule not in RULE_FUNCS:
            print(f"unknown rule {rule}; one of {', '.join(RULES)}", file=sys.stderr)
            return 2
        found = collect(rule)
        if "--list" in argv:
            for rel, ln, msg in found:
                print(f"{rel}:{ln}: {msg}")
        print(len(found))
        return 0
    counts = {rule: len(collect(rule)) for rule in RULES}
    if "--json" in argv:
        print(json.dumps(counts, indent=1))
        return 0
    for rule in RULES:
        print(f"{rule:20} {counts[rule]}")
    return 1 if counts["declared"] else 0


if __name__ == "__main__":
    sys.exit(main())

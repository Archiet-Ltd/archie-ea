#!/usr/bin/env python
"""Conformance checker for design/patterns.json.

Reads the pattern register and checks templates statically for the three
canonical components (dense_table, form_field/form_group, modal) and for the
banned patterns that can be recognised in markup. Nothing is rendered; this
is a source check, so it runs anywhere pytest runs.

    python scripts/check_design_patterns.py                 # the rank-1 routes
    python scripts/check_design_patterns.py --all           # every template
    python scripts/check_design_patterns.py --json out.json # report file
    python scripts/check_design_patterns.py --baseline design/conformance-baseline.json

The baseline holds the count per check; a run that exceeds a baselined count
fails (exit 2), a run that lowers one prints the new count so the baseline
can be ratcheted down. Without a baseline the exit code is 1 when anything is
found, 0 when nothing is.

Findings are a judgement on markup, not on people: a finding says "this
template builds the thing by hand where the register names one component".
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = ROOT / "design" / "patterns.json"
ROUTES = ROOT / "design" / "rank1-routes.json"
TEMPLATE_DIRS = [ROOT / "app" / "templates", ROOT / "app" / "modules"]

# The canonical homes. A template that imports one of these and uses it is
# conformant for that component; one that hand-builds the same thing is not.
CANONICAL = {
    "dense-table": ("components/dense_table.html", "dense_table"),
    "data-table": ("components/data_table.html", "data_table"),
    "form-field": ("components/form_field.html", "form_field"),
    "form-group": ("components/form_field.html", "form_group"),
    "dialog": ("components/modal.html", "modal"),
}

COMMENT = re.compile(r"\{#.*?#\}", re.S)
CALL_TABLE = re.compile(r"\{%\s*call\s+(?:dense_table|data_table)\(")
RAW_TABLE = re.compile(r"<table\b")
ENDCALL = re.compile(r"\{%\s*endcall\s*%\}")
RAW_DIALOG = re.compile(r'<div\b[^>]*\brole="dialog"[^>]*>')
LABEL_PAIR = re.compile(r'<div\b([^>]*)>\s*<label\s+for="([^"]+)"[^>]*>(.*?)</label>\s*<(input|select|textarea)\b', re.S)
FORM_BLOCK = re.compile(r"<form\b.*?</form>", re.S)
MULTI_COL = re.compile(r"\b(?:sm:|md:|lg:)?grid-cols-([2-9]|1[0-2])\b")
SHADOW = re.compile(r"(?<![\w-])shadow(?:-(?:sm|md|lg|xl|2xl|inner))?(?![\w-])")
BLUR = re.compile(r"backdrop-blur|backdrop-filter\s*:\s*blur")
FLOATING = re.compile(r"peer-placeholder-shown|peer-focus:-translate-y|floating-label")
DRAWER = re.compile(r"drawer-overlay|<(?:div|aside)[^>]*class=\"(?=[^\"]*fixed)(?=[^\"]*(?:right-0|inset-y-0))(?=[^\"]*translate-x-full)(?![^\"]*lg:translate-x-0)[^\"]*\"")
FULLSCREEN = re.compile(r"size=['\"]full['\"]|max-w-7xl[^\"]*modal|modal[^\"]*max-w-7xl|h-screen[^\"]*modal-panel")
MERMAID = re.compile(r"\bmermaid\b", re.I)
FORCE = re.compile(r"forceSimulation|d3\.force|force-directed", re.I)
D3 = re.compile(r"\bd3\.(?:select|scale|axis|line|arc|pie|tree|hierarchy)\b")
ACCORDION_FAQ = re.compile(r"<details\b[^>]*>(?:(?!</details>).)*?(?:FAQ|frequently asked)", re.S | re.I)
WARNING_BANNER = re.compile(r'class="[^"]*\bw-full\b[^"]*\b(?:bg-warning|bg-amber-\d+|bg-yellow-\d+)\b[^"]*"')
KPI_ICON = re.compile(r'data-testid="[^"]*kpi[^"]*"[^>]*>(?:(?!</div>).)*?<(?:i\s+data-lucide|svg)\b', re.S)

# Bans the register names that this script cannot recognise from markup alone.
UNCHECKED_BANS = ["card-grid-navigation", "equal-weight-action-bars"]


def strip_comments(src: str) -> str:
    return COMMENT.sub(lambda m: " " * len(m.group(0)), src)


def inside_call(src: str, pos: int) -> bool:
    """True when pos lies inside a {% call dense_table/data_table %} block."""
    opens = [m.start() for m in CALL_TABLE.finditer(src) if m.start() < pos]
    if not opens:
        return False
    last = opens[-1]
    end = ENDCALL.search(src, last)
    return bool(end) and end.start() > pos


def line_of(src: str, pos: int) -> int:
    return src.count("\n", 0, pos) + 1


def check_template(path: Path, bans: list[str]) -> list[dict]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    src = strip_comments(raw)
    rel = str(path.relative_to(ROOT)).replace("\\", "/")
    out: list[dict] = []

    def add(check, pos, detail=""):
        out.append({"template": rel, "line": line_of(src, pos), "check": check, "detail": detail})

    # dense-table: every <table> comes from the macro
    if "components/dense_table.html" not in src and "components/data_table.html" not in src:
        for m in RAW_TABLE.finditer(src):
            add("raw-table", m.start(), "hand-built <table>; use dense_table (components/dense_table.html)")
    else:
        for m in RAW_TABLE.finditer(src):
            if not inside_call(src, m.start()) and "dense-table w-full border-collapse" not in src[m.start():m.start() + 80]:
                add("raw-table", m.start(), "<table> outside a dense_table/data_table call")

    # dialog: role="dialog" roots are the macro's
    for m in RAW_DIALOG.finditer(src):
        add("raw-dialog", m.start(), "hand-built dialog root; use modal (components/modal.html)")

    # form: label above control through form_group/form_field
    for m in LABEL_PAIR.finditer(src):
        div_attrs = m.group(1)
        if "flex" in div_attrs and "items-center" in div_attrs:
            continue  # an inline toolbar filter, not a form field
        add("raw-form-field", m.start(), "label + %s outside form_group/form_field (%s)" % (m.group(4), m.group(2)))
    for f in FORM_BLOCK.finditer(src):
        for m in MULTI_COL.finditer(f.group(0)):
            if "multi-column-forms" in bans:
                add("ban:multi-column-forms", f.start() + m.start(), m.group(0))
    if "floating-labels" in bans:
        for m in FLOATING.finditer(src):
            add("ban:floating-labels", m.start(), m.group(0))

    # bans recognisable in markup
    checks = [
        ("drop-shadows", SHADOW), ("drop-shadows", BLUR), ("side-drawers", DRAWER), ("full-screen-modals", FULLSCREEN),
        ("mermaid", MERMAID), ("force-directed-graph", FORCE), ("page-level-d3", D3),
        ("accordion-faq", ACCORDION_FAQ), ("full-width-warning-banners", WARNING_BANNER), ("icons-inside-kpi-cards", KPI_ICON),
    ]
    for name, rx in checks:
        if name not in bans:
            continue
        for m in rx.finditer(src):
            if name == "drop-shadows" and "shadow-none" in m.group(0):
                continue
            add("ban:" + name, m.start(), m.group(0)[:60].replace("\n", " "))
    return out


def resolve(rel: str) -> Path | None:
    for d in TEMPLATE_DIRS:
        for p in d.rglob(rel.split("/")[-1]):
            if str(p).replace("\\", "/").endswith(rel):
                return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="every template, not only the rank-1 routes")
    ap.add_argument("--templates", nargs="*", help="template paths relative to app/templates (or a module's templates dir)")
    ap.add_argument("--json", help="write the report here")
    ap.add_argument("--baseline", help="counts per check that may only fall")
    ap.add_argument("--write-baseline", help="write the current counts here")
    args = ap.parse_args()

    patterns = json.loads(PATTERNS.read_text(encoding="utf-8"))
    bans = list(patterns.get("banned_patterns", []))

    if args.all:
        files = sorted(p for d in TEMPLATE_DIRS for p in d.rglob("*.html"))
        scope = {"kind": "all", "templates": len(files)}
    else:
        routes = json.loads(ROUTES.read_text(encoding="utf-8"))
        rels = args.templates or sorted({t for r in routes for t in r["templates"]})
        files = []
        missing = []
        for rel in rels:
            p = resolve(rel)
            (files if p else missing).append(p or rel)
        scope = {"kind": "rank-1", "routes": [r["route"] for r in routes], "templates": rels, "missing": missing}

    findings: list[dict] = []
    for p in files:
        findings.extend(check_template(p, bans))
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["check"]] = counts.get(f["check"], 0) + 1
    report = {
        "patterns": str(PATTERNS.relative_to(ROOT)),
        "scope": scope,
        "bans_checked": sorted({c[4:] for c in counts if c.startswith("ban:")} | {b for b in bans if b not in UNCHECKED_BANS}),
        "bans_not_checked_mechanically": [b for b in UNCHECKED_BANS if b in bans],
        "counts": dict(sorted(counts.items())),
        "total": len(findings),
        "findings": sorted(findings, key=lambda f: (f["template"], f["line"])),
    }
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1), encoding="utf-8")
    if args.write_baseline:
        Path(args.write_baseline).write_text(json.dumps(report["counts"], indent=1, sort_keys=True) + "\n", encoding="utf-8")

    print("templates checked: %d   findings: %d" % (len(files), len(findings)))
    for check, n in sorted(counts.items()):
        print("  %-32s %d" % (check, n))
    for f in report["findings"][:400]:
        print("  %s:%d  %s  %s" % (f["template"], f["line"], f["check"], f["detail"]))

    if args.baseline:
        base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        worse = {k: (base.get(k, 0), v) for k, v in counts.items() if v > base.get(k, 0)}
        better = {k: (v, counts.get(k, 0)) for k, v in base.items() if counts.get(k, 0) < v}
        for k, (b, n) in better.items():
            print("lower than baseline: %s %d -> %d (ratchet the baseline down)" % (k, b, n))
        if worse:
            for k, (b, n) in worse.items():
                print("ABOVE BASELINE: %s %d -> %d" % (k, b, n))
            return 2
        return 0
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())

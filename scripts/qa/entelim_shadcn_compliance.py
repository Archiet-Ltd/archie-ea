"""Score every Entelim page template for shadcn compliance, and count the ones that are not.

    python entelim_shadcn_compliance.py --repo <archie-ea clone> [--ref origin/main] [--out <json>] [--md <markdown>]

The founder's rule (24 Oct 2026): every one of Entelim's page templates is 100% shadcn-compliant in look and feel,
built from the flask-basecoat macros, with the brand colour carried only by the token file. A page template is a
template that extends a base and is not an email. It is compliant when it has none of these:

  raw_component   a raw <button>, <input>, <select>, <textarea>, <table>, <dialog>, [role=tab], [role=menu] tag where
                  the library has a macro (form inputs inside a {% call %} / macro are not counted: only tags typed
                  into the page itself)
  raw_colour      a hex colour, rgb()/hsl() literal, or an off-token Tailwind colour utility
                  (bg-blue-500, text-gray-600, border-slate-200 ...); token utilities (bg-primary, text-muted-foreground)
                  are fine
  shadow          shadow, shadow-md ..., box-shadow:, drop-shadow
  inline_style    style="..."
  no_macro        the page imports no component macro at all

The output is one row per template with the counts, a total of non-compliant templates, and the top offenders, so the
conversion can be planned screen by screen and the count published can only fall.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter

RAW_COMPONENT = re.compile(r"<(button|input|select|textarea|table|dialog)\b|role=[\"'](tab|menu|menuitem|dialog)[\"']", re.I)
RAW_HEX = re.compile(r"(?<![\w&])#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b")  # anywhere, macro arguments included (25 Oct)
RAW_FUNC = re.compile(r"\b(?:rgba?|hsla?)\(")
PALETTE = r"(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)"
OFF_TOKEN = re.compile(r"(?<![\w-])(?:bg|text|border|ring|from|to|via|fill|stroke|divide|outline|decoration|placeholder|accent|caret)-" + PALETTE + r"-\d{2,3}(?:/\d+)?(?![\w-])")
SHADOW = re.compile(r"(?<![\w-])shadow(?:-(?:sm|md|lg|xl|2xl|inner))?(?![\w-])|box-shadow\s*:|drop-shadow")
INLINE_STYLE = re.compile(r"\bstyle\s*=\s*[\"']")
MACRO_IMPORT = re.compile(r"{%-?\s*(?:from|import)\b[^%]*(?:components|basecoat)/|{%-?\s*from\s+['\"][^'\"]*(?:macros|components|basecoat)/|\{\{\s*[a-z_]+\.(button|card|badge|table|tabs|dialog|dropdown|input|select|empty_state|toast)\(|\{\{\s*(button|card|badge|table|tabs|dialog|dropdown|render_field|empty_state|list_table|element_table)\(")  # import or call (25 Oct)


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True).stdout


def strip_comments(body):
    return re.sub(r"{#.*?#}", "", body, flags=re.S)


def score(body):
    body = strip_comments(body)
    counts = {
        "raw_component": len(RAW_COMPONENT.findall(body)),
        "raw_colour": len(RAW_HEX.findall(body)) + len(RAW_FUNC.findall(body)) + len(OFF_TOKEN.findall(body)),
        "shadow": len(SHADOW.findall(body)),
        "inline_style": len(INLINE_STYLE.findall(body)),
        "no_macro": 0 if MACRO_IMPORT.search(body) else 1,
    }
    counts["total"] = sum(counts.values())
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--ref", default="origin/main")
    ap.add_argument("--out")
    ap.add_argument("--md")
    a = ap.parse_args()
    files = [f for f in git(a.repo, "ls-tree", "-r", "--name-only", a.ref).split()
             if f.endswith(".html") and "/templates/" in f and "/email" not in f]
    rows = []
    for f in files:
        body = git(a.repo, "show", f"{a.ref}:{f}")
        if "{% extends" not in body:
            continue
        rows.append({"template": f, **score(body)})
    bad = [r for r in rows if r["total"]]
    kinds = Counter()
    for r in bad:
        for k in ("raw_component", "raw_colour", "shadow", "inline_style", "no_macro"):
            if r[k]:
                kinds[k] += 1
    sha = git(a.repo, "rev-parse", "--short=8", a.ref).strip()
    result = {"ref": a.ref, "sha": sha, "page_templates": len(rows), "non_compliant": len(bad),
              "compliant": len(rows) - len(bad), "templates_with": dict(kinds), "rows": sorted(rows, key=lambda r: -r["total"])}
    if a.out:
        open(a.out, "w", encoding="utf-8").write(json.dumps(result, indent=1) + "\n")
    lines = [f"# Entelim shadcn compliance on {a.ref} ({sha})", "",
             f"Page templates: {len(rows)}. Compliant: {len(rows) - len(bad)}. **Not yet compliant: {len(bad)}.**", "",
             "Templates with each kind of defect: " + ", ".join(f"{k} {v}" for k, v in kinds.most_common()), "",
             "| template | raw components | raw colours | shadows | inline styles | no macro import |", "|---|---|---|---|---|---|"]
    for r in result["rows"][:40]:
        if r["total"]:
            lines.append(f"| {r['template']} | {r['raw_component']} | {r['raw_colour']} | {r['shadow']} | {r['inline_style']} | {'yes' if r['no_macro'] else ''} |")
    text = "\n".join(lines) + "\n"
    if a.md:
        open(a.md, "w", encoding="utf-8").write(text)
    print("\n".join(lines[:5]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

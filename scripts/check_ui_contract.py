#!/usr/bin/env python3
"""UI-contract enforcement — the audit's rules, made un-regressable.

The platform's design contract (DESIGN.md) is mostly honoured but was drifting
because nothing enforced the finish-level rules. This gate counts the clearest,
lowest-false-positive violations the UI/UX audit found and ratchets them: the
number may fall as pages are fixed, but a new violation fails the build. That is
what stops "not completed to standard" from shipping.

Rules counted (each with the escape hatch ``ui-contract-ok: <reason>`` on the
offending line or the line above, for the rare legitimate case):

  native-dialog   native alert()/confirm()/prompt() instead of Platform modals
  onclick-attr    an inline onclick="" HTML handler instead of Alpine @click
  button-no-type  a <button> with no type= (defaults to submit inside a form)
  arbitrary-type  an arbitrary text-[Npx] size off the type scale

Usage:
    python scripts/check_ui_contract.py            # list violations by rule
    python scripts/check_ui_contract.py --count    # print only the total (gate)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TPL = ROOT / "app" / "templates"
JS = ROOT / "app" / "static" / "js"

HATCH = "ui-contract-ok"

# native alert/confirm/prompt not preceded by a dot or word char (so `.confirm(`
# on Platform.modal.confirm and `x.alert(` are excluded) — genuine native calls.
# `window.alert(` and friends are the same native dialogs and are matched too.
_NATIVE = re.compile(
    r"(?<![.\w])(?:alert|confirm|prompt)\s*\(|\bwindow\.(?:alert|confirm|prompt)\s*\("
)
# a *definition* of something named alert/confirm/prompt (macro, function, or
# method) is not a native-dialog call — exclude it.
_NATIVE_DEF = re.compile(
    r"\{%\s*macro\s+(?:alert|confirm|prompt)\b"
    r"|function\s+(?:alert|confirm|prompt)\b"
    r"|(?:alert|confirm|prompt)\s*\([^)]*\)\s*\{")
# a real HTML onclick attribute (double-quoted); Jinja macro kwargs use = None / '…'
_ONCLICK = re.compile(r"\sonclick\s*=\s*\"")
# arbitrary pixel type off the scale
_PXTYPE = re.compile(r"text-\[\d+px\]")
_HAS_TYPE = re.compile(r"\btype\s*=")


def _button_tags(s: str):
    """Yield (start, end, tag_text) for each <button …> opening tag, respecting
    quotes so a '>' inside an attribute value (e.g. @click="a > b") does not end
    the tag early — the naive regex did, which both under-typed the codemod and
    mis-counted the gate."""
    i = 0
    while True:
        j = s.find("<button", i)
        if j < 0:
            return
        k = j + 7
        q = None
        while k < len(s):
            ch = s[k]
            if q:
                if ch == q:
                    q = None
            elif ch in "\"'":
                q = ch
            elif ch == ">":
                break
            k += 1
        yield j, k + 1, s[j:k + 1]
        i = k + 1


def _hatched(line: str, prev: str) -> bool:
    return HATCH in line or HATCH in prev


def _in_jinja(line: str, pos: int) -> bool:
    """True if character `pos` sits inside a {{ … }} or {% … %} expression on
    this line — used to tell an HTML onclick attribute from a macro kwarg."""
    before = line[:pos]
    open_at = max(before.rfind("{{"), before.rfind("{%"))
    close_at = max(before.rfind("}}"), before.rfind("%}"))
    return open_at > close_at


def _scan_line_rules(path: Path, kind: str):
    """native-dialog / onclick-attr / arbitrary-type — line-oriented rules."""
    out = []
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        prev = lines[i - 1] if i else ""
        stripped = line.lstrip()
        is_comment = stripped.startswith(("#", "//", "*", "/*", "<!--", "{#"))
        if _hatched(line, prev):
            continue
        # native dialogs — in JS files and inside <script> of templates; skip
        # obvious comments, the Platform wrapper, and definitions of a symbol
        # that merely shares the name (macro alert, function prompt, method).
        if (not is_comment and "Platform" not in line
                and _NATIVE.search(line) and not _NATIVE_DEF.search(line)):
            out.append(("native-dialog", path, i + 1, line.strip()[:100]))
        if kind == "html":
            m = _ONCLICK.search(line)
            # a real HTML onclick attribute, not a Jinja macro kwarg
            # (stat_card(onclick=...) sits inside a {{ … }} expression).
            if m and not _in_jinja(line, m.start()):
                out.append(("onclick-attr", path, i + 1, line.strip()[:100]))
            if _PXTYPE.search(line):
                out.append(("arbitrary-type", path, i + 1, line.strip()[:100]))
    return out


def _scan_buttons(path: Path):
    """button-no-type — tag-oriented (a button tag may span several lines)."""
    out = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for start, _end, tag in _button_tags(text):
        if _HAS_TYPE.search(tag):
            continue
        # locate line number + honour the escape hatch on that line / line above
        upto = text[:start]
        lineno = upto.count("\n") + 1
        lines = text.splitlines()
        line = lines[lineno - 1] if lineno - 1 < len(lines) else ""
        prev = lines[lineno - 2] if lineno - 2 >= 0 else ""
        if _hatched(line, prev):
            continue
        out.append(("button-no-type", path, lineno, tag.replace("\n", " ")[:90]))
    return out


def collect():
    findings = []
    for path in sorted(TPL.rglob("*.html")):
        findings += _scan_line_rules(path, "html")
        findings += _scan_buttons(path)
    for path in sorted(JS.rglob("*.js")):
        findings += _scan_line_rules(path, "js")
    return findings


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    findings = collect()
    if "--count" in sys.argv:
        print(len(findings))
        return 0
    by_rule: dict[str, list] = {}
    for rule, path, ln, snippet in findings:
        by_rule.setdefault(rule, []).append((path, ln, snippet))
    for rule in sorted(by_rule):
        rows = by_rule[rule]
        print(f"\n## {rule} ({len(rows)})")
        for path, ln, snippet in rows[:40]:
            rel = path.relative_to(ROOT)
            print(f"  {rel}:{ln}  {snippet}")
        if len(rows) > 40:
            print(f"  … and {len(rows) - 40} more")
    print(f"\nTOTAL ui-contract violations: {len(findings)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

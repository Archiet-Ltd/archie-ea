#!/usr/bin/env python
"""Find form controls a screen reader cannot name.

An `<input>`, `<select>` or `<textarea>` with no label is announced as its type
and nothing else -- "combo box", "edit text" -- so the only way to learn what a
field holds is to fill it in and see what breaks. A live browser audit of every
route as a signed-in user (Aug 2026) found 34 of them: every filter select in
the toolbars on /architecture/elements and /applications/rationalization/workbench,
the "Paste CSV Data" textarea on /batch-import/new (which *had* a visible
`<label>` -- it just had no `for`, so the association was never made), and the
"select all" checkbox in six different table headers, where a keyboard user has
no way to know the box selects the whole page of rows.

A `placeholder` is not a label. It is not exposed as an accessible name by every
combination of browser and screen reader, and it disappears the moment the user
types -- so the one person who most needs the field described loses the
description first.

What counts as a label
----------------------
``aria-label``, ``title``, ``aria-labelledby``, a ``<label for=...>`` naming the
control's id, being wrapped in a ``<label>`` that carries text, and a name
supplied at runtime by Alpine (``:aria-label`` / ``x-text`` on an associated
label). The runtime case is a genuine pass, not an allowlist: an ``:aria-label``
that interpolates the row's name is a better label than any static string, and a
checker that did not understand the rule would push authors to add a *worse*
hardcoded one just to go green.

Out of scope: ``type="hidden"``, and ``type="submit"``/``"button"``/``"reset"``,
whose name comes from ``value`` -- covered by scripts/check_control_labels.py.

Escape hatch: append ``input-label-ok: <reason>`` on the flagged line or the
line above it.

Usage:
    python scripts/check_input_labels.py            # list findings
    python scripts/check_input_labels.py --count    # trailing line = count
    python scripts/check_input_labels.py --root DIR # scan another tree
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import hygiene_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ALLOW = re.compile(r"input-label-ok:")

CONTROLS = ("input", "select", "textarea")

# Names that exist statically, or that Alpine supplies before the user arrives.
HAS_NAME = re.compile(r"\baria-label\s*=|\btitle\s*=|:aria-label\s*=|\baria-labelledby\s*=")

# Input types with no user-visible field to label.
SKIP_TYPES = ("hidden", "submit", "button", "reset", "image")

# The tag and Jinja-statement/comment patterns live in hygiene_text, shared
# with check_control_labels.py, which needed the identical pair.
JINJA_STMT = hygiene_text.JINJA_STMT_OR_COMMENT_RE
TAGS = hygiene_text.HTML_TAG_RE_LOOSE


def open_tags(src: str, tag: str):
    """Yield (start, attrs, body_start) for every ``<tag ...>`` in ``src``.

    Written by hand rather than with ``<tag[^>]*>`` because attribute values in
    this codebase routinely contain ``>``: an Alpine handler like
    ``x-init="$nextTick(() => ...)"`` or ``:checked="items.length > 0"`` ends the
    naive match early, and the checker then reads only half the attributes. That
    is the worst possible failure for a gate -- it silently reports a labelled
    control as unlabelled, and an author "fixes" a defect that was never there.
    """
    pattern = re.compile(r"<%s\b" % re.escape(tag), re.I)
    pos = 0
    while True:
        m = pattern.search(src, pos)
        if not m:
            return
        i = m.end()
        quote = ""
        while i < len(src):
            ch = src[i]
            if quote:
                if ch == quote:
                    quote = ""
            elif ch in "\"'":
                quote = ch
            elif ch == ">":
                break
            i += 1
        yield m.start(), src[m.end():i], i + 1
        pos = i + 1


def templates(root: str) -> list[str]:
    out = []
    for base, _dirs, files in os.walk(root):
        for fn in files:
            if fn.endswith((".html", ".jinja", ".jinja2")):
                out.append(os.path.join(base, fn))
    return sorted(out)


def label_context(src: str) -> tuple[set[str], list[tuple[int, int]]]:
    """Ids named by a `<label for>`, and the spans of labels that carry text.

    Only labels with text (or a runtime-filled child) count as wrappers: a
    `<label>` used purely as a click target around a bare checkbox names nothing.
    """
    for_ids: set[str] = set()
    spans: list[tuple[int, int]] = []

    # `for` is collected from every <label> independently of block matching.
    # Labels nest in this codebase -- a wrapper <label> used as a click target
    # around a row that itself contains a <label for=...>. A non-greedy
    # <label>...</label> match pairs the outer open tag with the INNER close tag,
    # so the real `for` never reaches this set and a correctly-labelled control
    # gets reported. Ask each open tag for its own attributes instead.
    opens = list(open_tags(src, "label"))
    for _start, attrs, _body in opens:
        fm = re.search(r'\bfor\s*=\s*"([^"]*)"', attrs)
        if fm:
            for_ids.add(fm.group(1))

    # Wrapper spans, matched with a depth counter for the same reason.
    closes = [m.start() for m in re.finditer(r"</label\b", src, re.I)]
    stack: list[int] = []
    events = sorted([(s, "o", b) for s, _a, b in opens] + [(c, "c", c) for c in closes])
    for pos, kind, body_start in events:
        if kind == "o":
            stack.append(body_start)
            continue
        if not stack:
            continue
        body = src[stack.pop():pos]
        text = JINJA_STMT.sub(" ", TAGS.sub(" ", body)).strip()
        if text or re.search(r"\bx-text\b|\bx-html\b", body):
            spans.append((pos - len(body), pos))
    return for_ids, spans


def excused(lines: list[str], idx: int) -> bool:
    if ALLOW.search(lines[idx]):
        return True
    return idx > 0 and bool(ALLOW.search(lines[idx - 1]))


def scan_file(path: str, rel: str) -> list[tuple[str, int, str, str]]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    lines = src.split("\n")
    for_ids, spans = label_context(src)
    findings = []

    controls = [(start, tag, attrs) for tag in CONTROLS
                for start, attrs, _body in open_tags(src, tag)]
    for start, tag, attrs in sorted(controls):
        tm = re.search(r'\btype\s*=\s*"([^"]*)"', attrs)
        if tm and tm.group(1).lower() in SKIP_TYPES:
            continue
        if HAS_NAME.search(attrs):
            continue
        im = re.search(r'\bid\s*=\s*"([^"]*)"', attrs)
        if im and im.group(1) in for_ids:
            continue
        if any(a <= start < b for a, b in spans):
            continue
        idx = src.count("\n", 0, start)
        if excused(lines, idx):
            continue
        findings.append((rel, idx + 1, tag, lines[idx].strip()[:120]))
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", action="store_true", help="print only the count")
    ap.add_argument(
        "--root",
        default=ROOT,
        help="tree to scan (its app/ subdirectory); defaults to the repository. "
        "Exists so the checker can be exercised against fixtures rather than "
        "against today's count.",
    )
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    findings: list[tuple[str, int, str, str]] = []
    for path in templates(os.path.join(root, "app")):
        rel = os.path.relpath(path, root).replace("\\", "/")
        findings.extend(scan_file(path, rel))

    if not args.count:
        for rel, line, tag, text in sorted(findings):
            print(f"{rel}:{line}: <{tag}> has no label: {text}")
        if findings:
            print()
            print(
                "Prefer a visible <label for=\"...\"> where the design has room;\n"
                "use aria-label for controls the layout labels only by position\n"
                "(toolbar filters, a table's select-all checkbox). A placeholder\n"
                "is not a label -- it vanishes as soon as the user types.\n"
                "If a hit is genuinely fine, append 'input-label-ok: <reason>'."
            )
    print(len(findings))
    return 0


if __name__ == "__main__":
    sys.exit(main())

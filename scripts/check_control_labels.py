#!/usr/bin/env python
"""Find interactive controls that a screen reader announces as just "button".

A button with no accessible name is not a cosmetic problem: to anyone using a
screen reader it is an unlabelled control in a row of unlabelled controls, and
the only way to find out what it does is to press it. Icon-only buttons are the
usual source -- an `<i data-lucide="x">` or an inline `<svg>` contributes no
text -- and a live browser audit of every route in Aug 2026 found 50 of them
across the app: close buttons on every side panel and modal, delete buttons on
template sets, the send button on two chat panels.

The subtler shape, and the reason this is a gate rather than a one-off cleanup,
is a name that *looks* present and resolves to nothing. `/account/manage`
carried

    aria-labelledby="pref_{{ pref_key }}"

on its notification toggles, which reads to every later author like a filed,
deliberate label. That id belonged to the `sr-only` checkbox next to the
toggle, not to the visible `<label>`, so it resolved to an element with no text
and the toggle announced as an empty switch. Nothing but a browser -- or this
checker, which follows the id -- could tell the two apart.

What counts as a name
---------------------
Text content (Jinja output ``{{ ... }}`` included), ``aria-label``, ``title``,
an ``aria-labelledby`` that points at an id **which actually carries text**,
and a runtime name: ``x-text`` / ``x-html`` / ``:aria-label`` on the control or
on a descendant. The runtime case is a real pass, not an exemption -- Alpine
fills the name in before a user reaches the control, and hardcoding a static
label over an ``x-text`` that toggles between "Next" and "Done" would make the
name wrong half the time. Recognising the rule keeps the gate honest; a
filename allowlist would have rotted the first time one of those buttons moved.

Escape hatch: append ``control-label-ok: <reason>`` on the flagged line or the
line above it.

Usage:
    python scripts/check_control_labels.py            # list findings
    python scripts/check_control_labels.py --count    # trailing line = count
    python scripts/check_control_labels.py --root DIR # scan another tree
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import hygiene_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ALLOW = re.compile(r"control-label-ok:")

# Tags that can carry role="button" and are therefore in scope alongside <button>.
ROLE_BUTTON_TAGS = ("a", "div", "span", "li")

# A name supplied at runtime by Alpine. Not an exemption -- see the docstring.
RUNTIME_NAME = re.compile(r"\bx-text\b|\bx-html\b|:aria-label\s*=|\baria-label\s*=")

STATIC_NAME = re.compile(r"\baria-label\s*=|\btitle\s*=")
LABELLEDBY = re.compile(r'\baria-labelledby\s*=\s*"([^"]*)"')

# The tag and Jinja-statement/comment patterns live in hygiene_text, shared
# with check_input_labels.py, which needed the identical pair.
JINJA_STMT = hygiene_text.JINJA_STMT_OR_COMMENT_RE
TAGS = hygiene_text.HTML_TAG_RE_LOOSE


def templates(root: str) -> list[str]:
    out = []
    for base, _dirs, files in os.walk(root):
        for fn in files:
            if fn.endswith((".html", ".jinja", ".jinja2")):
                out.append(os.path.join(base, fn))
    return sorted(out)


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


def _element_end(src: str, tag: str, open_end: int) -> int:
    """Offset of the matching close tag, handling nesting of the same tag.

    Returns the START of ``</tag``, so ``src[open_end:end]`` is the element body
    with no closing markup left in it to be mistaken for text.
    """
    depth = 1
    pattern = re.compile(r"<(/?)%s\b" % re.escape(tag), re.I)
    pos = open_end
    while True:
        m = pattern.search(src, pos)
        if not m:
            return len(src)
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return m.start()
        pos = m.end()


def _id_has_text(src: str, ident: str) -> bool:
    """Does the element carrying this id contribute any text?

    This is the /account/manage defect: an aria-labelledby pointing at an
    `sr-only` <input>, which has no text content, so the name resolved empty.
    """
    if not ident:
        return False
    # A Jinja-interpolated id ({{ ... }}) cannot be resolved statically; look for
    # the same literal source spelling used as an id somewhere in the file.
    m = re.search(r'\bid\s*=\s*"%s"' % re.escape(ident), src)
    if not m:
        return False
    line_start = src.rfind("<", 0, m.start())
    tag_m = re.match(r"<([a-zA-Z][\w-]*)", src[line_start:])
    if not tag_m:
        return False
    tag = tag_m.group(1).lower()
    if tag in ("input", "img", "br", "hr"):  # void elements hold no text
        return False
    open_end = src.find(">", m.end())
    if open_end == -1:
        return False
    body = src[open_end + 1:_element_end(src, tag, open_end + 1)]
    return bool(visible_text(body)) or bool(RUNTIME_NAME.search(body))


def visible_text(body: str) -> str:
    """Text a screen reader would read: markup stripped, Jinja *output* kept."""
    txt = TAGS.sub(" ", body)
    txt = JINJA_STMT.sub(" ", txt)
    return txt.strip()


def excused(lines: list[str], idx: int) -> bool:
    if ALLOW.search(lines[idx]):
        return True
    return idx > 0 and bool(ALLOW.search(lines[idx - 1]))


def scan_file(path: str, rel: str) -> list[tuple[str, int, str]]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    lines = src.split("\n")
    findings = []

    starts = [("button", t) for t in open_tags(src, "button")]
    for tag in ROLE_BUTTON_TAGS:
        for t in open_tags(src, tag):
            if re.search(r'\brole\s*=\s*"button"', t[1]):
                starts.append((tag, t))

    for tag, (start, attrs, body_start) in starts:
        if STATIC_NAME.search(attrs) or RUNTIME_NAME.search(attrs):
            continue
        lb = LABELLEDBY.search(attrs)
        if lb and _id_has_text(src, lb.group(1)):
            continue
        body = src[body_start:_element_end(src, tag, body_start)]
        if visible_text(body) or RUNTIME_NAME.search(body):
            continue
        idx = src.count("\n", 0, start)
        if excused(lines, idx):
            continue
        findings.append((rel, idx + 1, lines[idx].strip()[:120]))
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
    findings: list[tuple[str, int, str]] = []
    for path in templates(os.path.join(root, "app")):
        rel = os.path.relpath(path, root).replace("\\", "/")
        findings.extend(scan_file(path, rel))

    if not args.count:
        for rel, line, text in sorted(findings):
            print(f"{rel}:{line}: button with no accessible name: {text}")
        if findings:
            print()
            print(
                "Give the control an aria-label naming the ACTION, not the icon\n"
                "-- 'Delete board', never 'trash icon'. If the name is supplied\n"
                "at runtime, x-text / :aria-label already satisfies this gate.\n"
                "If a hit is genuinely fine, append 'control-label-ok: <reason>'."
            )
    print(len(findings))
    return 0


if __name__ == "__main__":
    sys.exit(main())

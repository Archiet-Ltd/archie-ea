#!/usr/bin/env python
"""Find Jinja macro calls passing a keyword the macro does not accept.

Jinja does not resolve a macro's signature until the call executes, so a
misspelled or borrowed keyword is invisible to every static check the repo
already runs. `template-syntax` parses the file and passes; `boot-health`
resolves url_for and passes; the page then raises

    TypeError: macro 'empty_state' takes no keyword argument 'cta_label'

the first time a real request renders that branch.

The defect this gate was written for, found by a live browser audit of every
route (Aug 2026): `capability_maturity/heatmap.html` imported `empty_state`
from `components/empty_state.html`, whose CTA parameter is `cta_text`, and
called it with `cta_label=`. `cta_label` is not a typo -- it is the correct
parameter name of a *different, identically named* `empty_state` macro in
`macros/page_shell.html`. Two macros share a name across two files, so the
call reads as correct to anyone who has seen the other one, and code review
cannot tell them apart without opening both.

What made it survive to production is the branch it sits in: the empty state
renders only for an organisation with **zero** capabilities. Every seeded
database, every developer machine and every demo tenant has capabilities, so
the crash was reachable only by a brand-new customer -- the worst possible
audience for a 500. That is the argument for a gate rather than a one-line
fix: the class is undetectable by running the app, because the crashing branch
is the one nobody's data reaches.

What is checked
---------------
For every template, each `{% from 'x.html' import macro_name %}` (aliases via
`as` included) is resolved against the macro signatures actually defined in
`x.html`, and every call site's **top-level** keyword arguments are compared
against that signature. Only imported macros are checked: a macro reached
through `{% import 'x.html' as m %}` or inherited from a parent template
cannot be resolved to one definition with confidence, and a gate that guesses
is worse than no gate.

Two rules keep the scan honest rather than approximately right, and both
replace what would otherwise be a filename allowlist:

* **Nesting is respected.** `page_header(title='x', actions=[{'href':
  url_for('v', capability_id=c.id)}])` passes `title` and `actions` -- not
  `capability_id`. Splitting on commas by regex reports every nested
  `url_for` kwarg as an unknown parameter; on this tree that is ~90 false
  positives, which is a gate nobody can act on.
* **Comments are not call sites.** A `{# USAGE: {{ label(for=name,
  text=placeholder) }} #}` block documents a macro; it never executes, and its
  arguments are routinely illustrative rather than real. Jinja comments are
  stripped before scanning, so commented-out code cannot fail the gate and
  no file needs excusing by name.

A macro whose body references `kwargs` accepts arbitrary keywords (Jinja's
implicit `**kwargs` capture) and is skipped.

Escape hatch: append ``macro-kwargs-ok: <reason>`` on the flagged line or the
line above it.

Usage:
    python scripts/check_macro_kwargs.py            # list findings
    python scripts/check_macro_kwargs.py --count    # trailing line = count
    python scripts/check_macro_kwargs.py --root DIR # scan another tree
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import hygiene_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ALLOW = re.compile(r"macro-kwargs-ok:")

# Shared with check_public_repo_hygiene.py and the other checkers that mask
# the same shape -- see hygiene_text's own module docstring.
JINJA_COMMENT = hygiene_text.JINJA_COMMENT_RE
MACRO_DEF = re.compile(r"\{%-?\s*macro\s+(\w+)\s*\(")
FROM_IMPORT = re.compile(r"{%-?\s*from\s+['\"]([^'\"]+)['\"]\s+import\s+([^%]+?)-?%}")
IDENT = re.compile(r"\w+$")


def templates(root: str) -> list[str]:
    out = []
    for base, _dirs, files in os.walk(root):
        for fn in files:
            if fn.endswith((".html", ".jinja", ".jinja2")):
                out.append(os.path.join(base, fn))
    return sorted(out)


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def blank_comments(src: str) -> str:
    """Replace every Jinja comment with spaces, preserving offsets and lines.

    Offsets must survive so a finding still reports the right line number; the
    content must not, so `{# USAGE: label(text=...) #}` is not read as a call.
    """
    return JINJA_COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), src)


def close_paren(src: str, start: int) -> int:
    """Offset just past the ``)`` matching the ``(`` that ends at ``start``."""
    depth = 1
    i = start
    quote = ""
    while i < len(src):
        ch = src[i]
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(src)


def top_level_parts(body: str) -> list[str]:
    """Split an argument list on commas that are not inside a nested group.

    This is the whole reason the checker is not a regex: a nested `url_for(...)`
    or a list of dicts carries keywords that belong to the inner call.
    """
    parts = []
    depth = 0
    quote = ""
    cur = ""
    for ch in body:
        if quote:
            if ch == quote:
                quote = ""
            cur += ch
            continue
        if ch in "\"'":
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
    return [p.strip() for p in parts if p.strip()]


def param_names(args: str) -> set[str]:
    """Parameter names from a macro signature, defaults stripped."""
    names = set()
    for part in top_level_parts(args):
        name = part.split("=", 1)[0].strip()
        if re.fullmatch(r"\w+", name):
            names.add(name)
    return names


def call_keywords(body: str) -> set[str]:
    """Top-level keyword-argument names of a call. Positionals are ignored."""
    names = set()
    for part in top_level_parts(body):
        head, sep, _rest = part.partition("=")
        # `a == b` and `a >= b` are comparisons, not keyword arguments.
        if not sep or head.rstrip().endswith(("=", "!", "<", ">")):
            continue
        name = head.strip()
        if re.fullmatch(r"\w+", name):
            names.add(name)
    return names


def collect_signatures(root: str) -> dict[tuple[str, str], set[str] | None]:
    """Map (template path relative to app/templates, macro name) -> parameters.

    A value of ``None`` means the macro captures arbitrary keywords via Jinja's
    implicit ``kwargs``, so no call to it can be wrong.
    """
    base = os.path.join(root, "app", "templates")
    sigs: dict[tuple[str, str], set[str] | None] = {}
    for path in templates(base):
        rel = os.path.relpath(path, base).replace("\\", "/")
        src = blank_comments(read(path))
        for m in MACRO_DEF.finditer(src):
            end = close_paren(src, m.end())
            params = param_names(src[m.end():end - 1])
            body_end = src.find("{% endmacro %}", end)
            body = src[end:body_end if body_end != -1 else len(src)]
            key = (rel, m.group(1))
            if re.search(r"\bkwargs\b", body):
                sigs[key] = None
            elif sigs.get(key, set()) is not None:
                # Same name defined twice in one file: accept either signature
                # rather than flag calls that match only one of them.
                sigs[key] = (sigs.get(key) or set()) | params
    return sigs


def excused(lines: list[str], idx: int) -> bool:
    if ALLOW.search(lines[idx]):
        return True
    return idx > 0 and bool(ALLOW.search(lines[idx - 1]))


def scan_file(path: str, rel: str, sigs) -> list[tuple[str, int, str, str, str]]:
    raw = read(path)
    src = blank_comments(raw)
    lines = raw.split("\n")
    findings = []

    imported: dict[str, tuple[str, str]] = {}
    for m in FROM_IMPORT.finditer(src):
        tpl = m.group(1)
        for part in m.group(2).split(","):
            part = part.strip()
            if " as " in part:
                orig, alias = part.split(" as ", 1)
                imported[alias.strip()] = (tpl, orig.strip())
            elif re.fullmatch(r"\w+", part):
                imported[part] = (tpl, part)

    for alias, (tpl, orig) in imported.items():
        params = sigs.get((tpl, orig))
        if params is None:  # unknown macro, or one capturing **kwargs
            continue
        for m in re.finditer(r"\b%s\s*\(" % re.escape(alias), src):
            before = src[:m.start()]
            if IDENT.search(before) or before.endswith("."):
                continue  # part of a longer name, or an attribute call
            end = close_paren(src, m.end())
            unknown = sorted(call_keywords(src[m.end():end - 1]) - params)
            if not unknown:
                continue
            idx = src.count("\n", 0, m.start())
            if excused(lines, idx):
                continue
            findings.append((rel, idx + 1, alias, tpl, ", ".join(unknown)))
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", action="store_true", help="print only the count")
    ap.add_argument(
        "--root",
        default=ROOT,
        help="tree to scan (its app/templates subdirectory); defaults to the "
        "repository. Exists so the checker can be exercised against fixtures "
        "rather than against today's count.",
    )
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    base = os.path.join(root, "app", "templates")
    sigs = collect_signatures(root)

    findings: list[tuple[str, int, str, str, str]] = []
    for path in templates(base):
        rel = os.path.relpath(path, root).replace("\\", "/")
        findings.extend(scan_file(path, rel, sigs))

    if not args.count:
        for rel, line, alias, tpl, unknown in sorted(findings):
            print(
                f"{rel}:{line}: macro '{alias}' (from {tpl}) "
                f"takes no keyword argument: {unknown}"
            )
        if findings:
            print()
            print(
                "Open the macro named in the message and use ITS parameter names.\n"
                "A macro of the same name in another file is the usual cause --\n"
                "empty_state takes cta_text in components/empty_state.html and\n"
                "cta_label in macros/page_shell.html. If a hit is genuinely fine,\n"
                "append 'macro-kwargs-ok: <reason>'."
            )
    print(len(findings))
    return 0


if __name__ == "__main__":
    sys.exit(main())

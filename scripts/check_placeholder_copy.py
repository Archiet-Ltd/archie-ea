#!/usr/bin/env python
"""A label that says "Field" satisfies every rule and tells the user nothing.

Found 31 Aug 2026 by the owner, in the capability map's "Map Applications to"
dialog on the deployed site:

    <label for="application-search">Field</label>

53 of these are in the templates. capability_map/index.html alone has 33.

Every gate in this repository passed it, and the reason is worth stating plainly
because it is a class of blindness rather than a missed case:

* the axe-core accessibility audit passes, because "Field" IS a valid accessible
  name -- axe checks that a control HAS a label, never that the label means
  anything;
* nav-label-clarity only reads the sidebar;
* rendered-legibility measures clipping and icon ambiguity, not whether words
  carry information;
* task-completion asks whether a persona can REACH a screen, and stops at the
  door -- nothing in the estate opens a dialog and looks inside it.

That last point is the structural one. In this product the modal IS the work:
mapping applications to capabilities is the core workflow, and forms were the
one surface no instrument examined.

The general failure is that gates check PRESENCE and not MEANING. A label
exists, so it passes. An icon exists, so it passes. It is the same shape as
eight destinations behind one `layout-dashboard` glyph: each individually
valid, collectively useless.

Detection: a <label>, <th>, <button>, <h1>-<h4> or aria-label whose entire
visible text is a placeholder word -- Field, Label, Text, Input, Value, Title,
Name here, TODO, TBD, Lorem, Untitled, Placeholder, Header, Column, Item,
Description, Sample, Example, Foo, Bar, Test.

Deliberately NOT flagged: the word inside a longer phrase ("Field name",
"Custom field"), which is ordinary English; anything inside a Jinja expression,
since `{{ field.label }}` renders whatever the form supplies; and <option>
elements, where "Name" is a legitimate sort key.

Escape hatch: `placeholder-copy-ok: <reason>` on the line -- for a genuine
generic macro whose caller supplies the real word. Say who supplies it.

    python scripts/check_placeholder_copy.py
    python scripts/check_placeholder_copy.py --count

Proven-against: a label changed to "Field" in a template -- the count rises by
one naming that line, and returns when the real word is restored. Pinned
red-and-green on a synthetic tree by tests/test_gates_actually_fail.py.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import hygiene_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOW = re.compile(r"placeholder-copy-ok:[ \t]*\S")

# Words that are never what a user needs to read. Lower-cased, compared against
# the element's ENTIRE trimmed text, so "Field name" and "Custom field" pass.
PLACEHOLDERS = {
    "field", "label", "text", "input", "value", "title", "name here",
    "todo", "tbd", "lorem", "lorem ipsum", "untitled", "placeholder",
    "header", "column", "item", "sample", "example", "foo", "bar",
    "test", "xxx", "change me", "edit me",
}

# A <th> is judged more narrowly than a <label>, and the distinction is real
# rather than a convenience. A properties table with the columns "Field |
# Value" is correct English and a standard pattern -- those words ARE the
# information. A <label> reading "Field" above a search box is not: a label
# exists to name the specific input it points at.
#
# Flagging <th>Field</th> was this checker's own first false positive, caught
# before it was ever given a baseline. A gate that cries wolf gets ignored, and
# this repository has already had one gate ratcheting five phantom findings.
TABLE_HEADER_PLACEHOLDERS = {
    "todo", "tbd", "lorem", "lorem ipsum", "untitled", "placeholder",
    "header", "column", "foo", "bar", "xxx", "change me", "edit me",
    "sample", "example",
}

# The elements whose words a user actually reads to decide what to do.
ELEMENT = re.compile(
    r"<(label|th|button|h1|h2|h3|h4)\b[^>]*>(.*?)</\1>", re.S | re.I
)
ARIA = re.compile(r'aria-label\s*=\s*"([^"]*)"', re.I)
# The tag and Jinja-expression/statement patterns live in hygiene_text,
# shared with check_public_repo_hygiene.py, which needed the identical
# Jinja pattern (its own tag pattern is a distinct, looser shape -- see
# hygiene_text's own docstring for why the two are kept apart).
TAGS = hygiene_text.HTML_TAG_RE
JINJA = hygiene_text.JINJA_EXPR_OR_STMT_RE


def _visible_text(fragment: str) -> str:
    """The words a person sees, with markup and Jinja removed."""
    without_jinja = JINJA.sub("", fragment)
    return " ".join(TAGS.sub(" ", without_jinja).split()).strip()


def scan(root: str) -> list:
    problems = []
    base = os.path.join(root, "app", "templates")
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for filename in sorted(filenames):
            if not filename.endswith(".html"):
                continue
            path = os.path.join(dirpath, filename)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            try:
                with open(path, encoding="utf-8") as fh:
                    source = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            lines = source.split("\n")

            def _report(start: int, kind: str, word: str):
                line_no = source.count("\n", 0, start) + 1
                context = "\n".join(lines[max(0, line_no - 2):line_no + 1])
                if ALLOW.search(context):
                    return
                problems.append(
                    "%s:%d [placeholder-copy] %s reads %r -- it satisfies every "
                    "rule and tells the user nothing"
                    % (rel, line_no, kind, word)
                )

            for match in ELEMENT.finditer(source):
                tag = match.group(1).lower()
                text = _visible_text(match.group(2))
                if not text:
                    continue
                vocabulary = (TABLE_HEADER_PLACEHOLDERS if tag == "th"
                              else PLACEHOLDERS)
                if text.lower() in vocabulary:
                    _report(match.start(), "<%s>" % tag, text)

            for match in ARIA.finditer(source):
                text = _visible_text(match.group(1))
                if text and text.lower() in PLACEHOLDERS:
                    _report(match.start(), "aria-label", text)
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", action="store_true")
    parser.add_argument("--root", default=ROOT)
    args = parser.parse_args()
    problems = scan(os.path.abspath(args.root))
    if not args.count:
        for line in problems:
            print("  " + line)
        if problems:
            print()
            print(
                "Say what the thing actually is. If a generic macro genuinely\n"
                "receives its word from the caller, append\n"
                "'placeholder-copy-ok: <reason>' naming who supplies it."
            )
    print(len(problems))
    return 0


if __name__ == "__main__":
    sys.exit(main())

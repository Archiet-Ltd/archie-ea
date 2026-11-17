#!/usr/bin/env python
"""A collapsed sidebar with no tooltips is a row of unlabelled buttons.

The UI / interaction architect's question, per CLAUDE.md: "what does this look
like in its non-default states -- collapsed, narrow, empty, overflowing?"

Written 31 Aug 2026 from a screenshot of this product's own sidebar, collapsed.
components/admin_sidebar.html implements the collapsed state as a width change
and nothing else:

    :style="{ width: $store.sidebar.collapsed ? '4rem' : '16rem' }"

The label is rendered unconditionally inside a span carrying `flex-1 truncate`,
so collapsing does not HIDE the labels -- it CLIPS them. The rail ends up
showing "All mo..." and "Bui..." beside icons, with no tooltip and nothing
anywhere saying what an icon means. Measured at the time: 10 of 12
icon-bearing nav anchors carried neither title nor aria-label.

This is the class of defect that survived seventy green gates, because every
other gate here reads source for STRUCTURE rather than for what a person ends
up looking at. The axe-core audit passes a link whose accessible name comes
from its clipped text. dead-interactions passes, because the link works.
design-tokens passes, because the colours are right. The screen is still
unusable, and nothing in the estate had eyes.

Scope: only templates that reference the collapse mechanism itself
(`$store.sidebar`, `sidebar-collapsed`, or the library's `sidebar_menu_button(` part,
whose anchor is written by the macro and so is checked at its call: a call with no
`tooltip=` leaves an icon in the rail with no name beside it). Breadcrumbs and the public
navbar are icon-bearing nav but never collapse, so flagging them would
be asserting a consequence that cannot happen to them.

Detection: an anchor in a sidebar/nav template that contains an icon
(data-lucide) and carries neither title= nor aria-label= on the anchor itself.
Those two are what survive the visible label being hidden or clipped.

Escape hatch: `collapsed-nav-ok: <reason>`, for an anchor that is never
rendered inside a collapsible rail.

    python scripts/check_collapsed_nav_affordance.py
    python scripts/check_collapsed_nav_affordance.py --count

Proven-against: the title attribute removed from a sidebar nav anchor -- the
count rises by one naming that file and line, and returns when restored. Pinned
red-and-green on a synthetic tree by tests/test_gates_actually_fail.py.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOW = re.compile(r"collapsed-nav-ok:[ \t]*\S")
MENU_BUTTON_CALL = re.compile(r"sidebar_menu_button\((?:(?!%\}).)*%\}", re.S)
ANCHOR = re.compile(r"<a\b[^>]*>(?:(?!</a>).)*?data-lucide(?:(?!</a>).)*?</a>", re.S)
NAMED = ("title=", "aria-label=")


def scan(root: str) -> list:
    problems = []
    patterns = [
        os.path.join(root, "app", "templates", "**", "*sidebar*.html"),
        os.path.join(root, "app", "templates", "components", "*nav*.html"),
    ]
    paths = sorted({p for pattern in patterns for p in glob.glob(pattern, recursive=True)})
    for path in paths:
        # Only templates that actually PARTICIPATE in the collapsible rail.
        # The first scan globbed every *nav* and *sidebar* template and flagged
        # breadcrumbs and the public navbar, neither of which ever collapses --
        # so the finding's own reason ("once the rail collapses to 4rem") was
        # untrue of 7 of its 8 results. A gate that cries wolf gets ignored,
        # and that has already happened once in this repo today.
        try:
            with open(path, encoding="utf-8") as probe:
                head = probe.read()
        except (OSError, UnicodeDecodeError):
            continue
        uses_parts = "sidebar_menu_button(" in head and not path.endswith("ui_sidebar.html")
        if "store.sidebar" not in head and "sidebar-collapsed" not in head and not uses_parts:
            continue
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        try:
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        for match in MENU_BUTTON_CALL.finditer(source) if uses_parts else ():
            call = match.group(0)
            if "tooltip=" in call or ALLOW.search(call):
                continue
            line_no = source.count("\n", 0, match.start()) + 1
            problems.append(
                "%s:%d [collapsed-nav-affordance] sidebar_menu_button with no tooltip -- "
                "in the icon rail this is an unlabelled button" % (rel, line_no)
            )
        for match in ANCHOR.finditer(source):
            segment = match.group(0)
            open_tag = segment.split(">", 1)[0]
            if any(marker in open_tag for marker in NAMED):
                continue
            if ALLOW.search(segment):
                continue
            line_no = source.count("\n", 0, match.start()) + 1
            problems.append(
                "%s:%d [collapsed-nav-affordance] icon link with no title or "
                "aria-label -- once the rail collapses to 4rem this is an "
                "unlabelled button" % (rel, line_no)
            )
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
                "Add a title naming the destination so the collapsed rail stays\n"
                "navigable, or append 'collapsed-nav-ok: <reason>'."
            )
    print(len(problems))
    return 0


if __name__ == "__main__":
    sys.exit(main())

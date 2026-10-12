#!/usr/bin/env python
"""Write an accessibility conformance statement from an audited journey.

The statement is generated, never written by hand: it is built from the axe
results and the keyboard walk that a browser journey recorded, so it can only
say what the run measured.

    python scripts/accessibility_conformance.py run.json            # prints the statement
    python scripts/accessibility_conformance.py run.json -o out.md  # writes it

``run.json`` is the record a journey writes (see
``tests/smoke/test_shared_dashboard_accessibility.py``)::

    {
      "journey": "Read a shared dashboard",
      "tags": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"],
      "generated_at": "2026-09-29T10:00:00Z",
      "pages": [
        {"name": "Shared maturity heatmap", "path": "/shared/<token>",
         "violations": [{"id": "color-contrast", "impact": "serious", "nodes": 2}],
         "passes": 41,
         "keyboard": {"controls": 3, "unnamed": [], "unreachable": [], "no_focus_ring": []}}
      ]
    }

The journey meets the standard only when every page was audited with the full
WCAG 2.2 AA tag set, no page has a violation, and every control was reached by
keyboard, carried a name and showed where focus was. Anything else is stated as
not meeting it, with each failure listed.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List

STANDARD = "WCAG 2.2 level AA"
#: The audit's tag set (tests/smoke/test_accessibility_audit.py TAGS): WCAG 2.0,
#: 2.1 and 2.2 at levels A and AA.
REQUIRED_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]


def _page_failures(page: Dict[str, Any]) -> List[str]:
    failures = []
    for v in page.get("violations") or []:
        nodes = v.get("nodes")
        count = nodes if isinstance(nodes, int) else len(nodes or [])
        failures.append(f"{v.get('id')} ({v.get('impact') or 'impact not given'}, {count} element(s))")
    keyboard = page.get("keyboard")
    if not keyboard:
        failures.append("keyboard walk not recorded")
    else:
        for label, key in (("control with no name", "unnamed"),
                           ("control not reached by keyboard", "unreachable"),
                           ("control with no visible focus", "no_focus_ring")):
            for item in keyboard.get(key) or []:
                failures.append(f"{label}: {item}")
    return failures


def build_statement(run: Dict[str, Any]) -> Dict[str, Any]:
    """{"meets": bool, "failures": {page name: [..]}, "text": markdown}."""
    pages = run.get("pages") or []
    tags = list(run.get("tags") or [])
    missing_tags = [t for t in REQUIRED_TAGS if t not in tags]
    failures: Dict[str, List[str]] = {}
    for page in pages:
        found = _page_failures(page)
        if found:
            failures[page.get("name") or page.get("path") or "page"] = found
    meets = bool(pages) and not missing_tags and not failures

    journey = run.get("journey") or "Journey"
    lines = [f"# Accessibility conformance: {journey}", ""]
    if meets:
        lines.append(f"This journey meets {STANDARD}.")
    elif not pages:
        lines.append(f"This journey has not been audited, so it is not stated to meet {STANDARD}.")
    else:
        lines.append(f"This journey does not yet meet {STANDARD}.")
    lines += [
        "",
        f"- Checked: {run.get('generated_at') or 'time not recorded'}",
        f"- Rules: axe-core, tags {', '.join(tags) if tags else 'none recorded'}",
        "- Keyboard: every control reached with Tab, named, and showing a visible focus indicator",
        "",
        "| Page | Rules passed | Controls reached by keyboard | Failures |",
        "|---|---|---|---|",
    ]
    for page in pages:
        name = page.get("name") or page.get("path") or "page"
        keyboard = page.get("keyboard") or {}
        lines.append(
            f"| {name} | {page.get('passes', 0)} | {keyboard.get('controls', 0)} | "
            f"{len(failures.get(name, []))} |"
        )
    if missing_tags:
        lines += ["", f"Not audited against: {', '.join(missing_tags)}."]
    for name, found in failures.items():
        lines += ["", f"## {name}", ""] + [f"- {f}" for f in found]
    lines += [
        "",
        "Automated rules and a keyboard walk cover the checks a browser can make; "
        "they are run again on every build.",
        "",
    ]
    return {"meets": meets, "failures": failures, "text": "\n".join(lines)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", help="the journey's recorded run (JSON)")
    parser.add_argument("-o", "--output", help="write the statement here instead of printing it")
    args = parser.parse_args(argv)
    with open(args.run, encoding="utf-8") as fh:
        statement = build_statement(json.load(fh))
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(statement["text"])
    else:
        sys.stdout.write(statement["text"])
    return 0 if statement["meets"] else 1


if __name__ == "__main__":
    sys.exit(main())

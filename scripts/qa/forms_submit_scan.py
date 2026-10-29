#!/usr/bin/env python
"""Count templates holding a <form> that has no way to submit.

    python scripts/qa/forms_submit_scan.py             # list offenders, exit 1 if the count rose
    python scripts/qa/forms_submit_scan.py --count     # count only
    python scripts/qa/forms_submit_scan.py --baseline  # record the current count (never raises it)

The button macro renders type="button" unless told otherwise, so a form whose primary
button was converted from a raw submit button can look right and post nothing. A form
counts as submitting when its markup holds a type="submit" control (raw or as a
button(...) argument), an <input type="submit">, the form_footer macro, a button that
names the form with form="<id>", or an @submit / x-on:submit / hx-post / data-autosubmit handler, a
submit-on-change control (this.form.submit()), a WTForms submit field, or a script that
submits the form by id.
The check reads templates statically.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE = REPO_ROOT / "scripts" / "forms_submit_baseline.json"

FORM_RE = re.compile(r"<form\b(?P<attrs>[^>]*)>(?P<body>.*?)</form\s*>", re.S | re.I)
SUBMIT_RES = [
    re.compile(r"""type\s*=\s*(['"])submit\1""", re.I),
    re.compile(r"\bform_footer\s*\("),
    re.compile(r"\bapp_picker\s*\((?![^)]*show_submit\s*=\s*False)"),
]
HANDLER_RE = re.compile(r"(?:@submit|x-on:submit|hx-post|data-autosubmit)\b", re.I)
# submit-on-change, or a WTForms field rendering the submit button
SCRIPTED_SUBMIT_RE = re.compile(r"data-autosubmit|\.form\.(?:submit|requestSubmit)\(|\{\{\s*\w+\.submit\(", re.I)
# A form submitted by a script, or split across macros, says so on the line above or on the <form> line.
EXEMPT_RE = re.compile(r"forms-submit-ok:\s*\S")
COMMENT_RE = re.compile(r"\{#.*?#\}", re.S)
FORM_ID_RE = re.compile(r"""\bid\s*=\s*(['"])(?P<id>[^'"]+)\1""")


def template_paths():
    for root in (REPO_ROOT / "app",):
        yield from sorted(root.rglob("*.html"))


def offenders() -> list[tuple[str, int]]:
    found = []
    for path in template_paths():
        raw = path.read_text(encoding="utf-8", errors="replace")
        raw_lines = raw.splitlines()
        text = COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), raw)
        if "<form" not in text.lower():
            continue
        for m in FORM_RE.finditer(text):
            attrs, body = m.group("attrs"), m.group("body")
            if HANDLER_RE.search(attrs) or any(r.search(body) for r in SUBMIT_RES):
                continue
            if SCRIPTED_SUBMIT_RE.search(body):
                continue
            fid = FORM_ID_RE.search(attrs)
            if fid and re.search(r"""(?:getElementById\(\s*['"]%s['"]\s*\)|\$\(\s*['"]#%s['"]\s*\)|\.form\b)[^;\n]*\.(?:submit|requestSubmit)\(""" % ((re.escape(fid.group("id")),) * 2), text):
                continue
            line = text.count("\n", 0, m.start()) + 1
            if any(EXEMPT_RE.search(raw_lines[i]) for i in (line - 2, line - 1) if 0 <= i < len(raw_lines)):
                continue
            if fid and re.search(r"""['"]?\bform['"]?\s*[=:]\s*\s*(['"])%s\1""" % re.escape(fid.group("id")), text):
                continue
            found.append((str(path.relative_to(REPO_ROOT)), line))
    return found


def main(argv: list[str]) -> int:
    found = offenders()
    count = len(found)
    if "--count" in argv:
        print(count)
        return 0
    baseline = json.loads(BASELINE.read_text())["forms_without_submit"] if BASELINE.exists() else 0
    if "--baseline" in argv:
        if count > baseline and BASELINE.exists():
            print(f"refusing to raise the baseline from {baseline} to {count}")
            return 1
        BASELINE.write_text(json.dumps({"forms_without_submit": count}, indent=2) + "\n")
        print(f"baseline set to {count}")
        return 0
    for rel, line in found:
        print(f"{rel}:{line}: <form> has no submit control")
    print(f"{count} forms without a submit control (baseline {baseline})")
    return 1 if count > baseline else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Shipped templates must not use the browser's own alert() or confirm().

DESIGN.md has said so for a long time and 20 call sites had accumulated anyway,
which is what happens to a rule with nothing enforcing it.

The reasons are not cosmetic:

  - They block the browser thread. Nothing renders, no timer fires, and any
    automation driving the page stops dead until a human clicks - which is also
    why they break the Playwright smoke journeys.
  - They cannot be styled or translated, so an enterprise product shows a Chrome
    dialog captioned "127.0.0.1 says" to a Fortune 500 buyer.
  - Chrome suppresses them after repeated use and inside cross-origin iframes.
    A delete guard that silently stops appearing is worse than no guard: the
    user still expects to be asked.

Platform.confirm() and Platform.alert() (core/07-dialog.js) are the
replacements. Both return a promise, so:

    if (await Platform.confirm('Delete this?')) doDelete();

This is an absolute gate rather than a ratchet, because the count is zero and
the fix for a new one is a two-line edit. A ratchet would be the right shape if
there were a backlog to burn down; there is not.
"""

import glob
import os
import re

import pytest

pytestmark = pytest.mark.journey

TEMPLATE_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "templates"
)

# `(?<![.\w])` is load-bearing: without it the pattern also matches the DOT in
# Platform.confirm( and window.confirm(, because "." is a word boundary. An
# earlier count using \bconfirm\( reported 52 native dialogs when the real
# number was 7 - the rest were the replacement API being counted as the problem.
NATIVE_DIALOG = re.compile(r"(?<![.\w])(?:alert|confirm)\(|window\.(?:alert|confirm)\(")


def _offending_lines(path):
    found = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, 1):
            if not NATIVE_DIALOG.search(line):
                continue
            stripped = line.strip()
            # A documented example that names the hatch scripts/check_ui_contract.py
            # honours (components/alert.html's call shapes) is not a call site either.
            if "ui-contract-ok" in line:
                continue
            # Jinja comments explaining what NOT to do are not call sites.
            if stripped.startswith("{#") or stripped.startswith("<!--"):
                continue
            # Neither is a JS comment saying "never alert()" - settings/index.html
            # was flagged for its own guidance note.
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            # Nor is DEFINING a macro called alert(). components/alert.html has
            # exported `alert` since it was written, and enterprise/
            # ai_architecture_analysis.html imports it by that name.
            #
            # This matters more than a tidy count: the previous attempt to satisfy
            # this test renamed the macro to `Platform.alert`, which Jinja cannot
            # parse - a macro name may not contain a dot - so components/alert.html
            # stopped compiling and every page importing it would 500. The
            # template-syntax gate caught it. A test that cannot tell a definition
            # from a call site will keep provoking that fix, so teach it the
            # difference instead.
            if re.search(r"\{%-?\s*macro\s+(?:alert|confirm)\s*\(", stripped):
                continue
            found.append((number, stripped[:120]))
    return found


def test_no_native_browser_dialogs_in_templates():
    offenders = []
    for path in glob.glob(os.path.join(TEMPLATE_ROOT, "**", "*.html"), recursive=True):
        for number, text in _offending_lines(path):
            relative = os.path.relpath(path, TEMPLATE_ROOT).replace(os.sep, "/")
            offenders.append("%s:%d  %s" % (relative, number, text))

    assert not offenders, (
        "%d native browser dialog(s) found. They block the browser thread, "
        "cannot be styled or translated, and Chrome suppresses them after "
        "repeated use - so a delete guard can silently stop appearing.\n\n"
        "Use Platform.confirm() / Platform.alert() instead; both return a "
        "promise:\n"
        "    if (await Platform.confirm('Delete this?')) doDelete();\n\n%s"
        % (len(offenders), "\n".join(offenders))
    )


def test_the_replacement_api_is_actually_shipped():
    """A ban with no alternative just moves the problem into JS files."""
    bundle = os.path.join(
        os.path.dirname(TEMPLATE_ROOT), "static", "js", "bundles", "core-admin.js"
    )
    assert os.path.exists(bundle), "the committed core-admin.js bundle is missing"

    source = open(bundle, encoding="utf-8").read()
    assert "// >>> app/static/js/core/07-dialog.js" in source, (
        "core-admin.js does not contain the 07-dialog source marker"
    )
    for api in ("register('confirm'", "register('alert'"):
        assert api in source, "core-admin.js does not register %s" % api

    base = os.path.join(TEMPLATE_ROOT, "layouts", "admin_base.html")
    assert "js/bundles/core-admin.js" in open(base, encoding="utf-8").read(), (
        "core-admin.js is never loaded, so Platform.confirm is undefined at "
        "runtime and every converted call site throws")


def test_the_dialog_is_keyboard_accessible():
    """A modal the keyboard can walk out of is not modal.

    Checked by reading the source rather than driving a browser: this file is a
    fast static gate, and the behavioural version belongs in the smoke journeys.
    """
    dialog_js = os.path.join(
        os.path.dirname(TEMPLATE_ROOT), "static", "js", "core", "07-dialog.js"
    )
    source = open(dialog_js, encoding="utf-8").read()

    for requirement, why in [
        ("role', 'alertdialog", "screen readers need the dialog role"),
        ("aria-modal", "without it the page behind is still announced"),
        ("aria-labelledby", "the dialog must be named by its heading"),
        ("Escape", "Escape must dismiss it"),
        ("Tab", "focus must be trapped inside"),
        ("textContent", "the message must not be an innerHTML sink"),
    ]:
        assert requirement in source, "%s - %s" % (requirement, why)

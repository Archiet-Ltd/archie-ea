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

Three further rules, from shadcn's own agent rules, are counted separately
(each its own ratchet in verify.py, not part of the total above) and run with
``--rule``. They read page and component templates outside the macro
definitions, so the canonical macros are never counted against themselves:

  dialog-title  a dialog, sheet or drawer with no title: a modal/dialog/drawer
                call with no title argument, or an element with role="dialog"
                whose aria-labelledby names no element in the template;
  empty-state   a hand-written "No ... yet" block with its own icon, instead
                of the ui.empty block (basecoat/empty.html);
  legacy-macro  a call to an old macro (empty_state, page_header, modal,
                dense_table, shadcn_*) outside the files that define it,
                counted whether the call is direct or through an aliased
                import or a namespace import;
  raw-button    a hand-written <button> outside a macro definition, instead
                of the button component;
  missing-value a bare dash, "N/A" or zero shown where a value is missing
                ({{ x or '—' }}, |default('—'), {% else %}—), instead of
                explained_value with its reason and next step;
  second-library  a macro defined outside app/templates/components and
                app/templates/basecoat under a name the library already
                exports (read from those folders), or a page importing such a
                name from anywhere but the library;
  form-field    a form control inside a <form> that is not rendered by
                form_field or placed inside a form_group call (label, control,
                description and error together).

Usage:
    python scripts/check_ui_contract.py            # list violations by rule
    python scripts/check_ui_contract.py --count    # print only the total (gate)
    python scripts/check_ui_contract.py --rule dialog-title|empty-state|form-field|legacy-macro|raw-button|missing-value|second-library [--count|--list]
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
_NATIVE = re.compile(r"(?<![.\w])(?:alert|confirm|prompt)\s*\(")
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


# ---- component rules (shadcn agent rules) --------------------------------

_MACRO_BLOCK = re.compile(r"{%-?\s*macro\b.*?{%-?\s*endmacro\s*-?%}", re.DOTALL)
_JINJA_COMMENT = re.compile(r"{#.*?#}", re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_DIALOG_CALL = re.compile(
    r"{%-?\s*call\s+(modal|dialog|drawer|modal_form|confirmation_dialog)\s*\((.*?)\)\s*-?%}", re.DOTALL)
_TITLE_ARG = re.compile(r"\b(title|labelledby)\s*=\s*(?!(?:''|\"\")\s*(?:[,)]|$))\S")
_LABELLEDBY = re.compile(r"aria-labelledby\s*=\s*[\"']([^\"']*)[\"']")
_EMPTY_TEXT = re.compile(
    r">\s*No\s+[A-Za-z][\w\s,'/-]{1,60}?\s(?:yet|found|available|recorded)\b[^<]*<")
_ICON = re.compile(r"data-lucide=|<svg\b")
_FORM_BLOCK = re.compile(r"<form\b.*?</form>", re.DOTALL | re.IGNORECASE)
_CONTROL = re.compile(r"<(input|select|textarea)\b", re.IGNORECASE)
_NON_FIELD_TYPES = ("hidden", "submit", "button", "reset", "image")
_GROUP_CALL = re.compile(
    r"{%-?\s*call\s+form_group\b.*?{%-?\s*endcall\s*-?%}", re.DOTALL)


def _mask(match: re.Match) -> str:
    return re.sub(r"[^\n]", " ", match.group(0))


def _template_view(text: str) -> str:
    """The template with comments and macro definitions blanked, line numbers intact."""
    for rx in (_JINJA_COMMENT, _HTML_COMMENT, _MACRO_BLOCK):
        text = rx.sub(_mask, text)
    return text


def _tag_end(text: str, start: int) -> int:
    """Index just past the '>' closing the tag opened at start, quotes respected."""
    q = None
    k = start + 1
    while k < len(text):
        ch = text[k]
        if q:
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
        elif ch == ">":
            return k + 1
        k += 1
    return len(text)


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _rule_dialog_title(rel: str, text: str, view: str):
    out = []
    for m in _DIALOG_CALL.finditer(view):
        if not _TITLE_ARG.search(m.group(2)):
            out.append((_line_of(text, m.start()),
                        f"{m.group(1)}() call has no title; pass title='...'"))
    for m in re.finditer(r"<(?:dialog\b|[a-z]+\b[^>]*?role\s*=\s*[\"'](?:alert)?dialog[\"'])", view):
        tag = view[m.start():_tag_end(view, m.start())]
        if "role" not in tag and not tag.startswith("<dialog"):
            continue
        lab = _LABELLEDBY.search(tag)
        if not lab or not lab.group(1).strip():
            out.append((_line_of(text, m.start()), "dialog has no aria-labelledby title"))
        elif f'id="{lab.group(1)}"' not in view and f"id='{lab.group(1)}'" not in view:
            out.append((_line_of(text, m.start()),
                        f"dialog aria-labelledby=\"{lab.group(1)}\" names no element in this template"))
    return out


def _rule_empty_state(rel: str, text: str, view: str):
    out = []
    lines = view.splitlines()
    for m in _EMPTY_TEXT.finditer(view):
        ln = _line_of(view, m.start() + 1)
        window = "\n".join(lines[max(0, ln - 9):ln])
        if _ICON.search(window):
            out.append((ln, "hand-written empty state; use the ui.empty block (basecoat/empty.html)"))
    return out


def _rule_form_field(rel: str, text: str, view: str):
    out = []
    grouped = [(g.start(), g.end()) for g in _GROUP_CALL.finditer(view)]
    for f in _FORM_BLOCK.finditer(view):
        for c in _CONTROL.finditer(view, f.start(), f.end()):
            tag = view[c.start():_tag_end(view, c.start())]
            kind = re.search(r"\btype\s*=\s*[\"']?(\w+)", tag)
            if c.group(1).lower() == "input" and kind and kind.group(1).lower() in (
                    _NON_FIELD_TYPES):
                continue
            if any(a <= c.start() < b for a, b in grouped):
                continue
            out.append((_line_of(text, c.start()),
                        f"<{c.group(1).lower()}> outside form_field/form_group"))
    return out

# Old macros kept only as compatibility wrappers. A call elsewhere is the
# second implementation the design system retires.
_LEGACY_NAME_PATTERN = r"empty_state|page_header|modal|dense_table|shadcn_\w+"
_LEGACY_CALL = re.compile(
    r"(?:{{-?|{%-?\s*call(?:\([^)]*\))?)\s*"
    r"(" + _LEGACY_NAME_PATTERN + r")\s*\(")
_LEGACY_HOMES = (
    "components/empty_state.html", "components/page_header.html",
    "components/modal.html", "components/dense_table.html",
    "macros/page_shell.html", "macros/shadcn_compat.html")
# `{% from '<legacy home>' import <name> as <alias> %}` then a call through
# the alias, or `{% import '<legacy home>' as <ns> %}` then a call through
# `ns.<name>(` — both reach the old macro exactly as a direct call does, so
# renaming the import is not a way out of the ratchet.
_IMPORT_NS = re.compile(
    r"{%-?\s*import\s+['\"]([^'\"]+)['\"]\s+as\s+(\w+)\s*-?%}")
_LEGACY_ITEM = re.compile(
    r"^\s*(" + _LEGACY_NAME_PATTERN + r")\s*(?:as\s+(\w+))?\s*$")
_BUTTON_TAG = re.compile(r"<button\b", re.IGNORECASE)
# A value expression whose last step substitutes a placeholder:
# {{ x or '—' }}, {{ a if x else 'N/A' }}, {{ x|default('-') }}. A bare 0 is a
# display only when the expression sits directly in element text, not in an
# attribute, a script or a condition.
_PLACEHOLDER = r"(?:—|&mdash;|-|N/A|n/a)"
_MISSING_EXPR = re.compile(
    r"{{-?\s*[^{}]*?(?:\bor\s+|\belse\s+)['\"]" + _PLACEHOLDER + r"['\"]\s*-?}}"
    r"|{{-?\s*[^{}]*?\|\s*default\(\s*['\"]" + _PLACEHOLDER + r"['\"]\s*(?:,[^)]*)?\)\s*-?}}")
_MISSING_ZERO = re.compile(
    r">\s*{{-?\s*[^{}()]*?(?:\bor\s+|\belse\s+)['\"]?0['\"]?\s*-?}}\s*<")
_MISSING_ELSE = re.compile(
    r"{%-?\s*else\s*-?%}\s*(?:<(\w+)[^>]*>\s*)?" + _PLACEHOLDER + r"\s*(?:</\1>\s*)?{%-?\s*endif")


def _legacy_message(name: str) -> str:
    if name == "empty_state":
        return f"{name}() is an old macro; use the ui.empty block (basecoat/empty.html)"
    return f"{name}() is an old macro; use the component"


def _legacy_call_rx(target: str) -> re.Pattern:
    return re.compile(r"(?:{{-?|{%-?\s*call(?:\([^)]*\))?)\s*" + target + r"\s*\(")


def _rule_legacy_macro(rel: str, text: str, view: str):
    if rel.replace("\\", "/").endswith(_LEGACY_HOMES):
        return []
    out = [(_line_of(text, m.start()), _legacy_message(m.group(1)))
           for m in _LEGACY_CALL.finditer(view)]
    for m in _FROM_IMPORT.finditer(view):
        if not m.group(1).replace("\\", "/").endswith(_LEGACY_HOMES):
            continue
        for item in m.group(2).split(","):
            im = _LEGACY_ITEM.match(item)
            if not im or not im.group(2):
                continue  # no "as alias": already counted as a direct call above
            name, alias = im.group(1), im.group(2)
            out += [(_line_of(text, c.start()), _legacy_message(name))
                    for c in _legacy_call_rx(re.escape(alias)).finditer(view)]
    for m in _IMPORT_NS.finditer(view):
        if not m.group(1).replace("\\", "/").endswith(_LEGACY_HOMES):
            continue
        rx = _legacy_call_rx(re.escape(m.group(2)) + r"\s*\.\s*(" + _LEGACY_NAME_PATTERN + r")")
        out += [(_line_of(text, c.start()), _legacy_message(c.group(1)))
                for c in rx.finditer(view)]
    return out


def _rule_raw_button(rel: str, text: str, view: str):
    return [(_line_of(text, m.start()), "hand-written <button>; use the button component")
            for m in _BUTTON_TAG.finditer(view)]


def _rule_missing_value(rel: str, text: str, view: str):
    out = []
    for rx in (_MISSING_EXPR, _MISSING_ZERO, _MISSING_ELSE):
        for m in rx.finditer(view):
            out.append((_line_of(text, m.start()),
                        "missing value shown as a bare dash or zero; use explained_value"))
    return out


_MACRO_DEF = re.compile(r"{%-?\s*macro\s+(\w+)\s*\(")
_FROM_IMPORT = re.compile(r"{%-?\s*from\s+['\"]([^'\"]+)['\"]\s+import\s+(.*?)\s*-?%}", re.DOTALL)
_LIBRARY_DIRS = ("components", "basecoat")


def _library_macro_names() -> frozenset:
    """Macro names exported by the component library, read from its files."""
    names = set()
    for sub in _LIBRARY_DIRS:
        for path in (TPL / sub).rglob("*.html"):
            text = _JINJA_COMMENT.sub("", path.read_text(encoding="utf-8", errors="replace"))
            names.update(n for n in _MACRO_DEF.findall(text) if not n.startswith("_"))
    return frozenset(names)


def _outside_library(rel: str) -> bool:
    try:
        parts = Path(rel).resolve().relative_to(TPL.resolve()).parts
    except ValueError:
        return True
    return not parts or parts[0] not in _LIBRARY_DIRS


def _rule_second_library(rel: str, text: str, view: str):
    if not _outside_library(rel):
        return []
    names = _library_macro_names()
    plain = _HTML_COMMENT.sub(_mask, _JINJA_COMMENT.sub(_mask, text))
    out = [(_line_of(text, m.start()),
            f"macro {m.group(1)}() duplicates a library component; use the library's")
           for m in _MACRO_DEF.finditer(plain) if m.group(1) in names]
    for m in _FROM_IMPORT.finditer(plain):
        if m.group(1).split("/")[0] in _LIBRARY_DIRS:
            continue
        for item in m.group(2).split(","):
            name = item.strip().split()[0] if item.strip() else ""
            if name in names:
                out.append((_line_of(text, m.start()),
                            f"imports {name} from {m.group(1)}, not from the library"))
    return out


COMPONENT_RULES = {
    "dialog-title": _rule_dialog_title,
    "empty-state": _rule_empty_state,
    "form-field": _rule_form_field,
    "legacy-macro": _rule_legacy_macro,
    "raw-button": _rule_raw_button,
    "missing-value": _rule_missing_value,
    "second-library": _rule_second_library,
}


def collect_component_rule(rule: str):
    """[(repo path, line, message)] for one component rule, hatch lines honoured."""
    fn = COMPONENT_RULES[rule]
    out = []
    for path in sorted(TPL.rglob("*.html")):
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for ln, msg in fn(str(path), text, _template_view(text)):
            line = lines[ln - 1] if ln - 1 < len(lines) else ""
            prev = lines[ln - 2] if ln >= 2 else ""
            if _hatched(line, prev):
                continue
            out.append((path.relative_to(ROOT).as_posix(), ln, msg))
    return out


def main() -> int:
    if "--rule" in sys.argv:
        rule = sys.argv[sys.argv.index("--rule") + 1]
        found = collect_component_rule(rule)
        if "--list" in sys.argv:
            for rel, ln, msg in found:
                print(f"{rel}:{ln}: {msg}")
        print(len(found))
        return 0
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

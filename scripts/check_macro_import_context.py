#!/usr/bin/env python
"""A macro containing a <script> must be imported `with context`, or its JS is dead.

Jinja's ``{% from 'x.html' import macro %}`` and ``{% import 'x.html' as x %}`` do
NOT pass the caller's context. ``CspNonceExtension``
(``app/_bootstrap/security.py``) rewrites every template-authored ``<script>`` at
compile time to carry ``nonce="{{ csp_nonce }}"``, and ``csp_nonce`` comes from a
context processor — so inside a context-less import it is undefined and renders as
``nonce=""``.

The CSP is ``script-src 'self' 'nonce-…' 'strict-dynamic'``. ``strict-dynamic``
disables host allowlisting, so an empty nonce is not a downgrade to "allowed by
origin" — it is a hard refusal, for inline and ``src=`` alike:

    Refused to execute inline script ...
    Refused to load the script ...

The page still returns 200, the template still compiles, and the script simply
never runs. One sweep found 14 live sites shipping dead JavaScript: the AI chat's
document-upload panel, the page-guide drawer included by ``layouts/admin_base.html``
(so: every admin page), the roadmap and gantt widgets, the LLM recommendation
panels on four strategic pages, and the password-strength meter on every account
form. It was found by loading a page in a browser, which is the only way it can be
found at runtime — hence this gate.

``{% include %}`` passes context by default and is only reported when it says
``without context`` explicitly.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

from jinja2 import Environment, nodes

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _template_roots() -> list[pathlib.Path]:
    """Every directory Jinja loads templates from, not just the main one.

    Blueprints may declare their own ``template_folder=``; a macro imported from
    one of those is subject to exactly the same defect.
    """
    roots = [ROOT / "app" / "templates"]
    for extra in (ROOT / "app" / "modules").glob("*/templates"):
        roots.append(extra)
    static_templates = ROOT / "app" / "static" / "templates"
    if static_templates.is_dir():
        roots.append(static_templates)
    return [r for r in roots if r.is_dir()]


def _rel(path: pathlib.Path, root: pathlib.Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


_SCRIPT_TAG_RE = re.compile(r"<script\b", re.IGNORECASE)
_TYPE_ATTR_RE = re.compile(r"""\btype\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.IGNORECASE)
_JAVASCRIPT_TYPE_RE = re.compile(
    r"^(?:(?:text|application)/(?:x-)?(?:java|ecma)script|text/(?:jscript|livescript|javascript1\.[0-5]))$",
    re.IGNORECASE,
)


def _script_tag_runs_code(text: str, start: int) -> bool:
    """True when the <script> opening at ``start`` is one a browser executes.

    A script element with no type, an empty type, ``module`` or a JavaScript MIME type is
    executed, so CSP applies to it. Any other type (``application/json`` for a chart's data
    block, ``application/ld+json``, ``text/template``) is a data block the browser never
    runs, so an empty nonce on it costs nothing. A tag whose attributes are cut short by a
    Jinja expression cannot be read, so it counts as executable.
    """
    end = text.find(">", start)
    if end == -1:
        return True
    attrs = text[start + len("<script"):end]
    match = _TYPE_ATTR_RE.search(attrs)
    if match is None:
        return True
    script_type = (match.group(1) or match.group(2) or match.group(3) or "").strip()
    if script_type == "" or script_type.lower() == "module":
        return True
    return bool(_JAVASCRIPT_TYPE_RE.match(script_type))


def _has_script_in_macro(tree) -> bool:
    """True when an executable <script> appears in template text inside a macro body.

    Checked against the parsed tree rather than the raw source so that a
    ``<script`` mentioned in a ``{# … #}`` comment does not count — Jinja strips
    comments before this point. components/modal.html is exactly that case: its
    only ``<script`` is a usage example in a doc comment and never renders.

    A data block such as ``<script type="application/json">`` is not executable, so a
    macro holding only data blocks may be imported without context.
    """
    for macro in tree.find_all(nodes.Macro):
        for data in macro.find_all(nodes.TemplateData):
            for found in _SCRIPT_TAG_RE.finditer(data.data):
                if _script_tag_runs_code(data.data, found.start()):
                    return True
    return False


def scan() -> list[str]:
    env = Environment(extensions=[])
    roots = _template_roots()

    script_macros: set[str] = set()
    parsed: dict[tuple[pathlib.Path, str], object] = {}

    for root in roots:
        for path in sorted(root.rglob("*.html")):
            try:
                tree = env.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue  # template-syntax owns unparseable templates
            name = _rel(path, root)
            parsed[(root, name)] = tree
            if _has_script_in_macro(tree):
                script_macros.add(name)

    failures: list[str] = []
    for (root, name), tree in parsed.items():
        for node in tree.find_all((nodes.Import, nodes.FromImport)):
            target = getattr(node.template, "value", None)
            if target in script_macros and not node.with_context:
                failures.append(
                    f"{_rel(root / name, ROOT)}:{node.lineno}: imports {target} "
                    f"without `with context` -> its <script> renders nonce=\"\" and is CSP-refused"
                )
        for node in tree.find_all(nodes.Include):
            target = getattr(node.template, "value", None)
            if target in script_macros and node.with_context is False:
                failures.append(
                    f"{_rel(root / name, ROOT)}:{node.lineno}: includes {target} "
                    f"`without context` -> its <script> renders nonce=\"\" and is CSP-refused"
                )
    return sorted(failures)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", action="store_true", help="print only the number of failures")
    args = ap.parse_args()

    failures = scan()
    if args.count:
        print(len(failures))
        return 0

    for f in failures:
        print(f)
    if failures:
        print(
            f"\n{len(failures)} macro import(s) will render nonce=\"\" and have their "
            f"JavaScript refused by CSP.\nAppend ` with context` to each import."
        )
    else:
        print("No context-less imports of script-bearing macros.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

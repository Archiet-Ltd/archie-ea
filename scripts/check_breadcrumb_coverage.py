"""Gate: every routed page with a header carries a breadcrumb.

Fortune-500 UI baseline — a page that shows a title must show a breadcrumb trail
so the user always knows where they are. A UI audit (1 Sep 2026) found 116/312
admin pages with no breadcrumb of any kind; 107 already called page_shell/
page_header and simply omitted the breadcrumb argument. Those were fixed. This
gate keeps the number from creeping back: it counts routed page templates that
render a header macro (page_shell/page_header) but pass NO breadcrumb and do NOT
override the {% block breadcrumb %} escape hatch.

PARTIALS ARE EXCLUDED — a fragment (`_`-prefixed, or under a partials/ directory)
is included by a page and must NOT carry its own breadcrumb; the parent page owns
it. Pages that render no header at all are a separate concern (they need a header
first) and are not counted here.

A page held by the navigation registry gets a shell-rendered breadcrumb from
page_trail() via page_shell()/page_header() even without a breadcrumbs= argument,
so it is not counted as missing.

Ratchet @ 0 in verify.py. Escape hatch: a page that deliberately has no breadcrumb
can carry the marker `breadcrumb-ok:` on any line.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.navigation.registry import entry_for_endpoint  # noqa: E402

ROOTS = ("app/templates", "app/modules")
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".worktrees", ".claude"}
_HEADER = re.compile(r"page_(?:shell|header)\s*\(")
_BC_ARG = re.compile(r"breadcrumbs?\s*=")
_ALLOW = re.compile(r"breadcrumb-ok:")

# Load the signed-in page inventory so we can map templates to endpoints and
# check whether page_trail() would return a non-empty trail (i.e. the page is
# held by the navigation registry). Such pages get a shell-rendered breadcrumb
# even without a breadcrumbs= argument.
_INVENTORY = json.loads(Path("docs/component-inventory.json").read_text())
_TEMPLATE_TO_ENDPOINTS: dict[str, list[str]] = {}
for _route in _INVENTORY.get("routes", []):
    if _route.get("page") and _route.get("templates"):
        for _t in _route["templates"]:
            _TEMPLATE_TO_ENDPOINTS.setdefault(_t, []).append(_route["endpoint"])


def _in_nav_registry(template_path: str) -> bool:
    """True if any endpoint rendering this template is held by the nav registry."""
    for _ep in _TEMPLATE_TO_ENDPOINTS.get(template_path, []):
        _entry = entry_for_endpoint(_ep)
        if _entry is not None and _entry.key != "home":
            return True
    return False


def _is_partial(path: Path) -> bool:
    if path.name.startswith("_"):
        return True
    return "partials" in path.parts


def _missing(root: Path, base: Path) -> list[str]:
    hits = []
    for path in root.rglob("*.html"):
        # Check skip-dirs against the path RELATIVE to root -- the absolute
        # root itself may sit under a dir named in SKIP_DIRS (e.g. a
        # .worktrees checkout), which would otherwise skip every file in the
        # tree and make this gate silently scan nothing.
        rel_parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        if _is_partial(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if _ALLOW.search(text):
            continue
        if "{% block breadcrumb %}" in text:
            continue  # renders its own breadcrumb via the block
        # Pages held by the nav registry get a shell-rendered breadcrumb from
        # page_trail() via page_shell()/page_header().
        template_path = path.relative_to(base).as_posix()
        if _in_nav_registry(template_path):
            continue
        if not _HEADER.search(text):
            continue  # no header macro — a different (header-first) concern
        # a header macro is present; does ANY header call carry a breadcrumb arg?
        has_bc = any(
            _BC_ARG.search(text[m.start():m.start() + 800])
            for m in _HEADER.finditer(text)
        )
        if not has_bc:
            hits.append(path.relative_to(base).as_posix())
    return hits


def main() -> int:
    count_only = "--count" in sys.argv
    root = Path(".").resolve()
    hits = []
    for r in ROOTS:
        rp = root / r
        if rp.exists():
            hits.extend(_missing(rp, base=root))
    hits.sort()
    if count_only:
        print(len(hits))
        return 1 if hits else 0
    for h in hits:
        print(f"{h}: header macro present but no breadcrumb (and no {{% block breadcrumb %}})")
    print(f"\n{len(hits)} page(s) with a header but no breadcrumb.")
    if hits:
        print("Add breadcrumb=[('Home','/'), (<title>, none)] to the page_shell call "
              "(or breadcrumbs=[{'label':'Home','href':'/'}, {'label': <title>}] for "
              "page_header), or mark the line 'breadcrumb-ok: <reason>'.")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())

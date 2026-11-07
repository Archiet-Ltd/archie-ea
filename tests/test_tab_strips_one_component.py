"""Every tab strip is drawn by the one tabs component, so every strip scrolls inside its own box on a phone."""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
# The tabs component and its library port are the only files allowed to write a tab list.
ALLOWED = {"app/templates/components/tabs.html", "app/templates/basecoat/tabs.html"}
STRIP = re.compile(r"""role\s*=\s*["']tablist["']""")


def _templates():
    for base in (ROOT / "app" / "templates", *(ROOT / "app" / "modules").glob("*/templates")):
        yield from base.rglob("*.html")


def test_only_the_tabs_component_writes_a_tab_list():
    offenders = sorted(
        str(p.relative_to(ROOT)) for p in _templates()
        if str(p.relative_to(ROOT)) not in ALLOWED and STRIP.search(p.read_text(encoding="utf-8", errors="ignore")))
    assert not offenders, "hand-written tab strips (use components/tabs.html): %s" % offenders

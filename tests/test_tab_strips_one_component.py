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


def test_the_page_header_tab_row_is_the_one_tab_strip(app):
    """The tabs a page header draws carry data-tab-strip, so the open tab is kept in view on a phone."""
    with app.test_request_context("/"):
        html = app.jinja_env.from_string("""
            {% from 'macros/page_shell.html' import page_shell %}
            {{ page_shell('Applications', tabs=[('Overview', '/a', true), ('Risks', '/b', false)]) }}
        """).render()
    strip = re.search(r"<nav[^>]*data-tab-strip[^>]*>(.*?)</nav>", html, re.S)
    assert strip, "the header tabs are not drawn by the tab strip component"
    assert 'aria-label="Applications sections"' in html
    assert re.search(r'<a href="/a"[^>]*data-active="true"[^>]*aria-current="page"', strip.group(1))
    assert re.search(r'<a href="/b"(?![^>]*aria-current)', strip.group(1))

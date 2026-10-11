"""Every graph and chart offers a table of the same rows.

The table alternative is one shared include (components/_graph_table_view.html)
plus one script (js/components/graph_table_view.js). These tests keep it that
way: every Chart.js chart in the templates carries the include for its own
canvas, the include renders a named toggle for a hidden region, and the script
writes text only through textContent.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / "app" / "templates"
INCLUDE = "components/_graph_table_view.html"
SCRIPT = REPO / "app" / "static" / "js" / "components" / "graph_table_view.js"

# A canvas that is not a chart of data: the Composer's minimap is a scaled
# thumbnail of the drawing the Composer itself shows.
NOT_A_CHART = {"minimap-canvas"}

CANVAS = re.compile(r"<canvas\b[^>]*\bid=\"([^\"]+)\"")
WIRED = re.compile(r"chart_id='([^']+)'[^%]*%\}\{% include '" + re.escape(INCLUDE) + "'")


def _chart_templates():
    for path in sorted(TEMPLATES.rglob("*.html")):
        if path.name == "_graph_table_view.html":
            continue
        text = path.read_text(encoding="utf-8")
        canvases = [c for c in CANVAS.findall(text) if c not in NOT_A_CHART]
        if canvases:
            yield path, text, canvases


def test_every_chart_canvas_has_its_table_view():
    missing = []
    found = 0
    for path, text, canvases in _chart_templates():
        wired = set(WIRED.findall(text))
        for canvas in canvases:
            found += 1
            if canvas not in wired:
                missing.append(f"{path.relative_to(REPO)}#{canvas}")
    assert found >= 25, f"expected the chart pages to be found, found {found} canvases"
    assert not missing, f"charts with no table view: {missing}"


def test_no_table_view_points_at_a_canvas_that_is_not_there():
    for path in sorted(TEMPLATES.rglob("*.html")):
        if path.name == "_graph_table_view.html":
            continue
        text = path.read_text(encoding="utf-8")
        for chart_id in WIRED.findall(text):
            assert f'id="{chart_id}"' in text, f"{path.relative_to(REPO)} wires a table to missing #{chart_id}"


def test_the_include_renders_a_named_toggle_for_a_hidden_region(app):
    with app.test_request_context():
        template = app.jinja_env.from_string(
            "{% with chart_id='demoChart', caption='Demo figures' %}"
            "{% include '" + INCLUDE + "' %}{% endwith %}"
        )
        html = template.render()
    assert 'data-graph-table-view data-for="demoChart" data-caption="Demo figures"' in html
    assert re.search(r'<button type="button" data-graph-table-toggle aria-expanded="false" aria-controls="demoChart-table-view"', html)
    assert ">Show as table<" in html
    assert re.search(r'<div id="demoChart-table-view" data-graph-table-region hidden', html)
    assert "js/components/graph_table_view.js" in html


def test_the_script_writes_text_only_and_reads_the_chart_on_the_page():
    source = SCRIPT.read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    assert "innerHTML" not in code and "insertAdjacentHTML" not in code
    assert "textContent" in code
    assert "Chart.getChart" in code
    # Nothing is fetched: the table shows what the picture shows.
    assert "fetch(" not in code and "XMLHttpRequest" not in code
    # The keys a keyboard uses.
    for key in ("ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End", "Escape"):
        assert f"'{key}'" in code
    # A missing value reads as a dash, never as "None" or a fabricated zero.
    assert "var MISSING = '\\u2014';" in source

"""The ARB "Review status" donut must paint the semantic token colours and stay a bounded size.

UX_IA_REVIEW.md finding 1 (Critical): the donut rendered as one solid black ring about 700px tall.
Two causes, both reproduced in a browser before this fix:

1. Chart.js hands `backgroundColor` strings to the canvas 2D context, which cannot resolve CSS
   custom properties. `'hsl(var(--warning))'` is an invalid fill, so every segment fell back to
   black. Only pixels of rgb(0,0,0) were painted.
2. A doughnut has a 1:1 aspect ratio; inside a wide flex box with `maintainAspectRatio: true`
   the canvas grew to the box width (778px square at a 1440px viewport).

The smoke test tests/smoke/test_arb_status_chart_renders.py only proves a Chart.js instance is
attached, so it passed throughout. This test renders the real module with the real Chart.js,
the real token CSS and the real Tailwind build in a bare page, so it needs no database.
"""
import math
import re
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

REPO = Path(__file__).resolve().parents[1]
STATIC = REPO / "app" / "static"
TEMPLATE = REPO / "app" / "templates" / "arb" / "dashboard.html"
LEGACY_PARTIAL = REPO / "app" / "templates" / "arb" / "partials" / "_legacy_dashboard.html"
TOKENS = ["--warning", "--success", "--destructive", "--muted-foreground"]
MAX_CHART_HEIGHT_PX = 220
CONTAINER_RE = re.compile(r'<div class="([^"]*)">\s*<canvas id="arbStatusChart"')


def _container_class(path):
    """The classes on the element that wraps the canvas, read from the real template."""
    m = CONTAINER_RE.search(path.read_text(encoding="utf-8"))
    assert m, f"{path.name}: no <div class=...> directly around #arbStatusChart"
    return m.group(1)


PAGE = """<!doctype html><html><head>
<link rel="stylesheet" href="{static}/css/shadcn_tokens.css">
<link rel="stylesheet" href="{static}/css/tailwind-output.css">
<script src="{static}/vendor/chart.umd.min.js"></script>
<script src="{static}/js/arb/status_chart.js"></script>
</head><body class="bg-background"><div style="width:1100px;padding:24px">
<section class="rounded-xl border border-border bg-card p-6 shadow-sm">
<div class="flex flex-wrap items-center gap-6">
<div class="{container_class}"><canvas id="arbStatusChart"></canvas></div>
<dl class="min-w-[200px] space-y-2 text-sm"><div>Pending</div><div>Approved</div><div>Rejected</div></dl>
</div></section></div>
<script>
window.chart = ArbStatusChart.render(document.getElementById('arbStatusChart'),
  {{pending: 3, approved: 5, rejected: 2, total: 11}});
</script></body></html>"""

MEASURE = """() => {
  const c = document.getElementById('arbStatusChart');
  const box = c.getBoundingClientRect();
  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  const seen = [];
  for (let i = 0; i < d.length; i += 4 * 53) {
    if (d[i + 3] > 200) seen.push([d[i], d[i + 1], d[i + 2]]);
  }
  const probe = document.createElement('div');
  document.body.appendChild(probe);
  const expected = %s.map(t => {
    probe.style.backgroundColor = 'hsl(var(' + t + '))';
    return getComputedStyle(probe).backgroundColor.match(/\\d+/g).slice(0, 3).map(Number);
  });
  return {cssH: Math.round(box.height), seen, expected, hasChart: !!window.chart};
}""" % str(TOKENS)


@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    page_file = tmp_path_factory.mktemp("arb_chart") / "harness.html"
    page_file.write_text(PAGE.format(static=STATIC.as_uri(), container_class=_container_class(TEMPLATE)), encoding="utf-8")
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(page_file.as_uri())
        page.wait_for_function("window.chart !== undefined", timeout=10000)
        page.wait_for_timeout(600)
        result = page.evaluate(MEASURE)
        browser.close()
    return result


def _near(a, b, tol=6):
    return math.dist(a, b) <= tol


def test_module_returns_a_chart(measured):
    assert measured["hasChart"], "ArbStatusChart.render returned no Chart.js instance"


def test_no_black_pixels_are_painted(measured):
    black = [c for c in measured["seen"] if c == [0, 0, 0]]
    assert not black, "canvas painted rgb(0,0,0): a colour string the canvas could not resolve"


@pytest.mark.parametrize("index,token", list(enumerate(TOKENS)))
def test_each_segment_uses_its_semantic_token(measured, index, token):
    want = measured["expected"][index]
    assert any(_near(c, want) for c in measured["seen"]), f"no pixel near {token} rgb{tuple(want)}"


def test_chart_height_is_capped(measured):
    assert measured["cssH"] <= MAX_CHART_HEIGHT_PX, (
        f"chart box is {measured['cssH']}px tall; the audit measured about 700px"
    )


@pytest.mark.parametrize("path", [TEMPLATE, LEGACY_PARTIAL])
def test_no_template_passes_css_variables_to_the_canvas(path):
    text = path.read_text(encoding="utf-8")
    assert "backgroundColor" not in text or "hsl(var(" not in text.split("backgroundColor", 1)[1][:400]


def test_typed_dashboard_uses_the_shared_module_not_an_inline_config():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "js/arb/status_chart.js" in text
    assert "new Chart(ctx" not in text, "an inline Chart config would bypass the token resolver"


@pytest.mark.parametrize("path", [TEMPLATE, LEGACY_PARTIAL])
def test_canvas_container_has_a_fixed_height_and_is_positioned(path):
    classes = _container_class(path).split()
    assert "relative" in classes, "Chart.js needs a positioned container to size against"
    assert any(re.fullmatch(r"h-(24|28|32|36|40|44|48|52|56)", c) or c == "h-[200px]" for c in classes), (
        f"{path.name}: container has no fixed height <= {MAX_CHART_HEIGHT_PX}px, so the doughnut "
        "grows to the container width (778px tall at 1440px in the audit)"
    )

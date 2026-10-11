"""Regression test for the ARB dashboard "Review status" donut rendering as a
solid black, oversized ring (bucket: arb-chart-and-solutions-data-disagreement,
Task A).

Root cause: `hsl(var(--warning))` is valid CSS but not a valid JavaScript
colour literal — `var(...)` only resolves inside a real CSS property value.
Handing it to Chart.js as a JS string made `@kurkle/color` fail to parse it
silently, and every segment collapsed to Chart.js's default fill (black).

This is a template/JS source-level test (the assertion class most likely to
have caught it before it shipped), not a rendered-pixel test — pixel/visual
coverage of this screen lives in `tests/smoke/test_visual_regression.py`,
which already covered `/arb/` before this change (round-2 refuter correction,
17 Sep 2026: this docstring previously and incorrectly claimed the visual
baseline was added as part of this fix).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_TEMPLATE = REPO_ROOT / "app" / "templates" / "arb" / "dashboard.html"
COLOR_TOKENS_JS = REPO_ROOT / "app" / "static" / "js" / "shared" / "css_color_tokens.js"


def _extract_script_blocks(html: str) -> list[str]:
    return re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, re.DOTALL)


def _strip_js_line_comments(block: str) -> str:
    """Strip `// ...` line comments so an explanatory comment mentioning the
    bug pattern by name does not itself trip the assertion."""
    return "\n".join(re.sub(r"//.*$", "", line) for line in block.splitlines())


def test_no_unresolved_css_var_inside_a_js_string_literal():
    """`hsl(var(--...))` inside a <script> block is the exact defect: CSS
    custom properties never resolve in a JS string, so Chart.js's colour
    parser silently fails and falls back to black."""
    html = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
    for block in _extract_script_blocks(html):
        assert "hsl(var(" not in _strip_js_line_comments(block), (
            "Found an unresolved CSS custom property inside a <script> block — "
            "this is a JS string, not CSS, so var(...) will not resolve and "
            "Chart.js will fall back to a default (black) fill."
        )


def test_exactly_one_chart_initialiser_for_the_status_donut():
    """The typed-queue and legacy branches previously carried two independent
    copies of the same `new Chart(...)` call, and that duplication is the
    mechanism by which the colour arrays diverged (one broken, one not).
    Collapsed into a single initialiser serving both branches."""
    html = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
    assert html.count("new Chart(") == 1


def test_chart_uses_shared_css_token_resolver_not_a_raw_hex_or_var_literal():
    html = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
    assert "ArchieColorTokens.cssHSL" in html or "window.ArchieColorTokens" in html
    assert COLOR_TOKENS_JS.exists()


def test_shared_css_token_resolver_uses_getComputedStyle():
    """This is the actual resolution mechanism — a var() literal never
    reaches Chart.js at all; getComputedStyle resolves it to a real H S% L%
    triplet first."""
    js = COLOR_TOKENS_JS.read_text(encoding="utf-8")
    assert "getComputedStyle" in js
    assert "getPropertyValue" in js
    assert "window.ArchieColorTokens" in js


def test_chart_options_do_not_let_a_1to1_aspect_ratio_square_off_against_width():
    """Second, independent bug: `responsive: true` + the doughnut default
    `maintainAspectRatio: true` sizes the canvas as a square as tall as its
    (wide) flex container, producing the reported ~700px ring. Capping the
    container height alone is not sufficient without also disabling
    maintainAspectRatio."""
    html = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
    match = re.search(r"new Chart\(ctx,\s*\{.*?options:\s*\{(.*?)\}\s*\}\s*\);", html, re.DOTALL)
    assert match, "could not locate the chart options block"
    options_block = match.group(1)
    assert "maintainAspectRatio: false" in options_block


def test_canvas_container_has_a_capped_height():
    """The canvas' own `height="160"` attribute is inert under
    `responsive: true` — Chart.js resizes from the CONTAINER, so the cap has
    to live on the wrapping element."""
    html = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
    # The legacy dashboard partial has been deleted. The typed queue
    # is the single code path. Only the main dashboard template is checked.
    assert re.search(r'id="arbStatusChart"', html)
    # Round-2 refuter fix (17 Sep 2026): `h-[220px]` was an arbitrary-value
    # Tailwind class that was never compiled into the committed
    # tailwind-output.css (this repo ships pre-built CSS with no Node
    # toolchain available in most environments -- see CLAUDE.md's
    # "CSS is committed pre-built" section), so the class was a no-op in any
    # environment that hadn't rebuilt Tailwind, and the canvas rendered at
    # its old ~654px oversized default. `h-48` is a standard, already-
    # compiled Tailwind utility (192px) that satisfies the same <=240px cap.
    assert "h-48" in html
    assert "h-[220px]" not in html, "arbitrary-value class not guaranteed compiled into shipped CSS"


def test_resolved_background_colors_are_four_distinct_semantic_tokens():
    """Assert the actual colour ARRAY passed to Chart.js resolves four
    distinct custom properties (not four copies of the same value, which
    would reproduce the all-segments-one-colour symptom a different way)."""
    html = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
    match = re.search(r"backgroundColor:\s*\[(.*?)\]", html, re.DOTALL)
    assert match, "could not locate the backgroundColor array"
    array_src = match.group(1)
    tokens = re.findall(r"cssHSL\('(--[a-z-]+)'\)", array_src)
    assert len(tokens) == 4
    assert len(set(tokens)) == 4
    assert set(tokens) == {"--warning", "--success", "--destructive", "--muted-foreground"}

"""Tests for the Business Architect capability-map visuals:

  1. Capability Model — nested-box BIZBOK view (new tab)
  2. Maturity Radar — current vs. target maturity per domain (new tab)
  3. Investment Bubble — 2x2 TIME quadrant chart (enterprise/investment_matrix.html)

These are lightweight, file-content assertions only — no Flask app import and no
database is required, since booting the full app is slow and shared in this repo.
They check that:
  - the new JS files exist and reference the EXISTING data endpoint they reuse
    (GET /capability-map/api/unified-capabilities), rather than a new route,
  - the new tab ids/Alpine wiring were actually added to
    capability_map/index.html so the tabs are reachable,
  - enterprise/investment_matrix.html still renders its original table and now
    also wires up the bubble chart,
  - no new external CDN library (beyond the Chart.js build already used
    elsewhere in the app, e.g. arb/dashboard.html) was introduced.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

INDEX_HTML = REPO_ROOT / "app" / "templates" / "capability_map" / "index.html"
INVESTMENT_MATRIX_HTML = REPO_ROOT / "app" / "templates" / "enterprise" / "investment_matrix.html"

NESTED_MAP_JS = REPO_ROOT / "app" / "static" / "js" / "capability_map" / "nested_map.js"
MATURITY_RADAR_JS = REPO_ROOT / "app" / "static" / "js" / "capability_map" / "maturity_radar.js"
INVESTMENT_BUBBLE_JS = REPO_ROOT / "app" / "static" / "js" / "capability_map" / "investment_bubble.js"
INDEX_JS = REPO_ROOT / "app" / "static" / "js" / "capability_map" / "index.js"

REUSED_ENDPOINT = "/capability-map/api/unified-capabilities"
# Chart.js is SELF-HOSTED, not loaded from a CDN. Every other template in the
# app uses this path, and the deployment sits behind a corporate network that
# filters outbound traffic - a jsdelivr <script> renders a blank chart for real
# users while passing every test that only checks the markup is present.
CHART_JS_SRC = "/static/vendor/chart.umd.min.js"


@pytest.fixture(scope="module")
def index_html_text():
    assert INDEX_HTML.exists(), f"missing {INDEX_HTML}"
    return INDEX_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_js_text():
    assert INDEX_JS.exists(), f"missing {INDEX_JS}"
    return INDEX_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def investment_matrix_text():
    assert INVESTMENT_MATRIX_HTML.exists(), f"missing {INVESTMENT_MATRIX_HTML}"
    return INVESTMENT_MATRIX_HTML.read_text(encoding="utf-8")


class TestNewJsFilesExist:
    def test_nested_map_js_exists(self):
        assert NESTED_MAP_JS.exists()

    def test_maturity_radar_js_exists(self):
        assert MATURITY_RADAR_JS.exists()

    def test_investment_bubble_js_exists(self):
        assert INVESTMENT_BUBBLE_JS.exists()

    def test_new_js_files_compile_as_valid_javascript_braces(self):
        # Not a real JS parser (none is available here) — a cheap sanity check
        # that braces/parens balance, catching obvious syntax breakage.
        for path in (NESTED_MAP_JS, MATURITY_RADAR_JS, INVESTMENT_BUBBLE_JS):
            text = path.read_text(encoding="utf-8")
            assert text.count("{") == text.count("}"), f"unbalanced braces in {path.name}"
            assert text.count("(") == text.count(")"), f"unbalanced parens in {path.name}"


class TestNestedMapReusesExistingEndpoint:
    def test_fetches_unified_capabilities_endpoint(self):
        text = NESTED_MAP_JS.read_text(encoding="utf-8")
        assert REUSED_ENDPOINT in text

    def test_does_not_define_a_new_flask_route_pattern(self):
        text = NESTED_MAP_JS.read_text(encoding="utf-8")
        # This is a static JS file — it must not contain a Flask route decorator,
        # confirming no backend route was smuggled in here.
        assert "@capability_map.route" not in text
        assert "@app.route" not in text

    def test_exposes_global_tab_loader(self):
        text = NESTED_MAP_JS.read_text(encoding="utf-8")
        assert "window.loadCapabilityModelTab" in text

    def test_reuses_existing_heatmap_drilldown_instead_of_duplicating_it(self):
        text = NESTED_MAP_JS.read_text(encoding="utf-8")
        assert "showHeatmapDetail" in text
        assert "tab-heatmap" in text


class TestMaturityRadarReusesExistingEndpoint:
    def test_fetches_unified_capabilities_endpoint(self):
        text = MATURITY_RADAR_JS.read_text(encoding="utf-8")
        assert REUSED_ENDPOINT in text

    def test_exposes_global_tab_loader(self):
        text = MATURITY_RADAR_JS.read_text(encoding="utf-8")
        assert "window.loadMaturityRadarTab" in text

    def test_uses_chartjs_radar_type(self):
        text = MATURITY_RADAR_JS.read_text(encoding="utf-8")
        assert "type: 'radar'" in text

    def test_has_domain_and_level_selector_hooks(self):
        text = MATURITY_RADAR_JS.read_text(encoding="utf-8")
        assert "maturity-domain-select" in text
        assert "maturity-level-select" in text


class TestInvestmentBubbleUsesEmbeddedTableData:
    def test_reads_embedded_data_global_not_a_new_fetch_endpoint(self):
        text = INVESTMENT_BUBBLE_JS.read_text(encoding="utf-8")
        assert "window.__investmentBubbleData" in text
        # The bubble chart must not introduce its own fetch() call — it consumes
        # data already embedded by the template from the existing `capabilities`
        # context variable, per the "reuse the data the table already renders" rule.
        assert "fetch(" not in text

    def test_uses_chartjs_bubble_type(self):
        text = INVESTMENT_BUBBLE_JS.read_text(encoding="utf-8")
        assert "type: 'bubble'" in text

    def test_has_time_quadrant_labels(self):
        text = INVESTMENT_BUBBLE_JS.read_text(encoding="utf-8")
        for label in ("INVEST", "MIGRATE", "TOLERATE", "ELIMINATE"):
            assert label in text


class TestIndexHtmlNewTabs:
    # shell-overhaul Wave 2 Task 3 replaced the competing 11-button Alpine tab
    # row (one <button role="tab"> per lens, each with its own inline
    # @click="activeTab = '...'; load...()") with a single "Lens" <select>
    # (id="capability-lens") that drives the same activeTab state via
    # onLensChange() in index.js. The tab *panels* (id="panel-...") and the
    # per-lens load functions are unchanged — only how a lens gets selected
    # changed, so these assertions now check the <option> + the JS dispatch
    # instead of the removed <button role="tab">.
    def test_capability_model_tab_button_and_panel_present(self, index_html_text, index_js_text):
        assert 'value="capability-model"' in index_html_text
        assert 'id="panel-capability-model"' in index_html_text
        assert "tab === 'capability-model'" in index_js_text
        assert "loadCapabilityModelTab()" in index_js_text

    def test_maturity_tab_button_and_panel_present(self, index_html_text, index_js_text):
        assert 'value="maturity"' in index_html_text
        assert 'id="panel-maturity"' in index_html_text
        assert "tab === 'maturity'" in index_js_text
        assert "loadMaturityRadarTab()" in index_js_text

    def test_lens_dropdown_has_a_label(self, index_html_text):
        # DESIGN.md: every form control needs an accessible name. The old
        # per-tab-button assertion (every <button> declares type=) no longer
        # applies since there is no longer a button row — check the <select>
        # that replaced it is properly labelled instead.
        # The select is now drawn by the native_select macro, so the id is a
        # macro argument (id='capability-lens') rather than a literal attribute.
        import re
        assert re.search(r"""id=['"]capability-lens['"]""", index_html_text)
        assert 'for="capability-lens"' in index_html_text

    def test_new_scripts_are_included(self, index_html_text):
        assert "js/capability_map/nested_map.js" in index_html_text
        assert "js/capability_map/maturity_radar.js" in index_html_text

    def test_chartjs_is_self_hosted_like_the_rest_of_the_app(self, index_html_text):
        # Same self-hosted asset already used by app/templates/arb/dashboard.html —
        # not a new external library, and not an external request.
        assert CHART_JS_SRC in index_html_text
        arb_dashboard = (REPO_ROOT / "app" / "templates" / "arb" / "dashboard.html").read_text(encoding="utf-8")
        assert CHART_JS_SRC in arb_dashboard

    def test_no_unrelated_new_cdn_libraries_added(self, index_html_text):
        # D3 was not needed for these three visuals (CSS grid + Chart.js only) —
        # make sure it wasn't accidentally introduced on this page.
        assert "d3.v7.min.js" not in index_html_text
        assert "d3@7" not in index_html_text


class TestInvestmentMatrixBubbleChart:
    def test_keeps_existing_table(self, investment_matrix_text):
        # The table of every capability is the shared list table now.
        assert "caption='All capabilities'" in investment_matrix_text
        assert "list_table(rows=caps" in investment_matrix_text
        assert "cap.strategic_importance" in investment_matrix_text

    def test_adds_bubble_canvas_and_script(self, investment_matrix_text):
        assert 'id="investment-bubble-chart"' in investment_matrix_text
        assert "js/capability_map/investment_bubble.js" in investment_matrix_text

    def test_embeds_data_from_existing_context_var_not_new_endpoint(self, investment_matrix_text):
        assert "window.__investmentBubbleData" in investment_matrix_text
        assert "capabilities|default([])" in investment_matrix_text

    def test_chartjs_cdn_present(self, investment_matrix_text):
        assert CHART_JS_SRC in investment_matrix_text

    def test_has_quadrant_legend(self, investment_matrix_text):
        # The key for the bubble colours is Chart.js's own legend, drawn from one
        # dataset per TIME quadrant (labelled Invest, Migrate, Tolerate,
        # Eliminate), rather than a hand-drawn legend beside the canvas.
        js = INVESTMENT_BUBBLE_JS.read_text(encoding="utf-8")
        for quadrant in ("INVEST", "MIGRATE", "TOLERATE", "ELIMINATE"):
            assert quadrant + ":" in js
        assert "label: q.charAt(0) + q.slice(1).toLowerCase()" in js
        assert "legend: { position: 'bottom' }" in js
        assert "js/capability_map/investment_bubble.js" in investment_matrix_text

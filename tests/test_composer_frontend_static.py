"""Static regression guards for composer front-end fixes (CMP-04, ...).

There is no JS test runtime in this environment (the JS suite is Playwright,
which needs a browser), so these guards assert the shape of the fix in source —
enough to catch a regression that silently reverts the change. Behavioural
confirmation of these UI fixes is done with a browser pass (fix-then-verify).
"""

from __future__ import annotations

import pathlib

JS = pathlib.Path(__file__).resolve().parents[1] / "app" / "static" / "js"


def _read(rel: str) -> str:
    return (JS / rel).read_text(encoding="utf-8")


def test_cmp04_confirm_accepts_object_first_argument():
    """Platform.modal.confirm must not render an options object as [object Object].

    ui/modal.js confirmDialog(message, options) String()'d message into the body;
    the composer calls it with a single {title, message, ...} object, so the body
    read literally "[object Object]". The fix normalises an object-first call.
    """
    src = _read("ui/modal.js")
    # The normalisation branch: when message is an object, treat it as options
    # and pull the body text out of it.
    assert "typeof message === 'object'" in src, \
        "confirmDialog must detect an object-first call"
    assert "options.message || options.text" in src, \
        "confirmDialog must extract the body text from the options object"
    # Both label naming conventions supported (confirmText/cancelText and
    # confirmLabel/cancelLabel) so the composer's confirmText/cancelText work.
    assert "options.confirmLabel || options.confirmText" in src
    assert "options.cancelLabel || options.cancelText" in src


def test_cmp02_delete_affordance_and_method_present():
    """The saved-viewpoint picker must expose delete, wired to deleteSavedViewpoint."""
    overlays = (JS.parents[1] / "templates" / "archimate" / "partials"
                / "_composer_overlays.html").read_text(encoding="utf-8")
    assert "deleteSavedViewpoint(svp.id" in overlays, \
        "picker rows must call deleteSavedViewpoint"
    persistence = _read("archimate/composer_persistence.js")
    assert "deleteSavedViewpoint:" in persistence, "method must exist"
    assert "Platform.fetch.delete('/archimate/api/saved-viewpoints/'" in persistence, \
        "must call the DELETE endpoint"


def test_bulk_ai_accept_is_rate_limit_safe():
    """AI-generated diagrams must not fire every create/wire POST at once.

    The global write rate limit is 30/min (app/_bootstrap/rate_limiting.py).
    Production evidence (10 Sep 2026): a 32-element "Place Full Diagram on
    Canvas" left one element silently missing -- one create-element call came
    back 429, and the old code fired all of them in one unthrottled forEach
    with no retry. Fixed with a bounded-concurrency runner and 429 retry;
    this guards both call sites still route through it.
    """
    src = _read("archimate/composer_ai.js")
    assert "_postWithRetry:" in src, "retry-on-429 helper must exist"
    assert "_runLimited:" in src, "bounded-concurrency helper must exist"
    assert "self._runLimited(factories, 5" in src, \
        "_doAcceptAllGenerated must create elements through the concurrency limiter"
    assert "self._postWithRetry('/api/architecture-assistant/create-element'" in src, \
        "element creation must retry on 429, not drop the element on first failure"
    assert "self._postWithRetry('/archimate/api/relationships'" in src, \
        "relationship wiring must retry on 429 too"


def test_cmp08_quick_add_avoids_overlap():
    """New elements must cascade off an occupied spot instead of stacking."""
    src = _read("archimate/composer.js")
    assert "_placementFor:" in src, "placement helper must exist"
    assert "this._placementFor(this.dropX, this.dropY)" in src, \
        "pickElement must route placement through the overlap-avoider"


def test_cmp11_quick_add_searches_catalog_by_name():
    """Quick-add must query the catalog by name, not only palette type labels."""
    src = _read("archimate/composer.js")
    assert "/archimate/api/elements/search?limit=10&q=" in src, \
        "doQuickAddSearch must query the catalog search API"
    assert "_existing" in src, "results must distinguish existing elements from new types"
    # CMP-11 no-flash: palette matches shown synchronously + loading + stale guard.
    assert "self.quickAddLoading = true" in src, "must set loading so the empty-state doesn't flash"
    assert "self._quickAddToken" in src, "must ignore stale out-of-order responses"
    overlays = (JS.parents[1] / "templates" / "archimate" / "partials"
                / "_composer_overlays.html").read_text(encoding="utf-8")
    assert "No matching element types found" not in overlays, \
        "misleading 'element types' copy must be gone"
    assert "No matching elements in catalog" in overlays


def test_cmp05_fit_to_content_is_reliable():
    """fitCanvas must not over-zoom and must use model geometry."""
    src = _read("archimate/composer.js")
    # The over-zooming maxScale:1.5 must be gone from fitCanvas.
    assert "maxScale: 1.5" not in src or src.count("maxScale: 1.5") == 0, \
        "fitCanvas must cap maxScale at 1, not 1.5"
    assert "useModelGeometry: true" in src, \
        "fit must read model geometry (stable during panel resize)"


def test_cmp06_middle_mouse_pans():
    """Middle-mouse drag on blank canvas must pan in any mode."""
    src = _read("archimate/composer.js")
    assert "evt.button === 1" in src, "middle-mouse button must trigger pan"


def test_cmp09_closing_scratch_tab_deletes_server_row():
    """Closing an unsaved 'Unsaved diagram' tab must delete its server row."""
    src = _read("archimate/composer_persistence.js")
    assert "closeViewpointTab:" in src
    assert "'Unsaved diagram'" in src, "scratch diagrams must be detected by name"
    assert "Platform.fetch.delete('/archimate/api/saved-viewpoints/' + tab.id" in src, \
        "scratch tab close must delete the server row so it does not reappear"
    composer_html = (JS.parents[1] / "templates" / "archimate" / "composer.html").read_text(encoding="utf-8")
    assert "closeViewpointTab(tab)" in composer_html, "tab close button must call the method"


def test_cmp10_menus_use_opacity_only_transition():
    """Toolbar dropdowns must fade (opacity only), not scale/translate, so menu
    items don't shift under the cursor mid-animation and swallow the click."""
    import re
    root = JS.parents[1] / "templates"
    toolbar = (root / "archimate" / "partials" / "_composer_toolbar.html").read_text(encoding="utf-8")
    shell = (root / "components" / "diagram" / "toolbar.html").read_text(encoding="utf-8")
    macro = (root / "components" / "dropdown_menu.html").read_text(encoding="utf-8")
    # Every menu the composer draws asks the dropdown macro for the opacity-only fade.
    calls = re.findall(r"dropdown_menu\(", toolbar + shell)
    assert calls and len(calls) == len(re.findall(r"transition='opacity'", toolbar + shell)), \
        "every composer menu must pass transition='opacity'"
    # No bare `x-transition` (default = scale+translate) in the composer's own markup.
    assert not re.findall(r"x-transition(?!\.)", toolbar + shell), \
        "toolbar menus must use opacity-only transitions, not the default"
    # The macro turns that request into an opacity-only transition with no scale.
    assert "x-transition.opacity" in macro


def test_cmp14_hover_raises_only_on_overlap_and_keeps_links_on_top():
    """Hover raises the element ONLY when it overlaps a neighbour (so its ports
    beat the overlap), and re-raises links so relationships never hide behind a
    raised element. The first version raised on every hover with deep:true, which
    reshuffled z-order across the whole diagram and hid arrows under boxes."""
    src = _read("archimate/composer.js")
    assert "_overlapsAnyElement(cellView.model)" in src, \
        "hover must gate toFront on actual overlap, not fire on every hover"
    assert "cellView.model.toFront({ deep: true })" not in src, \
        "the unconditional deep toFront-on-every-hover must be gone"
    assert "_raiseLinks" in src, "links must be re-raised so relationships stay on top"


def test_cmp07_single_save_indicator():
    """The status bar must show ONE unified save indicator, not competing ones."""
    src = _read("archimate/composer.js")
    assert "get saveIndicator()" in src, "unified saveIndicator getter must exist"
    composer_html = (JS.parents[1] / "templates" / "archimate" / "composer.html").read_text(encoding="utf-8")
    status_line = (JS.parents[1] / "templates" / "components" / "diagram" / "status_line.html").read_text(encoding="utf-8")
    assert "diagram_status_line()" in composer_html, "the page must draw the status line component"
    assert "saveIndicator.text" in status_line, "footer must render the unified indicator"
    # The old competing footer strings must be gone.
    assert "'Autosave: ' + _autosaveLabel" not in composer_html + status_line, \
        "the separate 'Autosave: <label>' footer block must be removed"


def test_cmp12_narrow_width_media_query():
    """A narrow-width media query must hide the overlapping minimap and let the
    toolbar scroll instead of clipping."""
    css = (JS.parents[1] / "static" / "css" / "archimate_composer.css").read_text(encoding="utf-8")
    assert "@media (max-width: 1100px)" in css
    # Within the narrow block, the minimap is hidden and the toolbar scrolls.
    idx = css.index("@media (max-width: 1100px)")
    block = css[idx:idx + 500]
    assert ".mini-map { display: none" in block
    assert "overflow-x: auto" in block


def test_cmp03_audit_failure_not_toasted():
    """Audit writes must stay non-blocking without becoming invisible failures."""
    src = _read("archimate/composer.js")
    # The old code toasted 'Failed to log audit event' inside the catch.
    assert "Failed to log audit event" not in src, \
        "audit-log failures must be silent (fire-and-forget), not toasted"
    assert "Platform.log.error('Failed to record composer audit event', err)" in src, \
        "an audit request that never reaches the server must remain diagnosable"


def test_cmp03_minimap_storage_failure_is_an_explicit_optional_exception():
    """Storage denial must not break minimap toggling or look like a silent API failure."""
    minimap = (JS.parents[1] / "templates" / "components" / "diagram" / "minimap.html").read_text(encoding="utf-8")
    minimap_toggle = minimap[minimap.index("archie.composer.miniMapExpanded"):]
    assert "swallow-ok: localStorage is unavailable in private browsing" in minimap_toggle, \
        "the optional local preference failure must be reviewed and explicitly bounded"


def test_ba04_pdf_export_is_one_click_and_vendored():
    """BA-04: the Export dropdown must offer a real PDF, from a vendored library.

    Iain Burgess asked for leadership-consumable output. 'Print Friendly' is a
    style template, not an export — it still needs a manual browser print step.
    The PDF path must be wired to exportPdf() and jsPDF must be served locally,
    because the air-gap gate forbids a CDN.
    """
    toolbar = (JS.parents[1] / "templates" / "archimate" / "partials"
               / "_composer_toolbar.html").read_text(encoding="utf-8")
    assert "exportPdf()" in toolbar, "the Export dropdown must call exportPdf()"

    composer_html = (JS.parents[1] / "templates" / "archimate"
                     / "composer.html").read_text(encoding="utf-8")
    assert "/static/vendor/jspdf.umd.min.js" in composer_html, \
        "jsPDF must be loaded from the vendored copy, never a CDN"

    vendor = JS.parents[1] / "static" / "vendor" / "jspdf.umd.min.js"
    assert vendor.is_file(), "the vendored jsPDF bundle must be present"


def test_ba04_pdf_export_surfaces_failure_and_cannot_emit_empty_file():
    """exportPdf must never silently no-op or save a blank page.

    The original version returned bare on four paths, reported the rest only in
    the status bar, had no img.onerror, and no try/catch around the jsPDF calls
    — so a rasterisation failure in front of a customer produced nothing at all.
    """
    src = _read("archimate/composer_persistence.js")
    start = src.index("exportPdf: function()")
    body = src[start:src.index("exportReport: function()", start)]

    assert "img.onerror" in body, \
        "a failed SVG rasterisation must be handled, not left as a silent no-op"
    assert "} catch (err) {" in body, \
        "the jsPDF render must be wrapped so an exception reaches the user"
    assert "_toast('error'" in body, \
        "failures must surface via Platform.toast, not only statusText"
    # No bare `return;` escape hatches that tell the user nothing.
    assert "if (!svgEl) return;" not in body, \
        "silent bare returns must be replaced by a reported failure"
    # An empty rasterisation must be rejected rather than embedded.
    assert "imgData.length < 1000" in body, \
        "an empty toDataURL result must abort rather than produce a blank PDF"
    assert "MAX_CANVAS_PX" in body, \
        "canvas size must be clamped below the browser limit that yields 'data:,'"


def test_ba04_pdf_output_is_leadership_readable():
    """The PDF must carry a title and a date on a standard ISO page, landscape
    for wide diagrams — not a raw canvas-sized sheet and not app chrome."""
    src = _read("archimate/composer_persistence.js")
    start = src.index("exportPdf: function()")
    body = src[start:src.index("exportReport: function()", start)]

    assert "doc.text(String(titleText)" in body, "the title must be drawn into the PDF"
    assert "generatedAt.toLocaleString()" in body, "the generation date must appear"
    assert "'landscape'" in body and "'portrait'" in body, \
        "orientation must follow the diagram aspect ratio"
    assert "aspect > 2.2 ? 'a3' : 'a4'" in body, \
        "the page must be a standard ISO size, not the raw canvas dimensions"

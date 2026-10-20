"""The diagram engine's opt-in surface, checked without a browser.

Behaviour is proved in tests/smoke/test_composer_renderer_engine.py (a real
browser, not run by the failing-first check); this file pins the contract that
cannot drift silently: the new methods exist on the renderer, every new option
is read from the options object (so a caller passing none keeps the old path),
and the reuse register names this engine as the one place ArchiMate diagrams
are drawn.
"""
import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
RENDERER = (ROOT / "app/static/js/archimate/composer_renderer.js").read_text(encoding="utf-8")


def test_renderer_exposes_the_update_and_selection_api():
    for name in ("update", "setSelected", "setCentre", "setHighlightedEdges", "getNodeBounds", "resize"):
        assert re.search(r"^\s+%s:\s*function" % name, RENDERER, re.M), name
    assert "computeBandLayout: computeBandLayout" in RENDERER


def test_every_new_option_is_opt_in():
    assert "opts.layout === 'bands'" in RENDERER
    assert "opts.nodeStyle === 'compact'" in RENDERER
    assert "opts.tokens === true" in RENDERER
    assert "typeof opts.onSelect === 'function'" in RENDERER
    # provenance styling only starts from a relationship that names one
    assert re.search(r"function decorateEdge\(link, rel\) \{\s*if \(!rel \|\| !rel\.provenance\) return;", RENDERER)


def test_derived_connections_carry_text_not_just_colour():
    assert "Worked out" in RENDERER
    assert "Worked out, may be out of date" in RENDERER
    assert "'4,4'" in RENDERER


def test_existing_helpers_are_still_exported():
    for name in ("createNode", "createLink", "createLayerZone", "createContainerNode", "layerColor", "REL_STYLES"):
        assert re.search(r"^\s+%s:\s*%s," % (name, name), RENDERER, re.M), name


def test_reuse_register_names_the_one_drawing_engine():
    data = yaml.safe_load((ROOT / "docs/reuse-register.yml").read_text(encoding="utf-8"))
    entry = next(e for e in data["entries"] if e["id"] == "archimate-drawing-engine")
    assert entry["status"] == "active"
    assert entry["canonical"]["path"] == "app/static/js/archimate/composer_renderer.js"
    assert entry["must_not_create"]


def test_connection_style_and_row_alignment_are_opt_in():
    """Both options are read from the options object behind an exact match, so a caller that
    passes neither keeps the old routing, labels, dashes, colours and left-aligned rows."""
    assert "opts.connectionStyle === 'plain'" in RENDERER
    assert "opts.rowAlign === 'centre'" in RENDERER
    # every plain-only branch hangs off the one flag
    assert "let plain = opts.connectionStyle === 'plain';" in RENDERER
    for fragment in ("if (plain) stylePlainLink(link);", "if (plain) paintPlainLink(l, on);", "if (!plain) return;"):
        assert fragment in RENDERER, fragment
    # computeBandLayout's fifth argument is optional and defaults to today's values
    assert re.search(r"function computeBandLayout\(elements, bands, width, compact, layoutOpts\)", RENDERER)
    assert "let colGap = lo.colGap != null ? lo.colGap : BAND_GAP;" in RENDERER
    assert "let header = lo.header != null ? lo.header : BAND_HEADER;" in RENDERER


def test_plain_style_does_not_change_the_default_link_builder():
    """createLink and REL_STYLES are untouched: the plain style restyles a link after it is built."""
    assert "router: { name: 'manhattan', args: { step: 12, padding: 36 } }" in RENDERER
    assert "connector: { name: 'rounded', args: { radius: 8 } }" in RENDERER
    assert re.search(r"function createLink\(sourceCell, targetCell, relType, relId\) \{", RENDERER)
    assert "humanizeRelType(relType)" in RENDERER
    assert "'8,4'" in RENDERER  # realization stays dashed in the default style


def test_plain_style_names_no_hex_colour_and_no_page():
    start = RENDERER.index("function plainInk()")
    end = RENDERER.index("/* Banded layout: draws the zones")
    block = RENDERER[start:end]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(", block)
    assert "currentColor" in block
    assert "shadow" not in block.lower().replace("no shadow", "")
    for token in ("--muted-foreground", "--ring", "--foreground", "--border", "--card", "--accent"):
        assert token in block
    assert not re.search(r"twin", RENDERER, re.I)
    assert "d3." not in RENDERER

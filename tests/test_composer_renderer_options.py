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

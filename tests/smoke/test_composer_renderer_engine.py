"""Isolated proof of the opt-in ComposerRenderer additions: banded layout,
edge provenance, update/selection API, node bounds, compact and token styling.

The page under test is tests/composer_renderer/harness.html, which loads only the
vendored JointJS stack and composer_renderer.js. No Flask app and no existing
consumer is involved, so a failure here is a failure of the new surface alone.
The nine existing consumers are covered separately by the regression walk.
"""
import pathlib

import pytest

HARNESS = pathlib.Path(__file__).resolve().parent.parent / "composer_renderer" / "harness.html"

BANDS = [
    {"layer": "strategy", "label": "Strategy"},
    {"layer": "business", "label": "Business"},
    {"layer": "application", "label": "Application"},
    {"layer": "technology", "label": "Technology"},
]


@pytest.fixture()
def page(browser):
    pg = browser.new_page(viewport={"width": 900, "height": 800})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(HARNESS.as_uri())
    pg.wait_for_function("typeof ComposerRenderer !== 'undefined'")
    yield pg
    assert errors == []
    pg.close()


SETUP = """
(() => {
  window.BANDS = %s;
  window.EL = [
    {id:'a', name:'Zed app', type:'ApplicationComponent', layer:'application', depth:1},
    {id:'b', name:'Alpha app', type:'ApplicationComponent', layer:'application', depth:1},
    {id:'c', name:'Centre', type:'BusinessProcess', layer:'business', depth:0},
    {id:'d', name:'Orphan', type:'Node', layer:'mystery', depth:2},
    {id:'e', name:'No layer', type:'Node', layer:'', depth:2},
  ];
  // provenance/kind use the real platform vocabulary (derived_relationship.py,
  // intelligence/services/query_service.py): 'explicit' is a genuinely recorded fact,
  // 'derivation' (or kind 'derived') is worked out.
  window.REL = [
    {id:'r1', key:'k1', source_id:'c', target_id:'a', type:'serving', provenance:'explicit', kind:'explicit'},
    {id:'r2', key:'k2', source_id:'c', target_id:'b', type:'serving', provenance:'derivation', kind:'derived', stale:false},
    {id:'r3', key:'k3', source_id:'a', target_id:'d', type:'flow', provenance:'derivation', kind:'derived', stale:true},
  ];
  window.mk = (o) => {
    const host = document.getElementById('host');
    host.innerHTML = '';
    window.R = ComposerRenderer.create(host, Object.assign({mode:'view', height:300}, o));
    return window.R;
  };
})();
""" % __import__("json").dumps(BANDS)


def _banded(page, extra=""):
    page.evaluate(SETUP)
    page.evaluate(
        "(extra) => { mk(Object.assign({layout:'bands', bands:BANDS}, extra));"
        " R.loadElements(EL, REL); }", extra or {}
    )


def test_bands_render_with_unplaced_band_and_empty_band_collapsed(page):
    _banded(page)
    zones = page.evaluate("""() => R.graph.getElements().filter(c => c.get('isLayerZone'))
        .map(z => ({layer: z.get('zoneLayer'), label: z.attr('label/text'), h: z.size().height, y: z.position().y}))""")
    zones.sort(key=lambda z: z["y"])
    assert [z["layer"] for z in zones] == ["strategy", "business", "application", "technology", "unplaced"]
    by = {z["layer"]: z for z in zones}
    assert by["unplaced"]["label"] == "Layer not recorded"
    # empty declared bands keep a header and nothing else
    assert by["strategy"]["h"] == 28 and by["technology"]["h"] == 28
    assert by["application"]["h"] > 28
    ys = [z["y"] for z in zones]
    assert ys == sorted(ys)
    # unknown and blank layers both land in the unplaced band
    bands = page.evaluate("() => Object.fromEntries(R.graph.getElements().filter(c => c.get('elementId')).map(c => [c.get('elementId'), c.get('band')]))")
    assert bands == {"a": "application", "b": "application", "c": "business", "d": "unplaced", "e": "unplaced"}


def test_unplaced_band_absent_when_nothing_is_unplaced(page):
    page.evaluate(SETUP)
    n = page.evaluate("""() => { mk({layout:'bands', bands:BANDS}); R.loadElements(EL.slice(0,3), []);
        return R.graph.getElements().filter(c => c.get('isLayerZone')).length; }""")
    assert n == 4


def test_nodes_sit_inside_their_band_ordered_by_depth_then_name(page):
    _banded(page)
    r = page.evaluate("""() => {
      const pos = id => R.graph.getElements().find(c => c.get('elementId') === id).position();
      const zone = l => R.graph.getElements().find(c => c.get('zoneLayer') === l);
      const z = zone('application'), a = pos('a'), b = pos('b');
      return {zy: z.position().y, zh: z.size().height, ay: a.y, by: b.y, ax: a.x, bx: b.x};
    }""")
    assert r["zy"] < r["ay"] < r["zy"] + r["zh"]
    assert r["bx"] < r["ax"]  # same depth: "Alpha app" before "Zed app"


def test_width_aware_wrapping_and_relayout_on_resize(page):
    _banded(page)
    rows_wide = page.evaluate("() => new Set(R.graph.getElements().filter(c => c.get('band') === 'application').map(c => c.position().y)).size")
    assert rows_wide == 1
    page.evaluate("document.getElementById('host').style.width = '200px'")
    page.wait_for_function("""() => new Set(R.graph.getElements().filter(c => c.get('band') === 'application').map(c => c.position().y)).size === 2""")
    w = page.evaluate("() => R.graph.getElements().find(c => c.get('elementId') === 'a').size().width")
    assert 96 <= w <= 168
    page.evaluate("document.getElementById('host').style.width = '640px'")
    page.wait_for_function("""() => new Set(R.graph.getElements().filter(c => c.get('band') === 'application').map(c => c.position().y)).size === 1""")


def test_node_width_is_clamped(page):
    page.evaluate(SETUP)
    got = page.evaluate("""() => [60, 100, 120, 500].map(w => ComposerRenderer.computeBandLayout(EL, BANDS, w, false).nodeWidth)""")
    assert got == [96, 96, 96, 168]


def test_derived_edges_dashed_with_text_badge_never_colour_alone(page):
    _banded(page)
    info = page.evaluate("""() => R.graph.getLinks().map(l => ({
        key: l.get('edgeKey'), kind: l.get('provenance'),
        dash: l.attr('line/strokeDasharray'),
        labels: (l.labels() || []).map(x => x.attrs.text.text)}))""")
    by = {i["key"]: i for i in info}
    assert by["k1"]["kind"] == "recorded" and by["k1"]["dash"] == ""
    assert by["k1"]["labels"] == ["Serving"]
    assert by["k2"]["kind"] == "derived" and by["k2"]["dash"]
    assert "Worked out" in by["k2"]["labels"]
    assert any(t.endswith("Worked out, may be out of date") for t in by["k3"]["labels"])
    # rendered DOM: attributes present and the badge is real SVG text
    attrs = page.evaluate("""() => Array.from(document.querySelectorAll('.joint-link')).map(e => [e.getAttribute('data-edge'), e.getAttribute('data-kind')])""")
    assert sorted(attrs) == [["k1", "recorded"], ["k2", "derived"], ["k3", "derived"]]
    text = page.evaluate("() => document.querySelector('[data-edge=\"k2\"]').textContent")
    assert "Worked\xa0out" in text or "Worked out" in text


def test_provenance_vocabulary_matches_the_real_api_shapes(page):
    """derived_relationship.py / query_service.py send provenance 'derivation' (or kind
    'derived') for a worked-out fact and provenance 'explicit' for a recorded one. An
    unrecognised provenance value must never fall through to the confident "recorded"
    styling -- it renders as worked-out (dashed) instead, the safer default."""
    page.evaluate(SETUP)
    r = page.evaluate("""() => {
      mk({});
      const el = ['x', 'y', 'z', 'w'].map(id => ({id, name: id, type: 'Node', layer: 'application', depth: 0}));
      const rel = [
        {id:'p1', source_id:'x', target_id:'y', type:'association', provenance:'derivation'},
        {id:'p2', source_id:'y', target_id:'z', type:'association', provenance:'something-else', kind:'derived'},
        {id:'p3', source_id:'z', target_id:'w', type:'association', provenance:'something-else'},
        {id:'p4', source_id:'x', target_id:'w', type:'association', provenance:'explicit'},
      ];
      R.loadElements(el, rel);
      return R.graph.getLinks().map(l => ({relId: l.get('relId'), kind: l.get('provenance'), dash: l.attr('line/strokeDasharray')}));
    }""")
    by = {i["relId"]: i for i in r}
    assert by["p1"]["kind"] == "derived" and by["p1"]["dash"]
    assert by["p2"]["kind"] == "derived" and by["p2"]["dash"]
    # unrecognised provenance alone (no kind:'derived' either) must still default to
    # "derived" (worked-out styling), never silently render as a recorded fact
    assert by["p3"]["kind"] == "derived" and by["p3"]["dash"]
    assert by["p4"]["kind"] == "recorded" and by["p4"]["dash"] == ""


def test_update_changes_only_what_changed(page):
    _banded(page)
    out = page.evaluate("""() => {
      const idOf = k => R.graph.getLinks().find(l => l.get('edgeKey') === k).id;
      const nodeOf = e => R.graph.getElements().find(c => c.get('elementId') === e).id;
      const keep = {a: nodeOf('a'), c: nodeOf('c'), k1: idOf('k1')};
      const el = EL.filter(e => e.id !== 'e').concat([{id:'f', name:'Newcomer', type:'Node', layer:'technology', depth:1}]);
      el.find(e => e.id === 'b').name = 'Renamed';
      const rel = REL.filter(r => r.key !== 'k3');
      const fresh = R.update({elements: el, relationships: rel});
      const now = Object.fromEntries(R.graph.getCells().map(c => [c.id, 1]));
      return {
        keptA: keep.a === nodeOf('a'), keptC: keep.c === nodeOf('c'), keptLink: keep.k1 === idOf('k1'),
        hasF: !!R.graph.getElements().find(c => c.get('elementId') === 'f'),
        noE: !R.graph.getElements().find(c => c.get('elementId') === 'e'),
        renamed: R.graph.getElements().find(c => c.get('elementId') === 'b').attr('nameLabel/text'),
        noK3: !R.graph.getLinks().find(l => l.get('edgeKey') === 'k3'),
        fresh: fresh, count: R.getElementCount(),
      };
    }""")
    assert out["keptA"] and out["keptC"] and out["keptLink"]
    assert out["hasF"] and out["noE"] and out["noK3"]
    assert out["renamed"] == "Renamed"
    assert out["count"] == 5


def test_update_with_same_model_touches_nothing(page):
    _banded(page)
    changed = page.evaluate("""() => {
      let n = 0; R.graph.on('change add remove', () => n++);
      R.update({elements: EL, relationships: REL});
      return n;
    }""")
    assert changed == 0


def test_selection_centre_and_highlight_and_on_select(page):
    page.evaluate(SETUP)
    page.evaluate("""() => { window.picked = []; mk({layout:'bands', bands:BANDS, onSelect: (id) => picked.push(id)});
        R.loadElements(EL, REL); }""")
    page.evaluate("() => { R.setSelected('a'); R.setCentre('c'); R.setHighlightedEdges(new Set(['k2'])); }")
    st = page.evaluate("""() => ({
        sel: Array.from(document.querySelectorAll('[data-selected=true]')).map(e => e.getAttribute('model-id')).length,
        centre: document.querySelectorAll('[data-centre=true]').length,
        hi: Array.from(document.querySelectorAll('[data-highlighted=true]')).map(e => e.getAttribute('data-edge'))})""")
    assert st["sel"] == 1 and st["centre"] == 1 and st["hi"] == ["k2"]
    page.evaluate("() => { R.setSelected(null); R.setHighlightedEdges([]); }")
    assert page.evaluate("() => document.querySelectorAll('[data-selected=true],[data-highlighted=true]').length") == 0
    box = page.evaluate("() => R.getNodeBounds().find(b => b.id === 'b')")
    host = page.locator("#host").bounding_box()
    page.mouse.click(host["x"] + box["x"] + box["width"] / 2, host["y"] + box["y"] + box["height"] / 2)
    assert page.evaluate("() => picked") == ["b"]


def test_get_node_bounds_matches_rendered_boxes_after_relayout(page):
    _banded(page)
    for width in ("640px", "220px", "400px"):
        page.evaluate("w => { document.getElementById('host').style.width = w; R.resize(); }", width)
        rows = page.evaluate("""() => {
          const host = document.getElementById('host').getBoundingClientRect();
          return R.getNodeBounds().map(b => {
            const v = R.paper.findViewByModel(R.graph.getElements().find(c => c.get('elementId') === b.id));
            const r = v.el.querySelector('[joint-selector=body]').getBoundingClientRect();
            return [b, {x: r.x - host.x, y: r.y - host.y, w: r.width, h: r.height}];
          });
        }""")
        assert len(rows) == 5
        for b, dom in rows:
            assert abs(b["x"] - dom["x"]) <= 1.5 and abs(b["y"] - dom["y"]) <= 1.5
            assert abs(b["width"] - dom["w"]) <= 2.5 and abs(b["height"] - dom["h"]) <= 2.5
        order = [(b["y"], b["x"]) for b, _ in rows]
        assert order == sorted(order)


def test_compact_nodes_are_name_only_without_ports(page):
    _banded(page, {"nodeStyle": "compact"})
    r = page.evaluate("""() => { const n = R.graph.getElements().find(c => c.get('elementId') === 'a');
        return {h: n.size().height, ports: n.getPorts().length, type: n.attr('typeLabel/display'),
                icon: n.attr('typeIcon/display'), name: n.attr('nameLabel/text')}; }""")
    assert r == {"h": 48, "ports": 0, "type": "none", "icon": "none", "name": "Zed app"}


def test_full_nodes_keep_ports_and_type_label_when_not_compact(page):
    _banded(page)
    r = page.evaluate("""() => { const n = R.graph.getElements().find(c => c.get('elementId') === 'a');
        return {h: n.size().height, ports: n.getPorts().length, type: n.attr('typeLabel/text')}; }""")
    assert r == {"h": 130, "ports": 12, "type": "Application Component"}


def test_token_colours_resolve_at_draw_time(page):
    _banded(page, {"tokens": True})
    before = page.evaluate("() => R.graph.getElements().find(c => c.get('elementId') === 'a').attr('body/stroke')")
    assert before.startswith("hsl(221 83% 53%")
    page.evaluate("document.documentElement.style.setProperty('--layer-application', '10 20% 30%'); R.resize();")
    # nodes are restyled on update, zones on every relayout
    zone = page.evaluate("() => R.graph.getElements().find(c => c.get('zoneLayer') === 'application').attr('body/stroke')")
    assert zone == "hsl(10 20% 30%)"
    page.evaluate("document.documentElement.style.removeProperty('--layer-application')")


def test_without_options_output_is_the_old_output(page):
    """No layout/tokens/compact options AND no provenance on the relationships: the
    true pre-existing baseline, undecorated. (Decoration is a separate opt-in proven by
    test_provenance_alone_turns_on_decoration_without_other_options below -- REL here
    must carry no provenance field at all, or this test would pass even if decorateEdge
    always ran.)"""
    page.evaluate(SETUP)
    r = page.evaluate("""() => {
        mk({});
        const relNoProvenance = REL.map(({id, key, source_id, target_id, type}) => ({id, key, source_id, target_id, type}));
        R.loadElements(EL, relNoProvenance);
        const link = R.graph.getLinks()[0];
        return {zones: R.graph.getElements().filter(c => c.get('isLayerZone')).length,
                dash: link.attr('line/strokeDasharray'), labels: link.labels().length,
                dataEdge: document.querySelectorAll('[data-edge],[data-kind],[data-band]').length,
                ports: R.graph.getElements()[0].getPorts().length, h: R.graph.getElements()[0].size().height}; }""")
    assert r["zones"] == 0 and r["labels"] == 1
    assert r["h"] == 130 and r["ports"] == 12
    assert r["dash"] == ""
    assert r["dataEdge"] == 0


def test_provenance_alone_turns_on_decoration_without_other_options(page):
    """The real positive-path counterpart to the baseline above: with no layout/tokens/
    compact option at all, relationships that DO carry provenance still get decorated
    (decorateEdge is not gated on layout:'bands')."""
    page.evaluate(SETUP)
    r = page.evaluate("""() => { mk({}); R.loadElements(EL, REL);
        return {
            dataEdge: document.querySelectorAll('[data-edge],[data-kind]').length,
            kinds: Array.from(document.querySelectorAll('.joint-link')).map(e => e.getAttribute('data-kind')).sort(),
        }; }""")
    assert r["dataEdge"] > 0
    assert r["kinds"] == ["derived", "derived", "recorded"]


def test_update_after_a_default_loadElements_replaces_a_changed_relationship(page):
    """update() is not gated on banded mode, so loadElements() in the plain/default
    (non-banded) mode must still leave every link with a real signature -- otherwise a
    relationship whose visual state changes (here: from an explicit/recorded fact to a
    worked-out one) is never actually replaced on the next update(), and the stale,
    wrongly-confident "recorded" rendering survives."""
    page.evaluate(SETUP)
    out = page.evaluate("""() => {
      mk({});
      R.loadElements(EL, REL);
      const before = R.graph.getLinks().find(l => l.get('edgeKey') === 'k1');
      const beforeKind = before.get('provenance'), beforeDash = before.attr('line/strokeDasharray');
      const changed = REL.map(r => r.key === 'k1'
          ? Object.assign({}, r, {provenance: 'derivation', kind: 'derived', stale: false})
          : r);
      R.update({elements: EL, relationships: changed});
      const after = R.graph.getLinks().find(l => l.get('edgeKey') === 'k1');
      return {
        beforeKind: beforeKind, beforeDash: beforeDash,
        afterKind: after ? after.get('provenance') : null,
        afterDash: after ? after.attr('line/strokeDasharray') : null,
      };
    }""")
    assert out["beforeKind"] == "recorded" and out["beforeDash"] == ""
    assert out["afterKind"] == "derived"
    assert out["afterDash"]


def test_relationship_with_no_id_or_key_still_renders_and_is_stable_across_updates(page):
    """A relationship that the API sends without a key or an id (both null/absent) must
    still render -- a synthetic key derived from (source, target, type) stands in for
    it -- and must not be dropped or recreated as a brand-new cell on a later update()
    with the exact same (still id-less) relationship."""
    _banded(page)
    out = page.evaluate("""() => {
      const noKeyRel = {source_id: 'a', target_id: 'b', type: 'association'};
      R.update({elements: EL, relationships: REL.concat([noKeyRel])});
      const find = () => R.graph.getLinks().find(l => {
          const src = R.graph.getCell(l.get('source').id), tgt = R.graph.getCell(l.get('target').id);
          return l.get('relType') === 'association' && src && tgt
              && src.get('elementId') === 'a' && tgt.get('elementId') === 'b';
      });
      const link1 = find();
      const id1 = link1 ? link1.id : null;
      const countAfterFirst = R.getRelCount();
      // same id-less relationship again: must be recognised as already present, not
      // dropped-then-recreated (which would change its JointJS cell id) or duplicated.
      R.update({elements: EL, relationships: REL.concat([noKeyRel])});
      const link2 = find();
      return {
        found1: !!link1, found2: !!link2,
        sameCell: !!link1 && !!link2 && id1 === link2.id,
        countAfterFirst: countAfterFirst, countAfterSecond: R.getRelCount(),
      };
    }""")
    assert out["found1"] is True
    assert out["found2"] is True
    assert out["sameCell"] is True
    assert out["countAfterFirst"] == out["countAfterSecond"]


# ---------------------------------------------------------------------------
# Default-style golden output
#
# GOLDEN was captured from the engine as it stood on main before any
# connectionStyle / rowAlign work (set COMPOSER_CAPTURE_GOLDEN=1 to re-capture,
# which is only ever done deliberately). A caller that passes neither new option
# must get this output exactly: graph JSON and the paper's SVG, with JointJS's
# generated ids replaced by their order of first appearance.
# ---------------------------------------------------------------------------
import json
import os

GOLDEN = pathlib.Path(__file__).resolve().parent.parent / "composer_renderer" / "golden_default_style.json"

_SERIALISE = r"""
(() => {
  const map = {};
  let n = 0;
  const norm = (s) => s.replace(
    /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\bj_\d+\b|\bv-\d+\b/g,
    (m) => (map[m] = map[m] || ('ID' + (++n))));
  const graph = JSON.stringify(R.graph.toJSON(), null, 1);
  const svg = R.paper.svg.outerHTML.replace(/></g, '>\n<');
  const both = norm(graph + '\n=====SVG=====\n' + svg).split('\n=====SVG=====\n');
  return {graph: both[0], svg: both[1]};
})()
"""


def _default_style_snapshot(page):
    page.evaluate(SETUP)
    out = {}
    page.evaluate("() => { mk({}); R.loadElements(EL, REL); }")
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
    out["no_options"] = page.evaluate(_SERIALISE)
    page.evaluate("""() => { mk({layout:'bands', bands:BANDS, nodeStyle:'compact', tokens:true});
        R.loadElements(EL, REL); R.setCentre('c'); R.setSelected('a'); R.setHighlightedEdges(new Set(['k2'])); }""")
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
    out["bands_compact_tokens_provenance"] = page.evaluate(_SERIALISE)
    return out


def test_default_style_output_matches_main_golden(page):
    got = _default_style_snapshot(page)
    if os.environ.get("COMPOSER_CAPTURE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for case in ("no_options", "bands_compact_tokens_provenance"):
        assert got[case]["graph"] == want[case]["graph"], case + ": graph JSON differs from the golden"
        assert got[case]["svg"] == want[case]["svg"], case + ": SVG differs from the golden"

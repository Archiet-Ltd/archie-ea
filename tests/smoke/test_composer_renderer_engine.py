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
    # Measure text only once the vendored Inter the harness pins is actually
    # loaded; its @font-face uses font-display:swap, so without this a label can
    # be measured against the fallback before Inter arrives, and the geometry in
    # the golden snapshot would not be reproducible.
    pg.evaluate("() => document.fonts.load('700 12px Inter').then(() => document.fonts.load('400 9px Inter')).then(() => document.fonts.ready)")
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


# ---------------------------------------------------------------------------
# Plain connection style (connectionStyle:'plain') and centred rows (rowAlign:'centre')
# ---------------------------------------------------------------------------
HEX = r"#[0-9a-fA-F]{3,8}\b|rgb\("

STYLE_SETUP = """
(() => {
  window.BANDS = %s;
  window.SEL = [
    {id:'c', name:'Centre', type:'BusinessProcess', layer:'business', depth:0},
    {id:'a', name:'Ledger app', type:'ApplicationComponent', layer:'application', depth:1},
    {id:'b', name:'Billing app', type:'ApplicationComponent', layer:'application', depth:1},
    {id:'d', name:'Goal', type:'Goal', layer:'strategy', depth:1},
    {id:'e', name:'Notes', type:'Node', layer:'technology', depth:1},
  ];
  window.SREL = [
    {id:'s1', key:'s1', source_id:'c', target_id:'a', type:'realization', provenance:'explicit', kind:'explicit'},
    {id:'s2', key:'s2', source_id:'c', target_id:'b', type:'flow', provenance:'explicit', kind:'explicit'},
    {id:'s3', key:'s3', source_id:'a', target_id:'d', provenance:'explicit', kind:'explicit'},
    {id:'s4', key:'s4', source_id:'b', target_id:'e', type:'serving', provenance:'derivation', kind:'derived'},
  ];
  window.resolved = (name) => 'hsl(' + getComputedStyle(document.getElementById('host')).getPropertyValue(name).trim() + ')';
  window.inspect = (R) => R.graph.getLinks().map(l => {
    const labels = (l.labels() || []).map(x => x.attrs.text.text);
    const tm = l.attr('line/targetMarker'), sm = l.attr('line/sourceMarker');
    return {key: l.get('edgeKey'), labels, dash: l.attr('line/strokeDasharray'), stroke: l.attr('line/stroke'),
            width: l.attr('line/strokeWidth'), arrow: !!(tm && tm.d), arrowFill: tm && tm.fill, source: !!(sm && sm.d),
            attrs: JSON.stringify([l.attributes.attrs, l.attributes.labels]),
            dom: document.querySelector('[model-id="' + l.id + '"]').outerHTML};
  });
})();
""" % __import__("json").dumps(BANDS)


def test_plain_style_switches_label_dash_arrow_and_colour(page):
    import re
    page.evaluate(STYLE_SETUP)
    out = page.evaluate("""() => {
      const host = document.getElementById('host');
      const make = (o) => { host.innerHTML = ''; const r = ComposerRenderer.create(host, Object.assign({mode:'view', height:300, layout:'bands', bands:BANDS}, o)); r.loadElements(SEL, SREL); return r; };
      const plain = make({connectionStyle:'plain'});
      const before = inspect(plain);
      plain.setHighlightedEdges(['s1']);
      const hi = inspect(plain);
      const plainRouters = plain.graph.getLinks().map(l => [l.get('router').name, l.get('connector').name]);
      const def = make({});
      return {before, hi, plainRouters, def: inspect(def), muted: resolved('--muted-foreground'), primary: resolved('--ring'), card: resolved('--card'), fg: resolved('--foreground'), mfg: resolved('--muted-foreground'),
              chrome: (() => { const nodes = plain.graph.getElements(); return nodes.map(n => JSON.stringify(n.attributes.attrs)); })(),
              defRouters: def.graph.getLinks().map(l => [l.get('router').name, l.get('connector').name])};
    }""")
    by = {i["key"]: i for i in out["before"]}
    for key, info in by.items():
        assert info["arrow"] is True, key
        assert info["source"] is False, key
        assert not re.search(HEX, info["attrs"]), (key, "hex or rgb in link attributes")
        assert not re.search(HEX, info["dom"]), (key, "hex or rgb in rendered link")
        assert info["stroke"] == out["muted"], key
        assert info["arrowFill"] == out["muted"], key
        assert info["width"] == 1, key
    # no relationship word on any link; the worked-out one has exactly its badge
    assert by["s1"]["labels"] == [] and by["s2"]["labels"] == [] and by["s3"]["labels"] == []
    assert by["s4"]["labels"] == ["Worked out"]
    # solid when recorded, whatever the type; dashed 5,5 when worked out
    assert by["s1"]["dash"] == "" and by["s2"]["dash"] == "" and by["s3"]["dash"] == ""
    assert by["s4"]["dash"] == "5,5"
    # highlighted links take the ring token and widen
    hi = {i["key"]: i for i in out["hi"]}
    assert hi["s1"]["stroke"] == out["primary"] and hi["s1"]["arrowFill"] == out["primary"]
    assert hi["s1"]["width"] == 2.5
    assert hi["s2"]["stroke"] == out["muted"]
    assert out["plainRouters"] == [["normal", "smooth"]] * 4
    # the same page, default style: today's values, so the switch is per renderer instance
    d = {i["key"]: i for i in out["def"]}
    assert d["s1"]["labels"] == ["Realization"] and d["s1"]["dash"] == "8,4"
    assert d["s2"]["labels"] == ["Flow"] and d["s2"]["dash"] == "10,4"
    assert d["s3"]["labels"] == ["Association"] and d["s3"]["arrow"] is False
    assert d["s4"]["labels"][0] == "Serving" and d["s4"]["dash"] == "4,4"
    assert re.search(HEX, d["s1"]["attrs"])
    # node cards and band zones of the plain style: tokens only, no shadow, no literal colour
    for blob in out["chrome"]:
        assert not re.search(HEX, blob), blob[:200]
        assert "shadow" not in blob.lower() and "filter" not in blob.lower()
    assert any(out["card"] in blob for blob in out["chrome"])
    assert any('"fontSize":14' in blob and out["fg"] in blob for blob in out["chrome"])
    assert any('"fontSize":10' in blob and out["mfg"] in blob for blob in out["chrome"])
    assert out["defRouters"] == [["manhattan", "rounded"]] * 4


ROUTE_SETUP = """
(() => {
  window.RBANDS = %s;
  const mkEl = (id, layer, depth, name) => ({id, name: name || ('Node ' + id), type: layer === 'strategy' ? 'Goal' : (layer === 'business' ? 'BusinessProcess' : (layer === 'application' ? 'ApplicationComponent' : 'Node')), layer, depth});
  window.REL_EL = [
    mkEl('g1','strategy',1,'Grow revenue'), mkEl('g2','strategy',1,'Cut cost'),
    mkEl('b0','business',0,'Order process'), mkEl('b1','business',1), mkEl('b2','business',1), mkEl('b3','business',2), mkEl('b4','business',2),
    mkEl('a1','application',1), mkEl('a2','application',1), mkEl('a3','application',2,'Application component with a rather long name'), mkEl('a4','application',2), mkEl('a5','application',3),
    mkEl('t1','technology',1), mkEl('t2','technology',2), mkEl('t3','technology',3),
  ];
  const R_ = (id, s, t, type, prov) => ({id, key: id, source_id: s, target_id: t, type, provenance: prov || 'explicit', kind: prov === 'derivation' ? 'derived' : 'explicit'});
  window.REL_REL = [
    R_('e1','b0','g1','influence'), R_('e2','b0','g2','influence','derivation'),
    R_('e3','b0','a1','serving'), R_('e4','b0','a5','serving','derivation'), R_('e5','b0','t3','realization','derivation'),
    R_('e6','a1','b3','serving'), R_('e7','a2','t1','realization'), R_('e8','a4','t2','flow','derivation'),
    R_('e9','b1','b4','triggering'), R_('e10','b1','b2','association'),
    R_('e11','a1','a5','flow'), R_('e12','t1','g1','influence','derivation'),
    R_('e13','b0','a1','flow','derivation'),
    R_('e14','a3','b0','serving'), R_('e15','g1','a4','realization'),
  ];
  window.mkRoute = (extra, width) => {
    const host = document.getElementById('host');
    host.innerHTML = '';
    window.R = ComposerRenderer.create(host, Object.assign({mode:'view', layout:'bands', bands:RBANDS, nodeStyle:'compact', tokens:true, connectionStyle:'plain'}, extra || {}));
    host.style.width = (width || 640) + 'px';
    R.loadElements(REL_EL, REL_REL);
    return true;
  };
  window.measure = () => {
    const nodes = R.graph.getElements().filter(c => c.get('elementId')).map(c => ({id: c.id, key: c.get('elementId'), b: c.getBBox()}));
    const zones = R.graph.getElements().filter(c => c.get('isLayerZone')).map(c => c.getBBox());
    const size = R.paper.getComputedSize();
    const inBox = (p, b, inset) => p.x > b.x + inset && p.x < b.x + b.width - inset && p.y > b.y + inset && p.y < b.y + b.height - inset;
    const borderDist = (p, b) => {
      const dx = Math.max(b.x - p.x, 0, p.x - (b.x + b.width)), dy = Math.max(b.y - p.y, 0, p.y - (b.y + b.height));
      if (dx > 0 || dy > 0) return Math.hypot(dx, dy);
      return Math.min(p.x - b.x, b.x + b.width - p.x, p.y - b.y, b.y + b.height - p.y);
    };
    return R.graph.getLinks().map(l => {
      const view = R.paper.findViewByModel(l);
      const path = view.getConnection();
      const total = path.length();
      const N = 400, pts = [];
      for (let i = 0; i <= N; i++) { const p = path.pointAtLength(total * i / N); pts.push({x: p.x, y: p.y}); }
      const s = nodes.find(n => n.id === l.source().id), t = nodes.find(n => n.id === l.target().id);
      const others = nodes.filter(n => n !== s && n !== t);
      const insideNode = pts.filter(p => others.some(n => inBox(p, n.b, 2))).length;
      const outside = pts.filter(p => p.x < 0 || p.x > size.width || p.y < 0 || p.y > size.height).length;
      /* longest run lying within 3px of a band zone's top or bottom edge */
      let longest = 0, run = 0, last = null;
      const edges = [];
      zones.forEach(z => { edges.push({y: z.y, x0: z.x, x1: z.x + z.width}); edges.push({y: z.y + z.height, x0: z.x, x1: z.x + z.width}); });
      pts.forEach(p => {
        const near = edges.some(e => Math.abs(p.y - e.y) <= 3 && p.x >= e.x0 && p.x <= e.x1);
        if (near) { run = last && last.near ? run + Math.hypot(p.x - last.x, p.y - last.y) : 0; longest = Math.max(longest, run); }
        else run = 0;
        last = {x: p.x, y: p.y, near};
      });
      const end = pts[pts.length - 1], start = pts[0];
      let clearance = Infinity;
      pts.forEach(p => others.forEach(n => { clearance = Math.min(clearance, inBox(p, n.b, -0.01) ? -1 : borderDist(p, n.b)); }));
      const mid = path.pointAtLength(total / 2);
      return {key: l.get('edgeKey'), from: s.key, to: t.key, insideNode, outside, longestEdgeRun: longest,
              endBorder: borderDist(end, t.b), endInterior: inBox(end, t.b, 1),
              startBorder: borderDist(start, s.b), startInterior: inBox(start, s.b, 1),
              minClearance: clearance === Infinity ? null : clearance,
              mid: {x: mid.x, y: mid.y},
              points: pts.filter((p, i) => i %% 20 === 0)};
    });
  };
})();
""" % __import__("json").dumps(BANDS)


def _assert_routes(rows, label, count=15):
    assert len(rows) == count, label
    for r in rows:
        where = "%s %s %s->%s" % (label, r["key"], r["from"], r["to"])
        assert r["insideNode"] == 0, where + " crosses a node"
        assert r["outside"] == 0, where + " leaves the drawing"
        assert r["longestEdgeRun"] <= 24, where + " runs along a band edge: %s" % r["longestEdgeRun"]
        assert r["endBorder"] <= 2 and not r["endInterior"], where + " does not end on the target border"
        assert r["startBorder"] <= 2 and not r["startInterior"], where + " does not start on the source border"


def test_plain_routes_avoid_nodes_stay_inside_and_end_on_a_border(page):
    page.evaluate(ROUTE_SETUP)
    page.evaluate("() => mkRoute()")
    assert page.evaluate("() => new Set(R.graph.getElements().filter(c => c.get('band') === 'business').map(c => c.position().y)).size") == 2
    _assert_routes(page.evaluate("() => measure()"), "initial")
    # narrower drawing: rows wrap differently, every route is recomputed
    page.evaluate("() => { document.getElementById('host').style.width = '420px'; R.resize(); }")
    _assert_routes(page.evaluate("() => measure()"), "narrower")
    # an update that adds a node and two connections
    page.evaluate("""() => { R.update({elements: REL_EL.concat([{id:'x1', name:'Late arrival', type:'Node', layer:'technology', depth:1}]),
        relationships: REL_REL.concat([{id:'e16', key:'e16', source_id:'x1', target_id:'b0', type:'serving', provenance:'explicit', kind:'explicit'},
                                       {id:'e17', key:'e17', source_id:'g2', target_id:'x1', type:'influence', provenance:'derivation', kind:'derived'}])}); }""")
    _assert_routes(page.evaluate("() => measure()"), "after update", 17)


def test_plain_style_also_works_without_bands(page):
    page.evaluate(ROUTE_SETUP)
    page.evaluate("() => mkRoute({layout: undefined, bands: undefined, nodeStyle: undefined, height: 900}, 1100)")
    rows = page.evaluate("() => measure()")
    assert len(rows) == 15
    for r in rows:
        assert r["insideNode"] == 0, r["key"]
        assert r["outside"] == 0, r["key"]
        assert r["endBorder"] <= 2 and not r["endInterior"], r["key"]


def test_a_recorded_and_a_worked_out_connection_between_one_pair_both_stay_visible(page):
    import math
    page.evaluate(ROUTE_SETUP)
    page.evaluate("() => mkRoute()")
    rows = {r["key"]: r for r in page.evaluate("() => measure()")}
    # e3 (recorded) and e13 (worked out) join b0 and a1 in different rows
    d = math.hypot(rows["e3"]["mid"]["x"] - rows["e13"]["mid"]["x"], rows["e3"]["mid"]["y"] - rows["e13"]["mid"]["y"])
    assert d >= 10, d
    # both keys are on the drawing, one link each
    keys = page.evaluate("() => Array.from(document.querySelectorAll('.joint-link')).map(e => e.getAttribute('data-edge')).sort()")
    assert "e3" in keys and "e13" in keys and len(keys) == len(set(keys))
    # a pair on one row
    page.evaluate("""() => { R.update({elements: REL_EL, relationships: REL_REL.concat([
        {id:'p1', key:'p1', source_id:'b1', target_id:'b2', type:'serving', provenance:'derivation', kind:'derived'}])}); }""")
    rows = {r["key"]: r for r in page.evaluate("() => measure()")}
    d = math.hypot(rows["e10"]["mid"]["x"] - rows["p1"]["mid"]["x"], rows["e10"]["mid"]["y"] - rows["p1"]["mid"]["y"])
    assert d >= 10, d


def test_centred_rows_have_equal_margins(page):
    page.evaluate(ROUTE_SETUP)
    for width in (640, 420, 900):
        margins = page.evaluate("""(w) => {
          mkRoute({connectionStyle: undefined, rowAlign: 'centre'}, w);
          const zones = {};
          R.graph.getElements().filter(c => c.get('isLayerZone')).forEach(z => { zones[z.get('zoneLayer')] = z.getBBox(); });
          const rows = {};
          R.graph.getElements().filter(c => c.get('elementId')).forEach(c => {
            const k = c.get('band') + '@' + c.position().y; const b = c.getBBox();
            const r = rows[k] = rows[k] || {band: c.get('band'), l: Infinity, r: -Infinity};
            r.l = Math.min(r.l, b.x); r.r = Math.max(r.r, b.x + b.width);
          });
          return Object.values(rows).map(r => ({left: r.l - zones[r.band].x, right: zones[r.band].x + zones[r.band].width - r.r}));
        }""", width)
        assert margins
        for m in margins:
            assert abs(m["left"] - m["right"]) <= 1, (width, m)
        # real positions still come back from getNodeBounds
        bounds = page.evaluate("() => R.getNodeBounds()")
        assert len(bounds) == 15 and all(b["x"] >= 0 for b in bounds)
    # without the option, rows are left-aligned exactly as before
    left = page.evaluate("""() => { mkRoute({connectionStyle: undefined}, 640);
        return Math.min.apply(null, R.graph.getElements().filter(c => c.get('elementId')).map(c => c.position().x)); }""")
    assert left == 12


def test_plain_layout_gaps_are_at_least_sixteen_pixels(page):
    page.evaluate(ROUTE_SETUP)
    gaps = page.evaluate("""() => {
      mkRoute({}, 900);
      const nodes = R.graph.getElements().filter(c => c.get('elementId')).map(c => c.getBBox());
      let col = Infinity, row = Infinity;
      nodes.forEach(a => nodes.forEach(b => {
        if (a === b) return;
        if (Math.abs(a.y - b.y) < 1 && b.x > a.x) col = Math.min(col, b.x - (a.x + a.width));
        if (Math.abs(a.x - b.x) < 1 && b.y > a.y) row = Math.min(row, b.y - (a.y + a.height));
      }));
      return {col, row};
    }""")
    assert gaps["col"] >= 16 and gaps["row"] >= 16, gaps


def test_arrivals_on_one_node_side_stay_apart_and_chip_and_ring_are_drawn_from_tokens(page):
    page.evaluate(ROUTE_SETUP)
    page.evaluate("() => mkRoute()")
    rows = {r["key"]: r for r in page.evaluate("() => measure()")}
    # e1 (recorded) and e12 (worked out) both arrive on the bottom of g1
    a, b = rows["e1"]["points"][-1], rows["e12"]["points"][-1]
    assert abs(a["x"] - b["x"]) >= 12, (a, b)
    chips = page.evaluate("""() => {
      const out = [];
      R.graph.getLinks().forEach(l => { (l.labels() || []).forEach(x => out.push([x.attrs.chip.fill, x.attrs.chip.stroke, x.attrs.chip.strokeDasharray])); });
      return {chips: out, card: resolved('--card'), border: resolved('--border')};
    }""".replace("resolved(", "((n) => 'hsl(' + getComputedStyle(document.getElementById('host')).getPropertyValue(n).trim() + ')')("))
    assert chips["chips"]
    for fill, stroke, dash in chips["chips"]:
        assert fill == chips["card"] and stroke == chips["border"] and not dash
    ring = page.evaluate("""() => { R.setCentre('b0'); const n = R.graph.getElements().find(c => c.get('elementId') === 'b0');
        return [n.attr('ring/display'), n.attr('ring/rx'), n.attr('ring/strokeWidth'), n.attr('ring/fill')]; }""")
    assert ring == ["block", 9, 2, "none"]
    off = page.evaluate("""() => { R.setCentre(null); return R.graph.getElements().filter(c => c.get('elementId')).every(n => n.attr('ring/display') === 'none'); }""")
    assert off is True

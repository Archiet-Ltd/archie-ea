"""The Composer's focus view fits the drawing to the screen, follows the theme and keeps its labels apart.

The person: an enterprise architect who followed a "what does this connect to" link and
lands on the Composer's focus view of the most connected application.
The goal: see the application and everything it connects to on screen, on a desktop or a
phone, in the light or the dark theme, with every connection label readable, and find the
same connections as a list of 14 rows.
"""
import datetime
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

APP = '[x-data^="composerApp"]'
DRAW_SECONDS = 15000
VIEWS = [(1440, 900), (390, 844), (375, 667)]
SCHEMES = ["light", "dark"]

# Ten direct neighbours of the hub, two more reached through the first two of them,
# and two connections worked out over the drawn ones.
NEIGHBOURS = [
    ("Customer Portal", "ApplicationComponent", "application"),
    ("Billing Service", "ApplicationService", "application"),
    ("Orders Database", "Node", "technology"),
    ("Payments Team", "BusinessActor", "business"),
    ("Settle Invoices", "BusinessProcess", "business"),
    ("Fraud Rules Engine", "ApplicationComponent", "application"),
    ("Reporting Warehouse", "Node", "technology"),
    ("Customer Records", "DataObject", "application"),
    ("Compliance Officer", "BusinessRole", "business"),
    ("Notification Gateway", "ApplicationComponent", "application"),
]


@pytest.fixture(scope="module")
def hub():
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    fresh = create_fresh_org("enterprise_architect")
    org_id = fresh["org_id"]
    suffix = uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        def element(name, kind, layer):
            row = ArchiMateElement(name="%s %s" % (name, suffix), type=kind, layer=layer, organization_id=org_id)
            db.session.add(row)
            db.session.commit()
            return row

        def link(kind, source, target):
            row = ArchiMateRelationship(type=kind, source_id=source.id, target_id=target.id, organization_id=org_id)
            db.session.add(row)
            db.session.commit()
            return row

        centre = element("Order Management Platform", "ApplicationComponent", "application")
        others = [element(*n) for n in NEIGHBOURS]
        direct = [link("Serving", centre, other) for other in others]
        beyond = element("Regulatory Reporting Hub", "ApplicationComponent", "application")
        first = link("Flow", others[0], beyond)
        audit = element("Retention Policy Register", "ApplicationComponent", "application")
        second = link("Flow", others[1], audit)
        # Worked-out connections go in last: a write to an element marks them out of date.
        db.session.add(DerivedRelationship(
            organization_id=org_id, source_element_id=centre.id, target_element_id=beyond.id,
            derived_type="Serving", rule_id="serving-through-serving", chain=[direct[0].id, first.id],
            chain_element_ids=[centre.id, others[0].id, beyond.id], depth=2, confidence=0.8,
            provenance="derivation", engine_version="1.0",
            computed_at=datetime.datetime.utcnow(), stale=False))
        db.session.add(DerivedRelationship(
            organization_id=org_id, source_element_id=centre.id, target_element_id=audit.id,
            derived_type="Serving", rule_id="serving-through-serving", chain=[direct[1].id, second.id],
            chain_element_ids=[centre.id, others[1].id, audit.id], depth=2, confidence=0.8,
            provenance="derivation", engine_version="1.0",
            computed_at=datetime.datetime.utcnow(), stale=False))
        db.session.commit()
        # A second view of 21 elements: a ledger application with twenty neighbours.
        ledger = element("Calibration Ledger", "ApplicationComponent", "application")
        for index in range(20):
            other = element("Ledger Neighbour %02d" % index, "ApplicationComponent", "application")
            link("Serving", ledger, other)
        out = {"email": fresh["emails"]["enterprise_architect"], "centre": centre.id,
               "name": centre.name, "ledger": ledger.id}
        db.session.remove()
    return out


def _open(browser, live_server, hub, width, height, scheme, centre=None):
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    page = context.new_page()
    sign_in(page, live_server, hub["email"])
    page.goto("%s/archimate/composer?element=%d" % (live_server, centre or hub["centre"]),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_function(
        """(sel) => { if (!window.Alpine || !document.querySelector(sel)) return false;
            const a = Alpine.$data(document.querySelector(sel));
            return !!a.focusView && a.graph.getElements().length > 1; }""",
        arg=APP, timeout=DRAW_SECONDS)
    page.wait_for_timeout(1500)
    return context, page


MEASURE = """() => {
  const canvas = document.querySelector('#composer-canvas').getBoundingClientRect();
  const boxes = (sel) => Array.from(document.querySelectorAll(sel)).map(n => {
    const r = n.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height, text: n.textContent.trim()}; });
  const rgb = getComputedStyle(document.querySelector('.composer-canvas-bg')).backgroundColor
      .match(/[\\d.]+/g).map(Number);
  const lin = c => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
  const lum = 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2]);
  const a = Alpine.$data(document.querySelector('[x-data^="composerApp"]'));
  return {
    canvas: {x: canvas.x, y: canvas.y, w: canvas.width, h: canvas.height},
    zoom: a.zoomPercent,
    nodes: boxes('#composer-canvas .joint-element'),
    labels: boxes('#composer-canvas .joint-link .label, #composer-canvas .joint-link [joint-selector=labelBody], #composer-canvas .joint-link g.labels > g'),
    luminance: lum,
    shadow: getComputedStyle(document.querySelector('#composer-focus-banner')).boxShadow,
  };
}"""


def _at_reading_size(page):
    return page.evaluate("(sel) => !!Alpine.$data(document.querySelector(sel)).focusView.atReadingSize", APP)


def _assert_inside(nodes, c, where):
    for n in nodes:
        assert n["x"] >= c["x"] - 1 and n["y"] >= c["y"] - 1 and \
            n["x"] + n["w"] <= c["x"] + c["w"] + 1 and n["y"] + n["h"] <= c["y"] + c["h"] + 1, \
            "%s: %r lies outside the canvas %r" % (where, n, c)


def _intersects(a, b):
    return a["x"] < b["x"] + b["w"] and a["x"] + a["w"] > b["x"] and a["y"] < b["y"] + b["h"] and a["y"] + a["h"] > b["y"]


@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("width,height", VIEWS)
def test_focus_view_is_fitted_themed_and_legible(browser, live_server, hub, width, height, scheme):
    context, page = _open(browser, live_server, hub, width, height, scheme)
    try:
        m = page.evaluate(MEASURE)
        page.screenshot(path="%s/composer-focus-%d-%s.png" % (_walk_dir(), width, scheme))
        where = "%dx%d %s" % (width, height, scheme)
        assert m["zoom"] > 10, "%s: zoom reads %s%%" % (where, m["zoom"])
        assert len(m["nodes"]) >= 13, "%s: %d elements drawn" % (where, len(m["nodes"]))
        c = m["canvas"]
        # The drawing opens either whole or, when whole would shrink the names below reading size, at reading
        # size on the chosen element with the sentence that says how to see everything.
        if not _at_reading_size(page):
            _assert_inside(m["nodes"], c, where)
        else:
            expect(page.locator("#composer-focus-reading-note")).to_contain_text("Choose Fit to see everything drawn")
        # Fit always shows every element.
        page.get_by_test_id("zoom-fit").click()
        page.wait_for_timeout(600)
        _assert_inside(page.evaluate(MEASURE)["nodes"], c, where + " after Fit")
        if scheme == "dark":
            assert m["luminance"] < 0.2, "%s: canvas luminance %.3f" % (where, m["luminance"])
        else:
            assert m["luminance"] > 0.7, "%s: canvas luminance %.3f" % (where, m["luminance"])
        visible = [b for b in m["labels"] if b["w"] > 0 and b["h"] > 0]
        for i, a in enumerate(visible):
            for b in visible[i + 1:]:
                assert not _intersects(a, b), "%s: labels overlap: %r and %r" % (where, a, b)
        assert m["shadow"] == "none", "%s: card shadow %s" % (where, m["shadow"])
    finally:
        context.close()


def _walk_dir():
    import os
    path = os.environ.get("COMPOSER_WALK_DIR")
    if path:
        os.makedirs(path, exist_ok=True)
        return path
    import tempfile
    return tempfile.gettempdir()


def test_connections_list_has_fourteen_rows_each_opening_details(browser, live_server, hub):
    context, page = _open(browser, live_server, hub, 1440, 900, "light")
    try:
        page.locator("#composer-focus-list-toggle").click()
        rows = page.locator("[data-focus-row]")
        expect(rows.first).to_be_visible()
        assert rows.count() == 14, rows.count()
        rows.nth(0).get_by_role("button").first.click()
        panel = page.locator(".detail-panel")
        expect(panel).to_be_visible(timeout=PAGE_TIMEOUT)
        assert panel.locator('input[aria-label="Element name"]').input_value()
    finally:
        context.close()


NAME_PX = """() => {
  const sel = '#composer-canvas .joint-element [joint-selector=nameLabel]';
  return Array.from(document.querySelectorAll(sel)).map(t => {
    const m = t.getScreenCTM();
    return {text: t.textContent.trim(), px: parseFloat(getComputedStyle(t).fontSize) * m.a};
  });
}"""

CARD_AND_BOXES = """() => {
  const card = document.querySelector('#composer-focus-banner').getBoundingClientRect();
  const canvas = document.querySelector('#composer-canvas').getBoundingClientRect();
  const hits = Array.from(document.querySelectorAll('#composer-canvas .joint-element')).map(n => {
    const full = n.getBoundingClientRect();
    // Only the part of a box inside the drawing is on screen; the rest is cut off by the drawing's edge.
    const r = {x: Math.max(full.x, canvas.x), y: Math.max(full.y, canvas.y), right: Math.min(full.right, canvas.right), bottom: Math.min(full.bottom, canvas.bottom)};
    return {text: n.textContent.trim().slice(0, 30), hit: r.right > r.x && r.bottom > r.y && r.x < card.right && r.right > card.x && r.y < card.bottom - 1 && r.bottom > card.y};
  }).filter(n => n.hit);
  return {cardBottom: card.bottom, canvasTop: canvas.top, hits: hits};
}"""


@pytest.mark.parametrize("scheme,width,height", [("light", 1440, 900), ("dark", 390, 844)])
@pytest.mark.parametrize("which", ["centre", "ledger"])
def test_focus_view_opens_with_names_a_person_can_read_and_no_card_over_a_box(
        browser, live_server, hub, which, scheme, width, height):
    context, page = _open(browser, live_server, hub, width, height, scheme, centre=hub[which])
    try:
        where = "%s %dx%d %s" % (which, width, height, scheme)
        page.screenshot(path="%s/composer-readable-%s-%d-%s.png" % (_walk_dir(), which, width, scheme))
        names = page.evaluate(NAME_PX)
        assert len(names) >= 13, "%s: %d names drawn" % (where, len(names))
        assert min(n["px"] for n in names) >= 10.99, \
            "%s: names as small as %.1f px tall" % (where, min(n["px"] for n in names))
        if _at_reading_size(page):
            # Opened at reading size on the chosen element, and the page says how to see everything.
            measured = page.evaluate(MEASURE)
            assert [n for n in measured["nodes"] if _intersects(n, measured["canvas"])], \
                "%s: nothing of the drawing is on screen" % where
            expect(page.locator("#composer-focus-reading-note")).to_contain_text("Choose Fit to see everything drawn")
        card = page.evaluate(CARD_AND_BOXES)
        assert not card["hits"], "%s: the unsaved-view card covers %r" % (where, card["hits"])
        assert card["cardBottom"] <= card["canvasTop"] + 1, \
            "%s: the card ends at %.0f but the drawing starts at %.0f" % (where, card["cardBottom"], card["canvasTop"])
        # Fit still shows every element.
        page.get_by_test_id("zoom-fit").click()
        page.wait_for_timeout(600)
        canvas = page.evaluate(MEASURE)
        _assert_inside(canvas["nodes"], canvas["canvas"], where + " after Fit")
    finally:
        context.close()


FOCUS_STATE = """() => {
  const a = Alpine.$data(document.querySelector('[x-data^="composerApp"]'));
  const cell = a._selectedCells && a._selectedCells[0];
  const ae = document.activeElement;
  return {selected: cell ? cell.get('elName') : null,
          inCanvas: !!(ae && document.querySelector('#composer-canvas').contains(ae)),
          active: ae ? (ae.getAttribute('aria-label') || ae.getAttribute('data-testid') || ae.id || ae.tagName) : null,
          isBody: ae === document.body};
}"""


@pytest.mark.parametrize("scheme,width,height", [("light", 1440, 900), ("dark", 390, 844)])
def test_keyboard_leaves_the_drawing_and_arrow_keys_walk_it(browser, live_server, hub, scheme, width, height):
    context, page = _open(browser, live_server, hub, width, height, scheme)
    try:
        where = "%dx%d %s" % (width, height, scheme)
        # The drawing says which keys walk it.
        described = page.evaluate(
            """() => { const c = document.querySelector('#composer-canvas');
               return document.getElementById(c.getAttribute('aria-describedby')).textContent; }""")
        assert "Arrow keys" in described and "Tab" in described, described
        # From the toolbar, Tab goes on along the page and leaves the chosen element alone.
        before = page.evaluate(FOCUS_STATE)["selected"]
        assert before and before.startswith(hub["name"].split(" ")[0]), before
        fit = page.get_by_test_id("zoom-fit")
        fit.focus()
        seen = [page.evaluate(FOCUS_STATE)]
        for _ in range(3):
            page.keyboard.press("Tab")
            seen.append(page.evaluate(FOCUS_STATE))
        assert len({s["active"] for s in seen}) == 4, "%s: Tab did not move on: %r" % (where, [s["active"] for s in seen])
        assert all(s["selected"] == before for s in seen), "%s: Tab walked the drawing: %r" % (where, seen)
        # Focus is in the drawing: Tab and Shift+Tab leave it for the page.
        page.locator("#composer-canvas").focus()
        assert page.evaluate(FOCUS_STATE)["inCanvas"]
        page.keyboard.press("Tab")
        after = page.evaluate(FOCUS_STATE)
        assert not after["inCanvas"], "%s: Tab stayed in the drawing: %r" % (where, after)
        assert after["selected"] == before
        page.locator("#composer-canvas").focus()
        page.keyboard.press("Shift+Tab")
        back = page.evaluate(FOCUS_STATE)
        assert not back["inCanvas"] and not back["isBody"], "%s: Shift+Tab stayed in the drawing: %r" % (where, back)
        assert back["selected"] == before
        # In the drawing, the arrow keys walk to each connected element in turn, and Enter opens one.
        page.locator("#composer-canvas").focus()
        walked = []
        for _ in range(10):
            page.keyboard.press("ArrowRight")
            walked.append(page.evaluate(FOCUS_STATE)["selected"])
        assert len(set(walked)) == 10, "%s: the arrows reached %r" % (where, walked)
        assert before not in walked
        page.keyboard.press("ArrowLeft")
        assert page.evaluate(FOCUS_STATE)["selected"] == walked[-2]
        page.keyboard.press("Enter")
        panel = page.locator(".detail-panel")
        expect(panel).to_be_visible(timeout=PAGE_TIMEOUT)
        assert panel.locator('input[aria-label="Element name"]').input_value() == walked[-2]
    finally:
        context.close()

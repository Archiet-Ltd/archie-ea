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
        out = {"email": fresh["emails"]["enterprise_architect"], "centre": centre.id,
               "name": centre.name}
        db.session.remove()
    return out


def _open(browser, live_server, hub, width, height, scheme):
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    page = context.new_page()
    sign_in(page, live_server, hub["email"])
    page.goto("%s/archimate/composer?element=%d" % (live_server, hub["centre"]),
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
        for n in m["nodes"]:
            assert n["x"] >= c["x"] - 1 and n["y"] >= c["y"] - 1 and \
                n["x"] + n["w"] <= c["x"] + c["w"] + 1 and n["y"] + n["h"] <= c["y"] + c["h"] + 1, \
                "%s: %r lies outside the canvas %r" % (where, n, c)
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

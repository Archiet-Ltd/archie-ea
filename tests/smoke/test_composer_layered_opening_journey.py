"""The Composer opens the layered viewpoint on a view a person can read, and its quality figure agrees with its warnings.

The person: an enterprise architect whose organisation holds far more elements than one
screen can draw legibly, who opens the layered viewpoint from the sidebar.
The goal: see what the model holds at a glance with every label readable, open one layer
to see its most connected elements, trust that the quality figure and the warnings beside
it describe the same drawing, and save the drawing and find it again after a reload.
"""
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

APP = '[x-data^="composerApp"]'
DRAW_SECONDS = 30000
MIN_LABEL_PX = 11
OPENING_LIMIT = 40

# (layer, element type, how many): the application layer is larger than one readable view.
LAYERS = [
    ("application", "ApplicationComponent", 45),
    ("business", "BusinessProcess", 15),
    ("technology", "Node", 12),
    ("motivation", "Goal", 8),
]
TITLES = {"strategy": "Strategy", "application": "Application", "business": "Business", "technology": "Technology", "motivation": "Motivation"}
HUB = "Core Ledger"

DATA = """() => {
  const a = Alpine.$data(document.querySelector('[x-data^="composerApp"]'));
  const cards = a.graph.getElements().filter(c => c.get('isLayerSummary')).map(c => ({
    layer: c.get('summaryLayer'), count: c.get('summaryCount'),
    name: c.attr('nameLabel/text'), caption: c.attr('typeLabel/text'),
    px: Math.min(c.attr('nameLabel/fontSize'), c.attr('typeLabel/fontSize')) * a.paper.scale().sx}));
  const els = a.graph.getElements().filter(c => !c.get('isLayerSummary') && !c.get('isLayerZone') && !c.get('isAnnotation'));
  return {
    cards, drawn: els.length, names: els.map(c => c.get('elName')),
    score: a.complianceScore, penalties: a.complianceDeductions.map(d => d.penalty),
    listed: a.complianceDeductions.length, warnings: a.validationWarnings, errors: a.validationErrors,
    zoom: a.zoomPercent, status: a.statusText, elementCount: a.elementCount, savedId: a.currentSavedVpId,
  };
}"""


def _seed(org_id, suffix, per_layer=LAYERS, connect=True):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    app = create_app("testing")
    with app.app_context():
        by_layer = {}
        for layer, kind, n in per_layer:
            rows = [ArchiMateElement(name="%s %s %02d %s" % (TITLES[layer], kind, i, suffix), type=kind, layer=layer,
                                     organization_id=org_id) for i in range(n)]
            db.session.add_all(rows)
            by_layer[layer] = rows
        db.session.commit()
        apps = by_layer.get("application")
        if apps and connect:
            apps[-1].name = "%s %s" % (HUB, suffix)  # the last one inserted would sort last without connections
            db.session.commit()
            hub = apps[-1]
            others = [e for rows in by_layer.values() for e in rows if e.id != hub.id][:12]
            for other in others:
                db.session.add(ArchiMateRelationship(type="Serving", source_id=hub.id, target_id=other.id,
                                                     organization_id=org_id))
            db.session.commit()
        db.session.remove()


@pytest.fixture(scope="module")
def estate():
    suffix = uuid.uuid4().hex[:6]
    own = create_fresh_org("enterprise_architect")
    _seed(own["org_id"], suffix)
    # A second organisation with a different, larger estate: none of it may show in the first's counts.
    foreign = create_fresh_org("enterprise_architect")
    _seed(foreign["org_id"], suffix + "x", [("motivation", "Goal", 70), ("strategy", "Capability", 5)])
    small = create_fresh_org("enterprise_architect")
    _seed(small["org_id"], suffix + "s", [("application", "ApplicationComponent", 3)], connect=False)
    empty = create_fresh_org("enterprise_architect")
    return {"suffix": suffix, "own": own["emails"]["enterprise_architect"],
            "foreign": foreign["emails"]["enterprise_architect"], "small": small["emails"]["enterprise_architect"],
            "empty": empty["emails"]["enterprise_architect"]}


def _walk_dir():
    path = os.environ.get("COMPOSER_WALK_DIR")
    if path:
        os.makedirs(path, exist_ok=True)
        return path
    import tempfile
    return tempfile.gettempdir()


def _open(browser, live_server, email, width, height, scheme, url="/archimate/composer?viewpoint=layered"):
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    page = context.new_page()
    sign_in(page, live_server, email)
    page.goto(live_server + url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_function(
        """(sel) => { if (!window.Alpine || !document.querySelector(sel)) return false;
            const a = Alpine.$data(document.querySelector(sel));
            return !a.viewpointLoading && (a.graph.getElements().length > 0 || /No elements/.test(a.statusText)); }""",
        arg=APP, timeout=DRAW_SECONDS)
    page.wait_for_timeout(2000)
    return context, page


def _wait_scored(page):
    """The quality figure is worked out shortly after a drawing settles; wait for it, not for a fixed time."""
    page.wait_for_function(
        """() => Alpine.$data(document.querySelector('[x-data^="composerApp"]')).complianceDeductions.length > 0""",
        timeout=DRAW_SECONDS)
    page.wait_for_timeout(1500)


def _assert_quality_agrees(d, where):
    """The figure is what the listed issues leave, and never perfect beside any of them."""
    assert d["listed"] == d["warnings"] + d["errors"], "%s: %r" % (where, d)
    if d["listed"] > 0:
        assert d["score"] < 100, "%s: quality %s%% beside %d listed issues" % (where, d["score"], d["listed"])
    assert d["score"] == min(max(0, 100 - sum(d["penalties"])), 99 if d["listed"] else 100), "%s: %r" % (where, d)


VIEWS = [(1440, 900, "light"), (390, 844, "dark")]


@pytest.mark.parametrize("width,height,scheme", VIEWS)
def test_layered_opening_is_readable_and_counts_this_organisation_only(browser, live_server, estate, width, height, scheme):
    context, page = _open(browser, live_server, estate["own"], width, height, scheme)
    try:
        d = page.evaluate(DATA)
        page.screenshot(path="%s/layered-opening-%d-%s.png" % (_walk_dir(), width, scheme))
        where = "%dx%d %s" % (width, height, scheme)
        assert d["drawn"] == 0, "%s: %d elements drawn on the opening" % (where, d["drawn"])
        assert {c["layer"]: c["count"] for c in d["cards"]} == {k: n for k, _, n in LAYERS}, "%s: %r" % (where, d["cards"])
        for card in d["cards"]:
            assert card["name"] == TITLES[card["layer"]]
            assert card["caption"] == "%d elements" % card["count"]
            assert card["px"] >= MIN_LABEL_PX, "%s: %s label reads %.1f px at %s%%" % (where, card["name"], card["px"], d["zoom"])
        # Nothing from the other organisation, and no quality figure for cards that are not elements.
        body = page.inner_text("body")
        assert "Capability" not in " ".join(c["name"] for c in d["cards"])
        assert not re.search(r"Quality \d+%", body), where
        assert "Open a layer" in d["status"], d["status"]
    finally:
        context.close()


@pytest.mark.parametrize("width,height,scheme", VIEWS)
def test_opening_a_layer_shows_most_connected_and_quality_agrees_with_warnings(browser, live_server, estate, width, height, scheme):
    context, page = _open(browser, live_server, estate["own"], width, height, scheme)
    try:
        where = "%dx%d %s" % (width, height, scheme)
        card = page.evaluate("""() => { const a = Alpine.$data(document.querySelector('[x-data^="composerApp"]'));
            const c = a.graph.getElements().find(e => e.get('summaryLayer') === 'application');
            const r = a.paper.findViewByModel(c).el.getBoundingClientRect();
            return {x: r.x + r.width / 2, y: r.y + r.height / 2}; }""")
        page.mouse.click(card["x"], card["y"])
        page.wait_for_function(
            """() => { const a = Alpine.$data(document.querySelector('[x-data^="composerApp"]'));
                return a.openedLayer === 'application' && a.elementCount > 1 && !a.viewpointLoading; }""",
            timeout=DRAW_SECONDS)
        _wait_scored(page)
        d = page.evaluate(DATA)
        page.screenshot(path="%s/layered-application-%d-%s.png" % (_walk_dir(), width, scheme))
        assert d["drawn"] == OPENING_LIMIT, "%s: %d drawn" % (where, d["drawn"])
        assert any(n.startswith(HUB) for n in d["names"]), "%s: the most connected element is not drawn" % where
        assert "40 most connected of 45" in d["status"], d["status"]
        assert d["listed"] > 0 and d["warnings"] > 0, "%s: %r" % (where, d)
        _assert_quality_agrees(d, where)
        if width >= 768:
            bar = page.locator('[data-testid="composer-view-actions"]')
            expect(bar).to_contain_text("Quality %d%%" % d["score"])
            expect(bar).to_contain_text("%d warning" % d["warnings"])
            page.get_by_role("button", name="Diagram quality score, open details").click()
            panel = page.locator(".compliance-panel")
            expect(panel).to_contain_text("%d%%" % d["score"])
            expect(panel).to_contain_text("Deductions (%d)" % d["listed"])
            assert panel.locator("div.overflow-y-auto > div").count() == d["listed"], "the panel lists a different number than it counts"
            page.screenshot(path="%s/layered-quality-%d-%s.png" % (_walk_dir(), width, scheme))
        # "All layers" returns to the opening.
        page.locator('[data-testid="composer-all-layers"]').click()
        page.wait_for_function(
            """() => Alpine.$data(document.querySelector('[x-data^="composerApp"]')).graph.getElements()
                .some(c => c.get('isLayerSummary'))""", timeout=DRAW_SECONDS)
    finally:
        context.close()


@pytest.mark.parametrize("width,height,scheme", VIEWS)
def test_a_layer_drawing_saves_and_reloads(browser, live_server, estate, width, height, scheme):
    context, page = _open(browser, live_server, estate["own"], width, height, scheme,
                          url="/archimate/composer?viewpoint=layered&layer=application")
    try:
        _wait_scored(page)
        before = page.evaluate(DATA)
        assert before["drawn"] == OPENING_LIMIT, before["drawn"]
        page.keyboard.press("Control+s")
        modal = page.locator('[aria-label="Viewpoint name"]')
        expect(modal).to_be_visible(timeout=PAGE_TIMEOUT)
        name = "Application layer %s %dx%d" % (estate["suffix"], width, height)
        modal.fill(name)
        page.get_by_role("button", name=re.compile("^Save$")).click()
        expect(modal).to_be_hidden(timeout=PAGE_TIMEOUT)
        page.wait_for_function(
            """() => !!Alpine.$data(document.querySelector('[x-data^="composerApp"]')).currentSavedVpId""",
            timeout=PAGE_TIMEOUT)
        saved_id = page.evaluate(DATA)["savedId"]
        page.goto("%s/archimate/composer?viewpoint_id=%s" % (live_server, saved_id),
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_function(
            """() => { const a = Alpine.$data(document.querySelector('[x-data^="composerApp"]'));
                return a.graph.getElements().length >= 40 && !a.viewpointLoading; }""", timeout=DRAW_SECONDS)
        _wait_scored(page)
        after = page.evaluate(DATA)
        page.screenshot(path="%s/layered-reloaded-%d-%s.png" % (_walk_dir(), width, scheme))
        assert sorted(after["names"]) == sorted(before["names"]), "the reloaded diagram differs from the saved one"
        assert after["listed"] == before["listed"] and after["warnings"] == before["warnings"], (before, after)
        _assert_quality_agrees(after, "reloaded")
    finally:
        context.close()


def test_small_model_opens_drawn_and_never_says_perfect_beside_a_warning(browser, live_server, estate):
    context, page = _open(browser, live_server, estate["small"], 1440, 900, "light")
    try:
        _wait_scored(page)
        d = page.evaluate(DATA)
        assert d["cards"] == [] and d["drawn"] == 3, d
        # Three unconnected elements: three orphan warnings, so the figure cannot read 100%.
        assert d["warnings"] == 3, d
        _assert_quality_agrees(d, "small model")
        bar = page.locator('[data-testid="composer-view-actions"]')
        expect(bar).to_contain_text("Quality %d%%" % d["score"])
        expect(bar).to_contain_text("3 warnings")
        assert d["score"] < 100
    finally:
        context.close()


def test_an_organisation_with_no_elements_says_so(browser, live_server, estate):
    context, page = _open(browser, live_server, estate["empty"], 1440, 900, "light")
    try:
        d = page.evaluate(DATA)
        assert d["cards"] == [] and d["drawn"] == 0
        assert "No elements for this viewpoint" in d["status"], d["status"]
    finally:
        context.close()

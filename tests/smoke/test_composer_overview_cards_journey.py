"""Every card on the Composer's opening overview opens a view, by click and by keyboard, and the figures on it agree.

The person: an enterprise architect whose organisation holds more elements than one screen can draw, including
measures, evidence, locations, groupings and junctions that belong to none of the six layers.
The goal: open each card, see its elements, and read one true count for what this layer leaves out and one for
what the other layers hold.
"""
import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org
from tests.smoke.test_composer_layered_opening_journey import (
    APP, DRAW_SECONDS, VIEWS, _open, _seed, _walk_dir,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# Application 45 and motivation 30 (25 goals, 3 metrics, 2 evidence records), then 5 elements no layer holds.
MAIN = [("application", "ApplicationComponent", 45), ("motivation", "Goal", 25)]
EXTRA = [("Metric", 3), ("EvidenceRecord", 2), ("Location", 1), ("Grouping", 1), ("Junction", 1),
         ("OrJunction", 1), ("SomethingNew", 1)]
CARD_TITLES = {"application": "Application", "motivation": "Motivation", "composite": "Composite and other elements"}

STATE = """() => { const a = Alpine.$data(document.querySelector('[x-data^="composerApp"]'));
  const els = a.graph.getElements().filter(c => !c.get('isLayerSummary') && !c.get('isLayerZone') && !c.get('isAnnotation'));
  return { opened: a.openedLayer, drawn: els.length, status: a.statusText, loading: a.viewpointLoading,
           cards: a.layerCards.map(c => c.layer), summaries: a.graph.getElements().filter(c => c.get('isLayerSummary')).length }; }"""


def _seed_extra(org_id, suffix, kinds=EXTRA):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement

    app = create_app("testing")
    with app.app_context():
        for kind, n in kinds:
            db.session.add_all([ArchiMateElement(name="%s %02d %s" % (kind, i, suffix), type=kind, organization_id=org_id)
                                for i in range(n)])
        db.session.commit()
        db.session.remove()


@pytest.fixture(scope="module")
def estate():
    suffix = uuid.uuid4().hex[:6]
    own = create_fresh_org("enterprise_architect")
    _seed(own["org_id"], suffix, MAIN, connect=False)
    _seed_extra(own["org_id"], suffix)
    foreign = create_fresh_org("enterprise_architect")
    _seed(foreign["org_id"], suffix + "x", [("motivation", "Goal", 90)], connect=False)
    _seed_extra(foreign["org_id"], suffix + "x", [("Location", 4)])
    empty = create_fresh_org("enterprise_architect")
    return {"own": own["emails"]["enterprise_architect"], "foreign": foreign["emails"]["enterprise_architect"],
            "empty": empty["emails"]["enterprise_architect"]}


def _wait_state(page, layer):
    page.wait_for_function(
        """(layer) => { const a = Alpine.$data(document.querySelector('[x-data^="composerApp"]'));
            return a.openedLayer === layer && a.graph.getElements().length > 0 && !a.viewpointLoading; }""",
        arg=layer, timeout=DRAW_SECONDS)
    page.wait_for_timeout(1500)


def _back_to_overview(page):
    page.locator('[data-testid="composer-all-layers"]').click()
    page.wait_for_function(
        """() => Alpine.$data(document.querySelector('[x-data^="composerApp"]')).graph.getElements()
            .some(c => c.get('isLayerSummary'))""", timeout=DRAW_SECONDS)
    page.wait_for_timeout(800)


@pytest.mark.parametrize("width,height,scheme", VIEWS)
def test_every_overview_card_opens_a_view_by_keyboard(browser, live_server, estate, width, height, scheme):
    context, page = _open(browser, live_server, estate["own"], width, height, scheme)
    where = "%dx%d %s" % (width, height, scheme)
    try:
        d = page.evaluate(STATE)
        assert sorted(d["cards"]) == ["application", "composite", "motivation"], "%s: %r" % (where, d)
        assert d["summaries"] == 3 and d["drawn"] == 0
        # The footer and the hint describe the overview, not an empty drawing.
        footer = page.locator('[data-testid="status-counts"]')
        expect(footer).to_have_text("80 elements in 3 layers")
        expect(page.locator('[data-testid="legend-overview"]')).to_have_count(1)
        assert "No relationships yet" not in page.inner_text("body"), where
        page.screenshot(path="%s/overview-cards-%d-%s.png" % (_walk_dir(), width, scheme))
        # Each card has a named button a keyboard reaches.
        nav = page.get_by_role("navigation", name="Open a layer")
        for layer, title in CARD_TITLES.items():
            expect(nav.get_by_role("button", name=re.compile("^Open %s, \\d+ elements?$" % re.escape(title)))).to_have_count(1)
        # The composite card requests a layer the route accepts and opens elements.
        for layer, title in CARD_TITLES.items():
            button = nav.get_by_role("button", name=re.compile("^Open %s," % re.escape(title)))
            button.focus()
            page.keyboard.press("Enter")
            _wait_state(page, layer)
            d = page.evaluate(STATE)
            where_layer = "%s %s" % (where, layer)
            assert d["drawn"] > 0, "%s: %r" % (where_layer, d)
            assert "Failed" not in d["status"] and "Error" not in d["status"], where_layer
            expect(page.locator("body")).not_to_contain_text("Failed to switch viewpoint")
            page.screenshot(path="%s/overview-opened-%s-%d-%s.png" % (_walk_dir(), layer, width, scheme))
            if layer == "composite":
                assert d["drawn"] == 5, d  # Location, Grouping, Junction, OrJunction and the unmapped type
            if layer == "motivation":
                assert d["drawn"] == 30, d  # 25 goals, 3 measures and 2 evidence records
            _back_to_overview(page)
    finally:
        context.close()


@pytest.mark.parametrize("width,height,scheme", VIEWS)
def test_a_card_click_opens_composite_and_the_two_counts_are_each_true(browser, live_server, estate, width, height, scheme):
    context, page = _open(browser, live_server, estate["own"], width, height, scheme)
    where = "%dx%d %s" % (width, height, scheme)
    try:
        card = page.evaluate("""() => { const a = Alpine.$data(document.querySelector('[x-data^="composerApp"]'));
            const c = a.graph.getElements().find(e => e.get('summaryLayer') === 'composite');
            const r = a.paper.findViewByModel(c).el.getBoundingClientRect();
            return {x: r.x + r.width / 2, y: r.y + r.height / 2}; }""")
        page.mouse.click(card["x"], card["y"])
        _wait_state(page, "composite")
        assert page.evaluate(STATE)["drawn"] == 5, where
        _back_to_overview(page)
        page.locator('[data-testid="composer-layer-application"]').click()
        _wait_state(page, "application")
        status = page.evaluate(STATE)["status"]
        page.screenshot(path="%s/overview-application-%d-%s.png" % (_walk_dir(), width, scheme))
        assert "the 40 most connected of 45 application elements are shown" in status, status
        assert "35 elements in other layers are not shown of 80 in your model" in status, status
        assert not re.search(r"\b40 elements in other layers", status), status
    finally:
        context.close()


def test_saving_the_overview_says_there_is_nothing_to_save(browser, live_server, estate):
    context, page = _open(browser, live_server, estate["own"], 1440, 900, "light")
    try:
        page.keyboard.press("Control+s")
        expect(page.get_by_text("Open a layer first")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.locator('[aria-label="Viewpoint name"]')).to_be_hidden()
    finally:
        context.close()


def test_another_organisations_estate_shows_its_own_cards(browser, live_server, estate):
    context, page = _open(browser, live_server, estate["foreign"], 1440, 900, "light")
    try:
        assert sorted(page.evaluate(STATE)["cards"]) == ["composite", "motivation"]
        expect(page.locator('[data-testid="status-counts"]')).to_have_text("94 elements in 2 layers")
    finally:
        context.close()

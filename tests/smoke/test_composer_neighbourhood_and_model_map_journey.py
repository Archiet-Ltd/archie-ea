"""An architect opens an application in the Composer and reads it; the model map can find an element.

The person: an enterprise architect whose application connects to about seventy elements.
The goals:
  * from the application's page, "Open in the Composer" opens a view she can read, with the
    application in the middle and marked, its figures labelled, and nothing saved until she saves;
  * on the model map, find an element by name and see it centred and marked, zoom with buttons,
    scroll the page past the map, and read what the ring of unconnected elements is.
"""
import os
import pathlib
import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

APP = '[x-data^="composerApp"]'
DRAW_MS = 60000
DIRECT = 60
BEYOND = 10
UNCONNECTED = 14
IDLE_SECONDS = 90

KINDS = [
    ("ApplicationComponent", "application"),
    ("BusinessActor", "business"),
    ("Node", "technology"),
    ("DataObject", "application"),
    ("BusinessProcess", "business"),
]


def _shot(page, name, scheme):
    """Save a screenshot when SMOKE_SCREENSHOT_DIR names a folder; the journey asserts either way."""
    folder = os.environ.get("SMOKE_SCREENSHOT_DIR")
    if not folder:
        return
    pathlib.Path(folder).mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(pathlib.Path(folder) / ("%s-%d-%s.png" % (name, page.viewport_size["width"], scheme))))


def _elements(db, E, org_id, names_kinds):
    rows = []
    for name, (kind, layer) in names_kinds:
        row = E(name=name, type=kind, layer=layer, organization_id=org_id)
        db.session.add(row)
        rows.append(row)
    db.session.commit()
    return rows


@pytest.fixture(scope="module")
def world():
    """One organisation with an application of 60 direct and 10 further neighbours and 14 unconnected
    elements, and a second organisation with elements of its own."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement as E, ArchiMateRelationship as R

    mine = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    suffix = uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        org = mine["org_id"]
        [seed] = _elements(db, E, org, [("Orion Platform %s" % suffix, KINDS[0])])
        direct = _elements(db, E, org, [("Neighbour %02d %s" % (i, suffix), KINDS[i % len(KINDS)]) for i in range(DIRECT)])
        beyond = _elements(db, E, org, [("Beyond %02d %s" % (i, suffix), KINDS[(i + 1) % len(KINDS)]) for i in range(BEYOND)])
        lonely = _elements(db, E, org, [("Lonely %02d %s" % (i, suffix), KINDS[i % len(KINDS)]) for i in range(UNCONNECTED)])
        for target in direct:
            db.session.add(R(type="Serving", source_id=seed.id, target_id=target.id, organization_id=org))
        for i, target in enumerate(beyond):
            db.session.add(R(type="Flow", source_id=direct[i].id, target_id=target.id, organization_id=org))
        component = ApplicationComponent(name="Orion app %s" % suffix, organization_id=org, archimate_element_id=seed.id)
        db.session.add(component)
        db.session.commit()
        # Another organisation: elements of its own, one of them named like a search a person here might type.
        foreign = _elements(db, E, other["org_id"],
                            [("Foreign Orphan %02d %s" % (i, suffix), KINDS[0]) for i in range(5)])
        out = {
            "email": mine["emails"]["enterprise_architect"],
            "org": org,
            "user": mine["user_ids"]["enterprise_architect"],
            "seed": seed.id, "seed_name": seed.name, "application": component.id,
            "neighbour_ids": [e.id for e in direct + beyond] + [seed.id],
            "direct_name": direct[7].name, "lonely_name": lonely[3].name,
            "suffix": suffix, "foreign_name": foreign[0].name,
        }
        db.session.remove()
    return out


def _count(sql, **params):
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        value = db.session.execute(db.text(sql), params).scalar()
        db.session.remove()
        return value


def _saved_diagrams(world):
    return _count("SELECT count(*) FROM saved_diagrams WHERE organization_id = :o OR created_by_id = :u",
                  o=world["org"], u=world["user"])


def _open_from_application(page, live_server, world):
    sign_in(page, live_server, world["email"])
    page.goto("%s/applications/%d" % (live_server, world["application"]),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    link = page.get_by_role("link", name="Open in the Composer")
    expect(link).to_be_visible(timeout=PAGE_TIMEOUT)
    link.click()
    page.wait_for_url(re.compile(r"/archimate/composer\?element=%d$" % world["seed"]), timeout=PAGE_TIMEOUT)
    page.wait_for_function(
        """(sel) => { if (!window.Alpine || !document.querySelector(sel)) return false;
            const a = Alpine.$data(document.querySelector(sel));
            return !!a.focusView && a.graph.getElements().length > 1 && !a._focusRendering; }""",
        arg=APP, timeout=DRAW_MS)
    page.wait_for_timeout(3500)


READING = """() => {
  const a = Alpine.$data(document.querySelector('[x-data^="composerApp"]'));
  const canvas = document.querySelector('#composer-canvas').getBoundingClientRect();
  const seed = a.graph.getElements().filter(c => c.get('focusRing') === 0)[0];
  const view = seed && a.paper.findViewByModel(seed);
  const box = view ? view.el.getBoundingClientRect() : null;
  const scale = a.paper.scale().sx;
  const label = view && view.el.querySelector('[joint-selector="nameLabel"]');
  const drawn = Array.from(document.querySelectorAll('#composer-canvas .joint-element')).map(n => {
    const r = n.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}; });
  return {
    canvas: {x: canvas.x, y: canvas.y, w: canvas.width, h: canvas.height},
    seedMarked: !!view && view.el.getAttribute('data-focus-seed') === 'true',
    seedBox: box && {x: box.x, y: box.y, w: box.width, h: box.height},
    scale: scale,
    labelPx: label ? parseFloat(getComputedStyle(label).fontSize) * scale : 0,
    drawn: drawn,
    header: document.querySelector('[data-testid="diagram-purpose"]').textContent.trim(),
    nodeCount: a.graph.getElements().length,
  };
}"""


@pytest.mark.parametrize("width,height,scheme", [(1440, 900, "light"), (390, 844, "dark")])
def test_application_opens_a_readable_view_centred_on_it(browser, live_server, world, width, height, scheme):
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    page = context.new_page()
    try:
        _open_from_application(page, live_server, world)
        m = page.evaluate(READING)
        where = "%dx%d %s" % (width, height, scheme)
        assert m["seedMarked"], "%s: the application is not marked" % where
        c, b = m["canvas"], m["seedBox"]
        dx = (b["x"] + b["w"] / 2) - (c["x"] + c["w"] / 2)
        dy = (b["y"] + b["h"] / 2) - (c["y"] + c["h"] / 2)
        assert abs(dx) <= 0.1 * c["w"] and abs(dy) <= 0.25 * c["h"], "%s: seed off centre by %.0f, %.0f in %r" % (where, dx, dy, c)
        assert m["labelPx"] >= 11, "%s: the label reads at %.1f px" % (where, m["labelPx"])
        assert m["header"].startswith(world["seed_name"] + " and its neighbours, 3 steps"), m["header"]
        assert "free canvas" not in m["header"]
        assert m["nodeCount"] == 1 + DIRECT, "%s: %d drawn on opening" % (where, m["nodeCount"])
        _shot(page, "composer-from-application", scheme)
    finally:
        context.close()


def test_figures_are_labelled_and_the_next_ring_and_fit_work(browser, live_server, world):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _open_from_application(page, live_server, world)
        counts = page.get_by_test_id("composer-focus-counts")
        expect(counts).to_contain_text("%d of %d elements" % (1 + DIRECT, 1 + DIRECT + BEYOND))
        # Both relationship figures are named: how many are drawn and how many the model holds between these.
        between = _count(
            "SELECT count(*) FROM archimate_relationships WHERE organization_id = :o"
            " AND source_id = ANY(:ids) AND target_id = ANY(:ids)",
            o=world["org"], ids=[world["seed"]] + world["neighbour_ids"][:DIRECT])
        expect(counts).to_contain_text("%d relationships shown" % DIRECT)
        expect(counts).to_contain_text("%d relationships in the model between these elements" % between)
        expect(page.get_by_test_id("status-counts")).to_have_text(
            "%d elements shown, %d relationships shown" % (1 + DIRECT, DIRECT))

        # The next ring adds the elements one step further out.
        page.get_by_role("button", name="Show next ring (%d more)" % BEYOND).click()
        expect(counts).to_contain_text("%d of %d elements" % (1 + DIRECT + BEYOND, 1 + DIRECT + BEYOND), timeout=DRAW_MS)
        expect(page.get_by_test_id("status-counts")).to_have_text(
            "%d elements shown, %d relationships shown" % (1 + DIRECT + BEYOND, DIRECT + BEYOND), timeout=DRAW_MS)
        expect(page.get_by_role("button", name=re.compile("Show next ring"))).to_have_count(0)

        # Fit shows all of it: every element lies inside the canvas.
        page.wait_for_function(
            "() => document.querySelectorAll('#composer-canvas .joint-element').length === %d" % (1 + DIRECT + BEYOND),
            timeout=DRAW_MS)
        page.get_by_test_id("zoom-fit").click()
        page.wait_for_timeout(500)
        m = page.evaluate(READING)
        c = m["canvas"]
        assert len(m["drawn"]) == 1 + DIRECT + BEYOND
        for n in m["drawn"]:
            assert n["x"] >= c["x"] - 1 and n["y"] >= c["y"] - 1 and \
                n["x"] + n["w"] <= c["x"] + c["w"] + 1 and n["y"] + n["h"] <= c["y"] + c["h"] + 1, \
                "fit cut off %r in %r at scale %.3f" % (n, c, m["scale"])
        # The marked element is still the application.
        assert m["seedMarked"]
    finally:
        context.close()


def test_nothing_is_saved_until_the_person_saves(browser, live_server, world):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        before = _saved_diagrams(world)
        _open_from_application(page, live_server, world)
        page.wait_for_timeout(IDLE_SECONDS * 1000)
        assert _saved_diagrams(world) == before, "a view was saved without a Save"
        assert "composer?element=%d" % world["seed"] in page.url

        # Moving an element is an edit of the unsaved view, and an edit is still not a Save: the automatic
        # save runs every thirty seconds, so wait past two of its turns.
        box = page.evaluate(READING)["seedBox"]
        page.mouse.move(box["x"] + box["w"] / 2, box["y"] + box["h"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + box["w"] / 2 + 60, box["y"] + box["h"] / 2 + 40, steps=5)
        page.mouse.up()
        page.wait_for_timeout(65000)
        assert _saved_diagrams(world) == before, "a moved view was saved without a Save"

        page.locator("#composer-focus-save").click()
        page.wait_for_url(re.compile(r"/archimate/composer\?viewpoint_id=\d+$"), timeout=PAGE_TIMEOUT)
        assert _saved_diagrams(world) == before + 1
        row = _count("SELECT created_by FROM saved_diagrams WHERE organization_id = :o ORDER BY id DESC LIMIT 1", o=world["org"])
        assert row == str(world["user"]), row
        owner = _count("SELECT created_by_id FROM saved_diagrams WHERE organization_id = :o ORDER BY id DESC LIMIT 1", o=world["org"])
        assert owner == world["user"], owner
        # The saved view opens with its elements.
        page.wait_for_function(
            """(sel) => { const a = Alpine.$data(document.querySelector(sel)); return a.graph.getElements().length > 1; }""",
            arg=APP, timeout=DRAW_MS)
    finally:
        context.close()


def _map(page, live_server, world, width=1440, height=700):
    page.set_viewport_size({"width": width, "height": height})
    sign_in(page, live_server, world["email"])
    page.goto(live_server + "/archimate/model-map", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.locator("#impact-svg[data-settled]").wait_for(state="attached", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(600)


LOCATED = """() => {
  const svg = document.querySelector('#impact-svg').getBoundingClientRect();
  const g = document.querySelector('#impact-svg g[data-located="true"]');
  if (!g) return null;
  const r = g.querySelector('circle').getBoundingClientRect();
  return {dx: r.x + r.width / 2 - (svg.x + svg.width / 2), dy: r.y + r.height / 2 - (svg.y + svg.height / 2),
          name: g.querySelector('title').textContent, ring: !!g.querySelector('[data-located-ring]'),
          marked: g.querySelector('[data-located-ring]') ? getComputedStyle(g.querySelector('[data-located-ring]')).stroke : ''};
}"""


@pytest.mark.parametrize("width,height,scheme", [(1440, 700, "light"), (390, 844, "dark")])
def test_map_finds_an_element_centres_it_and_zooms(browser, live_server, world, width, height, scheme):
    context = browser.new_context(color_scheme=scheme)
    page = context.new_page()
    try:
        _map(page, live_server, world, width=width, height=height)
        find = page.get_by_role("combobox", name="Find an element by name")
        find.fill(world["direct_name"])
        option = page.get_by_role("option", name=world["direct_name"])
        expect(option).to_be_visible(timeout=PAGE_TIMEOUT)
        option.click()
        page.wait_for_timeout(600)
        located = page.evaluate(LOCATED)
        assert located is not None, "no element was marked on the map"
        assert world["direct_name"] in located["name"]
        assert located["ring"] and abs(located["dx"]) <= 25 and abs(located["dy"]) <= 25, located
        expect(page.get_by_test_id("model-map-selected")).to_contain_text(world["direct_name"])
        _shot(page, "model-map-search", scheme)

        # The plus and minus buttons change the zoom, Fit shows every element again.
        def scale():
            return page.evaluate("() => d3.zoomTransform(document.querySelector('#impact-svg')).k")
        start = scale()
        page.get_by_role("button", name="Zoom in").click()
        page.wait_for_timeout(450)
        zoomed = scale()
        assert zoomed > start * 1.2, (start, zoomed)
        page.get_by_role("button", name="Zoom out").click()
        page.wait_for_timeout(450)
        assert scale() < zoomed * 0.9
        page.get_by_role("button", name="Fit the whole map in view").click()
        page.wait_for_timeout(600)
        inside = page.evaluate("""() => {
          const s = document.querySelector('#impact-svg').getBoundingClientRect();
          return Array.from(document.querySelectorAll('#impact-svg g[data-node-id] circle')).every(c => {
            const r = c.getBoundingClientRect();
            return r.x >= s.x - 1 && r.y >= s.y - 1 && r.right <= s.right + 1 && r.bottom <= s.bottom + 1; }); }""")
        assert inside, "Fit left an element outside the map"

        # Another organisation's element is not found.
        find.fill(world["foreign_name"])
        page.wait_for_timeout(900)
        expect(page.get_by_role("option")).to_have_count(0)
    finally:
        context.close()


def test_page_scrolls_over_the_map_until_the_map_is_clicked(browser, live_server, world):
    context = browser.new_context()
    page = context.new_page()
    try:
        _map(page, live_server, world, height=600)

        def top():
            return page.locator("#impact-svg").bounding_box()["y"]

        def zoom():
            return page.evaluate("() => d3.zoomTransform(document.querySelector('#impact-svg')).k")
        box = page.locator("#impact-svg").bounding_box()
        page.mouse.move(box["x"] + 40, min(box["y"] + 40, 560))
        k0, y0 = zoom(), top()
        page.mouse.wheel(0, 300)
        page.wait_for_timeout(600)
        assert top() < y0 - 50, "the page did not scroll over the map (map top %s -> %s)" % (y0, top())
        assert zoom() == k0, "the wheel zoomed the map before it was clicked"
        # Once the map has been clicked, the wheel zooms it instead.
        box = page.locator("#impact-svg").bounding_box()
        page.mouse.click(box["x"] + 5, max(box["y"] + 5, 5))
        k1, y1 = zoom(), top()
        page.mouse.wheel(0, -300)
        page.wait_for_timeout(600)
        assert zoom() > k1, "the wheel did not zoom the map once it had focus"
        assert abs(top() - y1) < 5, "the page scrolled while the map had focus"
    finally:
        context.close()


@pytest.mark.parametrize("width,height,scheme", [(1440, 900, "light"), (390, 844, "dark")])
def test_ring_of_unconnected_elements_says_what_it_is(browser, live_server, world, width, height, scheme):
    context = browser.new_context(color_scheme=scheme)
    page = context.new_page()
    try:
        _map(page, live_server, world, width=width, height=height)
        label = page.get_by_test_id("model-map-unconnected-label")
        expect(label).to_have_text("Not connected to anything (%d)" % UNCONNECTED)
        # The figure is the model's own: elements of this organisation that no relationship touches.
        alone = _count(
            "SELECT count(*) FROM archimate_elements e WHERE e.organization_id = :o AND NOT EXISTS ("
            " SELECT 1 FROM archimate_relationships r WHERE r.source_id = e.id OR r.target_id = e.id)", o=world["org"])
        assert alone == UNCONNECTED
        flagged = page.evaluate(
            """() => Array.from(document.querySelectorAll('#impact-svg g[data-node-id]')).length""")
        assert flagged == 1 + DIRECT + BEYOND + UNCONNECTED
        expect(page.get_by_test_id("model-map-unconnected")).to_contain_text("Not connected to anything (%d)" % UNCONNECTED)
        # The ring's label is readable: no dot and no dot's name sits on it.
        covered = page.evaluate(
            """() => {
              const l = document.querySelector('[data-testid="model-map-unconnected-label"]').getBoundingClientRect();
              return Array.from(document.querySelectorAll('#impact-svg g[data-node-id]')).map(g => {
                const r = g.getBoundingClientRect();
                return {name: g.querySelector('title').textContent, x: r.x, y: r.y, w: r.width, h: r.height,
                        hit: r.x < l.right && r.right > l.x && r.y < l.bottom && r.bottom > l.y};
              }).filter(n => n.hit);
            }""")
        assert not covered, "the ring's label is covered by %d element(s): %r" % (len(covered), covered[:3])
        # And it is drawn at a size a person can read at the opening zoom, not shrunk with the map.
        drawn = page.evaluate(
            """() => { const t = document.querySelector('[data-testid="model-map-unconnected-label"]');
               return parseFloat(getComputedStyle(t).fontSize) * t.getScreenCTM().a; }""")
        assert drawn >= 11, "the ring's label is drawn %.1f px tall at the opening zoom" % drawn
        _shot(page, "model-map-unconnected", scheme)
    finally:
        context.close()

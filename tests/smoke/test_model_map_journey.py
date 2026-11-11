"""The model map: a startup CTO watches their architecture appear as one cited graph.

The person: a CTO who has connected GitHub and wants to see the model Entelim built.
The goal: open Model map from the sidebar, see every element coloured by layer with the
count of each layer matching the model, hover an element and read where it came from and
when, click it and land on that element's page, and get back to the list with the same
filter. An organisation with no model is told why the map is empty and where to connect.
"""
import datetime
import json
import os
import pathlib
import re

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in
from tests.smoke.intelligence_graph import seed_impact_graph

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

LAYERS = ("strategy", "business", "application", "technology", "physical", "motivation", "implementation", "other")
RENDER_LIMIT_MS = 2000


def _shot(page, name, scheme):
    """Save a screenshot when SMOKE_SCREENSHOT_DIR names a folder; the journey asserts either way."""
    folder = os.environ.get("SMOKE_SCREENSHOT_DIR")
    if not folder:
        return
    page.locator("#impact-svg[data-settled]").wait_for(state="attached", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(400)
    pathlib.Path(folder).mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(pathlib.Path(folder) / ("model-map-%s-%d-%s.png" % (name, page.viewport_size["width"], scheme))))


def _layer_of(raw):
    key = (raw or "").strip().lower()
    return key if key in LAYERS else "other"


def _model_counts(org_id):
    """The organisation's elements per layer and its link count, read straight from the tables."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        rows = db.session.execute(
            db.text("SELECT layer, count(*) FROM archimate_elements WHERE organization_id = :o GROUP BY layer"),
            {"o": org_id}).all()
        links = db.session.execute(
            db.text("SELECT count(*) FROM archimate_relationships r WHERE r.organization_id = :o"
                    " AND r.source_id IN (SELECT id FROM archimate_elements WHERE organization_id = :o)"
                    " AND r.target_id IN (SELECT id FROM archimate_elements WHERE organization_id = :o)"),
            {"o": org_id}).scalar()
        counts = {k: 0 for k in LAYERS}
        for layer, n in rows:
            counts[_layer_of(layer)] += n
        db.session.remove()
    return counts, links


@pytest.fixture(scope="module")
def mapped(seeded):
    """The seeded organisation, plus a connected set of elements one of which was read from GitHub."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement

    org_id = seeded["ids"]["org"]
    graph = seed_impact_graph(org_id, "Mapped")
    app = create_app("testing")
    with app.app_context():
        row = db.session.get(ArchiMateElement, graph["service"])
        row.source_kind = "connector"
        row.source_label = "GitHub"
        row.source_ref = json.dumps({"system": "github", "repository": "acme/payments-service"})
        row.source_recorded_at = datetime.datetime(2026, 11, 2, 9, 30)
        db.session.commit()
        info = {"id": row.id, "name": row.name, "type": row.type, "layer": (row.layer or "").lower()}
        db.session.remove()
    return {"org": org_id, "graph": graph, "sourced": info}


def _wait_drawn(page):
    page.locator("#impact-svg[data-rendered-ms]").wait_for(state="attached", timeout=PAGE_TIMEOUT)


def test_cto_opens_the_model_map_reads_a_source_and_opens_the_element(page, live_server, seeded, mapped):
    page.set_viewport_size({"width": 1440, "height": 900})
    sign_in(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # Open Model map from the sidebar, as a person does.
    entry = page.locator('#sidebar-nav a[data-nav-key="model_map"]')
    expect(entry).to_be_visible(timeout=PAGE_TIMEOUT)
    entry.click()
    page.wait_for_url(re.compile(r"/archimate/model-map$"), timeout=PAGE_TIMEOUT)
    _wait_drawn(page)

    # The count beside each layer is the model's own count, and the nodes drawn are all of its elements.
    counts, links = _model_counts(mapped["org"])
    for layer in LAYERS:
        legend = page.get_by_test_id("model-map-count-" + layer)
        if counts[layer]:
            expect(legend).to_have_text(str(counts[layer]))
        else:
            expect(legend).to_have_count(0)
    svg = page.locator("#impact-svg")
    assert int(svg.get_attribute("data-node-count")) == sum(counts.values())
    assert page.locator("#impact-svg g[data-node-id]").count() == sum(counts.values())
    assert page.locator("#impact-svg line").count() == links
    expect(page.get_by_test_id("model-map-counts")).to_contain_text("%d elements" % sum(counts.values()))
    _shot(page, "overview", "light")

    # Hover an element: its type, source and date are read in the panel.
    gateway = mapped["sourced"]
    node = page.locator('#impact-svg g[data-node-id="%d"]' % gateway["id"])
    node.dispatch_event("mouseover")
    selected = page.get_by_test_id("model-map-selected")
    expect(selected).to_contain_text(gateway["name"])
    expect(selected).to_contain_text("GitHub: acme/payments-service")
    expect(selected).to_contain_text("2 Nov 2026")

    # Click it: the element's own page opens with its name.
    node.dispatch_event("click")
    page.wait_for_url(re.compile(r"/architecture/\w+/\w+/%d\?ae=1$" % gateway["id"]), timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name=gateway["name"]).first).to_be_visible(timeout=PAGE_TIMEOUT)


def test_every_node_opens_a_page_that_names_it(page, live_server, seeded, mapped):
    sign_in(page, live_server, seeded["emails"]["enterprise_architect"])
    names = mapped["graph"]["names"]
    page.goto(live_server + "/archimate/model-map", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _wait_drawn(page)
    for key in ("service", "ops", "portal"):
        row = page.locator("#impact-svg g[data-node-id]", has=page.locator("title", has_text=names[key]))
        row.first.dispatch_event("mouseover")
        link = page.get_by_test_id("model-map-selected").get_by_role("link", name="Open " + names[key])
        expect(link).to_be_visible()
        href = link.get_attribute("href")
        response = page.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 200, (names[key], href, response.status)
        expect(page.get_by_role("heading", name=names[key]).first).to_be_visible(timeout=PAGE_TIMEOUT)
        page.go_back(wait_until="domcontentloaded")
        _wait_drawn(page)


def test_view_as_list_opens_the_element_list_with_the_same_layer_and_figure(page, live_server, seeded, mapped):
    sign_in(page, live_server, seeded["emails"]["enterprise_architect"])
    counts, _ = _model_counts(mapped["org"])
    page.goto(live_server + "/archimate/model-map", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _wait_drawn(page)
    page.get_by_test_id("model-map-layer-technology").click()
    page.wait_for_url(re.compile(r"/archimate/model-map\?layer=technology$"), timeout=PAGE_TIMEOUT)
    _wait_drawn(page)
    assert int(page.locator("#impact-svg").get_attribute("data-node-count")) == counts["technology"]
    page.get_by_test_id("model-map-view-as-list").click()
    page.wait_for_url(re.compile(r"/architecture/elements\?layer=technology$"), timeout=PAGE_TIMEOUT)
    expect(page.get_by_text("technology layer (%d elements)" % counts["technology"], exact=True).first).to_be_visible(timeout=PAGE_TIMEOUT)
    # and the list leads back to the map on the same layer
    page.get_by_test_id("open-model-map").click()
    page.wait_for_url(re.compile(r"/archimate/model-map\?layer=technology$"), timeout=PAGE_TIMEOUT)


def test_keyboard_moves_from_node_to_node_and_announces_the_name(page, live_server, seeded, mapped):
    sign_in(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto(live_server + "/archimate/model-map", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _wait_drawn(page)
    first = page.locator("#impact-svg g[data-node-id]").first
    first.focus()
    label = first.get_attribute("aria-label")
    assert label and "Source:" in label
    expect(page.get_by_test_id("model-map-selected")).to_contain_text(label.split(",")[0])
    page.keyboard.press("ArrowRight")
    second = page.evaluate("() => document.activeElement.getAttribute('aria-label')")
    assert second and second != label


def test_narrow_dark_map_collapses_the_legend_to_a_button(browser, live_server, seeded, mapped):
    context = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
    context.add_init_script("localStorage.setItem('theme', 'dark');")
    page = context.new_page()
    try:
        sign_in(page, live_server, seeded["emails"]["enterprise_architect"])
        page.goto(live_server + "/archimate/model-map", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _wait_drawn(page)
        legend = page.get_by_test_id("model-map-legend")
        expect(legend).to_be_hidden()
        _shot(page, "overview", "dark")
        page.get_by_role("button", name="Show or hide the legend").click()
        expect(legend).to_be_visible()
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
        # the graph is pannable: a drag moves the whole drawing
        before = page.evaluate("() => document.querySelector('#impact-svg > g').getAttribute('transform')")
        page.locator("#impact-svg").scroll_into_view_if_needed()
        # start the drag on empty canvas: a drag that starts on an element moves that element
        start_x, start_y = page.evaluate(
            """() => { const r = document.querySelector('#impact-svg').getBoundingClientRect();
                for (let y = r.top + 80; y < Math.min(r.bottom, innerHeight) - 8; y += 24) {
                    for (let x = r.left + 8; x < r.right - 8; x += 24) {
                        const e = document.elementFromPoint(x, y);
                        if (e && e.id === 'impact-svg') return [x, y];
                    } }
                return null; }""")
        page.mouse.move(start_x, start_y)
        page.mouse.down()
        page.mouse.move(start_x + 70, start_y + 60, steps=5)
        page.mouse.up()
        after = page.evaluate("() => document.querySelector('#impact-svg > g').getAttribute('transform')")
        assert before != after
    finally:
        context.close()


def test_an_organisation_with_no_model_is_told_why_and_where_to_connect(page, live_server):
    fresh = create_fresh_org("enterprise_architect", org_admin=True)
    sign_in(page, live_server, fresh["emails"]["enterprise_architect"])
    page.goto(live_server + "/archimate/model-map", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    empty = page.get_by_test_id("model-map-empty")
    expect(empty).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(empty).to_contain_text("Your model is empty")
    expect(empty).to_contain_text("Connect GitHub or Jira")
    assert page.locator("#impact-svg").count() == 0
    page.get_by_test_id("model-map-connect-github").click()
    page.wait_for_url(re.compile(r"/connectors/github|/github"), timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name="Connect GitHub").first).to_be_visible(timeout=PAGE_TIMEOUT)


def test_another_organisations_elements_never_appear(page, live_server, seeded, mapped):
    other = create_fresh_org("enterprise_architect", org_admin=True)
    other_graph = seed_impact_graph(other["org_id"], "Foreign")
    sign_in(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto(live_server + "/archimate/model-map", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _wait_drawn(page)
    titles = page.eval_on_selector_all("#impact-svg g[data-node-id] title", "els => els.map(e => e.textContent)")
    assert not any(other_graph["noun"] in t for t in titles)
    assert any(mapped["graph"]["noun"] in t for t in titles)


def _bulk_model(org_id, count):
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        layers = ("business", "application", "technology", "motivation", "strategy")
        db.session.execute(
            db.text("INSERT INTO archimate_elements (name, type, layer, organization_id, source_kind, recorded_at)"
                    " SELECT 'Bulk ' || g, 'ApplicationComponent', (:layers)[1 + g % 5], :o, 'import', now()"
                    " FROM generate_series(1, :n) g").bindparams(
                db.bindparam("layers", list(layers), type_=db.ARRAY(db.String))),
            {"o": org_id, "n": count})
        db.session.execute(
            db.text("INSERT INTO archimate_relationships (source_id, target_id, type, organization_id)"
                    " SELECT e.id, e2.id, 'serving', :o FROM archimate_elements e"
                    " JOIN archimate_elements e2 ON e2.id = e.id + 1 AND e2.organization_id = :o"
                    " WHERE e.organization_id = :o"), {"o": org_id})
        db.session.commit()
        db.session.remove()


@pytest.mark.parametrize("count", [500, 2000])
def test_render_time_stays_under_two_seconds(page, live_server, count):
    fresh = create_fresh_org("enterprise_architect", org_admin=True)
    _bulk_model(fresh["org_id"], count)
    sign_in(page, live_server, fresh["emails"]["enterprise_architect"])
    page.goto(live_server + "/archimate/model-map", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _wait_drawn(page)
    svg = page.locator("#impact-svg")
    assert int(svg.get_attribute("data-node-count")) == count
    ms = int(svg.get_attribute("data-rendered-ms"))
    print("model map render time at %d elements: %d ms" % (count, ms))
    assert ms < RENDER_LIMIT_MS, "%d elements took %d ms" % (count, ms)
    # interactive at once: a node answers a hover with its source
    page.locator("#impact-svg g[data-node-id]").first.dispatch_event("mouseover")
    expect(page.get_by_test_id("model-map-selected")).to_contain_text("Source:")


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (1440, "dark"), (390, "light"), (390, "dark")])
def test_the_map_with_an_element_selected_and_the_empty_map_at_each_width_and_theme(browser, live_server, seeded, mapped, width, scheme):
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    context.add_init_script("localStorage.setItem('theme', %r);" % scheme)
    page = context.new_page()
    try:
        sign_in(page, live_server, seeded["emails"]["enterprise_architect"])
        page.goto(live_server + "/archimate/model-map", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _wait_drawn(page)
        page.locator('#impact-svg g[data-node-id="%d"]' % mapped["sourced"]["id"]).dispatch_event("mouseover")
        expect(page.get_by_test_id("model-map-selected")).to_contain_text("Source:")
        _shot(page, "selected", scheme)
    finally:
        context.close()
    fresh = create_fresh_org("enterprise_architect", org_admin=True)
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    context.add_init_script("localStorage.setItem('theme', %r);" % scheme)
    page = context.new_page()
    try:
        sign_in(page, live_server, fresh["emails"]["enterprise_architect"])
        page.goto(live_server + "/archimate/model-map", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("model-map-empty")).to_be_visible(timeout=PAGE_TIMEOUT)
        folder = os.environ.get("SMOKE_SCREENSHOT_DIR")
        if folder:
            pathlib.Path(folder).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(pathlib.Path(folder) / ("model-map-empty-%d-%s.png" % (width, scheme))))
    finally:
        context.close()


_NODE_FILLS = """() => {
    const out = {};
    document.querySelectorAll('#impact-svg g[data-node-id]').forEach(g => {
        const c = g.querySelector('circle');
        out[g.getAttribute('data-node-id')] = {layer: g.getAttribute('data-layer'),
            fill: getComputedStyle(c).fill, stroke: getComputedStyle(c).stroke};
    });
    const dots = {};
    document.querySelectorAll('[data-testid="model-map-legend"] li[data-layer]').forEach(li => {
        const d = li.querySelector('span span');
        dots[li.getAttribute('data-layer')] = {fill: getComputedStyle(d).backgroundColor,
            stroke: getComputedStyle(d).borderTopColor};
    });
    return {nodes: out, dots: dots};
}"""


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_business_and_technology_are_told_apart_in_the_map_and_its_legend(browser, live_server, seeded, mapped, width, scheme):
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    context.add_init_script("localStorage.setItem('theme', %r);" % scheme)
    page = context.new_page()
    try:
        sign_in(page, live_server, seeded["emails"]["enterprise_architect"])
        page.goto(live_server + "/archimate/model-map", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _wait_drawn(page)
        if width < 700:
            page.get_by_role("button", name="Show or hide the legend").click()
        legend = page.get_by_test_id("model-map-legend")
        expect(legend).to_be_visible()
        seen = page.evaluate(_NODE_FILLS)
        names = mapped["graph"]["names"]
        by_layer = {}
        for node in seen["nodes"].values():
            by_layer.setdefault(node["layer"], node)
        # the seeded model holds Business, Application and Technology elements at least
        assert {"business", "application", "technology"} <= set(by_layer), set(by_layer)
        assert set(by_layer) == set(seen["dots"]), (set(by_layer), set(seen["dots"]))
        # no two layers share a colour, in the drawing or in the legend
        for kind in ("fill", "stroke"):
            assert len({n[kind] for n in by_layer.values()}) == len(by_layer), (kind, by_layer)
            assert len({d[kind] for d in seen["dots"].values()}) == len(seen["dots"]), (kind, seen["dots"])
        # and the legend dot is the colour its nodes are drawn in
        for layer, node in by_layer.items():
            assert seen["dots"][layer]["fill"] == node["fill"], layer
        assert names["ops"] and names["portal"]
        business = page.locator("#impact-svg g[data-layer='business'] circle").first
        technology = page.locator("#impact-svg g[data-layer='technology'] circle").first
        assert business.evaluate("e => getComputedStyle(e).fill") != technology.evaluate("e => getComputedStyle(e).fill")
        _shot(page, "layers", scheme)
    finally:
        context.close()

"""An enterprise architect reads the model map, switches it to a list, and finds her way back to the model.

The person: an enterprise architect whose organisation holds a few hundred elements, most of them
not yet connected to anything, and a second organisation with elements of its own.
The goals:
  * see the whole ring of elements on first load, every one inside the canvas, each carrying its name and type;
  * read every legend label in full;
  * choose Application, switch to the list on the same page, and read the same count the filter shows;
  * open an element from the list and land on its own page;
  * follow the trail from the map to Home and to the model's page.
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

APPLICATIONS = 30
BUSINESS = 20
UNCONNECTED = 1100
LAYERED = [("ApplicationComponent", "application", APPLICATIONS), ("BusinessActor", "business", BUSINESS),
           ("Node", "technology", 6), ("Goal", "motivation", 4), ("WorkPackage", "implementation", 3)]


def _shot(page, name, scheme):
    """Save a screenshot when SMOKE_SCREENSHOT_DIR names a folder; the journey asserts either way."""
    folder = os.environ.get("SMOKE_SCREENSHOT_DIR")
    if not folder:
        return
    pathlib.Path(folder).mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(pathlib.Path(folder) / ("%s-%d-%s.png" % (name, page.viewport_size["width"], scheme))),
                    full_page=True)


@pytest.fixture(scope="module")
def world():
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement as E, ArchiMateRelationship as R

    mine = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    suffix = uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        org = mine["org_id"]
        connected = []
        for kind, layer, n in LAYERED:
            for i in range(n):
                e = E(name="%s %02d %s" % (kind, i, suffix), type=kind, layer=layer, organization_id=org)
                db.session.add(e)
                connected.append(e)
        for i in range(UNCONNECTED):
            db.session.add(E(name="Loose %03d %s" % (i, suffix), type="BusinessProcess", layer="business",
                             organization_id=org))
        for i in range(5):
            db.session.add(E(name="Foreign %d %s" % (i, suffix), type="ApplicationComponent", layer="application",
                             organization_id=other["org_id"]))
        db.session.commit()
        for a, b in zip(connected, connected[1:]):
            db.session.add(R(type="Serving", source_id=a.id, target_id=b.id, organization_id=org))
        db.session.commit()
        out = {"email": mine["emails"]["enterprise_architect"], "suffix": suffix}
        db.session.remove()
    return out


def _open(page, live_server, world, path="/archimate/model-map", width=1440, height=800):
    page.set_viewport_size({"width": width, "height": height})
    sign_in(page, live_server, world["email"])
    page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)


@pytest.mark.parametrize("width,height,scheme", [(1440, 800, "light"), (1440, 800, "dark"), (390, 844, "light")])
def test_first_view_holds_every_element_named_and_legend_is_whole(browser, live_server, world, width, height, scheme):
    context = browser.new_context(color_scheme=scheme)
    page = context.new_page()
    try:
        _open(page, live_server, world, width=width, height=height)
        page.locator("#impact-svg[data-settled]").wait_for(state="attached", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1000)
        total = APPLICATIONS + BUSINESS + 6 + 4 + 3 + UNCONNECTED
        measured = page.evaluate("""() => {
          const c = document.querySelector('#impact-svg').getBoundingClientRect();
          const nodes = Array.from(document.querySelectorAll('#impact-svg g[data-node-id]'));
          return {count: nodes.length,
                  outside: nodes.map(g => { const r = g.getBoundingClientRect(); return {n: g.getAttribute('aria-label'),
                      out: r.left < c.left - 1 || r.right > c.right + 1 || r.top < c.top - 1 || r.bottom > c.bottom + 1}; })
                      .filter(x => x.out).map(x => x.n),
                  unnamed: nodes.filter(g => !/, [A-Za-z]+\\./.test(g.getAttribute('aria-label') || '')).length,
                  legend: Array.from(document.querySelectorAll('[data-testid^="model-map-legend-"]')).map(li => {
                      const t = li.querySelector('.break-words'); return {text: t.textContent.trim(),
                          cut: t.scrollWidth > t.clientWidth + 1 || /\\u2026|\\.\\.\\.$/.test(t.textContent.trim())}; })};
        }""")
        assert measured["count"] == total
        assert measured["unnamed"] == 0, "nodes without a name and a type"
        assert not measured["outside"], "%d element(s) lie outside the canvas: %r" % (len(measured["outside"]), measured["outside"][:3])
        assert any(l["text"] == "Implementation and migration" for l in measured["legend"])
        assert not [l for l in measured["legend"] if l["cut"]], measured["legend"]
        _shot(page, "model-map", scheme)
    finally:
        context.close()


def test_list_view_matches_the_filter_opens_records_and_the_trail_leads_home(browser, live_server, world):
    context = browser.new_context()
    page = context.new_page()
    try:
        _open(page, live_server, world)
        page.get_by_test_id("model-map-layer-application").click()
        page.wait_for_url(re.compile(r"layer=application"), timeout=PAGE_TIMEOUT)
        label = page.get_by_test_id("model-map-layer-application").inner_text()
        filter_count = int(re.search(r"\((\d+)", label).group(1))
        assert filter_count == APPLICATIONS
        page.get_by_test_id("model-map-view-list").click()
        page.wait_for_url(re.compile(r"view=list"), timeout=PAGE_TIMEOUT)
        assert page.evaluate("location.pathname") == "/archimate/model-map"
        table = page.get_by_test_id("model-map-table-pager")
        expect(table).to_be_visible(timeout=PAGE_TIMEOUT)
        assert int(table.get_attribute("data-total")) == filter_count
        expect(page.get_by_test_id("model-map-counts")).to_have_text("%d elements" % filter_count)
        expect(page.get_by_test_id("model-map-row").first).to_be_visible()
        assert page.get_by_test_id("model-map-row").filter(has_text="Foreign").count() == 0
        # 25 to a page, and the next page carries the rest.
        assert page.locator('[data-testid="model-map-row"]:visible').count() == 25
        link = page.get_by_test_id("model-map-element-link").first
        name = link.inner_text()
        _shot(page, "model-map-list", "light")
        link.click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("main")).to_contain_text(name, timeout=PAGE_TIMEOUT)
        # Back to the list, narrow by type, and the diagram toggle returns to the drawing of the same layer.
        page.go_back()
        page.get_by_test_id("model-map-view-diagram").click()
        page.wait_for_url(re.compile(r"layer=application"), timeout=PAGE_TIMEOUT)
        assert "view=list" not in page.url
        expect(page.locator("#impact-svg g[data-node-id]").first).to_be_attached(timeout=PAGE_TIMEOUT)
        # The trail starts at Home; the Model crumb lands on the model's page, which has content.
        trail = page.get_by_label("Breadcrumb")
        assert [t.strip() for t in trail.locator("li").all_inner_texts()] == ["Home", "Model", "Model map"]
        trail.get_by_role("link", name="Model").click()
        page.wait_for_load_state("domcontentloaded")
        heading = page.get_by_role("heading", level=1).first
        expect(heading).to_have_text(re.compile(r"^\s*Model\s*$"), timeout=PAGE_TIMEOUT)
        assert len(page.locator("main").inner_text()) > 200
        page.go_back()
        page.get_by_label("Breadcrumb").get_by_role("link", name="Home").click()
        page.wait_for_load_state("domcontentloaded")
        assert page.evaluate("location.pathname") in ("/", "/dashboard", "/dashboard/overview") or "dashboard" in page.url
    finally:
        context.close()


def test_list_filtered_to_a_type_counts_only_that_type(browser, live_server, world):
    context = browser.new_context()
    page = context.new_page()
    try:
        _open(page, live_server, world, path="/archimate/model-map?view=list&type=Goal")
        table = page.get_by_test_id("model-map-table-pager")
        expect(table).to_be_visible(timeout=PAGE_TIMEOUT)
        assert int(table.get_attribute("data-total")) == 4
        expect(page.get_by_test_id("model-map-counts")).to_have_text("4 elements")
    finally:
        context.close()

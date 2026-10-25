"""The ArchiMate Composer's editor shell, in a real browser, as the Enterprise Architect.

The goal: an enterprise architect finds an element in a view, adds another from the palette,
connects the two, edits, undoes, redoes, exports and deletes, and at every step the shell
(toolbar, palette, finder, legend, status line, properties sheet) agrees with the diagram.

The view is the impact neighbourhood of a seeded service, so the finder, the legend and the
status line all have linked data to agree about. A second page opens an empty canvas so the
empty states are tested too.
"""
import re

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD
from tests.smoke.intelligence_graph import seed_impact_graph

PAGE_TIMEOUT = 30000
APP = '[x-data^="composerApp"]'

GROUP_COUNTS = {
    "strategy": 4, "business": 13, "application": 9, "technology": 13,
    "physical": 4, "motivation": 10, "implementation": 5, "other": 2,
}


def _assert_signed_in_as_enterprise_architect(email):
    """The journey's persona is the Enterprise Architect, not an administrator."""
    from app import create_app
    from app.models.user import User

    with create_app("testing").app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == "enterprise_architect"
        assert not user.is_platform_admin
        assert not user.is_org_admin


def _open(browser, live_server, seeded, query, width=1440):
    email = seeded["emails"]["enterprise_architect"]
    _assert_signed_in_as_enterprise_architect(email)
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    page.goto(live_server + "/archimate/composer" + query, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("nav[aria-label='Composer toolbar']")).to_be_visible(timeout=PAGE_TIMEOUT)
    return context, page, errors


def _graph(page):
    return page.evaluate(
        """(sel) => {
            const app = Alpine.$data(document.querySelector(sel));
            const nodes = app.graph.getElements().filter(c => c.get('elementId') != null && !c.get('isLayerZone'));
            const links = app.graph.getLinks();
            return {
                names: nodes.map(c => c.get('elName')).sort(),
                elements: nodes.length,
                links: links.length,
                relTypes: Array.from(new Set(links.map(l => (l.get('relType') || '').toLowerCase()))).sort(),
                elementCount: app.elementCount,
                relCount: app.relCount,
            };
        }""",
        APP,
    )


def _wait_view(page):
    page.wait_for_function(
        "(sel) => { const a = Alpine.$data(document.querySelector(sel)); return a.focusView && a.graph.getElements().length > 1; }",
        arg=APP, timeout=PAGE_TIMEOUT,
    )


def _settle(page):
    """Wait out the focus view's own refits while the canvas settles (three seconds after it opens),
    as a person looking at the drawing would, before reaching for the finder."""
    page.wait_for_function(
        "(sel) => { const a = Alpine.$data(document.querySelector(sel)); return !a._focusFitUntil || Date.now() > a._focusFitUntil + 600; }",
        arg=APP, timeout=PAGE_TIMEOUT, polling=200)


def _free_spot(page):
    """A point on the canvas, in canvas pixels, where a new element will not land on an existing one."""
    return page.evaluate(
        """(sel) => {
            const a = Alpine.$data(document.querySelector(sel)), s = a.paper.scale(), t = a.paper.translate();
            const c = document.getElementById('composer-canvas');
            const boxes = a.graph.getElements().map(e => { const b = e.getBBox();
                return [b.x * s.sx + t.tx - 110, b.y * s.sy + t.ty - 90, (b.x + b.width) * s.sx + t.tx + 110, (b.y + b.height) * s.sy + t.ty + 90]; });
            for (let y = 80; y < c.clientHeight - 60; y += 20) for (let x = 90; x < c.clientWidth - 60; x += 20) {
                if (boxes.every(b => x < b[0] || x > b[2] || y < b[1] || y > b[3])) return {x, y};
            }
            return null;
        }""", APP)


def _counts_text(elements, links):
    return "%d %s, %d %s" % (elements, "element" if elements == 1 else "elements", links, "relationship" if links == 1 else "relationships")


def _status_counts(page):
    text = page.get_by_test_id("status-counts").inner_text()
    m = re.match(r"(\d+) elements?, (\d+) relationships?", text)
    assert m, text
    return int(m.group(1)), int(m.group(2))


@pytest.fixture(scope="module")
def graph(seeded):
    return seed_impact_graph(seeded["ids"]["org"], noun_prefix="Shellview")


@pytest.mark.smoke
def test_empty_canvas_explains_itself_and_disables_selection_actions(browser, live_server, seeded):
    context, page, errors = _open(browser, live_server, seeded, "?blank=1")
    try:
        # Nothing is selected: Properties and Delete wait for a selection.
        expect(page.get_by_test_id("action-properties")).to_be_disabled()
        expect(page.get_by_test_id("action-delete")).to_be_disabled()
        expect(page.get_by_test_id("history-undo")).to_be_disabled()
        expect(page.get_by_test_id("history-redo")).to_be_disabled()
        # The empty legend and finder say why they are empty and what to do next.
        expect(page.get_by_test_id("legend-empty")).to_contain_text("No relationships yet")
        page.get_by_test_id("rail-tab-view").click()
        expect(page.get_by_test_id("finder-empty")).to_contain_text("no elements yet")
        page.get_by_role("button", name="Open the element palette").click()
        expect(page.get_by_test_id("diagram-palette")).to_be_visible()
        # The header names the view and what it is for.
        expect(page.get_by_test_id("diagram-title")).not_to_be_empty()
        expect(page.get_by_test_id("diagram-purpose")).to_contain_text("free canvas")
        assert _status_counts(page) == (0, 0)
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.smoke
def test_palette_holds_every_element_type_in_eight_groups_with_icons(browser, live_server, seeded):
    context, page, errors = _open(browser, live_server, seeded, "?blank=1")
    try:
        for key, count in GROUP_COUNTS.items():
            expect(page.get_by_test_id("palette-count-" + key)).to_have_text(str(count))
        assert sum(GROUP_COUNTS.values()) == 60
        # Open every group: all 60 types are reachable, each with its notation icon from the sprite.
        for key in GROUP_COUNTS:
            if page.get_by_test_id("palette-group-" + key).locator("ul").is_hidden():
                page.get_by_test_id("palette-group-" + key).get_by_role("button").first.click()
        items = page.locator("[data-testid^='palette-item-']")
        assert items.count() == 60
        hrefs = page.locator("[data-testid^='palette-item-'] use").evaluate_all("els => els.map(e => e.getAttribute('href'))")
        assert len(set(hrefs)) == 60 and all("archimate-notation.svg#am-" in h for h in hrefs)
        # Every icon the palette points at exists in the sprite, so no row is drawn blank.
        sprite = page.evaluate("async () => (await (await fetch('/static/img/archimate-notation.svg')).text())")
        assert all(('id="%s"' % h.split("#")[1]) in sprite for h in hrefs)
        # Searching narrows the rows and the counts; clearing restores them.
        page.get_by_test_id("palette-search").fill("process")
        expect(page.get_by_test_id("palette-count-business")).to_have_text("1")
        expect(page.get_by_test_id("palette-count-technology")).to_have_text("1")
        names = page.locator("[data-testid^='palette-item-']:visible").evaluate_all("els => els.map(e => e.innerText.trim())")
        assert names and all("process" in n.lower() for n in names)
        page.get_by_test_id("palette-search").fill("zzzz")
        expect(page.get_by_test_id("palette-empty")).to_contain_text("No element type matches")
        page.get_by_role("button", name="Show all element types").click()
        expect(page.get_by_test_id("palette-count-business")).to_have_text("13")
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.smoke
def test_architect_finds_adds_connects_edits_undoes_exports_and_deletes(browser, live_server, seeded, graph):
    names = graph["names"]
    context, page, errors = _open(
        browser, live_server, seeded, "?element=%s&depth=2&derived=0" % graph["service"],
    )
    try:
        _wait_view(page)
        _settle(page)
        before = _graph(page)
        # The status line counts agree with the diagram.
        assert _status_counts(page) == (before["elements"], before["links"])
        assert before["links"] >= 2

        # FIND: a view opened to look at one element's neighbourhood keeps the room for the drawing, so the
        # element list starts closed; the toolbar opens it. The finder lists exactly the elements drawn,
        # a search narrows it, a click focuses one.
        expect(page.get_by_test_id("composer-rail")).to_be_hidden()
        page.get_by_test_id("shell-rail-toggle").click()
        expect(page.get_by_test_id("composer-rail")).to_be_visible()
        page.get_by_test_id("rail-tab-view").click()
        expect(page.get_by_test_id("finder-count")).to_have_text(str(before["elements"]))
        assert sorted(page.locator("[data-testid^='finder-row-'] .truncate:first-child").evaluate_all(
            "els => els.map(e => e.innerText.trim())")) == before["names"]
        page.get_by_test_id("finder-search").fill(names["ops"])
        expect(page.locator("[data-testid^='finder-row-']")).to_have_count(1)
        page.locator("[data-testid^='finder-row-']").first.click()
        selected = page.evaluate("(sel) => (Alpine.$data(document.querySelector(sel)).selectedNode || {}).label", APP)
        assert selected == names["ops"]
        page.wait_for_timeout(400)
        centred = page.evaluate(
            """(sel) => {
                const a = Alpine.$data(document.querySelector(sel));
                const cell = a.graph.getElements().filter(c => c.get('elName') === a.selectedNode.label)[0];
                const box = cell.getBBox(), s = a.paper.scale(), t = a.paper.translate();
                const c = document.getElementById('composer-canvas');
                return [Math.abs(box.center().x * s.sx + t.tx - c.clientWidth / 2), Math.abs(box.center().y * s.sy + t.ty - c.clientHeight / 2)];
            }""", APP)
        assert centred[0] < 6 and centred[1] < 6, centred
        # A selection enables Properties and Delete.
        expect(page.get_by_test_id("action-properties")).to_be_enabled()
        expect(page.get_by_test_id("action-delete")).to_be_enabled()

        # ADD: drag a Business Process from the palette onto the canvas and name it.
        page.get_by_test_id("rail-tab-add").click()
        process_name = "Book a parcel"
        page.get_by_test_id("zoom-out").click()
        page.get_by_test_id("zoom-out").click()
        spot = _free_spot(page)
        assert spot, "no free place on the canvas to drop an element"
        page.get_by_test_id("palette-item-BusinessProcess").drag_to(page.locator("#composer-canvas"), target_position=spot)
        page.get_by_label("New Element Name").fill(process_name)
        page.get_by_role("button", name=re.compile("^Create$")).click()
        page.wait_for_function(
            "([sel, n]) => Alpine.$data(document.querySelector(sel)).graph.getElements().some(c => c.get('elName') === n)",
            arg=[APP, process_name], timeout=PAGE_TIMEOUT)
        after_add = _graph(page)
        assert after_add["elements"] == before["elements"] + 1
        expect(page.get_by_test_id("status-counts")).to_contain_text("%d elements" % after_add["elements"])
        # The finder lists the new element too.
        page.get_by_test_id("rail-tab-view").click()
        page.get_by_test_id("finder-search").fill("")
        expect(page.get_by_test_id("finder-count")).to_have_text(str(after_add["elements"]))
        expect(page.locator("[data-testid='finder-list']")).to_contain_text(process_name)

        # CONNECT: with the Connect tool, let the gateway application serve the new process.
        page.get_by_test_id("tool-connect").click()
        expect(page.get_by_test_id("tool-connect")).to_have_attribute("aria-pressed", "true")
        page.locator("g.joint-element", has_text=names["gateway"]).first.click()
        page.locator("g.joint-element", has_text=process_name).first.click()
        page.locator(".rel-picker-item", has_text=re.compile("^\\s*serving", re.I)).first.click()
        page.wait_for_function(
            "(sel) => Alpine.$data(document.querySelector(sel)).graph.getLinks().length > %d" % before["links"],
            arg=APP, timeout=PAGE_TIMEOUT)
        connected = _graph(page)
        assert connected["links"] == before["links"] + 1
        expect(page.get_by_test_id("status-counts")).to_have_text(_counts_text(connected["elements"], connected["links"]))
        # The legend names every relationship type the diagram draws, and nothing else.
        legend = page.locator("[data-testid^='legend-']:not([data-testid='legend-empty'])").evaluate_all(
            "els => els.map(e => e.getAttribute('data-testid').replace('legend-', ''))")
        assert sorted(legend) == connected["relTypes"] and "serving" in legend
        expect(page.get_by_test_id("legend-serving")).to_contain_text("Serving")

        # UNDO and REDO go through the engine's own history.
        page.get_by_test_id("tool-select").click()
        expect(page.get_by_test_id("history-undo")).to_be_enabled()
        page.get_by_test_id("history-undo").click()
        undone = _graph(page)
        expect(page.get_by_test_id("history-redo")).to_be_enabled()
        assert undone["links"] == connected["links"] - 1
        page.get_by_test_id("history-redo").click()
        page.wait_for_function(
            "([sel, n]) => Alpine.$data(document.querySelector(sel)).graph.getLinks().length === n",
            arg=[APP, connected["links"]], timeout=PAGE_TIMEOUT)
        redone = _graph(page)
        assert (redone["elements"], redone["links"]) == (connected["elements"], connected["links"])
        expect(page.get_by_test_id("status-counts")).to_have_text(_counts_text(redone["elements"], redone["links"]))

        # FIT centres the whole diagram in the canvas.
        page.get_by_test_id("zoom-fit").click()
        fitted = page.evaluate(
            """(sel) => {
                const a = Alpine.$data(document.querySelector(sel));
                const b = a.graph.getBBox(), s = a.paper.scale(), t = a.paper.translate();
                const c = document.getElementById('composer-canvas');
                // The unsaved-view card floats over the top of the canvas: the diagram is centred in the space below it.
                const card = document.getElementById('composer-focus-banner');
                const top = card && card.offsetHeight ? Math.max(40, card.getBoundingClientRect().bottom - c.getBoundingClientRect().top + 16) : 40;
                return {dx: Math.abs((b.x + b.width / 2) * s.sx + t.tx - c.clientWidth / 2),
                        dy: Math.abs((b.y + b.height / 2) * s.sy + t.ty - (top + (c.clientHeight - top - 40) / 2)),
                        pct: a.zoomPercent};
            }""", APP)
        assert fitted["dx"] < 30 and fitted["dy"] < 30, fitted
        expect(page.get_by_test_id("zoom-percent")).to_have_text("%d%%" % fitted["pct"])

        # EDIT: the Properties sheet shows the element and the relationship just drawn, and a rename persists.
        page.locator("g.joint-element", has_text=process_name).first.click()
        page.get_by_test_id("action-properties").click()
        sheet = page.get_by_test_id("properties-sheet")
        expect(sheet.get_by_test_id("properties-name")).to_have_value(process_name)
        expect(sheet.get_by_test_id("properties-type")).to_have_text("Business Process")
        expect(sheet.get_by_test_id("properties-relations")).to_contain_text("Serving from")
        expect(sheet.get_by_test_id("properties-relations")).to_contain_text(names["gateway"])
        renamed = "Book a parcel online"
        sheet.get_by_test_id("properties-name").fill(renamed)
        sheet.get_by_test_id("properties-documentation").fill("How a merchant books a parcel.")
        sheet.get_by_test_id("properties-name").press("Tab")
        sheet.get_by_test_id("properties-documentation").press("Tab")
        page.keyboard.press("Escape")
        page.wait_for_function(
            "([sel, n]) => Alpine.$data(document.querySelector(sel)).graph.getElements().some(c => c.get('elName') === n)",
            arg=[APP, renamed], timeout=PAGE_TIMEOUT)
        element_id = page.evaluate(
            "([sel, n]) => Alpine.$data(document.querySelector(sel)).graph.getElements().filter(c => c.get('elName') === n)[0].get('elementId')",
            [APP, renamed])
        page.wait_for_function(
            """async (id) => { const r = await fetch('/archimate/api/elements/' + id, {credentials: 'same-origin'}); const b = await r.json();
                const d = b.data || b.element || b; return (d.name || '') === 'Book a parcel online'; }""",
            arg=element_id, timeout=PAGE_TIMEOUT)

        # EXPORT: the Export menu hands over a real file.
        page.get_by_test_id("menu-export").click()
        with page.expect_download(timeout=PAGE_TIMEOUT) as download:
            page.get_by_role("menuitem", name="SVG image").click()
        assert download.value.suggested_filename.lower().endswith(".svg")

        # DELETE: the new element leaves the diagram; the finder and the counts follow.
        page.locator("g.joint-element", has_text=renamed).first.click()
        page.get_by_test_id("action-delete").click()
        page.get_by_role("button", name="Delete", exact=True).last.click()
        page.wait_for_function(
            "([sel, n]) => !Alpine.$data(document.querySelector(sel)).graph.getElements().some(c => c.get('elName') === n)",
            arg=[APP, renamed], timeout=PAGE_TIMEOUT)
        final = _graph(page)
        assert final["elements"] == before["elements"]
        expect(page.get_by_test_id("finder-count")).to_have_text(str(final["elements"]))
        expect(page.get_by_test_id("status-counts")).to_contain_text("%d elements" % final["elements"])
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.smoke
def test_shell_fits_a_phone_without_hiding_the_job(browser, live_server, seeded, graph):
    context, page, errors = _open(
        browser, live_server, seeded, "?element=%s&depth=1&derived=0" % graph["service"], width=390,
    )
    try:
        _wait_view(page)
        # The element rail starts closed on a phone and opens from the toolbar.
        expect(page.get_by_test_id("composer-rail")).to_be_hidden()
        page.get_by_test_id("shell-rail-toggle").click()
        expect(page.get_by_test_id("composer-rail")).to_be_visible()
        # No horizontal scroll on the page.
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        # The tools scroll sideways in one row, so every one of them can be brought into view and used;
        # the Import, Export and More menus stay in view, so a menu is never clipped.
        for tid in ("tool-connect", "zoom-fit", "action-delete", "menu-import", "menu-export", "menu-more"):
            page.get_by_test_id(tid).scroll_into_view_if_needed()
            box = page.get_by_test_id(tid).bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= 391, (tid, box)
        page.get_by_test_id("menu-export").click()
        menu = page.get_by_role("menuitem", name="SVG image").bounding_box()
        assert menu and menu["x"] >= 0 and menu["x"] + menu["width"] <= 391, menu
        assert not errors, errors
    finally:
        context.close()

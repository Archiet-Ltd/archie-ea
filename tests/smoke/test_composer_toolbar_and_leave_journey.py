"""The Composer keeps every toolbar action reachable, treats a look as not an edit and reads in the dark.

The person: an enterprise architect who followed a "what does this connect to" link to the
Composer's focus view, then switches to edit mode to change the drawing.
The goal: reach every toolbar action by mouse at any width, leave an unchanged view without a
browser prompt, find the library panel by the name it shows, and read the connection words on
the dark canvas.
"""
import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.test_composer_focus_view_fits_journey import APP, _open, hub  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWS = [(1440, 900), (390, 844)]



@pytest.mark.parametrize("width,height", VIEWS)
def test_overflow_menus_are_reachable_by_mouse_in_edit_mode(browser, live_server, hub, width, height):
    context, page = _open(browser, live_server, hub, width, height, "light")
    try:
        def inside(locator, what):
            box = locator.bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, \
                "%dpx: %s sits at x=%s..%s, outside the screen" % (
                    width, what, box and box["x"], box and box["x"] + box["width"])

        more = page.get_by_role("button", name="More")
        expect(more).to_be_visible()
        inside(more, "More")
        more.click()
        menu = page.locator(".toolbar-dropdown-menu:visible")
        expect(menu.first).to_be_visible()
        inside(menu.first, "the More menu")
        expect(menu.first.get_by_text("Validate Diagram")).to_be_visible()
        more.click()

        # The actions that used to sit past the edge are in the folded menu, beside More.
        folded = page.get_by_role("button", name="Diagrams, diagram metadata, quick add and AI")
        expect(folded).to_be_visible()
        inside(folded, "the folded actions button")
        folded.click()
        items = page
        expect(items.get_by_role("menuitem", name="New Diagram")).to_be_visible()
        inside(items.get_by_role("menuitem", name="New Diagram"), "the folded actions menu")
        for name in ("New Diagram", "Diagram metadata", "Quick add", "Generate from Description"):
            expect(items.get_by_role("menuitem", name=name)).to_be_visible()
        # And one of them does its job: Diagram metadata opens the metadata panel.
        items.get_by_role("menuitem", name="Diagram metadata").click()
        expect(page.get_by_label("Close metadata panel")).to_be_visible()
    finally:
        context.close()


def test_leaving_an_unchanged_view_fires_no_prompt_and_a_changed_one_does(browser, live_server, hub):
    context, page = _open(browser, live_server, hub, 1440, 900, "light")
    try:
        dialogs = []
        page.on("dialog", lambda d: (dialogs.append(d.type), d.accept()))
        page.mouse.click(700, 500)  # the person touches the page: leaving may now prompt
        page.goto("%s/archimate/composer?element=%d&depth=1" % (live_server, hub["centre"]),
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert dialogs == [], "leaving an unchanged view prompted: %r" % dialogs
        page.wait_for_function(
            """(sel) => { const a = Alpine.$data(document.querySelector(sel));
                return !!a.focusView && a.graph.getElements().length > 1; }""", arg=APP, timeout=15000)
        page.wait_for_timeout(3500)
        page.evaluate("""(sel) => { const a = Alpine.$data(document.querySelector(sel));
            const c = a.graph.getElements()[0]; c.position(c.position().x + 40, c.position().y + 40); }""", APP)
        page.mouse.click(700, 500)
        page.goto("%s/dashboard/" % live_server, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert dialogs == ["beforeunload"], "a moved element should still prompt: %r" % dialogs
    finally:
        context.close()


def test_library_panel_is_named_by_its_heading(browser, live_server, hub):
    context, page = _open(browser, live_server, hub, 1440, 900, "light")
    try:
        page.evaluate("(sel) => { Alpine.$data(document.querySelector(sel)).componentsPanelOpen = true; }", APP)
        panel = page.locator('[data-testid="composer-capabilities-panel"]')
        expect(panel).to_be_visible()
        heading = panel.locator("h3").inner_text().strip()
        assert panel.get_attribute("aria-label") == heading == "Components"
        expect(page.get_by_role("complementary", name="Components")).to_be_visible()
    finally:
        context.close()


def test_connection_words_read_the_canvas_colour_in_the_dark(browser, live_server, hub):
    context, page = _open(browser, live_server, hub, 1440, 900, "dark")
    try:
        result = page.evaluate("""() => {
            const rgb = (css) => {
                const n = css.match(/[\\d.]+/g).map(Number);
                return css.startsWith('color(') ? n.slice(-3).map(v => Math.round(v * 255)) : n.slice(0, 3);
            };
            const canvas = document.querySelector('.composer-canvas-bg');
            const c = getComputedStyle(canvas).backgroundColor.match(/[\\d.]+/g).map(Number);
            const a = c.length > 3 ? c[3] : 1;
            // The page colour behind the canvas, read from the nearest ancestor that paints one.
            let behind = canvas.parentElement, page = null;
            while (behind && !page) {
                const v = getComputedStyle(behind).backgroundColor.match(/[\\d.]+/g).map(Number);
                if (v.length < 4 || v[3] === 1) page = v.slice(0, 3);
                behind = behind.parentElement;
            }
            page = page || [255, 255, 255];
            const expected = [0, 1, 2].map(i => Math.round(c[i] * a + page[i] * (1 - a)));
            const boxes = Array.from(document.querySelectorAll('#composer-canvas .joint-link g.label rect'));
            return {expected, fills: boxes.map(r => rgb(getComputedStyle(r).fill)), n: boxes.length};
        }""")
        assert result["n"] >= 10, result
        for fill in result["fills"]:
            assert all(abs(fill[i] - result["expected"][i]) <= 4 for i in range(3)), result
    finally:
        context.close()

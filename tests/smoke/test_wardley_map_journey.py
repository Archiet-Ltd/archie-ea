"""A business architect opens the Wardley Map of a seeded company and reads it as a map: components plotted by
evolution stage across and by distance from the user's need down, joined by dependency lines, with movement as an
arrow and inertia as a bar. Every dot opens its element. The same canvas pages carry one count per box, the operating
model only on the Business Model Canvas, and Delete behind a More menu.

Signed in as the business architect, never an administrator.
"""

import re

import pytest
from playwright.sync_api import expect

from tests.smoke.test_business_model_canvas_journey import PAGE_TIMEOUT, _assert_persona, _login
from tests.smoke.test_framework_canvases_journey import _seed

expect.set_options(timeout=PAGE_TIMEOUT)


def _canvas_ids(org_id):
    from app import create_app
    from app.models.business_model import BusinessModelCanvas

    app = create_app("testing")
    with app.app_context():
        return {
            c.canvas_type or "business_model_canvas": c.id
            for c in BusinessModelCanvas.query.filter_by(organization_id=org_id).all()
        }


def _open(browser, live_server, email, width):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 900})
    page = context.new_page()
    _login(page, live_server, email)
    return context, page


@pytest.mark.smoke
@pytest.mark.journey
def test_architect_reads_the_wardley_map_as_a_map_and_follows_a_dot(browser, live_server, seeded):
    email = seeded["emails"]["business_architect"]
    _assert_persona(email)  # enterprise_role is business_architect, not an administrator or platform role
    ids = _seed(seeded["ids"]["org"])
    context, page = _open(browser, live_server, email, 1440)
    try:
        page.goto(f"{live_server}/business-model/{ids['wardley_mapping']}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        svg = page.locator("svg[data-wardley-plot]")
        expect(svg).to_be_visible()
        # the axes a Wardley map has
        for label in ("Visible", "Invisible", "Genesis", "Custom-built", "Product (+rental)", "Commodity (+utility)"):
            expect(svg.get_by_text(label, exact=True).first).to_be_visible()
        dots = svg.locator("a[data-wardley-dot]")
        assert dots.count() >= 5
        assert svg.locator("line[data-wardley-line]").count() >= 4
        assert svg.locator("a[data-wardley-movement]").count() >= 1 and svg.locator("a[data-wardley-inertia]").count() >= 1
        # no grid of note cards any more
        assert page.locator('[data-slot=card] [data-canvas-notes]').count() == 0

        # evolution runs left to right, visibility top to bottom, from the need
        pos = page.evaluate(
            "() => Object.fromEntries([...document.querySelectorAll('a[data-wardley-dot]')].map(a => {"
            " const c = a.querySelector('circle'); return [a.getAttribute('aria-label'), [+c.getAttribute('cx'), +c.getAttribute('cy')]]; }))"
        )
        need, svc = pos["Operators know their readings can be trusted"], pos["Calibrated sensors with a certificate for every batch"]
        rig, stock, cloud = pos["Calibration Rig Pool"], pos["Component stock and supplier panel"], pos["Cloud hosting"]
        assert need[1] < svc[1] < rig[1] < stock[1]          # need above its service, above what serves it, above its supplier
        assert rig[0] < svc[0] < cloud[0]                    # custom-built, then product, then commodity

        # the list is the accessible alternative: every component, its stage and what it depends on, in text
        page.get_by_role("button", name="List", exact=True).click()
        row = page.locator('[data-canvas-rows] tr', has_text="3 steps from the need").filter(has_text="Calibration Rig Pool").first
        expect(row).to_contain_text("Custom-built")
        expect(row).to_contain_text("Component stock and supplier panel")
        expect(row).to_contain_text("3 steps from the need")
        listed = page.locator("[data-canvas-rows] tr").count()
        assert listed >= dots.count()
        page.get_by_role("button", name="Canvas", exact=True).click()

        # clicking a dot opens the element it names, with content
        target = dots.filter(has=page.locator("text=Calibration Rig Pool")).first
        target.locator("circle").click()
        page.wait_for_url(re.compile(r"/architecture/"), timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text("Calibration Rig Pool")

        # every dot, arrow and bar lands on a page that shows its name
        page.goto(f"{live_server}/business-model/{ids['wardley_mapping']}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        links = page.evaluate(
            "() => [...document.querySelectorAll('svg[data-wardley-plot] a')].map(a => [a.getAttribute('href'), a.getAttribute('aria-label')])"
        )
        assert len(links) >= 7
        for href, label in links:
            response = context.request.get(live_server + href)
            assert response.status == 200, (href, response.status)
            name = label.split(": ", 1)[-1].split(",")[0]
            assert name in response.text(), (href, name)
    finally:
        context.close()


@pytest.mark.smoke
@pytest.mark.journey
def test_wardley_map_keeps_its_shape_on_a_phone(browser, live_server, seeded):
    email = seeded["emails"]["business_architect"]
    _assert_persona(email)
    ids = _seed(seeded["ids"]["org"])
    context, page = _open(browser, live_server, email, 390)
    try:
        page.goto(f"{live_server}/business-model/{ids['wardley_mapping']}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        widths = page.evaluate(
            "() => { const r = document.querySelector('[data-entelim-form]');"
            " return [r.scrollWidth, r.clientWidth, document.documentElement.scrollWidth, window.innerWidth]; }"
        )
        assert widths[0] > widths[1] and widths[2] <= widths[3] + 1, widths
        a, b = page.evaluate(
            "() => { const xs = [...document.querySelectorAll('a[data-wardley-dot=component] circle')].map(c => c.getBoundingClientRect());"
            " return [Math.min(...xs.map(r => r.left)), Math.max(...xs.map(r => r.left))]; }"
        )
        assert b - a > 300, "the dots collapsed instead of keeping the map's width"
    finally:
        context.close()


@pytest.mark.smoke
@pytest.mark.journey
@pytest.mark.parametrize("width", [1440, 390])
def test_each_box_shows_one_count_and_only_the_controls_of_its_framework(browser, live_server, seeded, width):
    email = seeded["emails"]["business_architect"]
    _assert_persona(email)
    ids = _seed(seeded["ids"]["org"])
    canvases = _canvas_ids(seeded["ids"]["org"])
    context, page = _open(browser, live_server, email, width)
    try:
        for kind in ("lean_canvas", "porters_five_forces", "business_model_canvas"):
            page.goto(f"{live_server}/business-model/{canvases[kind]}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            bands = page.locator("[data-canvas-band]")
            assert bands.count() >= 5
            for i in range(bands.count()):
                band = bands.nth(i)
                # one number per box: the count pill. A bare number before the title is a second count.
                numbers = band.get_by_text(re.compile(r"^\s*\d+\s*$"))
                assert numbers.count() == 1, (kind, band.inner_text())
            operating = page.get_by_test_id("bmc-operating-model-panel")
            if kind == "business_model_canvas":
                expect(operating).to_be_visible()
            else:
                assert operating.count() == 0 and page.get_by_text("Operating model").count() == 0, kind
            # Delete is never the first action; it sits behind More, and keeps its confirmation
            first = page.locator("main a, main button").evaluate_all(
                "els => els.filter(e => e.offsetParent !== null).map(e => (e.innerText || e.getAttribute('aria-label') || '').trim()).filter(Boolean)[0]"
            )
            assert "delete" not in first.lower(), (kind, first)
            assert page.get_by_role("button", name=re.compile(r"^Delete canvas", re.I)).count() == 0 or not page.get_by_role("button", name=re.compile(r"^Delete canvas", re.I)).first.is_visible()
        page.get_by_role("button", name="More").click()
        page.get_by_role("menuitem", name=re.compile("Delete canvas", re.I)).click()
        expect(page.get_by_test_id("bmc-delete-form")).to_be_visible()
        expect(page.get_by_role("heading", name="Delete this canvas?")).to_be_visible()
        page.get_by_role("button", name="Cancel").last.click()
        page.goto(f"{live_server}/business-model/{ids['wardley_mapping']}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.get_by_test_id("bmc-operating-model-panel").count() == 0
    finally:
        context.close()


@pytest.mark.smoke
@pytest.mark.journey
def test_architect_adds_a_component_to_the_wardley_map_and_it_stays_after_a_reload(browser, live_server, seeded):
    email = seeded["emails"]["business_architect"]
    _assert_persona(email)
    ids = _seed(seeded["ids"]["org"])
    context, page = _open(browser, live_server, email, 1440)
    try:
        url = f"{live_server}/business-model/{ids['wardley_mapping']}"
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Add to the map").click()
        page.get_by_role("menuitem", name="Genesis").click()
        page.get_by_test_id("bmc-name-genesis").fill("Satellite level sensing")
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.get_by_test_id("bmc-add-submit-genesis").click()
        page.reload()
        svg = page.locator("svg[data-wardley-plot]")
        expect(svg.get_by_text("Satellite level sensing")).to_be_visible()
        # it has no dependency yet, so the list says it is not linked rather than inventing a place for it
        page.get_by_role("button", name="List", exact=True).click()
        expect(page.locator("[data-canvas-rows] tr", has_text="Satellite level sensing")).to_contain_text("Not yet linked to the need")
    finally:
        context.close()

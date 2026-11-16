"""Journey, in a browser, on a phone: a signed-in person reaches the Composer from the navigation.

At 1440 wide the sidebar lists the Composer. A person on a phone, 390 wide in the dark scheme, has to be
able to open the same navigation and follow the Composer link to a drawing that shows their elements,
without typing an address. The organisation is the demonstration company, seeded by the product's own
seed, so the drawing has linked elements.
"""

import os

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_USER = "demo@lantern-quay.example.com"
SHOTS = os.environ.get("SMOKE_SHOT_DIR")


@pytest.fixture(scope="module")
def demo_org(live_server):
    from app import create_app
    from app.commands.seed_demo_company import seed_demo_company

    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with create_app("testing").app_context():
            seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    return DEMO_USER


def test_a_person_on_a_phone_opens_the_composer_from_the_navigation(browser, live_server, demo_org):
    context = browser.new_context(viewport={"width": 390, "height": 800}, color_scheme="dark",
                                  is_mobile=True, has_touch=True)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    try:
        page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#email", demo_org)
        page.fill("#password", PASSWORD)
        page.locator("#submit").click()
        page.wait_for_url(lambda url: "/account/login" not in url, wait_until="commit", timeout=PAGE_TIMEOUT)
        page.goto(live_server + "/dashboard/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")

        trigger = page.locator("[data-slot='sidebar-trigger']:visible").first
        assert trigger.count() == 1, "no visible menu button on a phone"
        trigger.click()
        link = page.locator("a[data-slot='sidebar-menu-button'][href*='/archimate/composer']", has_text="Composer").first
        link.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        if SHOTS:
            page.screenshot(path=os.path.join(SHOTS, "composer_phone_menu_390_dark.png"))
        link.click()
        page.wait_for_url(lambda url: "/composer" in url, wait_until="commit", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        canvas = page.locator("#composer-canvas")
        canvas.wait_for(state="attached", timeout=PAGE_TIMEOUT)
        page.wait_for_function("document.querySelectorAll('#composer-canvas svg .joint-element').length > 0",
                               timeout=PAGE_TIMEOUT)
        assert page.locator("#composer-canvas svg .joint-element").count() > 0, \
            "the Composer opened with no elements drawn"
        if SHOTS:
            page.screenshot(path=os.path.join(SHOTS, "composer_phone_opened_390_dark.png"))

        # The drawing fills the screen width and the page does not scroll sideways.
        box = canvas.bounding_box()
        assert box and box["width"] >= 380 and box["x"] <= 10, "the drawing does not fill the phone screen: %r" % box
        overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        assert overflow <= 0, "the page scrolls sideways by %dpx" % overflow

        # A named element can be opened and its details name it.
        # The drawing opens on the layers; tapping a layer card shows its elements, and tapping an element
        # opens its details.
        def tappable(summary):
            return page.evaluate("""(summary) => {
                const a = Alpine.$data(document.querySelector('[x-data^="composerApp"]'));
                const c = document.querySelector('#composer-canvas').getBoundingClientRect();
                for (const cell of a.graph.getElements()) {
                    const named = summary ? cell.get('isLayerSummary') : (cell.get('elementId') && !cell.get('isLayerZone')
                        && !cell.get('isAnnotation') && !cell.get('isLayerSummary'));
                    if (!named) continue;
                    const r = a.paper.findViewByModel(cell).el.getBoundingClientRect();
                    const mx = r.left + r.width / 2, my = r.top + r.height / 2;
                    if (mx > c.left + 10 && mx < c.right - 10 && my > c.top + 10 && my < c.bottom - 10 && r.width > 20)
                        return {name: cell.get('elName'), x: mx, y: my};
                }
                return null; }""", summary)

        layer = tappable(True)
        assert layer, "no layer card sits inside the drawing on a phone"
        page.mouse.click(layer["x"], layer["y"])
        page.wait_for_function(
            """() => Alpine.$data(document.querySelector('[x-data^="composerApp"]')).graph.getElements()
                .some(c => c.get('elementId') && !c.get('isLayerSummary'))""", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(800)
        target = tappable(False)
        assert target and target["name"], "no named element sits inside the drawing on a phone"
        page.mouse.click(target["x"], target["y"])
        panel = page.locator(".detail-panel")
        panel.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        name = panel.locator('input[aria-label="Element name"]').input_value()
        assert name == target["name"], "the details opened %r, not the element tapped, %r" % (name, target["name"])
        pbox = panel.bounding_box()
        assert pbox["x"] >= 0 and pbox["x"] + pbox["width"] <= 390, "the details run off the screen: %r" % pbox
    finally:
        context.close()

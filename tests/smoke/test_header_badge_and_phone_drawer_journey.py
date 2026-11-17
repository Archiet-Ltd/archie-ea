"""Journey: a CTO sees the persona they work as in the page header, and on a phone opens the
navigation drawer, sees it over a scrim, and closes it with its own close button.

Screenshots go to JOURNEY_SHOT_DIR when it is set, else to a temporary folder.
"""
import os

import pytest

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _shot_dir(tmp_path):
    root = os.environ.get("JOURNEY_SHOT_DIR")
    if root:
        os.makedirs(root, exist_ok=True)
        return root
    return str(tmp_path)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_header_shows_the_persona_on_a_wide_screen(browser, live_server, seeded, tmp_path, scheme):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme=scheme)
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["cto"])
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        badge = page.locator("#user-role-badge")
        badge.wait_for(state="visible")
        assert badge.inner_text().strip() == "CTO / CIO"
        page.screenshot(path=os.path.join(_shot_dir(tmp_path), "header-1440-%s.png" % scheme))
    finally:
        ctx.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_phone_drawer_layers_over_the_page_and_closes_from_its_button(browser, live_server, seeded, tmp_path, scheme):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme=scheme)
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    shots = _shot_dir(tmp_path)
    try:
        _login(page, live_server, seeded["emails"]["cto"])
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1000)
        page.eval_on_selector_all("[x-show='showOnboarding']", "els => els.forEach(e => e.remove())")
        trigger = page.get_by_test_id("sidebar-trigger")
        trigger.click()
        drawer = page.get_by_role("dialog", name="Navigation")
        drawer.wait_for(state="visible")
        close = drawer.get_by_role("button", name="Close navigation")
        assert close.is_visible()
        box = close.bounding_box()
        assert box["width"] >= 32 and box["height"] >= 32, box
        # The scrim sits under the panel, and both sit over the page header.
        z = page.evaluate(
            """() => {
                const z = s => parseInt(getComputedStyle(document.querySelector(s)).zIndex, 10);
                return {scrim: z('[data-fb-sidebar-mobile] [data-slot=sheet-overlay]'),
                        panel: z('[data-fb-sidebar-mobile] [data-slot=sheet-content]'),
                        header: z('#admin-header')};
            }"""
        )
        assert z["header"] < z["scrim"] < z["panel"], z
        assert drawer.get_by_role("link").count() >= 3, "the drawer opened with no destinations"
        page.screenshot(path=os.path.join(shots, "phone-drawer-open-390-%s.png" % scheme))
        close.click()
        drawer.wait_for(state="hidden")
        assert page.evaluate("document.activeElement && document.activeElement.dataset.testid") == "sidebar-trigger"
        page.screenshot(path=os.path.join(shots, "phone-drawer-closed-390-%s.png" % scheme))
        # Closed from the scrim too.
        trigger.click()
        drawer.wait_for(state="visible")
        page.mouse.click(380, 400)
        drawer.wait_for(state="hidden")
    finally:
        ctx.close()


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_home_page_labels_its_sections_and_offers_start_free(browser, live_server, tmp_path, width, scheme):
    ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        labels = [t.strip().title() for t in page.locator("p.home-eyebrow").all_inner_texts()]
        assert "Roadmap" in labels and "Stages" not in labels, labels
        assert "waiting list" not in page.inner_text("body").lower()
        coming = page.get_by_role("link", name="Coming soon").first
        sentence = coming.locator("xpath=..").inner_text().lower()
        assert "early access" in sentence
        features = ctx.new_page()
        features.goto(live_server + "/features", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        entries = features.locator("[data-testid=coming-soon-list] li")
        total = entries.count()
        linked = features.locator("[data-testid=coming-soon-list] li a[href]").count()
        features.close()
        assert total > 0
        if linked < total:
            # Not every entry offers a request, so the home page must not say every one does.
            assert "each open" not in sentence and "on request" not in sentence, (total, linked, sentence)
            assert "where it is offered" in sentence, sentence
        assert page.get_by_role("link", name="Start free").count() >= 1
        coming.click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.rstrip("/").endswith("/features#coming-soon") or "/features" in page.url
        assert len(page.inner_text("main").strip()) > 200, "the Coming soon link landed on an empty page"
        page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.locator("p.home-eyebrow", has_text="Roadmap").scroll_into_view_if_needed()
        page.screenshot(path=os.path.join(_shot_dir(tmp_path), "home-roadmap-%d-%s.png" % (width, scheme)))
    finally:
        ctx.close()

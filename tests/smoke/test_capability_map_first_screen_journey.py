"""A business architect opens the capability map and sees the levelled heat map.

Goal: "I know capability maps; show me mine, nested, in its three bands, shaded by
how far each capability is from where it should be, with the key beside it."

Runs in the demonstration organisation (capabilities nested under parents, mapped
to applications) as a business architect, at 1440x900 without scrolling and at
390x844 with the map's own scroll area.
"""

import os
import pathlib

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_USER = "demo@lantern-quay.example.com"
ARCHITECT = "capmap03.business.architect@example.com"
SHOTS = pathlib.Path(os.environ.get("CAPMAP03_SHOTS", "/tmp/capmap03-shots"))
MAP_URL = "/capability-map/dashboard"
LIST_URL = "/enterprise/capability-map/capabilities"
MAP = '[data-testid="capability-map"]'


@pytest.fixture(scope="module")
def business_architect(live_server):
    from app import create_app, db
    from app.commands.seed_demo_company import seed_demo_company
    from app.models.user import Role, User

    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    app = create_app("testing")
    try:
        with app.app_context():
            seed_demo_company()
            org_id = User.query.filter_by(email=DEMO_USER).one().organization_id
            if not User.query.filter_by(email=ARCHITECT).first():
                Role.insert_roles()
                user = User(
                    email=ARCHITECT, first_name="Demo", last_name="Business",
                    organization_id=org_id, enterprise_role="business_architect",
                    confirmed=True, role=Role.query.filter_by(name="Viewer").first(),
                )
                user.password = PASSWORD
                db.session.add(user)
                db.session.commit()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    return ARCHITECT


def _open(browser, base, email, width, height, scheme="light"):
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    _login(page, base, email)
    page.goto(base + MAP_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")
    return context, page


def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s.png" % name)))


def test_first_screen_is_the_nested_heat_map_with_bands_and_key(browser, live_server, business_architect):
    context, page = _open(browser, live_server, business_architect, 1440, 900)
    try:
        region = page.locator(MAP)
        expect(region).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(region.locator('[data-testid="cap-box"]').first).to_be_visible(timeout=PAGE_TIMEOUT)

        # Without scrolling: the map's first nested tile sits inside the 900px viewport.
        nested_tile = region.locator('[data-testid="cap-box"][data-level="1"]:has([data-level="2"])').first
        expect(nested_tile).to_be_visible()
        box = nested_tile.bounding_box()
        assert box["y"] + 120 < 900, "the first nested tile starts below the fold at y=%s" % box["y"]
        assert page.evaluate("window.scrollY") == 0

        # Capabilities sit inside their parents: level 2 inside level 1, level 3 inside level 2.
        assert region.locator('[data-testid="cap-box"][data-level="1"] [data-level="2"]').count() > 0
        assert region.locator('[data-testid="cap-box"][data-level="2"] [data-level="3"]').count() > 0

        # Three bands, labelled.
        for band in ("strategic", "core", "supporting"):
            expect(region.locator('[data-testid="cap-band"][data-band="%s"]' % band)).to_be_visible()
        expect(region.locator('[data-testid="cap-band"][data-band="strategic"]')).to_contain_text("Strategic")
        expect(region.locator('[data-testid="cap-band"][data-band="core"]')).to_contain_text("Core")
        expect(region.locator('[data-testid="cap-band"][data-band="supporting"]')).to_contain_text("Supporting")

        # The legend names the measure and its steps; a figure accompanies each step.
        legend = page.locator('[data-testid="capability-legend"]')
        expect(legend).to_be_visible()
        expect(legend).to_contain_text("Maturity gap")
        assert legend.locator('[data-testid="legend-step"]').count() >= 3
        first = region.locator('[data-testid="cap-box"][data-level="2"]').first
        assert first.get_attribute("data-assessed") == "true"
        assert "gap" in first.inner_text().lower()
        _shot(page, "map-1440-light")

        # The stat row sits beside the map, with figures.
        stats = page.locator('[data-testid="capmap-stats"]')
        expect(stats).to_be_visible()
        assert stats.locator('[data-slot="card"]').count() <= 4

        # Switching the measure changes the legend and the boxes.
        before = legend.inner_text()
        page.select_option("#capmap-measure", "coverage")
        expect(legend).to_contain_text("Application coverage")
        assert legend.inner_text() != before
        expect(legend).not_to_contain_text("Maturity gap")
        assert "app" in first.inner_text().lower()

        # The map's count equals the capabilities list's.
        tiles = region.locator('[data-testid="cap-box"]').count()
        page.goto(live_server + LIST_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator(".cap-card").count() == tiles
    finally:
        context.close()


def test_phone_keeps_nesting_and_scrolls_inside_the_map(browser, live_server, business_architect):
    context, page = _open(browser, live_server, business_architect, 390, 844, scheme="dark")
    try:
        region = page.locator(MAP)
        expect(region.locator('[data-testid="cap-box"]').first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert region.locator('[data-testid="cap-box"][data-level="1"] [data-level="2"]').count() > 0
        scroller = region.locator('[data-testid="cap-scroll"]')
        assert scroller.evaluate("el => el.scrollWidth > el.clientWidth"), "the map should scroll sideways inside its own area"
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "the page itself must not scroll sideways"
        expect(page.locator('[data-testid="capability-legend"]')).to_be_visible()
        _shot(page, "map-390-dark")
    finally:
        context.close()


def test_a_link_can_open_the_map_on_a_measure(browser, live_server, business_architect):
    context, page = _open(browser, live_server, business_architect, 1440, 900)
    try:
        page.goto(live_server + MAP_URL + "?heat=coverage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        legend = page.locator('[data-testid="capability-legend"]')
        expect(legend).to_contain_text("Application coverage", timeout=PAGE_TIMEOUT)
        expect(page.locator("#capmap-measure")).to_have_value("coverage")
    finally:
        context.close()

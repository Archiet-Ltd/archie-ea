"""An architect reads one count for every layer, on the dashboard and in the Composer.

The person: an enterprise architect whose organisation holds a model of sixty-odd linked elements. Some are
stored under a layer name their type does not belong to (a goal filed under Business), one is spelt as a
model file wrote it ("Work Package"), two are equipment, two are locations, and a gap sits in the gap
register with no model element. A second organisation holds a larger Business layer of its own.

The goal: every tab on the architecture dashboard shows the number the Composer's matching layer card
shows, the totals agree, opening a tab never moves its number, no tab reads "unavailable" while the counts
load, and a button on the dashboard opens the Composer on the same model.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")]

# What the seed holds, by the layer each element's type belongs to.
EXPECTED = {
    "Motivation": 14,
    "Strategy": 6,
    "Business": 14,
    "Application": 8,
    "Technology": 6,
    "Implementation & Migration": 7,
    "Composite and other elements": 2,
}


def _shot(page, name, viewport, scheme):
    """Save a screenshot when SMOKE_WALK_DIR names a folder; the journey asserts either way."""
    walk = os.environ.get("SMOKE_WALK_DIR")
    if walk:
        os.makedirs(walk, exist_ok=True)
        page.screenshot(path=os.path.join(walk, "layercount-%s-%d-%s.png" % (name, viewport["width"], scheme)),
                        full_page=True)


def _seed(label, business_extra=0):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateRelationship
    from app.services.archimate_backbone import create_backbone_element
    from app.services.gap_register_service import create_gap

    org = create_fresh_org("enterprise_architect")
    app = create_app("testing")
    with app.app_context():
        org_id, suffix = org["org_id"], "%s %s" % (label, org["suffix"])
        made = []

        def add(element_type, layer, count, tag):
            for n in range(count):
                made.append(create_backbone_element(element_type=element_type, layer=layer,
                                                    name="%s %02d %s" % (tag, n, suffix), organization_id=org_id))

        add("Goal", "motivation", 10, "Goal")
        add("Goal", "business", 2, "Misfiled goal")  # a goal stored under another layer's name
        add("Principle", None, 2, "Principle")  # no layer stored at all
        add("Capability", "strategy", 6, "Capability")
        add("BusinessProcess", "business", 12, "Process")
        add("BusinessProcess", "application", 2, "Misfiled process")
        add("ApplicationComponent", "application", 8, "Application")
        add("Node", "technology", 4, "Node")
        add("Equipment", "physical", 2, "Equipment")
        add("WorkPackage", "implementation", 4, "Work package")
        add("Work Package", "implementation & migration", 2, "Imported work")
        add("Location", None, 2, "Location")
        create_gap(org_id, "Register gap %s" % suffix)
        for n in range(business_extra):
            create_backbone_element(element_type="BusinessActor", layer="business",
                                    name="Actor %02d %s" % (n, suffix), organization_id=org_id)
        db.session.flush()
        for n in range(len(made) - 1):
            db.session.add(ArchiMateRelationship(type="serving", source_id=made[n].id, target_id=made[n + 1].id,
                                                 organization_id=org_id))
        db.session.commit()
        db.session.remove()
    return org


@pytest.fixture(scope="module")
def orgs():
    return {"mine": _seed("Mine"), "theirs": _seed("Theirs", business_extra=30)}


def _tabs(page):
    """{layer name: count} read from the dashboard's tab strip, once every count has arrived."""
    spine = page.get_by_test_id("layer-spine")
    expect(spine.locator('[data-testid="layer-count-loading"]:visible')).to_have_count(0)
    tabs = {}
    for link in spine.locator("a").all():
        name = link.locator("span.truncate").inner_text().strip()
        tabs[name] = int(link.locator("span.rounded-full:visible").inner_text().strip())
    return tabs


def _plain(text):
    return " ".join(text.split())


def _open(browser, viewport, scheme):
    expect.set_options(timeout=PAGE_TIMEOUT)
    context = browser.new_context(viewport=viewport, color_scheme=scheme, ignore_https_errors=True)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    if scheme == "dark":
        context.add_init_script("document.addEventListener('DOMContentLoaded', () => "
                                "document.documentElement.classList.add('dark'));")
    return context, context.new_page()


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=["1440-light", "390-dark"])
def test_a_tab_says_it_is_counting_while_the_counts_load(browser, live_server, orgs, viewport, scheme):
    context, page = _open(browser, viewport, scheme)
    try:
        sign_in(page, live_server, orgs["mine"]["emails"]["enterprise_architect"])

        # The counts are held back for a moment so the page is seen while they load: it says it is counting,
        # never that the figure is unavailable.
        held = []
        gate = {"open": False}

        def hold(route):
            if gate["open"]:
                route.continue_()
            else:
                held.append(route)

        page.route(re.compile(r"/architecture/api/layer/counts"), hold)
        page.goto(live_server + "/architecture/dashboard?layer=business", wait_until="domcontentloaded")
        header = page.get_by_test_id("layer-count-label")
        expect(page.get_by_test_id("layer-spine").locator('[data-testid="layer-count-loading"]:visible').first).to_be_visible()
        page.wait_for_timeout(1500)
        assert held, "the counts request was not made"
        loading_text = _plain(page.locator("main").inner_text())
        assert "unavailable" not in loading_text.lower(), loading_text[:600]
        assert "counting" in _plain(header.inner_text()).lower()
        _shot(page, "dashboard-loading", viewport, scheme)
        gate["open"] = True
        for route in held:
            route.continue_()
        expect(page.get_by_test_id("layer-count-label")).to_contain_text("14 elements in this layer")
    finally:
        context.close()


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=["1440-light", "390-dark"])
def test_dashboard_tabs_and_composer_cards_show_one_count_per_layer(browser, live_server, orgs, viewport, scheme):
    mine, theirs = orgs["mine"], orgs["theirs"]
    context, page = _open(browser, viewport, scheme)
    try:
        sign_in(page, live_server, mine["emails"]["enterprise_architect"])
        page.goto(live_server + "/architecture/dashboard?layer=business", wait_until="domcontentloaded")

        # Every tab shows its count; they are the seed's, by layer, and Physical is not a tab of its own.
        tabs = _tabs(page)
        assert tabs == EXPECTED, tabs
        assert "Physical" not in tabs
        dashboard_total = sum(tabs.values())
        _shot(page, "dashboard", viewport, scheme)

        # Opening a tab never moves its number: the tab, the page header, the Elements card and the list agree.
        for name, count in EXPECTED.items():
            page.get_by_test_id("layer-spine").get_by_role("link", name=re.compile("^" + re.escape(name))).click()
            expect(page.get_by_test_id("architecture-loading-state")).to_be_hidden()
            word = "element" if count == 1 else "elements"
            expect(page.get_by_test_id("layer-count-label")).to_contain_text("%d %s in this layer" % (count, word))
            expect(page.get_by_test_id("layer-list-total")).to_have_text("%d total" % count)
            assert _tabs(page) == EXPECTED, "a tab's count moved when %s was opened" % name
        assert theirs["suffix"] not in page.locator("main").inner_text()

        # Equipment is Technology's: its address opens the Technology tab and its two rows are listed.
        page.goto(live_server + "/architecture/dashboard?layer=technology", wait_until="domcontentloaded")
        expect(page.locator('[data-field="element-name"]:visible').filter(has_text="Equipment")).to_have_count(2)

        # The button on the dashboard opens the Composer on the same model.
        page.goto(live_server + "/architecture/dashboard?layer=business", wait_until="domcontentloaded")
        _tabs(page)
        link = page.get_by_test_id("btn-open-composer")
        expect(link).to_be_visible()
        link.click()
        page.wait_for_url(re.compile(r"/archimate/composer\?.*viewpoint=layered"))
        buttons = page.get_by_test_id("composer-layer-buttons")
        expect(buttons).to_be_visible()
        expect(buttons.locator("button")).to_have_count(len(EXPECTED))
        cards = {}
        for card in buttons.locator("button").all():
            title = card.locator("span").nth(0).inner_text().strip()
            cards[title] = int(card.locator("span").nth(1).inner_text().strip())
        _shot(page, "composer", viewport, scheme)

        # One count per layer, and one total, on both screens.
        assert cards == tabs, {"composer": cards, "dashboard": tabs}
        assert sum(cards.values()) == dashboard_total

        # A card opens its layer, and the layer says how many it holds: the count on its card.
        page.get_by_test_id("composer-layer-business").click()
        expect(page.get_by_test_id("composer-all-layers")).to_be_visible()
        expect(page.locator("body")).to_contain_text(re.compile(r"— %d elements, " % EXPECTED["Business"]))
        _shot(page, "composer-business", viewport, scheme)
        assert theirs["suffix"] not in page.locator("body").inner_text()
    finally:
        context.close()

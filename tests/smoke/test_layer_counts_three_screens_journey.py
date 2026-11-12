"""An architect reads the same count for every layer on the dashboard, in the Composer and on the model map.

The person: an enterprise architect whose organisation holds linked elements stored under layer names their
type does not belong to (a goal filed under Business, a process under Application), a type spelt as a model
file wrote it ("Work Package"), two pieces of equipment, two locations and a gap entered in the gap register.
A second organisation holds a larger model of its own.

The goal: for each layer the dashboard tab, the Composer card and the model map's button show one figure (the
map draws model elements only, so it is the tab's figure less the records the page says are not on the map),
Physical is no layer of its own on any of them, and each map button opens a map with exactly that many elements.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import sign_in
from .test_layer_counts_agree_journey import EXPECTED, VIEWPORTS, _open, _shot, _tabs, orgs  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# The dashboard's name for each layer, and the key the model map files it under.
MAP_KEY = {
    "Motivation": "motivation", "Strategy": "strategy", "Business": "business", "Application": "application",
    "Technology": "technology", "Implementation & Migration": "implementation",
    "Composite and other elements": "other",
}


def _number(text):
    return int(re.search(r"\d+", text).group(0))


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=["1440-light", "390-dark"])
def test_every_layer_has_one_count_on_the_dashboard_the_composer_and_the_model_map(
        browser, live_server, orgs, viewport, scheme):  # noqa: F811
    mine, theirs = orgs["mine"], orgs["theirs"]
    context, page = _open(browser, viewport, scheme)
    try:
        sign_in(page, live_server, mine["emails"]["enterprise_architect"])

        # Screen one: the dashboard's tabs.
        page.goto(live_server + "/architecture/dashboard?layer=business", wait_until="domcontentloaded")
        tabs = _tabs(page)
        assert tabs == EXPECTED, tabs

        # What the dashboard says is not on the model map, layer by layer, read from each layer's own header.
        off_map = {}
        for name, count in EXPECTED.items():
            page.get_by_test_id("layer-spine").get_by_role("link", name=re.compile("^" + re.escape(name))).click()
            expect(page.get_by_test_id("architecture-loading-state")).to_be_hidden()
            header = page.get_by_test_id("layer-count-label")
            expect(header).to_contain_text("%d element" % count)
            said = re.search(r"(\d+) of them not on the model map", header.inner_text())
            off_map[name] = int(said.group(1)) if said else 0

        # Screen two: the Composer's layer cards.
        page.goto(live_server + "/architecture/dashboard?layer=business", wait_until="domcontentloaded")
        _tabs(page)
        page.get_by_test_id("btn-open-composer").click()
        page.wait_for_url(re.compile(r"/archimate/composer\?.*viewpoint=layered"))
        buttons = page.get_by_test_id("composer-layer-buttons")
        expect(buttons).to_be_visible()
        cards = {}
        for card in buttons.locator("button").all():
            cards[card.locator("span").nth(0).inner_text().strip()] = _number(card.locator("span").nth(1).inner_text())
        assert cards == tabs, {"composer": cards, "dashboard": tabs}

        # Screen three: the model map's buttons, one for each layer that holds elements.
        page.goto(live_server + "/archimate/model-map", wait_until="domcontentloaded")
        group = page.get_by_role("group", name="Show one layer")
        expect(group).to_be_visible()
        on_map = {}
        for name, key in MAP_KEY.items():
            button = page.get_by_test_id("model-map-layer-" + key)
            wanted = EXPECTED[name] - off_map[name]
            if wanted:
                expect(button).to_contain_text("(%d)" % wanted)
                on_map[key] = _number(button.inner_text().split("(")[-1])
            else:
                expect(button).to_have_count(0)
        # Physical is Technology's on the map as it is on the other two: no button, and its two pieces of
        # equipment are inside Technology's figure.
        expect(page.get_by_test_id("model-map-layer-physical")).to_have_count(0)
        assert on_map["technology"] == EXPECTED["Technology"] == 6
        assert on_map["implementation"] == EXPECTED["Implementation & Migration"] - 1
        for name, key in MAP_KEY.items():
            assert on_map.get(key, 0) + off_map[name] == tabs[name] == cards[name], (name, on_map, off_map)
        assert sum(on_map.values()) + sum(off_map.values()) == sum(tabs.values()) == sum(cards.values())
        _shot(page, "three-screens-model-map", viewport, scheme)

        # Each button opens the map of that layer with exactly its elements drawn.
        for key, count in on_map.items():
            page.goto(live_server + "/archimate/model-map?layer=" + key, wait_until="domcontentloaded")
            page.locator("#impact-svg[data-rendered-ms]").wait_for(state="attached")
            assert int(page.locator("#impact-svg").get_attribute("data-node-count")) == count, key
        assert theirs["suffix"] not in page.locator("body").inner_text()
    finally:
        context.close()

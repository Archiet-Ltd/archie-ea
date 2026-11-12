"""An architect reads the same figure for every layer on the dashboard and on the model map.

The person: an enterprise architect whose organisation holds linked elements in every layer, a gap entered in the
gap register (Implementation and migration) and two goals entered in the motivation register that no model element
stands for. A second organisation holds a larger model of its own.

The goal: on the model map each layer button, its legend figure and the total read the dashboard's figure for that
layer, as drawn of total where the register holds elements the map cannot draw; the map says once, by layer, how
many are not drawn, and each layer named there opens that layer's tab on the dashboard, which says the same number.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import sign_in
from .test_layer_counts_agree_journey import VIEWPORTS, _open, _seed, _shot, _tabs
from .test_layer_counts_three_screens_journey import MAP_KEY

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

UNDRAWN_GOALS = 2


@pytest.fixture(scope="module")
def orgs():
    from app import create_app, db
    from app.models.motivation import Goal

    mine, theirs = _seed("Mine"), _seed("Theirs", business_extra=30)
    app = create_app("testing")
    with app.app_context():
        for n in range(UNDRAWN_GOALS):
            db.session.add(Goal(name="Register goal %d %s" % (n, mine["suffix"]), organization_id=mine["org_id"]))
        db.session.commit()
        db.session.remove()
    return {"mine": mine, "theirs": theirs}


def _figure(text):
    """(drawn, total) from 'Label (96)' or 'Label (96 of 104)'."""
    found = re.search(r"\((\d+)(?: of (\d+))?\)", text)
    drawn = int(found.group(1))
    return drawn, int(found.group(2)) if found.group(2) else drawn


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=["1440-light", "390-dark"])
def test_the_model_map_reads_the_dashboards_figure_for_every_layer(browser, live_server, orgs, viewport, scheme):
    mine, theirs = orgs["mine"], orgs["theirs"]
    context, page = _open(browser, viewport, scheme)
    try:
        sign_in(page, live_server, mine["emails"]["enterprise_architect"])

        # The dashboard: every layer's figure and what it says is not on the model map.
        page.goto(live_server + "/architecture/dashboard?layer=business", wait_until="domcontentloaded")
        tabs = _tabs(page)
        not_on_map = {}
        for name, count in tabs.items():
            page.get_by_test_id("layer-spine").get_by_role("link", name=re.compile("^" + re.escape(name))).click()
            expect(page.get_by_test_id("architecture-loading-state")).to_be_hidden()
            header = page.get_by_test_id("layer-count-label")
            expect(header).to_contain_text("%d element" % count)
            said = re.search(r"(\d+) of them not on the model map", header.inner_text())
            not_on_map[name] = int(said.group(1)) if said else 0
        assert not_on_map["Motivation"] == UNDRAWN_GOALS and not_on_map["Implementation & Migration"] == 1, not_on_map

        # The map: each layer control reads the dashboard's figure, drawn of total where they differ.
        page.goto(live_server + "/archimate/model-map", wait_until="domcontentloaded")
        expect(page.get_by_role("group", name="Show one layer")).to_be_visible()
        for name, key in MAP_KEY.items():
            drawn = tabs[name] - not_on_map[name]
            button = page.get_by_test_id("model-map-layer-" + key)
            legend = page.get_by_test_id("model-map-count-" + key)
            if not drawn:
                expect(button).to_have_count(0)
                continue
            expected = "%d of %d" % (drawn, tabs[name]) if not_on_map[name] else str(drawn)
            expect(legend).to_have_text(expected)
            assert _figure(button.inner_text()) == (drawn, tabs[name]), (name, button.inner_text())
        expect(page.get_by_test_id("model-map-counts")).to_have_text(
            re.compile(r"^%d of %d elements" % (sum(tabs.values()) - sum(not_on_map.values()), sum(tabs.values()))))

        # The map says once how many are not drawn, by layer, and each layer named opens its dashboard tab.
        note = page.get_by_test_id("model-map-not-drawn")
        expect(note).to_contain_text("%d elements entered in a register are not drawn" % sum(not_on_map.values()))
        _shot(page, "model-map-figures", viewport, scheme)
        said = {name: n for name, n in not_on_map.items() if n}
        for name, n in said.items():
            row = page.get_by_test_id("model-map-not-drawn-" + MAP_KEY[name])
            expect(row).to_have_text(re.compile(r": %d$" % n))
        for name, n in said.items():
            page.goto(live_server + "/archimate/model-map", wait_until="domcontentloaded")
            page.get_by_test_id("model-map-not-drawn-" + MAP_KEY[name]).get_by_role("link").click()
            page.wait_for_url(re.compile(r"/architecture/(dashboard)?\?layer=" + MAP_KEY[name]), wait_until="domcontentloaded")
            expect(page.get_by_test_id("architecture-loading-state")).to_be_hidden()
            expect(page.get_by_test_id("layer-count-label")).to_contain_text(
                "%d elements in this layer, %d of them not on the model map" % (tabs[name], n))

        # A layer opened on the map says its own figure too.
        page.goto(live_server + "/archimate/model-map?layer=motivation", wait_until="domcontentloaded")
        expect(page.get_by_test_id("model-map-counts")).to_have_text(
            re.compile(r"^%d of %d elements" % (tabs["Motivation"] - UNDRAWN_GOALS, tabs["Motivation"])))
        assert theirs["suffix"] not in page.locator("body").inner_text()
    finally:
        context.close()

"""A signed-in architect compares real saved scenarios, reads why a change has no effect, and finds the simulation first.

In the Lantern Quay Systems demonstration organisation, as its architect:
- opens /simulations and finds the simulation first; the prioritised actions are not repeated there, a link
  leads to the Impact Analysis page that ranks them, and that page lists them;
- opens Compare Scenarios without saving anything and finds the two scenarios the demonstration keeps,
  picks both and reads their figures side by side;
- runs a minor modification on the ledger and reads one sentence saying why none of the figures moves;
- saves that run under a name, opens Compare Scenarios and finds three scenarios to compare.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_simulation_journey import VIEWPORT_IDS, VIEWPORTS, _pick, _shot, _sign_in, demo  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SEEDED = ("Retire Calibration Ledger", "Replace Calibration Ledger with a managed service")
SAVED_NAME = "Minor change to the ledger"


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_the_page_opens_on_the_simulation_and_links_to_the_ranked_actions(browser, live_server, demo, viewport, scheme):
    context, page = _sign_in(browser, live_server, viewport, scheme)
    try:
        page.goto(live_server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", level=1)).to_have_text("Scenarios")
        # the list of prioritised actions is not drawn again on this page
        assert page.get_by_test_id("priority-actions").count() == 0
        assert page.get_by_test_id("priority-action-row").count() == 0
        # the first section after the header is the one that selects an element to simulate
        first = page.locator("main h2, main h3").first
        expect(first).to_have_text("Select Element")
        _shot(page, "scenarios-opening", viewport, scheme)

        # the link lands on the page that ranks the actions, and that page lists them
        link = page.get_by_test_id("priority-actions-link")
        expect(link).to_be_visible()
        link.click()
        page.wait_for_url(re.compile(r"/enterprise/analysis/impact-analysis"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("priority-actions-list")).to_be_visible()
        assert page.get_by_test_id("priority-action-row").count() >= 1
    finally:
        context.close()


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_compare_scenarios_holds_the_demonstration_scenarios_and_one_a_person_saves(
        browser, live_server, app, demo, viewport, scheme):
    context, page = _sign_in(browser, live_server, viewport, scheme)
    try:
        # two scenarios are already saved: the dialog lists both and compares them with their figures
        page.goto(live_server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Compare Scenarios").click()
        compare = page.get_by_role("dialog", name="Compare Impact Scenarios")
        expect(compare).to_be_visible()
        expect(compare.get_by_text("No Saved Scenarios")).to_be_hidden()
        for name in SEEDED:
            expect(compare.get_by_text(name).first).to_be_visible()
            compare.get_by_text(name).first.click()
        expect(compare.get_by_text("Decommission / Retirement").first).to_be_visible()
        expect(compare.get_by_text("Full Replacement").first).to_be_visible()
        head = compare.get_by_role("columnheader")
        expect(head).to_have_count(3)
        for label in ("Affected Capabilities", "Affected Processes", "Financial Risk", "Direct Dependencies"):
            row = compare.get_by_role("row").filter(has_text=label)
            expect(row).to_have_count(1)
            cells = [c.strip() for c in row.get_by_role("cell").all_inner_texts()]
            assert len(cells) == 3 and all(c and c not in ("—", "-") for c in cells[1:]), (label, cells)
        _shot(page, "scenarios-compare-seeded", viewport, scheme)
        page.keyboard.press("Escape")

        # a minor change that moves nothing says why in one sentence
        page.goto(live_server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _pick(page, change="Minor Modification")
        page.get_by_role("button", name="Run scenario").click()
        expect(page.get_by_test_id("simulation-panel")).to_be_visible(timeout=PAGE_TIMEOUT)
        sentence = page.get_by_test_id("no-effect")
        expect(sentence).to_be_visible()
        text = sentence.inner_text()
        assert text.startswith("This change moves none of these figures:"), text
        assert "no element is added or removed" in text and "no link is added or removed" in text, text
        _shot(page, "scenarios-no-effect", viewport, scheme)
        walk = os.environ.get("SIMULATION_WALK_DIR")
        if walk:
            page.get_by_test_id("simulation-panel").screenshot(
                path=os.path.join(walk, "scenarios-no-effect-panel-%d-%s.png" % (viewport["width"], scheme)))

        # saving it adds a third scenario to the comparison
        page.get_by_role("button", name="Save Scenario for Comparison", exact=True).click()
        dialog = page.get_by_role("dialog", name="Save scenario")
        dialog.get_by_label("Scenario name").fill(SAVED_NAME)
        dialog.get_by_role("button", name="Save scenario").click()
        expect(dialog).to_be_hidden()
        page.reload(wait_until="domcontentloaded")
        page.get_by_role("button", name="Compare Scenarios").click()
        compare = page.get_by_role("dialog", name="Compare Impact Scenarios")
        for name in SEEDED + (SAVED_NAME,):
            expect(compare.get_by_text(name).first).to_be_visible()
            compare.get_by_text(name).first.click()
        expect(compare.get_by_role("columnheader")).to_have_count(4)
        expect(compare.get_by_text("Minor Modification").first).to_be_visible()
        _shot(page, "scenarios-compare-three", viewport, scheme)
    finally:
        context.close()
        # the scenario belongs to the organisation, so the journey removes the one it saved, by name
        from app import db
        from app.models.saved_scenario import SavedScenario

        with app.app_context():
            SavedScenario.query.filter(SavedScenario.name == SAVED_NAME).delete(synchronize_session=False)
            db.session.commit()
            db.session.remove()

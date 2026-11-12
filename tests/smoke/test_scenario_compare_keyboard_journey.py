"""A person using only the keyboard picks scenarios to compare and reads a comparison that explains itself.

In the Lantern Quay Systems demonstration organisation, as its architect:
- opens Compare Scenarios with the keyboard; Tab never leaves the open dialog, however many times it is pressed,
  and the order it visits is recorded;
- reaches each saved scenario's selector with Tab and selects two with Space and Enter; the comparison appears;
- every saved scenario shows the day it was saved, and the comparison repeats the day in its own row;
- a scenario that moves none of the figures carries, in the comparison, the very sentence its own page gives;
- Escape closes the dialog and focus returns to the button that opened it;
- on a phone the selector and the scenario's name do not overlap.
"""
import os

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_simulation_journey import VIEWPORT_IDS, VIEWPORTS, _pick, _shot, _sign_in, demo  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SEEDED = "Retire Calibration Ledger"
SAVED_NAME = "Keyboard walk minor change"
TAB_LIMIT = 60

ACTIVE = """() => {
    const a = document.activeElement;
    const dlg = document.querySelector('[role="dialog"][aria-labelledby="impact-scenario-comparison-title"]');
    return {
        inside: !!(dlg && a && dlg.contains(a)),
        label: (a && (a.getAttribute('aria-label') || (a.innerText || '').trim().slice(0, 40) || a.tagName)) || '',
    };
}"""


def _tab_until(page, label, order):
    """Tab until the control named *label* has focus, recording every stop and checking each stays in the dialog."""
    for _ in range(TAB_LIMIT):
        page.keyboard.press("Tab")
        state = page.evaluate(ACTIVE)
        order.append(state["label"])
        assert state["inside"], ("focus left the open dialog", order)
        if state["label"] == label:
            return
    pytest.fail("Tab never reached %r; it visited %r" % (label, order))


def _save_minor_change(page, live_server):
    page.goto(live_server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _pick(page, change="Minor Modification")
    page.get_by_role("button", name="Run scenario").click()
    expect(page.get_by_test_id("simulation-panel")).to_be_visible(timeout=PAGE_TIMEOUT)
    own_page_sentence = page.get_by_test_id("no-effect").inner_text().strip()
    page.get_by_role("button", name="Save Scenario for Comparison", exact=True).click()
    dialog = page.get_by_role("dialog", name="Save scenario")
    dialog.get_by_label("Scenario name").fill(SAVED_NAME)
    dialog.get_by_role("button", name="Save scenario").click()
    expect(dialog).to_be_hidden()
    return own_page_sentence


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_a_keyboard_user_selects_two_scenarios_and_reads_why_one_moves_nothing(
        browser, live_server, app, demo, viewport, scheme):
    context, page = _sign_in(browser, live_server, viewport, scheme)
    try:
        own_page_sentence = _save_minor_change(page, live_server)
        assert own_page_sentence.startswith("This change moves none of these figures:"), own_page_sentence

        page.goto(live_server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        opener = page.get_by_role("button", name="Compare Scenarios")
        opener.focus()
        page.keyboard.press("Enter")
        dialog = page.get_by_role("dialog", name="Compare Impact Scenarios")
        expect(dialog).to_be_visible()
        cards = dialog.get_by_test_id("scenario-select")
        expect(cards.first).to_be_visible()
        count = cards.count()
        assert count >= 3, count

        # Tab never leaves the dialog: more presses than there are controls, forwards and back
        order = [page.evaluate(ACTIVE)["label"]]
        for _ in range(TAB_LIMIT):
            page.keyboard.press("Tab")
            state = page.evaluate(ACTIVE)
            order.append(state["label"])
            assert state["inside"], ("focus left the open dialog going forward", order)
        for _ in range(TAB_LIMIT):
            page.keyboard.press("Shift+Tab")
            state = page.evaluate(ACTIVE)
            assert state["inside"], ("focus left the open dialog going back", order)
        assert any(label.startswith("Compare ") for label in order), order

        # every saved scenario says when it was saved
        saved_lines = dialog.get_by_test_id("scenario-saved-on")
        assert saved_lines.count() == count
        for line in saved_lines.all_inner_texts():
            assert line.startswith("Saved ") and "20" in line and "not recorded" not in line, line

        # bounding boxes: the selector and the scenario's name never overlap
        for index in range(count):
            card = cards.nth(index)
            title = card.locator("xpath=following-sibling::div[1]")
            a, b = card.bounding_box(), title.bounding_box()
            apart = a["x"] + a["width"] <= b["x"] + 0.5 or b["x"] + b["width"] <= a["x"] + 0.5 \
                or a["y"] + a["height"] <= b["y"] + 0.5 or b["y"] + b["height"] <= a["y"] + 0.5
            assert apart, ("selector overlaps the name", a, b)

        # select two with the keyboard alone: Space on one, Enter on the other
        retire = "Compare " + SEEDED
        saved = "Compare " + SAVED_NAME
        page.evaluate("() => document.querySelector('#impact-scenario-comparison-modal [aria-label=\"Close scenario comparison dialog\"]').focus()")
        tab_order = []
        _tab_until(page, saved, tab_order)
        expect(page.locator(":focus")).to_have_attribute("aria-pressed", "false")
        page.keyboard.press("Space")
        expect(page.locator(":focus")).to_have_attribute("aria-pressed", "true")
        _tab_until(page, retire, tab_order)
        page.keyboard.press("Enter")
        expect(page.locator(":focus")).to_have_attribute("aria-pressed", "true")

        expect(dialog.get_by_role("columnheader")).to_have_count(3)
        head = " ".join(dialog.get_by_role("columnheader").all_inner_texts())
        assert SAVED_NAME in head and SEEDED in head, head
        dialog.get_by_test_id("compare-saved-row").scroll_into_view_if_needed()
        saved_row = [c.strip() for c in dialog.get_by_test_id("compare-saved-row").get_by_role("cell").all_inner_texts()]
        assert len(saved_row) == 3 and all(c and c != "Not recorded" for c in saved_row[1:]), saved_row

        # the no-effect scenario says why, in the sentence its own page gave
        cells = [c.strip() for c in dialog.get_by_test_id("compare-no-effect").all_inner_texts()]
        assert own_page_sentence in cells, (own_page_sentence, cells)
        assert any(c == "This change moves the figures above." for c in cells), cells
        print("TAB ORDER", viewport["width"], scheme, tab_order)
        _shot(page, "keyboard-compare-two-selected", viewport, scheme)
        walk = os.environ.get("SIMULATION_WALK_DIR")
        if walk:
            os.makedirs(walk, exist_ok=True)
            page.screenshot(path=os.path.join(walk, "keyboard-compare-two-selected-view-%d-%s.png" % (viewport["width"], scheme)))

        # Escape closes it and focus is back on the button that opened it
        page.keyboard.press("Escape")
        expect(dialog).to_be_hidden()
        expect(opener).to_be_focused()
    finally:
        context.close()
        from app import db
        from app.models.saved_scenario import SavedScenario

        with app.app_context():
            SavedScenario.query.filter(SavedScenario.name == SAVED_NAME).delete(synchronize_session=False)
            db.session.commit()
            db.session.remove()

"""The portfolio Kanban reads like the gallery kanban: each column is a lane,
cards are compact with a drag handle and an actions menu, epics are shown by
name and never by number, and the demonstration carries a person who may look
but not change anything.

Signed in as the demonstration's Enterprise Architect (moves epics) and as its
Business Analyst on the Viewer role (reads only)."""
import os
import pathlib
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import sign_in
from .test_portfolio_kanban_journey import _drag_card, _move_by_menu
from .test_seeded_boards_journey import DEMO_EMAIL, demo_org  # noqa: F401  (fixture)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

READER_EMAIL = "drew.dataanalyst@lantern-quay.example.com"
BOARD = "/safe/portfolio/"
WALK = pathlib.Path(os.environ.get("KANBAN_WALK_DIR", "/tmp/kanban-walk"))
# The gallery kanban's cards are about 110px tall; ours may carry one more line.
MAX_CARD_HEIGHT = 150


def _persona(email):
    from app import create_app
    from app.models.user import User

    with create_app("testing").app_context():
        user = User.query.filter_by(email=email).one()
        return user.enterprise_role, user.role.name if user.role else None


def _open(browser, live_server, email, width=1440, scheme="light"):
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    page = context.new_page()
    sign_in(page, live_server, email)
    page.goto(live_server + BOARD, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.wait_for_selector(".kanban-card", timeout=PAGE_TIMEOUT)
    return context, page


def _state_of(page, name):
    return page.locator(".kanban-column").filter(
        has=page.get_by_role("heading", name=name, exact=True)).get_attribute("data-state")


def test_architect_sees_lanes_and_compact_cards_with_names(browser, live_server, demo_org):
    persona, platform = _persona(DEMO_EMAIL)
    assert persona == "enterprise_architect" and platform != "Administrator", (persona, platform)
    context, page = _open(browser, live_server, DEMO_EMAIL)
    try:
        # Each column is a lane: a bordered container on the muted surface.
        for state in ("Funnel", "Reviewing", "Analyzing", "Portfolio Backlog", "Implementing", "Done"):
            lane = page.locator(".kanban-column[data-state='%s']" % state)
            style = lane.evaluate("el => { const c = getComputedStyle(el);"
                                  " return [c.borderTopWidth, c.backgroundColor]; }")
            assert style[0] != "0px", "%s is not a bordered lane" % state
            assert style[1] != "rgba(0, 0, 0, 0)", "%s has no lane surface" % state

        cards = page.locator(".kanban-card")
        assert cards.count() >= 12
        for i in range(min(cards.count(), 12)):
            card = cards.nth(i)
            height = card.bounding_box()["height"]
            assert height <= MAX_CARD_HEIGHT, "card %d is %dpx tall" % (i, height)
            # The actions menu replaces the full-width select.
            assert card.locator("select").count() == 0
            expect(card.get_by_role("button", name=re.compile(r"^Move .+ to a different column$"))).to_be_visible()
            expect(card.locator("[data-drag-handle]")).to_be_visible()
            # An epic is shown by name, never by its number.
            lines = [l.strip() for l in card.inner_text().splitlines() if l.strip()]
            assert not any(re.fullmatch(r"#?\d+", l) for l in lines), lines
        # Menu opens, names every column, and moving from it persists.
        funnel_top = page.locator(".kanban-column[data-state='Funnel'] .kanban-card").first
        name = funnel_top.locator("h3").inner_text().strip()
        assert not name.startswith("Walk epic"), "the Funnel's top card is a leftover walk epic"
        funnel_top.get_by_role("button", name="Move %s to a different column" % name).click()
        for state in ("Funnel", "Reviewing", "Analyzing", "Portfolio Backlog", "Implementing", "Done"):
            expect(funnel_top.get_by_role("menuitem", name="Move to %s" % state)).to_be_visible()
        WALK.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(WALK / "board-menu-1440-light.png"))
    finally:
        context.close()


def test_architect_drags_an_epic_with_the_mouse_and_it_persists(browser, live_server, demo_org):
    context, page = _open(browser, live_server, DEMO_EMAIL)
    try:
        card = page.locator(".kanban-column[data-state='Funnel'] .kanban-card").first
        name = card.locator("h3").inner_text().strip()
        with page.expect_response(lambda r: r.request.method == "PATCH" and "/api/safe/portfolio/" in r.url) as moved:
            _drag_card(page, card, "Reviewing")
        assert moved.value.status == 200
        expect(page.locator(".kanban-column[data-state='Reviewing']").get_by_role(
            "heading", name=name, exact=True)).to_be_visible(timeout=PAGE_TIMEOUT)
        page.reload(timeout=PAGE_TIMEOUT)
        page.wait_for_selector(".kanban-card", timeout=PAGE_TIMEOUT)
        assert _state_of(page, name) == "Reviewing"
        # Put it back through the card's own menu so the shared demo stays as seeded.
        back = page.locator(".kanban-column[data-state='Reviewing'] .kanban-card").filter(has_text=name)
        with page.expect_response(lambda r: r.request.method == "PATCH" and "/api/safe/portfolio/" in r.url):
            _move_by_menu(back, name, "Funnel")
        page.reload(timeout=PAGE_TIMEOUT)
        page.wait_for_selector(".kanban-card", timeout=PAGE_TIMEOUT)
        assert _state_of(page, name) == "Funnel"
    finally:
        context.close()


def test_demo_reader_sees_the_board_and_cannot_change_it(browser, live_server, demo_org):
    persona, platform = _persona(READER_EMAIL)
    assert persona == "business_analyst" and platform == "Viewer", (persona, platform)
    context, page = _open(browser, live_server, READER_EMAIL)
    try:
        expect(page.get_by_test_id("board-read-only")).to_contain_text("You can view this board")
        assert page.locator(".kanban-card").count() >= 12
        assert page.get_by_role("button", name="Add epic").count() == 0
        assert page.locator("[data-testid='card-actions']").count() == 0
        assert page.locator("[data-drag-handle]").count() == 0
        card = page.locator(".kanban-column[data-state='Funnel'] .kanban-card").first
        assert card.get_attribute("draggable") == "false"
        card_id = int(card.get_attribute("data-card-id"))
        answer = page.evaluate("""async (id) => {
            const token = document.querySelector('meta[name="csrf-token"]')?.content || '';
            const r = await fetch('/api/safe/portfolio/' + id, {method: 'PATCH',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': token},
                body: JSON.stringify({kanban_state: 'Reviewing'})});
            return r.status;
        }""", card_id)
        assert answer == 403
    finally:
        context.close()


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (1440, "dark"), (390, "light"), (390, "dark")])
def test_board_walk_screenshots(browser, live_server, demo_org, width, scheme):
    context, page = _open(browser, live_server, DEMO_EMAIL, width, scheme)
    try:
        WALK.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(WALK / ("board-%d-%s.png" % (width, scheme))), full_page=True)
        assert page.locator(".kanban-column").count() == 6
    finally:
        context.close()

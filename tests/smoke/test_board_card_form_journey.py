"""The shared board keeps its lanes wide and its cards small.

An enterprise architect opens the portfolio board (the demonstration's linked
epics), the backlog and the product roadmap at 1440 wide. Every lane is at
least 280px and never shrinks to fit, the board scrolls sideways inside itself,
a long title wraps at word boundaries to at most two lines, the chips share one
row, and no card is taller than 120px.
"""
import os
import pathlib
import uuid

import pytest

from .conftest import PAGE_TIMEOUT, _seed_standard_org
from .test_archetype_journeys import _login
from .test_one_board_journey import (
    BACKLOG, ROADMAP, _assert_persona, _open, _seed_epics, _seed_requirements, _wait_cards,
)
from .test_portfolio_board_lanes_journey import BOARD, DEMO_EMAIL, _open as _open_portfolio, _persona
from .test_portfolio_kanban_enterprise_architect import _make_enterprise_architect
from .test_seeded_boards_journey import demo_org  # noqa: F401  (fixture)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = pathlib.Path(os.environ.get("KANBAN_WALK_DIR", "/tmp/kanban-walk"))
MIN_LANE = 280
MAX_CARD = 120
LONG_TITLE = "Consolidate the regional settlement reconciliation and exception handling workflow %s"


@pytest.fixture
def own_org(seeded, request):
    return _seed_standard_org(request, None)["ids"]["org"]


def _assert_form(page):
    lanes = page.locator(".kanban-column")
    assert lanes.count() >= 3
    for i in range(lanes.count()):
        width = lanes.nth(i).bounding_box()["width"]
        assert width >= MIN_LANE - 0.5, "lane %d is %.0fpx wide" % (i, width)
    scroll = page.locator("[data-kanban-scroll]").first
    assert scroll.evaluate("el => el.scrollWidth > el.clientWidth"), "the board does not scroll inside itself"
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1"), "the page scrolls sideways"
    cards = page.locator(".kanban-card")
    assert cards.count() >= 3
    for i in range(min(cards.count(), 24)):
        card = cards.nth(i)
        height = card.bounding_box()["height"]
        assert height <= MAX_CARD, "card %d is %.0fpx tall" % (i, height)
        title = card.locator("h3").first
        wrap = title.evaluate("el => { const s = getComputedStyle(el);"
                              " return [s.overflowWrap, s.wordBreak, el.getBoundingClientRect().height,"
                              " parseFloat(s.lineHeight)]; }")
        assert wrap[0] == "normal" and wrap[1] == "normal", wrap
        assert wrap[2] <= 2 * wrap[3] + 1, "title runs to more than two lines: %s" % wrap
        tops = card.locator("[data-slot=badge]").evaluate_all(
            "els => els.filter(e => e.offsetParent).map(e => Math.round(e.getBoundingClientRect().top))")
        assert len(set(tops)) <= 1, "chips sit on more than one row: %s" % tops


def test_portfolio_board_lanes_and_cards_keep_their_form(browser, live_server, demo_org):
    persona, platform = _persona(DEMO_EMAIL)
    assert persona == "enterprise_architect" and platform != "Administrator", (persona, platform)
    context, page = _open_portfolio(browser, live_server, DEMO_EMAIL)
    try:
        # At least one card carries all three chips (WSJF, stage, Top WSJF).
        assert page.get_by_text("Top WSJF").first.is_visible()
        _assert_form(page)
    finally:
        context.close()


def test_backlog_lanes_and_cards_keep_their_form(browser, live_server, own_org):
    tag = uuid.uuid4().hex[:6]
    _seed_requirements(own_org, [LONG_TITLE % tag, "Archive ledgers %s" % tag, "Export invoices %s" % tag])
    email = _make_enterprise_architect(own_org)
    _assert_persona(email, "enterprise_architect")
    context, page = _open(browser, live_server, email, BACKLOG, "Backlog Prioritisation")
    try:
        _wait_cards(page)
        _assert_form(page)
    finally:
        context.close()


def test_roadmap_lanes_and_cards_keep_their_form(browser, live_server, own_org):
    tag = uuid.uuid4().hex[:6]
    _seed_epics(own_org, [(LONG_TITLE % tag, "now"), ("Epic B %s" % tag, None), ("Epic C %s" % tag, "next")])
    email = _make_enterprise_architect(own_org)
    _assert_persona(email, "enterprise_architect")
    context, page = _open(browser, live_server, email, ROADMAP, "Product Roadmap")
    try:
        _wait_cards(page)
        _assert_form(page)
    finally:
        context.close()


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (1440, "dark"), (390, "light"), (390, "dark")])
@pytest.mark.parametrize("path,heading,name", [(BACKLOG, "Backlog Prioritisation", "backlog"),
                                               (ROADMAP, "Product Roadmap", "roadmap")])
def test_boards_walk_screenshots(browser, live_server, own_org, path, heading, name, width, scheme):
    tag = uuid.uuid4().hex[:6]
    _seed_requirements(own_org, [LONG_TITLE % tag, "Archive ledgers %s" % tag])
    _seed_epics(own_org, [(LONG_TITLE % tag, "now"), ("Epic B %s" % tag, "next")])
    email = _make_enterprise_architect(own_org)
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    page = context.new_page()
    try:
        _login(page, live_server, email)
        page.goto(live_server + path, timeout=PAGE_TIMEOUT)
        _wait_cards(page)
        page.wait_for_load_state("networkidle")
        WALK.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(WALK / ("%s-%d-%s.png" % (name, width, scheme))), full_page=True)
    finally:
        context.close()

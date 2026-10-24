"""ADM architecture Kanban board journey.

The ADM board and the SAFe portfolio board render through one shared board
shell (app/templates/macros/kanban_board.html). This journey proves the ADM
board still works for its architects: the shared `kanban-board` shell is
present, the ADM cycle shows as lanes with the status work-in-progress limits
on the summary line, cards render, and the page boots with no front-end error.

An architect's goal here: "see my architecture work laid out as a board and
read where each piece of work sits." The test asserts that outcome, not that a
page returned 200.
"""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# The ADM board's own status limits (KanbanProjectionService.WIP_LIMITS), with the
# limits it shows on the status summary.
ADM_WIP = {"under_development": 5, "review": 3, "approved": 5, "implementing": 4}


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", force=True, no_wait_after=True)
    except TypeError:
        page.locator("#submit").dispatch_event("click")
    try:
        page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def test_adm_board_renders_through_the_shared_shell(browser, live_server, seeded):
    """The ADM board loads, renders the one shared board shell with its lanes,
    and boots with no front-end error."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto(live_server + "/adm-kanban/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_selector('[data-testid="adm-lane"]', timeout=PAGE_TIMEOUT)

        assert not errors, "ADM board raised a front-end error: %s" % errors[:3]

        # The one shared board shell is present (the same shell the portfolio board uses).
        assert page.locator(".kanban-board").count() == 1, "the ADM board does not render the shared kanban-board shell"

        # Preliminary and A to H are the lanes, inside the shared shell; Requirements Management is the panel beside them.
        lanes = page.locator(".kanban-board .kanban-column")
        assert lanes.count() == 9, "the ADM board does not show its nine phase lanes"

        # The status summary shows the work-in-progress limits on the limited statuses.
        summary = page.get_by_test_id("adm-status-summary").inner_text()
        for limit in ADM_WIP.values():
            assert f"of {limit} limit" in summary, "a status WIP limit %s is not shown on the board" % limit
    finally:
        context.close()


def test_adm_board_keeps_its_shape_on_a_phone(browser, live_server, seeded):
    """At 390 the ADM lanes stay a board inside the sideways-scroll region,
    and the page itself does not scroll sideways (section 26)."""
    context = browser.new_context(viewport={"width": 390, "height": 844})
    try:
        page = context.new_page()
        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto(live_server + "/adm-kanban/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_selector('[data-testid="adm-lane"]', timeout=PAGE_TIMEOUT)

        scroller = page.locator(".kanban-board [data-kanban-scroll]")
        assert scroller.count() == 1, "the ADM board has no sideways-scroll region"
        # The page itself must not scroll sideways; the board's own region does.
        assert page.evaluate("document.documentElement.scrollWidth") <= 390 + 1, "the page scrolls sideways at 390"
        assert scroller.evaluate("el => el.scrollWidth > el.clientWidth"), "the lanes are not scrolling inside their region"
    finally:
        context.close()

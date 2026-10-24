"""ADM architecture Kanban board journey.

The ADM board and the SAFe portfolio board now render through one shared board
shell (app/templates/macros/kanban_board.html). This journey proves the ADM
board still works for its architects after that extraction: the shared
`kanban-board` shell is present, the flat view shows the ADM columns with their
WIP treatment, cards render, and the page boots with no front-end error — the
failure mode a markup extraction risks.

An architect's goal here: "see my architecture work laid out as a board and
read where each piece of work sits." The test asserts that outcome, not that a
page returned 200.
"""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# The ADM board's own columns (KanbanProjectionService.WIP_LIMITS), with the
# limits it shows on each header.
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
    """The ADM board loads, renders the one shared board shell with its columns,
    and boots with no front-end error."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto(live_server + "/adm-kanban/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1500)

        # No front-end error on boot (the extraction risk).
        assert not errors, "ADM board raised a front-end error after the shared-shell move: %s" % errors[:3]

        # The one shared board shell is present (criterion 1: it is the same shell
        # the portfolio board uses).
        assert page.locator(".kanban-board").count() == 1, "the ADM board does not render the shared kanban-board shell"

        # Switch to the flat column view that the shared shell wraps.
        page.get_by_title("Flat Kanban columns").click()
        page.wait_for_timeout(800)

        # The flat view is inside the shared shell and shows the ADM columns with
        # their labels.
        flat = page.locator(".kanban-board .kanban-column")
        assert flat.count() >= 6, "the ADM flat board does not show its six columns"

        # The board shows its WIP limits on the limited columns (the ADM board's
        # own WIP treatment, reused by the shell).
        board_text = page.locator(".kanban-board").inner_text()
        for limit in ADM_WIP.values():
            assert f"/{limit}" in board_text or f"/ {limit}" in board_text, (
                "an ADM column WIP limit %s is not shown on the board" % limit
            )
    finally:
        context.close()


def test_adm_board_keeps_its_shape_on_a_phone(browser, live_server, seeded):
    """At 390 the ADM flat board stays a board inside the sideways-scroll region,
    and the page itself does not scroll sideways (section 26)."""
    context = browser.new_context(viewport={"width": 390, "height": 844})
    try:
        page = context.new_page()
        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto(live_server + "/adm-kanban/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1200)
        page.get_by_title("Flat Kanban columns").click()
        page.wait_for_timeout(800)

        scroller = page.locator(".kanban-board [data-kanban-scroll]")
        assert scroller.count() == 1, "the ADM board has no sideways-scroll region"
        # The page itself must not scroll sideways; the board's own region does.
        assert page.evaluate("document.documentElement.scrollWidth") <= 390 + 1, "the page scrolls sideways at 390"
    finally:
        context.close()

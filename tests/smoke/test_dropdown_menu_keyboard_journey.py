"""Every dropdown menu works by keyboard, the way shadcn's does.

An architect who never touches the mouse opens a menu with Enter and lands on
its first item, walks it with the arrow keys, chooses with Enter, and finds
focus where they need it: back on the menu button, or on the board card that
the choice moved. Escape closes the menu and returns to its button.
"""
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login
from .test_portfolio_kanban_enterprise_architect import (
    _assert_signed_in_as_enterprise_architect,
    _make_enterprise_architect,
    _open_board,
)
from .test_portfolio_kanban_journey import _seed_epics

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

TEMPLATES = Path(__file__).resolve().parents[2] / "app" / "templates"


@pytest.fixture
def own_org(seeded, request):
    from .conftest import _seed_standard_org

    return _seed_standard_org(request, None)["ids"]["org"]


def _focused_name(page):
    return page.evaluate(
        "() => { const e = document.activeElement; return e ? "
        "(e.getAttribute('aria-label') || e.textContent || '').trim() : ''; }")


def _enterprise_architect_email(seeded):
    from app import create_app
    from app.models.user import User

    email = seeded["emails"]["enterprise_architect"]
    with create_app("testing").app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == "enterprise_architect"
        assert not user.is_platform_admin
    return email


def test_board_card_menu_by_keyboard_returns_focus_to_the_moved_card(browser, live_server, own_org):
    org_id = own_org
    _seed_epics(org_id)
    email = _make_enterprise_architect(org_id)
    _assert_signed_in_as_enterprise_architect(email)
    context, page = _open_board(browser, live_server, email)
    try:
        card = page.locator(".kanban-card").filter(has_text="Legacy Migration Epic")
        trigger = card.get_by_test_id("card-actions")
        trigger.focus()
        page.keyboard.press("Enter")
        expect(trigger).to_have_attribute("aria-expanded", "true")
        # Focus moves to the first choice, not left on the button.
        page.wait_for_function("() => document.activeElement.getAttribute('role') === 'menuitem'")
        first = _focused_name(page)
        assert first, "the first menu item has no name"
        page.keyboard.press("ArrowDown")
        second = _focused_name(page)
        assert second and second != first
        page.keyboard.press("End")
        last = _focused_name(page)
        page.keyboard.press("Home")
        assert _focused_name(page) == first
        assert last != first

        # Escape closes, focus back on the button.
        page.keyboard.press("Escape")
        expect(trigger).to_have_attribute("aria-expanded", "false")
        assert page.evaluate("() => document.activeElement.dataset.testid") == "card-actions"

        # Typing a letter jumps to the item that starts with it.
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement.getAttribute('role') === 'menuitem'")
        page.keyboard.press("r")
        assert page.evaluate("() => document.activeElement.textContent.trim()") == "Reviewing"

        # Choose Reviewing by keyboard: the card moves and keeps the keyboard.
        with page.expect_response(lambda r: r.request.method == "PATCH" and "/api/safe/portfolio/" in r.url) as moved:
            page.keyboard.press("Enter")
        assert moved.value.status == 200
        page.wait_for_function(
            "() => { const e = document.activeElement; return e && e.classList.contains('kanban-card') "
            "&& e.closest('.kanban-column').dataset.state === 'Reviewing' "
            "&& e.textContent.includes('Legacy Migration Epic'); }", timeout=PAGE_TIMEOUT)
    finally:
        context.close()


def test_application_row_menu_by_keyboard_opens_the_chosen_page(browser, live_server, seeded):
    email = _enterprise_architect_email(seeded)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/applications/", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        trigger = page.locator("table [data-testid^='btn-actions-']").first
        expect(trigger).to_be_visible(timeout=PAGE_TIMEOUT)
        trigger.focus()
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement.getAttribute('role') === 'menuitem'")
        assert _focused_name(page) == "View"
        page.keyboard.press("ArrowDown")
        assert _focused_name(page) == "Fact sheet"
        page.keyboard.press("ArrowUp")
        assert _focused_name(page) == "View"
        page.keyboard.press("Escape")
        assert page.evaluate("() => document.activeElement.dataset.testid") == trigger.get_attribute("data-testid")

        # Enter on a link choice lands on a page that has the application on it.
        page.keyboard.press("ArrowDown")
        page.wait_for_function("() => document.activeElement.getAttribute('role') === 'menuitem'")
        page.keyboard.press("Space")
        page.wait_for_url(re.compile(r"/applications/\d+"), timeout=PAGE_TIMEOUT)
        expect(page.locator("main, #main-content").first).not_to_be_empty()
        assert page.locator("h1").first.inner_text().strip()
    finally:
        context.close()


def test_user_menu_by_keyboard(browser, live_server, seeded):
    email = _enterprise_architect_email(seeded)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/applications/", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        trigger = page.locator("#user-menu-btn")
        trigger.focus()
        page.keyboard.press("Enter")
        expect(trigger).to_have_attribute("aria-expanded", "true")
        page.wait_for_function("() => document.activeElement.getAttribute('role') === 'menuitem'")
        assert _focused_name(page) == "Profile"
        page.keyboard.press("ArrowDown")
        assert _focused_name(page) == "Settings"
        page.keyboard.press("End")
        assert _focused_name(page) == "Log out"
        page.keyboard.press("p")
        assert _focused_name(page) == "Profile"
        page.keyboard.press("Escape")
        expect(trigger).to_have_attribute("aria-expanded", "false")
        assert page.evaluate("() => document.activeElement.id") == "user-menu-btn"
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement.getAttribute('role') === 'menuitem'")
        page.keyboard.press("Space")
        page.wait_for_url(re.compile(r"/account/"), timeout=PAGE_TIMEOUT)
        assert page.locator("h1").first.inner_text().strip()
    finally:
        context.close()


def test_every_page_that_uses_the_menu_loads_its_keyboard_script():
    """Every template using the shared menu reaches a layout that loads the script,
    and the menu markup carries the hooks the script needs."""
    layout_ok = {}

    def reaches_script(rel):
        if rel in layout_ok:
            return layout_ok[rel]
        text = (TEMPLATES / rel).read_text()
        if "ui/dropdown_menu.js" in text:
            layout_ok[rel] = True
            return True
        parent = re.search(r"{%-?\s*extends\s+['\"]([^'\"]+)['\"]", text)
        layout_ok[rel] = bool(parent) and reaches_script(parent.group(1))
        return layout_ok[rel]

    users = [
        p.relative_to(TEMPLATES).as_posix()
        for p in TEMPLATES.rglob("*.html")
        if "components/dropdown_menu.html" in p.read_text() and p.name != "dropdown_menu.html"
        and "{% extends" in p.read_text()
    ]
    assert users, "no page uses the shared menu"
    missing = [u for u in users if not reaches_script(u)]
    assert not missing, "pages whose menus would have no keyboard support: %s" % missing

    macro = (TEMPLATES / "components" / "dropdown_menu.html").read_text()
    for hook in ("data-dropdown-menu", "data-menu-trigger", 'aria-haspopup="menu"', 'role="menu"'):
        assert hook in macro
    assert 'role="dialog"' not in macro

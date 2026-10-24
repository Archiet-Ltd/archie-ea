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
        # A screen reader hears the whole phrase, not just the column.
        assert _focused_name(page) == "Move to Reviewing"

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
        # Navigation v3: the user menu sits in the sidebar footer.
        assert _focused_name(page) == "Account"
        page.keyboard.press("ArrowDown")
        assert _focused_name(page).startswith("Dark theme")
        page.keyboard.press("End")
        assert _focused_name(page) == "Sign out"
        page.keyboard.press("a")
        assert _focused_name(page) == "Account"
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


def _name_of_menu(page, menu):
    """What a screen reader says for a menu: the text of the element it points at."""
    return menu.evaluate(
        "m => { const ids = (m.getAttribute('aria-labelledby') || '').split(/\\s+/).filter(Boolean); "
        "const label = ids.map(i => { const e = document.getElementById(i); "
        "return e ? (e.getAttribute('aria-label') || e.textContent || '').trim() : ''; }).join(' ').trim(); "
        "return label || (m.getAttribute('aria-label') || '').trim(); }")


def test_every_open_menu_has_a_name_and_the_user_menu_opens_on_arrow_down(browser, live_server, seeded):
    email = _enterprise_architect_email(seeded)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/applications/", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")

        # ArrowDown on the user menu button opens it and lands on Profile.
        user = page.locator("#user-menu-btn")
        assert user.get_attribute("aria-haspopup") == "menu"
        user.focus()
        page.keyboard.press("ArrowDown")
        expect(user).to_have_attribute("aria-expanded", "true")
        page.wait_for_function("() => document.activeElement.getAttribute('role') === 'menuitem'")
        assert _focused_name(page) == "Profile"
        assert _name_of_menu(page, page.locator("#user-dropdown")) == "User menu"
        page.keyboard.press("Escape")

        # The business unit menu is named too.
        scope = page.locator("#bu-scope-btn")
        assert scope.get_attribute("aria-haspopup") == "menu"
        assert _name_of_menu(page, page.locator("#bu-scope-dropdown")) == "Business unit scope"
        page.keyboard.press("Escape")

        # A row menu says which application it belongs to.
        trigger = page.locator("table [data-testid^='btn-actions-']").first
        label = trigger.get_attribute("aria-label")
        assert label and label.startswith("Actions for ")
        trigger.click()
        menu = trigger.locator("xpath=ancestor::*[@data-dropdown-menu][1]").locator("[role=menu]")
        expect(menu).to_be_visible()
        assert _name_of_menu(page, menu) == label
    finally:
        context.close()


def test_bell_opens_a_popover_with_the_notification_rows_inside(browser, live_server, seeded, own_org):
    from app import create_app, db
    from app.models.models import Notification
    from app.models.user import User

    email = seeded["emails"]["enterprise_architect"]
    with create_app("testing").app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == "enterprise_architect"
        assert not user.is_platform_admin
        db.session.add(Notification(user_id=user.id, organization_id=user.organization_id,
                                    message="Review the Payments Hub proposal", url="/applications/",
                                    read=False))
        db.session.commit()
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/applications/", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        bell = page.locator("#notif-btn")
        assert bell.get_attribute("aria-haspopup") == "dialog"
        bell.click()
        panel = page.locator("#notif-dropdown")
        expect(panel).to_be_visible()
        # Not a menu: a popover, named by its heading, with the rows inside it.
        assert panel.get_attribute("role") == "dialog"
        assert page.locator("#notif-dropdown [role=menu]").count() == 0
        assert _name_of_menu(page, panel) == "Notifications"
        row = panel.locator("a.notif-item").first
        expect(row).to_contain_text("Review the Payments Hub proposal", timeout=PAGE_TIMEOUT)
        # Focus is inside the popover and the row is reachable by keyboard.
        page.wait_for_function("() => document.activeElement.closest('#notif-dropdown') !== null")
        for _ in range(4):
            if page.evaluate("() => document.activeElement.classList.contains('notif-item')"):
                break
            page.keyboard.press("Tab")
        assert page.evaluate("() => document.activeElement.classList.contains('notif-item')")
        # Escape closes it and gives the bell its focus back.
        page.keyboard.press("Escape")
        expect(panel).to_be_hidden()
        assert page.evaluate("() => document.activeElement.id") == "notif-btn"
        # The row lands on a page with content.
        bell.click()
        panel.locator("a.notif-item").first.click()
        page.wait_for_url(re.compile(r"/applications/"), timeout=PAGE_TIMEOUT)
        assert page.locator("h1").first.inner_text().strip()
    finally:
        context.close()


def test_row_menu_in_the_last_column_is_not_clipped_by_the_table(browser, live_server, seeded):
    email = _enterprise_architect_email(seeded)
    for width in (1440, 390):
        context = browser.new_context(viewport={"width": width, "height": 900})
        page = context.new_page()
        try:
            _login(page, live_server, email)
            page.goto(live_server + "/applications/", timeout=PAGE_TIMEOUT)
            page.wait_for_load_state("networkidle")
            triggers = page.locator("[data-testid^='btn-actions-']:visible")
            assert triggers.count() >= 2, "need several rows to see an overlap"
            trigger = triggers.first
            trigger.scroll_into_view_if_needed()
            trigger.click()
            menu = trigger.locator("xpath=ancestor::*[@data-dropdown-menu][1]").locator("[role=menu]")
            expect(menu).to_be_visible()
            page.wait_for_timeout(300)
            items = menu.locator("[role=menuitem]")
            assert items.count() >= 2
            vw = page.evaluate("() => document.documentElement.clientWidth")
            # Every item is on screen and is the topmost thing at its own centre:
            # nothing from the table clips it or covers it.
            for i in range(items.count()):
                box = items.nth(i).bounding_box()
                assert box and box["x"] >= 0 and box["x"] + box["width"] <= vw + 1, (width, i, box)
                top = items.nth(i).evaluate(
                    "(el) => { const r = el.getBoundingClientRect(); "
                    "return [[0.5, 0.5], [0.97, 0.5], [0.03, 0.2], [0.97, 0.8]].every(([fx, fy]) => { "
                    "const t = document.elementFromPoint(r.x + r.width * fx, r.y + r.height * fy); "
                    "return !!t && el.contains(t); }); }")
                assert top, "menu item %d is covered or clipped at %dpx" % (i, width)
            # The next row's button must not be what a click on the menu hits.
            last = items.nth(items.count() - 1).bounding_box()
            hit = page.evaluate(
                "([x, y]) => { const t = document.elementFromPoint(x, y); "
                "return t && t.closest('[role=menu]') ? 'menu' : (t ? t.tagName : 'none'); }",
                [last["x"] + last["width"] / 2, last["y"] + last["height"] / 2])
            assert hit == "menu", (width, hit)
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

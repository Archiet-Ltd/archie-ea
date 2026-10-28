"""The account menu opens as a real panel, not six items drawn over the sidebar.

A signed-in person on the dashboard opens the account menu in the sidebar footer.
The panel sizes itself to its contents, has its own background, and sits above the
sidebar without overlapping its entries. Every item is reachable, Escape closes it
and returns focus to the trigger, and on a phone the panel fits the viewport with
no horizontal overflow. axe reports no serious or critical violation with the menu
open, at both widths and in both themes.

Played at 1440x900 and 390x844. Screenshots under the seat's shots directory.
"""
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

TRIGGER = "#user-menu-btn"
PANEL = "div[role=menu][aria-labelledby=user-menu-btn]"
ITEMS = ("Account", "Billing", "Team", "Settings", "Dark theme", "Sign out")

SHOTS = Path("/home/ubuntu/verify/seats/acctmenu01k/shots")
AXE = Path(
    "/home/ubuntu/verify/archie-ea/venv/lib/python3.12/site-packages/"
    "axe_playwright_python/axe.min.js"
)
AXE_RUN = """async () => {
  const r = await axe.run(document, {
    runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa']},
    resultTypes: ['violations']
  });
  return r.violations
    .filter(v => v.impact === 'serious' || v.impact === 'critical')
    .map(v => ({id: v.id, impact: v.impact, help: v.help, n: v.nodes.length}));
}"""

# A person who administers the organisation sees Billing and Team; an architect
# would not, so the panel's six items are asserted against an org admin.


def _org_admin_email():
    import uuid

    from app import create_app, db
    from app.models.user import Role, User
    from app.modules.account.services.account_service import AccountService
    from tests.smoke.conftest import PASSWORD

    app = create_app("testing")
    email = "acctmenu.owner.%s@example.com" % uuid.uuid4().hex[:8]
    with app.test_request_context():
        Role.insert_roles()
        AccountService.register_user("Ada", "Owner", email, PASSWORD)
        db.session.remove()
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.is_org_admin and not user.is_platform_admin
    return email


def _close_menu(page):
    trigger = page.locator(TRIGGER)
    if trigger.get_attribute("aria-expanded") == "true":
        page.keyboard.press("Escape")
        expect(trigger).to_have_attribute("aria-expanded", "false")


def _open_drawer_on_phone(page):
    """The phone sidebar is a drawer; open it so the footer trigger is on screen."""
    drawer = page.get_by_role("dialog", name="Navigation")
    if not drawer.is_visible():
        page.get_by_test_id("sidebar-trigger").click()
    expect(drawer).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.locator(TRIGGER)).to_be_visible(timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(200)


def _open_menu(page):
    trigger = page.locator(TRIGGER)
    expect(trigger).to_be_visible(timeout=PAGE_TIMEOUT)
    # The trigger toggles, so open from a known-closed state.
    _close_menu(page)
    trigger.click()
    expect(trigger).to_have_attribute("aria-expanded", "true")
    panel = page.locator(PANEL)
    expect(panel).to_be_visible()
    # The script places the open panel on the next tick; let it settle.
    page.wait_for_timeout(200)
    return panel


def _boxes_intersect(a, b):
    return not (
        a["x"] + a["width"] <= b["x"]
        or b["x"] + b["width"] <= a["x"]
        or a["y"] + a["height"] <= b["y"]
        or b["y"] + b["height"] <= a["y"]
    )


def _inject_axe(page):
    page.add_script_tag(content=AXE.read_text())


def test_account_menu_is_a_real_panel_above_the_sidebar(browser, live_server):
    email = _org_admin_email()
    SHOTS.mkdir(parents=True, exist_ok=True)

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        sign_in(page, live_server, email)
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        page.wait_for_function("window.Alpine !== undefined", timeout=PAGE_TIMEOUT)

        panel = _open_menu(page)
        box = panel.bounding_box()

        # The panel is sized to its contents, not collapsed to a sliver.
        assert box and box["height"] >= 100, box

        # It has its own background: not see-through.
        bg = panel.evaluate("el => getComputedStyle(el).backgroundColor")
        assert bg not in ("rgba(0, 0, 0, 0)", "transparent"), bg

        # The panel covers the sidebar: wherever a sidebar entry's box would meet
        # the panel's box, the panel is the thing painted on top, not the entry
        # bleeding through. A sidebar entry is painted over the panel only when
        # the panel has no background and collapses behind it, which is the defect.
        entries = page.locator("[data-sidebar=menu-button]")
        for i in range(entries.count()):
            entry = entries.nth(i)
            # The trigger itself carries data-sidebar=menu-button; skip it.
            if entry.evaluate("(el, p) => el.closest(p) !== null", "[data-dropdown-menu]"):
                continue
            eb = entry.bounding_box()
            if not eb or not _boxes_intersect(box, eb):
                continue
            # Sample the centre of the overlap region: the panel must be on top.
            cx = (max(box["x"], eb["x"]) + min(box["x"] + box["width"], eb["x"] + eb["width"])) / 2
            cy = (max(box["y"], eb["y"]) + min(box["y"] + box["height"], eb["y"] + eb["height"])) / 2
            on_top = page.evaluate(
                "([x, y]) => { const t = document.elementFromPoint(x, y); "
                "return !!t && !!t.closest('[role=menu][aria-labelledby=user-menu-btn]'); }",
                [cx, cy])
            assert on_top, (
                "sidebar entry %d bleeds through the account panel at (%d, %d)"
                % (i, cx, cy))

        # Every item is visible inside the panel's box.
        for name in ITEMS:
            item = panel.get_by_text(name, exact=False).first
            expect(item).to_be_visible()
            ib = item.bounding_box()
            assert ib and _boxes_intersect(box, ib), "%r is not inside the panel" % name

        page.screenshot(path=str(SHOTS / "light1440-menu-open.png"))

        # axe: no serious or critical violation with the menu open, light 1440.
        _inject_axe(page)
        light = page.evaluate(AXE_RUN)
        assert not light, "serious/critical axe violations (light 1440): %r" % light

        # Dark theme at 1440: open the menu again and sweep axe.
        page.evaluate("() => document.documentElement.classList.add('dark')")
        panel = _open_menu(page)
        page.screenshot(path=str(SHOTS / "dark1440-menu-open.png"))
        _inject_axe(page)
        dark = page.evaluate(AXE_RUN)
        assert not dark, "serious/critical axe violations (dark 1440): %r" % dark
        page.evaluate("() => document.documentElement.classList.remove('dark')")

        # Escape closes the panel and returns focus to the trigger.
        page.keyboard.press("Escape")
        expect(page.locator(TRIGGER)).to_have_attribute("aria-expanded", "false")
        assert page.evaluate("() => document.activeElement.id") == "user-menu-btn"
    finally:
        context.close()


def test_account_menu_fits_the_phone_viewport(browser, live_server):
    email = _org_admin_email()
    SHOTS.mkdir(parents=True, exist_ok=True)

    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    try:
        sign_in(page, live_server, email)
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        page.wait_for_function("window.Alpine !== undefined", timeout=PAGE_TIMEOUT)

        # On a phone the sidebar is a drawer; open it to reach the footer trigger.
        _open_drawer_on_phone(page)

        panel = _open_menu(page)
        box = panel.bounding_box()
        assert box and box["height"] >= 100, box

        # No horizontal overflow and the panel's right edge is on screen.
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
        assert box["x"] + box["width"] <= 390 + 1, box

        page.screenshot(path=str(SHOTS / "light390-menu-open.png"))
        _inject_axe(page)
        light = page.evaluate(AXE_RUN)
        assert not light, "serious/critical axe violations (light 390): %r" % light

        _close_menu(page)
        page.evaluate("() => document.documentElement.classList.add('dark')")
        _open_drawer_on_phone(page)
        panel = _open_menu(page)
        box = panel.bounding_box()
        assert box["x"] + box["width"] <= 390 + 1, box
        page.screenshot(path=str(SHOTS / "dark390-menu-open.png"))
        _inject_axe(page)
        dark = page.evaluate(AXE_RUN)
        assert not dark, "serious/critical axe violations (dark 390): %r" % dark
    finally:
        context.close()

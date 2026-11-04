"""A person on a phone can open every dialog and menu and reach its Save button.

Goal: at 390 and 360 wide an architect opens Add Plateau, Add Gap, Add Work Package,
Create Change Request and the composer's Viewpoint and Save menus, and each one fits the
screen or scrolls inside it, with its submit control reachable and pressable.
"""

import pytest

from .conftest import PAGE_TIMEOUT
from .test_a11y04_detail_pages import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# The box of an open overlay must lie inside the window, or its content must scroll within it.
FIT = """el => {
  const W = window.innerWidth, H = window.innerHeight;
  const box = el.getBoundingClientRect();
  const scroller = [el, ...el.querySelectorAll('*')].find(e => {
    const o = getComputedStyle(e).overflowY;
    return (o === 'auto' || o === 'scroll') && e.scrollHeight > e.clientHeight + 1 && e.clientHeight > 0;
  });
  return {left: box.left, right: box.right, top: box.top, bottom: box.bottom, W, H,
          scrolls: !!scroller, topPageScroll: document.scrollingElement.scrollHeight > H};
}"""

DIALOGS = [
    ("/capability-roadmap", "Add Plateau", "#rdm-create-plateau-modal", "Create Plateau"),
    ("/capability-roadmap", "Add Gap", "#rdm-create-gap-modal", "Create Gap"),
    ("/capability-roadmap", "Add Work Package", "#rdm-create-wp-modal", "Create Work Package"),
    ("/strategic/impact-analysis", "Create Change Request", "#initiative-change-request-modal", "Create"),
]
MENUS = [("Viewpoint", "composer-viewpoint-menu-trigger"), ("Save", "composer-save-menu-trigger")]


def _page(browser, width):
    ctx = browser.new_context(viewport={"width": width, "height": 800}, is_mobile=True, has_touch=True)
    ctx.set_default_timeout(PAGE_TIMEOUT)
    return ctx, ctx.new_page()


@pytest.mark.parametrize("width", [390, 360])
@pytest.mark.parametrize("path,label,root,submit", DIALOGS)
def test_phone_dialog_fits_and_save_is_reachable(browser, live_server, seeded, width, path, label, root, submit):
    ctx, page = _page(browser, width)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + path, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        page.locator('main button:has-text("%s")' % label).first.click()
        page.wait_for_timeout(700)
        dialog = page.locator(root)
        panel = dialog.locator('[class*="max-h-"]').first
        fit = panel.evaluate(FIT)
        assert fit["left"] >= -1 and fit["right"] <= fit["W"] + 1, fit
        assert fit["top"] >= -1 and fit["bottom"] <= fit["H"] + 1, "dialog runs past the screen: %s" % fit
        button = dialog.get_by_role("button", name=submit, exact=True).last
        # The submit button is in view without scrolling the form, and nothing covers it.
        box = button.bounding_box()
        assert box and box["y"] >= 0 and box["y"] + box["height"] <= fit["H"] + 1, "Save is off screen: %s" % box
        covered = button.evaluate(
            "b => { const r = b.getBoundingClientRect(); const t = document.elementFromPoint(r.x + r.width/2, r.y + r.height/2); return !(t === b || b.contains(t)); }"
        )
        assert not covered, "something covers the Save button"
    finally:
        ctx.close()


@pytest.mark.parametrize("width", [390, 360])
@pytest.mark.parametrize("label,trigger_id", MENUS)
def test_phone_composer_menu_fits(browser, live_server, seeded, width, label, trigger_id):
    ctx, page = _page(browser, width)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/archimate/composer", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        page.locator("#" + trigger_id).click()
        page.wait_for_timeout(500)
        menu = page.locator("[role=menu]:visible").first
        fit = menu.evaluate(FIT)
        assert fit["left"] >= -1 and fit["right"] <= fit["W"] + 1, "menu leaves the side of the screen: %s" % fit
        assert fit["bottom"] <= fit["H"] + 1 or fit["scrolls"], "menu runs past the bottom and does not scroll: %s" % fit
    finally:
        ctx.close()


def _open(page, live_server, path):
    page.goto(live_server + path, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(800)


@pytest.mark.parametrize("width", [390, 360])
def test_phone_chat_new_chat_button_can_be_pressed(browser, live_server, seeded, width):
    ctx, page = _page(browser, width)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        _open(page, live_server, "/ai-chat")
        page.get_by_role("button", name="Show chat history", exact=True).click()
        page.wait_for_timeout(500)
        new_chat = page.get_by_role("button", name="Start a new chat")
        box = new_chat.bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, "New chat is off screen: %s" % box
        new_chat.click(timeout=4000)
    finally:
        ctx.close()


@pytest.mark.parametrize("width", [390, 360])
@pytest.mark.parametrize(
    "path,opener,first_name,dialog_selector",
    [
        ("/admin/governance-gates", "Add your first gate", None, "[role=dialog][aria-modal=true]:has(#gg-risk-mitigations)"),
        ("/codegen/system-boundaries", "Create First Boundary", None, "h2:has-text(\"New System Boundary\")"),
    ],
)
def test_phone_modal_closes_on_escape_and_opens_again(browser, live_server, seeded, width, path, opener, first_name, dialog_selector):
    ctx, page = _page(browser, width)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        _open(page, live_server, path)
        trigger = page.get_by_role("button", name=opener).first
        trigger.click(timeout=4000)
        shown = page.locator(dialog_selector).first
        shown.wait_for(state="visible")
        page.keyboard.press("Escape")
        page.wait_for_timeout(600)
        assert not shown.is_visible(), "Escape did not close the dialog"
        trigger.click(timeout=4000)  # nothing is left covering the page
        shown.wait_for(state="visible")
    finally:
        ctx.close()


@pytest.mark.parametrize("width", [390, 360])
@pytest.mark.parametrize(
    "path,button_name",
    [
        ("/archimate/import", "Upload & Create Elements"),
        ("/archimate/import/diagram", "Preview"),
        ("/architecture/import/oef", "Preview import"),
        ("/batch-import/new", "Create Import Job"),
    ],
)
def test_phone_disabled_button_says_why(browser, live_server, seeded, width, path, button_name):
    ctx, page = _page(browser, width)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        _open(page, live_server, path)
        button = page.get_by_role("button", name=button_name, exact=True).first
        assert button.is_disabled()
        reason = page.locator('[data-testid="disabled-reason"]:visible').first
        assert reason.is_visible(), "a disabled button gives no reason"
        assert len(reason.inner_text().strip()) > 15
        assert button.get_attribute("aria-describedby") == reason.get_attribute("id")
    finally:
        ctx.close()


@pytest.mark.parametrize("width", [390, 360])
def test_phone_boundary_create_button_says_why_and_enables(browser, live_server, seeded, width):
    ctx, page = _page(browser, width)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        _open(page, live_server, "/codegen/system-boundaries")
        page.get_by_role("button", name="Create First Boundary").first.click(timeout=4000)
        create = page.get_by_role("button", name="Create", exact=True)
        assert create.is_disabled()
        assert "Name the boundary" in page.locator('[data-testid="disabled-reason"]:visible').inner_text()
        page.get_by_placeholder("e.g. Claims Processing Platform").fill("Claims")
        assert create.is_enabled()
    finally:
        ctx.close()


@pytest.mark.parametrize("width", [390, 360])
def test_phone_fetch_filter_options_says_what_to_do(browser, live_server, seeded, width):
    ctx, page = _page(browser, width)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        _open(page, live_server, "/admin/abacus-settings")
        page.locator("#btn-discover-now").click()
        error = page.locator('[data-testid="discover-filters-error"]')
        error.wait_for(state="visible")
        assert "No Abacus connection is saved yet" in error.inner_text()
        assert page.locator("#btn-discover-now").inner_text().strip() == "Fetch Filter Options Now"
    finally:
        ctx.close()

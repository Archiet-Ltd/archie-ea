"""A person on a phone can open every dialog and menu and reach its Save button.

Goal: at 390 and 360 wide an architect opens Add Plateau, Add Gap, Add Work Package,
Create Change Request and the composer's Viewpoint and Save menus, and each one fits the
screen or scrolls inside it, with its submit control reachable and pressable.
"""

import re

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

# Phone widths, light, plus a desktop width and a dark phone: a change for the phone must not
# move or break anything at 1440, and must read the same in the dark theme.
VIEWS = [(390, "light"), (360, "light"), (1440, "light"), (390, "dark")]
PHONE_VIEWS = [(390, "light"), (360, "light"), (390, "dark")]


def _page(browser, width, scheme="light"):
    phone = width <= 500
    ctx = browser.new_context(
        viewport={"width": width, "height": 800 if phone else 900},
        is_mobile=phone,
        has_touch=phone,
        color_scheme=scheme,
    )
    ctx.add_init_script("try{localStorage.setItem('theme','%s')}catch(e){}" % scheme)
    ctx.set_default_timeout(PAGE_TIMEOUT)
    return ctx, ctx.new_page()


@pytest.mark.parametrize("width,scheme", VIEWS)
@pytest.mark.parametrize("path,label,root,submit", DIALOGS)
def test_phone_dialog_fits_and_save_is_reachable(browser, live_server, seeded, width, scheme, path, label, root, submit):
    ctx, page = _page(browser, width, scheme)
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


@pytest.mark.parametrize("width,scheme", VIEWS)
@pytest.mark.parametrize("label,trigger_id", MENUS)
def test_phone_composer_menu_fits(browser, live_server, seeded, width, scheme, label, trigger_id):
    ctx, page = _page(browser, width, scheme)
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


@pytest.mark.parametrize("width,scheme", PHONE_VIEWS)
def test_phone_chat_new_chat_button_can_be_pressed(browser, live_server, seeded, width, scheme):
    ctx, page = _page(browser, width, scheme)
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


@pytest.mark.parametrize("width,scheme", VIEWS)
@pytest.mark.parametrize(
    "path,opener,first_name,dialog_selector",
    [
        ("/admin/governance-gates", "Add your first gate", None, "[role=dialog][aria-modal=true]:has(#gg-risk-mitigations)"),
        ("/codegen/system-boundaries", "Create First Boundary", None, "h2:has-text(\"New System Boundary\")"),
    ],
)
def test_phone_modal_closes_on_escape_and_opens_again(browser, live_server, seeded, width, scheme, path, opener, first_name, dialog_selector):
    ctx, page = _page(browser, width, scheme)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        _open(page, live_server, path)
        trigger = page.get_by_role("button", name=opener).first
        trigger.click(timeout=4000)
        shown = page.locator(dialog_selector).first
        shown.wait_for(state="visible")
        page.wait_for_timeout(400)
        # A screen reader is told a dialog opened, and it has a name.
        dialog = page.locator("[role=dialog][aria-modal=true]:visible").first
        assert dialog.count() == 1, "the open dialog is not announced as a modal dialog"
        assert dialog.evaluate(
            "d => { const id = d.getAttribute('aria-labelledby'); const h = id && document.getElementById(id); return !!(d.getAttribute('aria-label') || (h && h.textContent.trim())); }"
        ), "the dialog has no name"
        # Keyboard focus moves into the dialog and stays there while tabbing.
        assert dialog.evaluate("d => d.contains(document.activeElement)"), "focus did not move into the dialog"
        for _ in range(8):
            page.keyboard.press("Tab")
            assert dialog.evaluate("d => d.contains(document.activeElement)"), "Tab left the dialog for the page behind it"
        page.keyboard.press("Escape")
        page.wait_for_timeout(600)
        assert not shown.is_visible(), "Escape did not close the dialog"
        # Focus goes back to the button that opened it, so a keyboard user is not dropped on the page.
        assert trigger.evaluate("e => e === document.activeElement"), "Escape did not return focus to the opener"
        trigger.click(timeout=4000)  # nothing is left covering the page
        shown.wait_for(state="visible")
    finally:
        ctx.close()


@pytest.mark.parametrize("width,scheme", VIEWS)
@pytest.mark.parametrize(
    "path,button_name",
    [
        ("/archimate/import", "Upload & Create Elements"),
        ("/archimate/import/diagram", "Preview"),
        ("/architecture/import/oef", "Preview import"),
        ("/batch-import/new", "Create Import Job"),
    ],
)
def test_phone_disabled_button_says_why(browser, live_server, seeded, width, scheme, path, button_name):
    ctx, page = _page(browser, width, scheme)
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


@pytest.mark.parametrize("width,scheme", VIEWS)
def test_phone_boundary_create_button_says_why_and_enables(browser, live_server, seeded, width, scheme):
    ctx, page = _page(browser, width, scheme)
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


@pytest.mark.parametrize("width,scheme", VIEWS)
def test_phone_fetch_filter_options_says_what_to_do(browser, live_server, seeded, width, scheme):
    ctx, page = _page(browser, width, scheme)
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


# ── menus stay beside their buttons ────────────────────────────────────────────────────────────

RECT = "e => { const r = e.getBoundingClientRect(); return {left: r.left, top: r.top, right: r.right, bottom: r.bottom}; }"

# A menu with the shared script's markup, attached straight to the body so no ancestor clips it, in
# the real admin page that loads the real script. The trigger sits near the right edge, low down.
SYNTH_MENU = """() => {
  const spacer = document.createElement('div');
  spacer.id = 'synth-spacer';
  spacer.style.cssText = 'height:3000px;width:1px;position:absolute;top:0;left:0;pointer-events:none';
  document.body.appendChild(spacer);
  const root = document.createElement('div');
  root.setAttribute('data-dropdown-menu', '');
  root.style.cssText = 'position:absolute;top:560px;right:12px;width:56px;z-index:40';
  root.innerHTML = '<button type="button" data-menu-trigger aria-expanded="false" id="synth-trigger" style="width:56px;height:32px;background:#ccc">Menu</button>' +
    '<div role="menu" id="synth-panel" hidden style="position:absolute;left:0;top:100%;width:220px;height:180px;background:#eee;border:1px solid #999"><button role="menuitem">One</button></div>';
  document.body.appendChild(root);
  const t = document.getElementById('synth-trigger');
  const p = document.getElementById('synth-panel');
  t.addEventListener('click', () => {
    const open = t.getAttribute('aria-expanded') === 'true';
    t.setAttribute('aria-expanded', open ? 'false' : 'true');
    p.hidden = open;
  });
}"""


@pytest.mark.parametrize("width,scheme", VIEWS)
def test_menu_reopened_after_a_scroll_sits_beside_its_button(browser, live_server, seeded, width, scheme):
    """A menu that had to be moved once is measured again from its button every time it opens."""
    ctx, page = _page(browser, width, scheme)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        _open(page, live_server, "/admin/governance-gates")
        page.evaluate(SYNTH_MENU)
        trigger = page.locator("#synth-trigger")
        panel = page.locator("#synth-panel")
        page.evaluate("window.scrollTo(0, 400)")
        page.wait_for_timeout(200)
        trigger.click()
        page.wait_for_timeout(300)
        first = panel.evaluate(RECT)
        t1 = trigger.evaluate(RECT)
        trigger.click()  # close
        page.wait_for_timeout(200)
        page.evaluate("window.scrollTo(0, 120)")
        page.wait_for_timeout(200)
        trigger.click()
        page.wait_for_timeout(300)
        second = panel.evaluate(RECT)
        t2 = trigger.evaluate(RECT)
        W = page.evaluate("document.documentElement.clientWidth")
        H = page.evaluate("window.innerHeight")
        assert t1["top"] != t2["top"], "the page did not scroll between the two openings"
        for name, box, anchor in (("first", first, t1), ("second", second, t2)):
            assert box["left"] >= -1 and box["right"] <= W + 1, "%s opening leaves the side of the screen: %s" % (name, box)
            beside = abs(box["top"] - anchor["bottom"]) <= 12 or abs(box["bottom"] - anchor["top"]) <= 12
            assert beside, "%s opening is not beside its button: panel %s, button %s" % (name, box, anchor)
            assert box["bottom"] <= H + 1 or box["top"] >= -1, box
    finally:
        ctx.close()


@pytest.mark.parametrize("width", [1024, 768])
def test_composer_save_menu_that_fits_does_not_move(browser, live_server, seeded, width):
    """The Save menu fits beside its button at these widths, so the shared script leaves it where the
    page puts it: the box it has with no placement written on it."""
    ctx, page = _page(browser, width)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/archimate/composer", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        page.locator("#composer-save-menu-trigger").click()
        page.wait_for_timeout(500)
        menu = page.locator("[role=menu]:visible").first
        now = menu.evaluate(RECT)
        natural = menu.evaluate(
            """e => { const keep = e.getAttribute('style');
              ['position','left','top','right','bottom','margin','maxHeight','maxWidth','overflowY'].forEach(k => e.style[k] = '');
              const r = e.getBoundingClientRect(); const out = {left: r.left, top: r.top, right: r.right, bottom: r.bottom};
              if (keep === null) e.removeAttribute('style'); else e.setAttribute('style', keep); return out; }"""
        )
        W = page.evaluate("document.documentElement.clientWidth")
        H = page.evaluate("window.innerHeight")
        assert natural["left"] >= 0 and natural["right"] <= W and natural["bottom"] <= H, "the menu does not fit here: %s" % natural
        for k in ("left", "top", "right", "bottom"):
            assert abs(now[k] - natural[k]) <= 1, "a menu that fits was moved: now %s, where the page puts it %s" % (now, natural)
    finally:
        ctx.close()


# ── the roadmap's three Create buttons save ────────────────────────────────────────────────────

ROADMAP_CREATE = [
    ("Add Plateau", "#rdm-create-plateau-modal", "#rdm-plateau-name", "Create Plateau", {}),
    ("Add Gap", "#rdm-create-gap-modal", "#rdm-gap-name", "Create Gap", {}),
    ("Add Work Package", "#rdm-create-wp-modal", "#rdm-wp-name", "Create Work Package",
     {"#rdm-wp-start": "2026-11-01", "#rdm-wp-target": "2027-01-31"}),
]


@pytest.mark.parametrize("width,scheme", VIEWS)
@pytest.mark.parametrize("label,root,name_field,submit,extra", ROADMAP_CREATE)
def test_roadmap_create_button_saves_and_the_record_is_in_the_roadmap(
    browser, live_server, seeded, width, scheme, label, root, name_field, submit, extra
):
    ctx, page = _page(browser, width, scheme)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        name = "Journey %s %d%s" % (label.split()[-1], width, scheme[0])
        _open(page, live_server, "/capability-roadmap")
        page.locator('main button:has-text("%s")' % label).first.click()
        dialog = page.locator(root)
        panel = dialog.locator('[class*="max-h-"]').first  # the dialog's own wrapper has no box of its own
        panel.wait_for(state="visible")
        # Pressing Create with nothing typed says which field is missing and keeps the dialog open.
        press = dialog.get_by_role("button", name=submit, exact=True).last
        press.scroll_into_view_if_needed()
        press.click()
        page.wait_for_timeout(600)
        assert panel.is_visible(), "the dialog closed with nothing typed"
        message = page.get_by_text(re.compile(r"name.*required", re.I)).first
        assert message.is_visible(), "no plain message names the missing field"
        dialog.locator(name_field).fill(name)
        for selector, value in extra.items():
            dialog.locator(selector).fill(value)
        press.scroll_into_view_if_needed()
        with page.expect_response(lambda r: "/capability-map/api/roadmap/" in r.url and r.request.method == "POST") as sent:
            press.click()
        assert sent.value.status in (200, 201), sent.value.status
        page.wait_for_timeout(800)
        assert not panel.is_visible(), "the dialog stayed open after saving"
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        assert name in page.locator("#capability-roadmap-app").inner_text(), "the new record is not in the roadmap after a reload"
    finally:
        ctx.close()


# ── the source dots of the data architecture cascade can be tapped ─────────────────────────────

@pytest.mark.parametrize("width,scheme", PHONE_VIEWS)
def test_cascade_source_dots_are_tap_sized_and_their_note_closes(browser, live_server, seeded, width, scheme):
    from .test_data_flow_journey import _seed

    _seed(seeded)
    ctx, page = _page(browser, width, scheme)
    try:
        _login(page, live_server, seeded["emails"]["cto"])
        _open(page, live_server, "/architecture/data-architecture")
        dots = page.locator('#data-flow-cascade button[aria-label="Source of this connection"]')
        dots.first.wait_for(state="attached")
        count = dots.count()
        assert count >= 2, "the seeded flow shows %d source dots" % count
        for i in range(count):
            box = dots.nth(i).bounding_box()
            assert box["width"] >= 24 and box["height"] >= 24, "dot %d is %sx%s, under 24x24" % (i, box["width"], box["height"])
        tip = page.locator('#data-flow-cascade [role=tooltip]:visible')
        dots.nth(0).scroll_into_view_if_needed()
        dots.nth(0).click()
        page.wait_for_timeout(300)
        assert tip.count() == 1, "tapping a dot did not open its note"
        dots.nth(0).click()  # a second tap closes it
        page.wait_for_timeout(300)
        assert tip.count() == 0, "a second tap did not close the note"
        dots.nth(0).click()
        page.wait_for_timeout(300)
        assert tip.count() == 1
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        assert tip.count() == 0, "Escape did not close the note"
        dots.nth(0).click()
        page.wait_for_timeout(300)
        page.locator("#data-flow-heading").click()  # a tap elsewhere
        page.wait_for_timeout(300)
        assert tip.count() == 0, "a tap elsewhere did not close the note"
        # With one note open, the next dot can still be tapped and its own note opens.
        dots.nth(0).click()
        page.wait_for_timeout(300)
        dots.nth(1).scroll_into_view_if_needed()
        dots.nth(1).click(timeout=4000)
        page.wait_for_timeout(300)
        assert tip.count() == 1, "the open note blocked or doubled the next dot"
    finally:
        ctx.close()


# ── an unreachable saved Abacus connection is not reported as a success ────────────────────────

def _save_unreachable_connection():
    """A saved Abacus connection whose host does not exist."""
    import json

    from app import create_app, db
    from app.models.models import ExternalSystem

    app = create_app("testing")
    with app.app_context():
        row = ExternalSystem.query.filter_by(system_name="abacus").first() or ExternalSystem(system_name="abacus")
        row.system_type = "ea_tool"
        row.base_url = "https://abacus.invalid"
        row.api_endpoint = "https://abacus.invalid/api"
        row.auth_type = "oauth2"
        row.credentials = json.dumps({"client_id": "journey", "client_secret": "journey-secret"})
        row.enabled = True
        db.session.add(row)
        db.session.commit()


def test_abacus_unreachable_host_says_so_and_does_not_reload(browser, live_server, seeded):
    ctx, page = _page(browser, 390)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        _open(page, live_server, "/admin/abacus-settings")
        _save_unreachable_connection()
        _open(page, live_server, "/admin/abacus-settings")
        loads = []
        page.on("load", lambda: loads.append(1))
        page.locator("#btn-discover-now").click()
        error = page.locator('[data-testid="discover-filters-error"]')
        error.wait_for(state="visible", timeout=30000)
        text = error.inner_text().lower()
        assert "could not" in text or "unreachable" in text or "reach" in text, text
        page.wait_for_timeout(2500)
        assert not loads, "the page reloaded as if the fetch worked"
        assert "discovered" not in page.locator("main").inner_text().lower()
        assert page.locator("#btn-discover-now").inner_text().strip() == "Fetch Filter Options Now"
    finally:
        ctx.close()


# ── a diagram that cannot be read says so; the vendor wizard strip stays in its box ───────────

@pytest.mark.parametrize("width,scheme", PHONE_VIEWS)
def test_unreadable_diagram_preview_shows_the_reason(browser, live_server, seeded, width, scheme):
    ctx, page = _page(browser, width, scheme)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        _open(page, live_server, "/archimate/import/diagram")
        page.locator("input[type=file]").first.set_input_files(
            {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"this is not a diagram"}
        )
        button = page.get_by_role("button", name="Preview", exact=True).first
        button.scroll_into_view_if_needed()
        button.click()
        # The reason is announced to a screen reader as well as drawn beside the button.
        shown = page.locator("[role=alert]:visible", has_text="Import failed").first
        shown.wait_for(state="visible", timeout=15000)
        assert len(shown.inner_text().strip()) > 15, shown.inner_text()
        box = shown.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1, "the reason runs off the screen: %s" % box
        assert 0 <= box["y"] <= page.evaluate("window.innerHeight"), "the reason is out of sight below the button just pressed: %s" % box
    finally:
        ctx.close()


@pytest.mark.parametrize("width,scheme", PHONE_VIEWS)
def test_vendor_analysis_step_strip_stays_inside_the_screen(browser, live_server, seeded, width, scheme):
    ctx, page = _page(browser, width, scheme)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        _open(page, live_server, "/dashboard/vendor-analysis/new")
        nav = page.locator('nav[aria-label="Vendor analysis wizard progress"]')
        nav.wait_for(state="visible")
        box = nav.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1, "the step strip runs past the screen: %s" % box
        # Every step fits inside the strip, or the strip scrolls inside its own box.
        inside = nav.evaluate(
            """n => { const nr = n.getBoundingClientRect(); const items = [...n.querySelectorAll('li')];
              const last = items[items.length - 1].getBoundingClientRect();
              const scrolls = /(auto|scroll)/.test(getComputedStyle(n).overflowX);
              return {fits: last.right <= nr.right + 1, scrolls, lastRight: last.right, navRight: nr.right}; }"""
        )
        assert inside["fits"] or inside["scrolls"], "the last step runs past the strip and the strip does not scroll: %s" % inside
        assert page.evaluate("document.documentElement.scrollWidth") <= width + 1, "the page scrolls sideways"
    finally:
        ctx.close()
